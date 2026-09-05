#!/usr/bin/env python3
"""Replay captured SPTM table bytes through QEMU's explicit-I/O walker on HVF.

This tests the ordinary two-range translation needed at the captured handoff.
It does not execute SPTM or claim virtual EL2/GXF integration. Captured SPRR
must be disabled. No descriptors or firmware bytes are patched for the walk.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time
from arm_island_bench import ROOT, Remote, assemble, terminate


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--native-read', action='store_true',
                    help='Read permitted addresses using a native shadow mapping')
    ap.add_argument('--deny-native-read', action='store_true',
                    help='Negative control: remove read permission from the native alias')
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    if a.deny_native_read and not a.native_read:
        ap.error('--deny-native-read requires --native-read')
    a.out.mkdir(exist_ok=False)
    capture = json.loads((a.capture / 'results.json').read_text())
    assert capture['passed']
    state = {k: int(v, 16) for k, v in capture['state'].items()}
    assert state['SPRR_CONFIG_EL1'] == state['SPRR_CONFIG_EL2'] == 0
    assert state['CURRENTG'] == 0
    assert capture['dtree_sha256'] == hashlib.sha256(a.dtree.read_bytes()).hexdigest()
    assert capture['sptm_sha256'] == hashlib.sha256((ROOT / 'firmware/sptm').read_bytes()).hexdigest()
    code = assemble(a.out, Path(__file__).with_name('native_ptw_qemu.S'))
    payload = a.out / 'payload.bin'
    payload.write_bytes(code)
    symbols = {p[-1]: int(p[0], 16) for line in
               subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True).splitlines()
               if len(p := line.split()) == 3}
    base, output = 0x800040000, 0x800048000
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
           '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
           '-display', 'none', '-serial', 'none', '-monitor', 'none', '-S',
           '-gdb', f'tcp:127.0.0.1:{port}', '-dtree', str(a.dtree.resolve()),
           '-device', f'loader,file={payload.resolve()},addr={base:#x},force-raw=on']
    for option, name in (('-bootkc', 'bootkc'), ('-sptm', 'sptm'), ('-txm', 'txm'),
                         ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')):
        cmd.extend((option, str(ROOT / 'firmware' / name)))
    report = {'command': cmd, 'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'capture_sha256': hashlib.sha256((a.capture / 'results.json').read_bytes()).hexdigest(),
              'cases': [], 'source_tables_unchanged': False}
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
    env['QEMU_HVF_PTW_PROBE'] = '1'
    remote = None
    with (a.out / 'stderr.log').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stderr=log, stdout=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError((a.out / 'stderr.log').read_text())
                try:
                    remote = Remote(port)
                    break
                except ConnectionRefusedError:
                    time.sleep(.02)
            if remote is None:
                raise RuntimeError('GDB startup timeout')
            remote.sock.settimeout(5)
            remote.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.command('?')
            assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
            for page in capture['pages']:
                data = (a.capture / page['file']).read_bytes()
                assert hashlib.sha256(data).hexdigest() == page['sha256']
                pa = int(page['pa'], 16)
                assert pa >= 0x807000000 and len(data) == 0x4000
                for off in range(0, len(data), 512):
                    chunk = data[off:off + 512]
                    assert remote.command(f'M{pa+off:x},{len(chunk):x}:{chunk.hex()}') == 'OK'
            # Every high-half 32 MiB block, both ends of the low bootstrap
            # mapping, and holes on both sides. Expected translations come
            # from the captured descriptors, not the tested C walker.
            high = state['x22']
            ram = state['x23']
            points = [(high + i * 0x2000000 + 0x1238,
                       ram + i * 0x2000000 + 0x1238, 0, 2) for i in range(256)]
            points += [(0x8070a0000, 0x8070a0000, 0, 3),
                       (0x8070a7ff8, 0x8070a7ff8, 0, 3),
                       (0x80709fff8, 0, 7, 3), (0x8070a8000, 0, 7, 3),
                       (high + 0x200000000, 0, 6, 2)]
            if a.deny_native_read:
                points = points[:1]
            assert remote.command(f'Z1,{base + symbols["_ptw_stop"]:x},4') == 'OK'
            for va, expected, fault, reads in points:
                for access in (range(1) if a.deny_native_read else range(3)):
                    regs = {0: va, 1: access, 2: 1, 3: state['TCR_EL2'],
                            4: state['TTBR0_EL2'], 5: state['TTBR1_EL2'],
                            6: state['x0'], 7: state['MAIR_EL2'], 8: 0,
                            9: (2 if a.deny_native_read else 1) if a.native_read and access == 0 else 0,
                            12: 0, 13: 0, 20: output, 32: base}
                    for reg, value in regs.items():
                        assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
                    assert remote.command('P21=c5030000') == 'OK'
                    remote.send('c')
                    remote.receive()
                    raw = bytes.fromhex(remote.command('g'))
                    pc = struct.unpack_from('<33Q', raw)[32]
                    values = struct.unpack('<9Q', bytes.fromhex(remote.command(f'm{output:x},48')))
                    if a.deny_native_read:
                        log.flush()
                        message = (a.out / 'stderr.log').read_text()
                        match = re.search(r'API=0x0 reason=1 syndrome=(0x[0-9a-f]+) '
                                          r'pc=(0x[0-9a-f]+) ipa=(0x[0-9a-f]+) va=(0x[0-9a-f]+)', message)
                        assert match, message[-2000:]
                        syn, fault_pc, ipa, fault_va = (int(x, 16) for x in match.groups())
                        # On the tested HVF host, a no-read alias produces a
                        # stage-2 translation abort, not a permission FSC.
                        # Assert the actual read and target, not a guessed FSC.
                        assert syn == 0x93d08006, hex(syn)
                        assert fault_pc == 0xe00000014 and ipa == 0xe40000000 + (va & 0x3fff)
                        assert fault_va == va and pc == base and not values[8]
                        report['cases'].append({'va': hex(va), 'access': access,
                                                'passed': False, 'verified_rejection': True,
                                                'syndrome': hex(syn), 'ipa': hex(ipa)})
                        continue
                    passed = (pc == base + symbols['_ptw_stop'] and values[0] == int(not fault)
                              and values[1] == expected and values[3] & 0x3f == fault
                              and values[4] == reads and values[5] == 0)
                    if not fault:
                        passed &= values[2] == 7
                    if a.native_read and access == 0 and not fault:
                        actual = int.from_bytes(bytes.fromhex(remote.command(f'm{expected:x},8')), 'little')
                        passed &= values[8] == 1 and values[7] == actual
                    item = {'va': hex(va), 'access': access, 'values': [hex(v) for v in values],
                            'expected_pa': hex(expected), 'expected_fsc': fault, 'passed': bool(passed)}
                    report['cases'].append(item)
                    if not passed:
                        raise RuntimeError(f'Walk mismatch: {item}')
            from native_sptm_tables import read_phys
            for page in capture['pages']:
                data = read_phys(remote, int(page['pa'], 16), 0x4000)
                assert hashlib.sha256(data).hexdigest() == page['sha256']
            report['source_tables_unchanged'] = True
            report['passed'] = True
        except Exception as exc:
            report.update(passed=False, error=str(exc))
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
    print(json.dumps({k: v for k, v in report.items() if k != 'cases'}, indent=2))
    print(f"{sum(c['passed'] or c.get('verified_rejection', False) for c in report['cases'])}/{len(report['cases'])} outcomes verified")
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
