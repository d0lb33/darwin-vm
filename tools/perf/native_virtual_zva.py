#!/usr/bin/env python3
"""Test real SPTM DC ZVA, exact block boundaries and protected-page rejection."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import traceback
from arm_island_bench import ROOT, Remote, terminate
from native_sptm_tables import read_phys, registers


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--sptm', type=Path, required=True)
    ap.add_argument('--ledger', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    pc = 0xfffffff0070a3be4
    table_pas = (0x807024000, 0x807028000, 0x80702c000, 0x807110000, 0x807114000)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(), 'runs': []}
    for name in ('existing-data', 'fresh-data', 'code', 'table', 'unmapped'):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
               '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
               '-display', 'none', '-serial', 'none', '-monitor', 'none',
               '-dtree', str(a.dtree.resolve()), '-S', '-gdb', f'tcp:127.0.0.1:{port}',
               '-sptm', str(a.sptm.resolve())]
        for option, file in [('-bootkc', 'bootkc'), ('-txm', 'txm'),
                             ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')]:
            cmd.extend([option, str(ROOT / 'firmware' / file)])
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
        env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_SHADOW='1',
                   QEMU_HVF_VIRTUAL_LEDGER=str(a.ledger.resolve()))
        item = {'name': name, 'command': cmd, 'passed': False}
        remote = None
        with (a.out / f'{name}.stderr').open('w') as log:
            child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError('QEMU exited before debugger connection')
                    try:
                        remote = Remote(port)
                        break
                    except ConnectionRefusedError:
                        time.sleep(.02)
                assert remote is not None, 'Debugger startup timeout'
                remote.sock.settimeout(5)
                remote.command('?')
                assert remote.command(f'Z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['initial_stop'] = remote.receive()
                before = registers(remote)
                assert before['pc'] == pc, hex(before['pc'])
                # This SPTM routine advances by 64 at 0xfffffff0070a3be8.
                # Verify the actual changed byte range, not an unavailable
                # GDB view of DCZID (its cpreg is ARM_CP_NO_RAW).
                length = 64
                va = {'existing-data': before['x3'] + 7,
                      'fresh-data': 0xfffffff1f0000047,
                      'code': pc + 3,
                      'table': 0xfffffff007110047,
                      'unmapped': 0xfffffff400000047}[name]
                item['address'] = hex(va)
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                def digest(pa):
                    return hashlib.sha256(read_phys(remote, pa, 0x4000)).hexdigest()
                watched = (*table_pas, 0x8070a0000)
                hashes = {hex(pa): digest(pa) for pa in watched}
                positive = name.endswith('data')
                if positive:
                    pa = (va & ~(length - 1)) - 0xfffffff000000000 + 0x800000000
                    assert 0x800000000 < pa < 0xa00000000 - length
                    # Nonzero guards and target distinguish a true exact zero from a no-op.
                    pattern = bytes((i % 251) + 1 for i in range(length + 32))
                    assert remote.command(f'M{pa - 16:x},{len(pattern):x}:' + pattern.hex()) == 'OK'
                    assert read_phys(remote, pa - 16, len(pattern)) == pattern
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                assert remote.command('P3=' + va.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                assert remote.command(f'Z1,{pc + 4:x},4') == 'OK'
                remote.send('c')
                item['test_stop'] = remote.receive()
                after = registers(remote)
                item['final_pc'] = hex(after['pc'])
                assert after['x3'] == va
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                after_hashes = {hex(pa): digest(pa) for pa in watched}
                item['protected_hashes_before'] = hashes
                item['protected_hashes_after'] = after_hashes
                assert hashes == after_hashes, 'Code or source page table changed'
                if positive:
                    assert after['pc'] == pc + 4, hex(after['pc'])
                    actual = read_phys(remote, pa - 16, len(pattern))
                    item['actual'] = actual.hex()
                    assert actual == pattern[:16] + bytes(length) + pattern[-16:]
                else:
                    assert after['pc'] == pc, hex(after['pc'])
                item['passed'] = True
            except Exception as exc:
                item['error'] = str(exc)
                item['traceback'] = traceback.format_exc()
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
                item['process_returncode'] = child.returncode
        report['runs'].append(item)
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(item), flush=True)
    return int(not all(r['passed'] for r in report['runs']))


if __name__ == '__main__':
    raise SystemExit(main())
