#!/usr/bin/env python3
"""Boot only the disk of an immutable checkpoint, with no guest debugger.

This deliberately discards saved RAM and CPU state. Fresh frame submissions,
not a restored screenshot, are the display witness. Leaves the owned VM paused
for read-only postmortem only when --keep-paused is requested.
"""
import argparse
import collections
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import time

from checkpoint_common import HMP, SAFE_TAG, atomic_json, sha256, verify_backing_chain, wait_for_path


def boot_command(argv, out):
    command, i = [], 0
    remove = {'-incoming', '-loadvm', '-monitor', '-qmp', '-gdb', '-chardev', '-serial'}
    while i < len(argv):
        key = argv[i]
        if key in remove:
            i += 2
        elif key in ('-S', '-s'):
            i += 1
        elif key == '-drive':
            if 'id=ans,' not in argv[i + 1]:
                raise ValueError('unexpected non-ANS drive')
            command += [key, f'if=none,id=ans,file={out}/disk.qcow2,format=qcow2']
            i += 2
        else:
            command.append(key)
            i += 1
    command += ['-monitor', f'unix:{out}/monitor.sock,server=on,wait=off',
                '-qmp', f'unix:{out}/qmp.sock,server=on,wait=off',
                '-chardev', f'socket,id=warm_uart,path={out}/uart.sock,server=on,wait=off,logfile={out}/serial.log',
                '-serial', 'chardev:warm_uart']
    return command


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path, nargs='?',
                   default=Path.home() / 'dvm-artifacts/native-smc/default.json')
    p.add_argument('--tag', required=True)
    p.add_argument('--seconds', type=int, default=180)
    p.add_argument('--keep-paused', action='store_true')
    p.add_argument('--stop-on', help='stop after this literal serial/stderr milestone')
    p.add_argument('--trace-line', help='record timestamps for matching log lines (diagnostic runs)')
    args = p.parse_args()
    trace_line = re.compile(args.trace_line) if args.trace_line else None
    if not SAFE_TAG.fullmatch(args.tag) or len(args.tag) > 40:
        p.error('invalid tag')
    if not 1 <= args.seconds <= 600:
        p.error('seconds must be 1..600')
    manifest = json.loads(args.manifest.read_text())
    if Path(manifest['disk']['path']).resolve() != Path(manifest['disk']['backing_chain'][0]['path']).resolve():
        raise ValueError('selected disk does not match the verified backing chain')
    verify_backing_chain(manifest['disk']['backing_chain'])
    for name, expected in manifest['qemu_inputs'].items():
        if sha256(Path(name)) != expected['sha256']:
            raise RuntimeError(f'checkpoint input changed: {name}')
    out = Path('/tmp/dvm') / args.tag
    out.mkdir(exist_ok=False)
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b',
                    manifest['disk']['path'], str(out / 'disk.qcow2')], check=True)
    command = boot_command(manifest['qemu_argv'], out)
    model_env = {k: v for k, v in manifest['qemu_env'].items()
                 if k.startswith(('DARWIN_', 'GXFSTAT_'))}
    model_env['DARWIN_TOUCH_EVENTS'] = str(out / 'events.jsonl')
    if model_env.get('DARWIN_INPUT_UART', '0') != '0':
        # Native transport: QEMU keeps its own per-run counters/readiness here.
        model_env['DARWIN_INPUT_STATUS'] = str(out / 'input-status.json')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'GXFSTAT_', 'DVM_'))}
    env.update(model_env)
    atomic_json(out / 'launch.json', dict(format='darwin-vm-qemu-launch-v1', argv=command, env=model_env))
    report = dict(manifest=str(args.manifest.resolve()), ram_restored=False,
                  guest_debugger=False, events=[], frames=[], counts={})
    counts = collections.Counter(presentations=0, completions=0, panics=0)
    seen = set()
    patterns = ['Early boot complete', 'AP DRIVER START', 'SpringBoard',
                'backboardd', 'DVM_INPUT_READY', 'DVMI2R R', 'panic(cpu',
                'rebooting due to critical process crashes', 'rejected unsupported']
    if args.stop_on and args.stop_on not in patterns:
        patterns.append(args.stop_on)
    streams = {}
    pending = collections.defaultdict(bytes)
    start = time.monotonic()
    reason = 'observation deadline'
    proc = None
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        with (out / 'stderr.log').open('wb') as log:
            proc = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        (out / 'qemu.pid').write_text(str(proc.pid) + '\n')
        print(f'{args.tag}: PID {proc.pid}; disk-only boot, no GDB endpoint', flush=True)
        wait_for_path(out / 'monitor.sock', time.monotonic() + 15)
        hmp = HMP(out / 'monitor.sock', timeout=10)
        next_frame, next_status = 10, 30
        while time.monotonic() - start < args.seconds:
            elapsed = time.monotonic() - start
            for name in ('serial.log', 'stderr.log'):
                path = out / name
                if name not in streams and path.exists():
                    streams[name] = path.open('rb')
                if name not in streams:
                    continue
                lines = (pending[name] + streams[name].read()).split(b'\n')
                pending[name] = lines.pop()
                for raw in lines:
                    if b'TXM [Error]' in raw:
                        continue
                    line = raw.decode(errors='replace')
                    if trace_line and trace_line.search(line):
                        event = dict(seconds=round(elapsed, 3), file=name, trace=True, line=line)
                        report['events'].append(event)
                        print(json.dumps(event), flush=True)
                    if 'iomfb: presented ' in line:
                        counts['presentations'] += 1
                    if 'D594 completed' in line:
                        counts['completions'] += 1
                    if 'panic(cpu' in line:
                        counts['panics'] += 1
                    for pattern in patterns:
                        if pattern in line and pattern not in seen:
                            seen.add(pattern)
                            event = dict(seconds=round(elapsed, 3), file=name, marker=pattern, line=line)
                            report['events'].append(event)
                            print(json.dumps(event), flush=True)
            if {'panic(cpu', 'rebooting due to critical process crashes', 'rejected unsupported'} & seen:
                reason = 'guest failure'
                break
            if args.stop_on and args.stop_on in seen:
                reason = f'matched milestone: {args.stop_on}'
                break
            if proc.poll() is not None:
                reason = f'QEMU exited {proc.returncode}'
                break
            if elapsed >= next_frame:
                frame = out / f'frame-{int(elapsed):04d}.png'
                answer = hmp.command(f'screendump {frame} -f png')
                if not frame.exists():
                    raise RuntimeError(f'screendump failed: {answer}')
                report['frames'].append(dict(seconds=round(elapsed, 3), path=str(frame),
                                             sha256=sha256(frame), presentations=counts['presentations']))
                next_frame += 10
            if elapsed >= next_status:
                print(f'{elapsed:.1f}s: {dict(counts)}', flush=True)
                next_status += 30
            time.sleep(.2)
    except BaseException as error:
        reason = f'{type(error).__name__}: {error}'
        raise
    finally:
        for stream in streams.values():
            stream.close()
        try:
            if proc and proc.poll() is None:
                hmp = HMP(out / 'monitor.sock', timeout=10)
                hmp.command('stop')
                for name, request in [('status', 'info status'), ('cpus', 'info cpus'),
                                      ('registers', 'info registers'), ('jit', 'info jit')]:
                    (out / f'{name}.txt').write_text(hmp.command(request) + '\n')
                hmp.command(f'screendump {out}/final.png -f png')
        finally:
            if proc and proc.poll() is None and not args.keep_paused:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            report.update(elapsed=time.monotonic()-start, stop_reason=reason,
                          counts=dict(counts), kept_paused=bool(proc and proc.poll() is None))
            atomic_json(out / 'result.json', report)
            print(f'{reason}; {dict(counts)}; evidence: {out}', flush=True)


if __name__ == '__main__':
    main()
