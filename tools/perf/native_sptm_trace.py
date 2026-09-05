#!/usr/bin/env python3
"""Capture a bounded unmodified-SPTM instruction trace under TCG.

This describes firmware's requested operations, not Apple hardware semantics.
No register, code, data, or disk writes are issued by the debugger. Only the
fresh child process launched here is controlled and cleaned up.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time

from arm_island_bench import ROOT, Remote, terminate
from native_sptm_tables import registers


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    trigger = ap.add_mutually_exclusive_group(required=True)
    trigger.add_argument('--pc', type=lambda x: int(x, 0))
    trigger.add_argument('--watch-write', type=lambda x: int(x, 0), action='append',
                         help='Stop on a byte write; repeat for known VA aliases')
    ap.add_argument('--steps', type=int, default=256)
    ap.add_argument('--seconds', type=int, default=30)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    if (not 1 <= a.steps <= 4096 or not 1 <= a.seconds <= 60 or
            (a.pc is not None and a.pc & 3)):
        ap.error('Require aligned PC, 1..4096 steps, and 1..60 seconds')
    a.out.mkdir(exist_ok=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'max',
           '-accel', 'tcg', '-smp', '1', '-m', '8G', '-display', 'none',
           '-monitor', 'none', '-serial', f'file:{a.out.resolve() / "serial.log"}',
           '-S', '-gdb', f'tcp:127.0.0.1:{port}', '-dtree', str(a.dtree.resolve())]
    for option, name in (('-bootkc', 'bootkc'), ('-sptm', 'sptm'), ('-txm', 'txm'),
                         ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')):
        cmd += [option, str(ROOT / 'firmware' / name)]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
    report = {'command': cmd, 'passed': False, 'trace': [],
              'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256((ROOT / 'firmware/sptm').read_bytes()).hexdigest(),
              'dtree_sha256': hashlib.sha256(a.dtree.read_bytes()).hexdigest()}
    remote = None
    extra = ('SP_', 'SPSR_GL', 'ASPSR_GL', 'ELR_GL', 'ESR_GL', 'VMSA')
    with (a.out / 'stderr.log').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
        try:
            deadline = time.monotonic() + a.seconds
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError('Owned QEMU exited before debugger connection')
                try:
                    remote = Remote(port)
                    break
                except ConnectionRefusedError:
                    time.sleep(.02)
            if remote is None:
                raise RuntimeError('Debugger startup timed out')
            remote.sock.settimeout(5)
            remote.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.command('?')
            triggers = ([f'1,{a.pc:x},4'] if a.pc is not None else
                        [f'2,{address:x},1' for address in a.watch_write])
            for trigger in triggers:
                assert remote.command('Z' + trigger) == 'OK'
            remote.sock.settimeout(min(5, max(.001, deadline - time.monotonic())))
            remote.send('c')
            stop = remote.receive()
            before = registers(remote, extra)
            if a.pc is not None:
                assert before['pc'] == a.pc, f'Unexpected initial stop: {before["pc"]:#x}'
            else:
                assert any(f'watch:{address:x};' in stop for address in a.watch_write), stop
            report['initial_stop'] = stop
            report['before'] = {k: hex(v) for k, v in before.items()}
            for trigger in triggers:
                assert remote.command('z' + trigger) == 'OK'
            previous = None
            for index in range(a.steps):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Trace reached wall-clock bound')
                raw = bytes.fromhex(remote.command('g'))
                values = list(struct.unpack_from('<33Q', raw))
                values += [struct.unpack_from('<I', raw, 33 * 8)[0]]
                pc = values[32]
                code = bytes.fromhex(remote.command(f'm{pc:x},4'))
                assert len(code) == 4
                word, = struct.unpack('<I', code)
                delta = {('sp' if i == 31 else 'pc' if i == 32 else
                          'pstate' if i == 33 else f'x{i}'): hex(v)
                         for i, v in enumerate(values)
                         if previous is None or previous[i] != v}
                item = {'pc': hex(pc), 'word': hex(word), 'register_changes': delta,
                        'stop': stop}
                if word & 0xffd00000 == 0xd5100000:
                    rt = word & 31
                    item.update(read=bool(word & 0x200000), rt=rt,
                                encoding=[(word >> 19) & 3, (word >> 16) & 7,
                                          (word >> 12) & 15, (word >> 8) & 15,
                                          (word >> 5) & 7],
                                value_before=hex(values[rt] if rt < 31 else 0))
                report['trace'].append(item)
                previous = values
                remote.sock.settimeout(min(5, max(.001, deadline - time.monotonic())))
                remote.send('s')
                stop = remote.receive()
                if not stop.startswith(('T05', 'S05')):
                    raise RuntimeError(f'Unexpected single-step stop: {stop}')
            report['after'] = {k: hex(v) for k, v in registers(remote, extra).items()}
            report['passed'] = True
        except Exception as exc:
            report['error'] = str(exc)
        finally:
            if remote:
                remote.sock.close()
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            report['process_returncode'] = child.returncode
            (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report.get(k) for k in ('passed', 'error', 'process_returncode')}))
    print(f'Captured {len(report["trace"])} instructions in {a.out}')
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
