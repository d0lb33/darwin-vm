#!/usr/bin/env python3
"""Bounded exact-System load trial with a continuously drained, private UART.

Uses a fresh child of a hash-pinned installed parent. Never restores old RAM,
publishes a GPU, changes guest registers/memory, or targets another VM.
"""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, SAFE_TAG, atomic_json, sha256, verify_backing_chain, wait_for_path
from proxy_uart import ProxyUART


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest', type=Path)
    p.add_argument('--tag', required=True)
    p.add_argument('--seconds', type=int, default=600)
    p.add_argument('--keep-paused', action='store_true')
    p.add_argument('--worker', type=Path, help='enable one-shot forwarding bridge with this host worker')
    a = p.parse_args()
    if not SAFE_TAG.fullmatch(a.tag) or len(a.tag) > 40 or not 1 <= a.seconds <= 1200:
        p.error('invalid tag or seconds (1..1200)')
    m = json.loads(a.manifest.read_text())
    verify_backing_chain(m['disk']['backing_chain'])
    for name, expected in m['qemu_inputs'].items():
        if sha256(Path(name)) != expected['sha256']:
            raise RuntimeError(f'changed pinned input: {name}')
    out = Path('/tmp/dvm')/a.tag
    out.mkdir(exist_ok=False)
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2',
        '-b', m['disk']['path'], str(out/'disk.qcow2')], check=True)
    original, argv, i = m['qemu_argv'], [], 0
    while i < len(original):
        key = original[i]
        if key in ('-incoming', '-loadvm', '-monitor', '-qmp', '-gdb', '-chardev', '-serial'):
            i += 2
        elif key in ('-S', '-s'):
            i += 1
        elif key == '-drive':
            if not original[i+1].startswith('if=none,id=ans,'):
                raise ValueError('unexpected drive')
            argv += [key, f'if=none,id=ans,file={out}/disk.qcow2,format=qcow2']; i += 2
        elif key == '-display':
            argv += [key, 'none']; i += 2
        else:
            argv.append(key); i += 1
    argv += ['-monitor', f'unix:{out}/monitor.sock,server=on,wait=off',
        '-qmp', f'unix:{out}/qmp.sock,server=on,wait=off',
        '-chardev', f'socket,id=gpu_uart,path={out}/uart.sock,server=on,wait=off,logfile={out}/serial.log',
        '-serial', 'chardev:gpu_uart']
    model = m['qemu_env'].copy()
    model['DARWIN_TOUCH_EVENTS'] = str(out/'events.jsonl')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'DVM_', 'GXFSTAT_'))}
    env.update(model)
    atomic_json(out/'launch.json', dict(format='darwin-vm-qemu-launch-v1', argv=argv, env=model))
    report = dict(manifest=str(a.manifest.resolve()), ram_restored=False, debugger=False, events=[])
    proc, wire, bridge = None, None, None
    started, reason = time.monotonic(), 'deadline'
    try:
        with (out/'stderr.log').open('wb') as log:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        (out/'qemu.pid').write_text(str(proc.pid)+'\n')
        wait_for_path(out/'uart.sock', time.monotonic()+15)
        wire = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        wire.settimeout(5); wire.connect(str(out/'uart.sock')); wire.setblocking(False)
        if a.worker:
            bridge = ProxyUART(a.worker.resolve(), out)
            report['host_worker_sha256'] = sha256(a.worker)
        print(f'{a.tag}: own PID {proc.pid}; UART connected and continuously drained', flush=True)
        pending = b''
        with (out/'wire.log').open('wb') as log:
            while time.monotonic()-started < a.seconds and proc.poll() is None:
                if bridge:
                    bridge.pump(wire)
                if not select.select([wire], [], [], .01 if bridge else .2)[0]:
                    continue
                chunk = wire.recv(65536)
                if not chunk:
                    reason = 'UART closed'; break
                log.write(chunk); log.flush()
                lines = (pending+chunk).split(b'\n'); pending = lines.pop()[-65536:]
                for raw in lines:
                    line = raw.decode(errors='replace').strip()
                    if bridge:
                        bridge.line(line)
                    if any(x in line for x in ('GPU_LOAD_', 'GPU_BUNDLE_', 'DVMGPU_READY', 'DVMGPU_DONE', 'HARNESS_', 'DVM_INPUT_', 'panic(cpu')):
                        event = dict(seconds=round(time.monotonic()-started, 3), line=line)
                        report['events'].append(event); print(json.dumps(event), flush=True)
                    if 'GPU_LOAD_COMPLETE' in line:
                        reason = 'guest load probe completed'
                    elif 'GPU_LOAD_ERROR' in line or 'GPU_BUNDLE_ERROR' in line or 'panic(cpu' in line:
                        reason = 'guest reported failure'
                    elif bridge and bridge.finished and 'DVM_INPUT_START' in line:
                        reason = 'guest forwarding diagnostic finished; original input exec observed'
                if reason != 'deadline':
                    break
    except BaseException as error:
        reason = f'{type(error).__name__}: {error}'
        raise
    finally:
        # A connected but undrained UART can block the event loop. Close it
        # before issuing monitor commands at teardown.
        if wire:
            wire.close()
        if bridge:
            bridge.close()
        try:
            if proc and proc.poll() is None:
                h = HMP(out/'monitor.sock', timeout=15)
                h.command('stop')
                (out/'status.txt').write_text(h.command('info status')+'\n')
                (out/'registers.txt').write_text(h.command('info registers')+'\n')
                h.command(f'screendump {out}/final.png -f png')
        finally:
            if proc and proc.poll() is None and not a.keep_paused:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)
            report.update(elapsed=time.monotonic()-started, stop_reason=reason,
                kept_paused=bool(proc and proc.poll() is None))
            atomic_json(out/'result.json', report)
            print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
