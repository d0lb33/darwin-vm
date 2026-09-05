#!/usr/bin/env python3
"""Bounded CPU benchmark from an immutable RAM/device/disk checkpoint.

Every run verifies checkpoint hashes through restore_checkpoint.py, starts
paused, records an exact-PC witness, then stops at N new User dir-stats events
or a deadline. These events are a work proxy, not migration percent or FPS.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from checkpoint_common import HMP, process_argv_env, wait_pid_exit

EVENT = re.compile(r'set_dir_stats:\d+: (disk1s[25]) setting dir-stats for ino (\d+) ')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('manifest', type=Path)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--qemu', type=Path)
    ap.add_argument('--vector-ext', choices=['0', '1'], default='0')
    ap.add_argument('--events', type=int, default=80)
    ap.add_argument('--seconds', type=float, default=150)
    ap.add_argument('--sample-at', type=float, nargs='*', default=[])
    a = ap.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,40}', a.tag):
        ap.error('invalid tag')
    if a.events < 1 or not 0 < a.seconds <= 180:
        ap.error('events must be positive and seconds in (0, 180]')
    out = Path('/tmp/dvm') / a.tag
    out.mkdir(exist_ok=False, mode=0o700)
    restore = out / 'restore'
    command = [sys.executable, str(ROOT / 'tools/restore_checkpoint.py'),
               str(a.manifest.resolve()), '--tag', a.tag, '--leave-paused', '--out', str(restore)]
    if a.qemu:
        command += ['--qemu', str(a.qemu.resolve())]
    env = dict(os.environ, QEMU_ARM_TCG_VECTOR_EXT=a.vector_ext)
    with (out / 'restore.log').open('w') as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    report = json.loads((restore / 'restore-report.json').read_text())
    pid = report['qemu_pid']
    hmp = HMP(Path(report['monitor']))
    live_argv, live_env = process_argv_env(pid)
    if live_argv != report['argv'] or live_env.get('QEMU_ARM_TCG_VECTOR_EXT') != a.vector_ext:
        raise RuntimeError('restored process identity/options differ')
    serial = restore / 'serial.log'
    samplers = []
    pending = sorted(a.sample_at)
    events, seen, host = [], set(), []
    reason = 'deadline'
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    start = time.monotonic()
    next_stat = 0
    try:
        hmp.command('cont')
        while time.monotonic() - start < a.seconds:
            elapsed = time.monotonic() - start
            text = serial.read_text(errors='replace')
            if 'panic(cpu' in text:
                reason = 'panic'
                break
            for match in EVENT.finditer(text):
                key = match.groups()
                if key not in seen:
                    seen.add(key)
                    events.append({'volume': key[0], 'inode': int(key[1]), 'seconds': elapsed})
                    users = sum(e['volume'] == 'disk1s5' for e in events)
                    if key[0] == 'disk1s5' and users % 20 == 0:
                        print(f'{a.tag}: {users} User metadata updates in {elapsed:.3f}s', flush=True)
            if sum(e['volume'] == 'disk1s5' for e in events) >= a.events:
                reason = 'work limit'
                break
            if elapsed >= next_stat:
                stat = subprocess.check_output(['ps', '-p', str(pid), '-o', 'pid=,pcpu=,time=,rss='], text=True).strip()
                host.append({'seconds': elapsed, 'ps': stat, 'loadavg': os.getloadavg()})
                next_stat = elapsed + 5
            if pending and elapsed >= pending[0]:
                at = pending.pop(0)
                with (out / f'host{at:g}.stderr').open('w') as err:
                    samplers.append(subprocess.Popen(['sample', str(pid), '5', '10', '-file', str(out / f'host{at:g}.txt')], stdout=err, stderr=err))
                print(f'{a.tag}: sampling owned CPU threads at {elapsed:.3f}s', flush=True)
            time.sleep(.02)
    finally:
        elapsed = time.monotonic() - start
        hmp.command('stop')
        (out / 'final-registers.txt').write_text(hmp.command('info registers'))
        (out / 'jit-info.txt').write_text(hmp.command('info jit'))
        for sampler in samplers:
            if sampler.poll() is None:
                sampler.terminate()
            sampler.wait(timeout=10)
        hmp.command('quit')
        if not wait_pid_exit(pid, 10):
            raise RuntimeError(f'owned QEMU {pid} did not exit')
    stderr = (restore / 'qemu.stderr.log').read_text(errors='replace')
    profiles = [dict((k, int(v)) for k, v in re.findall(r'(\w+)=(\d+)', line))
                for line in stderr.splitlines() if 'PROFILE elapsed_ns=' in line]
    result = {'tag': a.tag, 'checkpoint': str(a.manifest.resolve()),
              'binary': report['qemu_binary'], 'exact_pc': report['pc_match_witness'],
              'vector_ext': a.vector_ext, 'seconds': elapsed, 'stop_reason': reason,
              'cpu_options': {k: v for k, v in live_env.items()
                              if k.startswith(('QEMU_ARM_TCG_', 'QEMU_TCG_'))},
              'events': events, 'host': host, 'storage_profiles': profiles,
              'sample_at': a.sample_at, 'qemu_exited': True,
              'panics': serial.read_text(errors='replace').count('panic(cpu')}
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('events', 'host', 'storage_profiles')}, indent=2), flush=True)
    if reason != 'work limit':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
