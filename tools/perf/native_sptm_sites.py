#!/usr/bin/env python3
"""Record what unmodified SPTM writes and reads at named gate sites under TCG.

Given a list of runtime PCs (typically the gates enumerated by
native_sptm_exec_trace.py), this runs a fresh TCG child, places a breakpoint
on every site plus a terminal PC, and records the general registers, the
instruction word, the operand value for MSR, the loaded value for MRS (by one
single step), and the Apple/translation system registers at each hit, in
execution order. Loop sites are recorded once per pass.

This documents the firmware's requested operations under the existing TCG
model. It performs no debugger writes and controls only its own child.
"""
import argparse
import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import time
from pathlib import Path

from arm_island_bench import ROOT, Remote, terminate
from native_sptm_tables import registers

EXTRA = ('SP_', 'SPSR_GL', 'ASPSR_GL', 'ELR_GL', 'ESR_GL', 'VMSA', 'ACFG',
         'APCTL', 'JCTL', 'BP_OBJC', 'MDSCR', 'AGTCNT', 'APL_INTENABLE',
         'TPIDR', 'SPRR', 'SPSR_EL', 'ELR_EL', 'ESR_EL', 'CNTHCTL', 'CNTVOFF',
         'CNTV_', 'CNTP_', 'CPTR', 'PMCR', 'AMX', 'KERNKEY', 'APIA', 'APIB',
         'APDA', 'APDB', 'APGA', 'DAIF', 'PAN', 'FAR', 'HID', 'EHID')


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--sites', type=Path, required=True,
                    help='JSON list of runtime PCs, or an exec-trace results.json')
    ap.add_argument('--start-pc', type=lambda x: int(x, 0), default=None,
                    help='PC to reach before recording (breakpoints armed afterwards)')
    ap.add_argument('--terminal-pc', type=lambda x: int(x, 0), required=True)
    ap.add_argument('--max-hits', type=int, default=4096)
    ap.add_argument('--seconds', type=int, default=120)
    ap.add_argument('--qemu', type=Path,
                    default=ROOT / 'qemu-sptm/build/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    raw = json.loads(a.sites.read_text())
    if isinstance(raw, dict):
        limit = raw.get('first_outside_sptm', {}).get('order')
        sites = sorted({int(g['pc'], 16) for g in raw['gates']
                        if limit is None or g['order'] <= limit})
    else:
        sites = sorted({int(x, 0) if isinstance(x, str) else int(x) for x in raw})
    sites = [pc for pc in sites if pc != a.terminal_pc]
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
    report = {'command': cmd, 'passed': False, 'sites': [hex(s) for s in sites],
              'hits': [],
              'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256((ROOT / 'firmware/sptm').read_bytes()).hexdigest(),
              'dtree_sha256': hashlib.sha256(a.dtree.read_bytes()).hexdigest()}
    remote = None
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
            remote.sock.settimeout(10)
            remote.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.command('?')

            def cont():
                remote.sock.settimeout(min(10, max(.001, deadline - time.monotonic())))
                remote.send('c')
                return remote.receive()

            def pc_of():
                raw = bytes.fromhex(remote.command('g'))
                return struct.unpack_from('<Q', raw, 32 * 8)[0]

            if a.start_pc is not None:
                assert remote.command(f'Z1,{a.start_pc:x},4') == 'OK'
                stop = cont()
                assert pc_of() == a.start_pc, f'Unexpected initial stop {pc_of():#x}: {stop}'
                assert remote.command(f'z1,{a.start_pc:x},4') == 'OK'
                report['start'] = {k: hex(v) for k, v in registers(remote, EXTRA).items()}
            for pc in sites + [a.terminal_pc]:
                assert remote.command(f'Z1,{pc:x},4') == 'OK', f'breakpoint {pc:#x}'
            for index in range(a.max_hits):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Site capture reached wall-clock bound')
                stop = cont()
                if not stop.startswith(('T05', 'S05')):
                    raise RuntimeError(f'Unexpected stop: {stop}')
                raw = bytes.fromhex(remote.command('g'))
                values = list(struct.unpack_from('<33Q', raw))
                pc = values[32]
                if pc == a.terminal_pc:
                    report['terminal'] = {k: hex(v) for k, v in registers(remote, EXTRA).items()}
                    report['terminal_hit_index'] = index
                    break
                code = bytes.fromhex(remote.command(f'm{pc:x},4'))
                word, = struct.unpack('<I', code)
                hit = {'index': index, 'pc': hex(pc), 'word': hex(word),
                       'gprs': {f'x{i}': hex(v) for i, v in enumerate(values[:31])},
                       'sp': hex(values[31]),
                       'pstate': hex(struct.unpack_from('<I', raw, 33 * 8)[0])}
                if word & 0xffd00000 == 0xd5100000:
                    rt = word & 31
                    read = bool(word & 0x200000)
                    hit.update(read=read, rt=rt,
                               encoding=[(word >> 19) & 3, (word >> 16) & 7,
                                         (word >> 12) & 15, (word >> 8) & 15,
                                         (word >> 5) & 7])
                    if not read:
                        hit['value'] = hex(values[rt] if rt < 31 else 0)
                # System registers before the instruction executes.
                hit['sysregs'] = {k: hex(v) for k, v in registers(remote, EXTRA).items()
                                  if not k.startswith(('x', 'sp', 'pc', 'pstate'))}
                # Single step so MRS results and post-write states are visible.
                remote.sock.settimeout(10)
                remote.send('s')
                sstop = remote.receive()
                if not sstop.startswith(('T05', 'S05')):
                    raise RuntimeError(f'Unexpected single-step stop: {sstop}')
                after = bytes.fromhex(remote.command('g'))
                avalues = list(struct.unpack_from('<33Q', after))
                if hit.get('read') and hit['rt'] < 31:
                    hit['value'] = hex(avalues[hit['rt']])
                hit['next_pc'] = hex(avalues[32])
                if not hit.get('read', True):
                    hit['sysregs_after'] = {k: hex(v) for k, v in registers(remote, EXTRA).items()
                                            if not k.startswith(('x', 'sp', 'pc', 'pstate'))}
                report['hits'].append(hit)
            else:
                raise RuntimeError('Hit limit reached before terminal PC')
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
    print(json.dumps({k: report.get(k) for k in ('passed', 'error', 'process_returncode',
                                                 'terminal_hit_index')}))
    print(f'Recorded {len(report["hits"])} site hits in {a.out}')
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
