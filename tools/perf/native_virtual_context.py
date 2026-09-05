#!/usr/bin/env python3
"""Test live HVF shadow-context replacement and stale-alias rejection.

Boot real SPTM to its first TCR change. While stopped, install a three-word
fixture at that site, and optionally clone the high guest tables with a new
mapping/permission. These debugger writes are test setup, not guest table-
write support. The adapted MSR uses the same production dispatcher ledger.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import struct
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
    pc = 0xfffffff0070d9a4c
    code_pa = 0x8070d9a4c
    target_va, target_pa = 0xfffffff016974000, 0x816974000
    clone_root, clone_l2 = 0x91fff8000, 0x91fffc000
    remap_pa = 0x920000000 + (target_va & 0x1ffffff)
    words = struct.unpack_from('<' + str((a.ledger.stat().st_size - 8) // 4) + 'I',
                               a.ledger.read_bytes(), 8)
    table_pas = (0x807024000, 0x807028000, 0x80702c000, 0x807110000, 0x807114000)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(), 'runs': []}
    for name in ('tcr-width', 'tcr-invalid-granule', 'tcr-disabled-high',
                 'ttbr-remap-read', 'ttbr-remap-write', 'ttbr-ro', 'ttbr-nx',
                 'ttbr-unmapped', 'ttbr-table-ro'):
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
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                def write(pa, data):
                    for offset in range(0, len(data), 512):
                        chunk = data[offset:offset + 512]
                        assert remote.command(f'M{pa + offset:x},{len(chunk):x}:' + chunk.hex()) == 'OK'
                    assert read_phys(remote, pa, len(data)) == data
                value = 0x1122334455667788
                old_data = bytes.fromhex('a5a5a5a5a5a5a5a5')
                new_data = bytes.fromhex('bcbcbcbcbcbcbcbc')
                write(target_pa, old_data)
                write(remap_pa, new_data)
                address = target_va
                is_tcr = name.startswith('tcr-')
                is_store = name in ('ttbr-remap-write', 'ttbr-ro', 'ttbr-table-ro')
                msr_word = 0xd5182048 if is_tcr else 0xd5182028
                payload = struct.pack('<3I', 0xd4000003 | ((0xe000 + words.index(msr_word)) << 5),
                                      0xf9000020 if is_store else 0xf9400020,
                                      0xd4000003 | (0xda22 << 5))
                write(code_pa, payload)
                regvalue = (before['TCR_EL2'] & ~63) | 21
                if name == 'tcr-invalid-granule':
                    regvalue &= ~(3 << 14)
                elif name == 'tcr-disabled-high':
                    regvalue |= 1 << 23
                if not is_tcr:
                    root = bytearray(read_phys(remote, 0x807110000, 0x4000))
                    l2 = bytearray(read_phys(remote, 0x807114000, 0x4000))
                    root_index = ((target_va >> 36) & 7) * 8
                    old_desc = struct.unpack_from('<Q', root, root_index)[0]
                    assert old_desc & ~0x3fff == 0x807114000, hex(old_desc)
                    struct.pack_into('<Q', root, root_index, clone_l2 | (old_desc & 0x3fff))
                    index = ((target_va >> 25) & 0x7ff) * 8
                    desc = struct.unpack_from('<Q', l2, index)[0]
                    assert desc & 3 == 1 and not (desc & 0x80)
                    if name.startswith('ttbr-remap'):
                        desc = (desc & ~0x0000fffffe000000) | 0x920000000
                    elif name == 'ttbr-ro':
                        desc |= 0x80
                    elif name == 'ttbr-unmapped':
                        desc = 0
                    elif name == 'ttbr-table-ro':
                        address = clone_l2 - 0x800000000 + 0xfffffff000000000
                        table_index = ((address >> 25) & 0x7ff) * 8
                        table_desc = struct.unpack_from('<Q', l2, table_index)[0]
                        struct.pack_into('<Q', l2, table_index, table_desc | 0x80)
                    struct.pack_into('<Q', l2, index, desc)
                    if name == 'ttbr-nx':
                        code_index = ((pc >> 25) & 0x7ff) * 8
                        code_desc = struct.unpack_from('<Q', l2, code_index)[0]
                        struct.pack_into('<Q', l2, code_index, code_desc | (1 << 53))
                    write(clone_root, root)
                    write(clone_l2, l2)
                    regvalue = clone_root
                watched = (*table_pas, clone_root, clone_l2, 0x8070d8000)
                hashes = {hex(pa): hashlib.sha256(read_phys(remote, pa, 0x4000)).hexdigest()
                          for pa in watched}
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                for reg, val in ((0, value), (1, address), (8, regvalue)):
                    assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['test_stop'] = remote.receive()
                after = registers(remote)
                item.update(final_pc=hex(after['pc']), x0=hex(after['x0']),
                            requested=hex(regvalue), tcr=hex(after['TCR_EL2']),
                            ttbr1=hex(after['TTBR1_EL2']))
                positive = name in ('tcr-width', 'ttbr-remap-read', 'ttbr-remap-write')
                invalid = name == 'tcr-invalid-granule'
                expected_pc = pc + (8 if positive else 0 if invalid else 4)
                assert after['pc'] == expected_pc, hex(after['pc'])
                assert after['TCR_EL2' if is_tcr else 'TTBR1_EL2'] == (
                    before['TCR_EL2'] if invalid else regvalue), 'Wrong architectural register state'
                expected_x0 = (int.from_bytes(new_data if name == 'ttbr-remap-read' else old_data,
                                              'little') if positive and not is_store else value)
                assert after['x0'] == expected_x0, 'Load used a stale mapping or a rejected load changed x0'
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                actual_hashes = {hex(pa): hashlib.sha256(read_phys(remote, pa, 0x4000)).hexdigest()
                                 for pa in watched}
                assert hashes == actual_hashes, 'Code or source page tables changed'
                assert read_phys(remote, target_pa, 8) == old_data, 'Stale writable alias survived'
                assert read_phys(remote, remap_pa, 8) == (
                    value.to_bytes(8, 'little') if name == 'ttbr-remap-write' else new_data)
                item.update(hashes_before=hashes, hashes_after=actual_hashes)
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
