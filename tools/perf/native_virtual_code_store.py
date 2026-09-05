#!/usr/bin/env python3
"""Test real SPTM STR64 into executable backing without granting native writes."""
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
    pc = 0xfffffff0070d9a28
    table_pas = (0x807024000, 0x807028000, 0x80702c000, 0x807110000, 0x807114000)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(), 'runs': []}
    for name in ('nonzero', 'guest-ro', 'unsafe-code', 'table-ro', 'unaligned'):
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
                va = 0xfffffff0070a2018
                value = 0x1122334455667788
                if name == 'unsafe-code':
                    value = 0xd5181000d5181000  # Unadapted MSR SCTLR_EL1.
                elif name == 'table-ro':
                    va = 0xfffffff007110018
                elif name == 'unaligned':
                    va += 1
                item.update(address=hex(va), value=hex(value))
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                if name in ('guest-ro', 'table-ro'):
                    # Explicit fixture mutation, not a guest table-update feature.
                    # This high VA uses the captured L2 32 MiB block descriptor.
                    pte_pa = 0x807114000 + ((va >> 25) & 0x7ff) * 8
                    pte = int.from_bytes(read_phys(remote, pte_pa, 8), 'little')
                    assert pte & 3 == 1 and not (pte & 0x80), hex(pte)
                    new_pte = (pte | 0x80).to_bytes(8, 'little')
                    assert remote.command(f'M{pte_pa:x},8:' + new_pte.hex()) == 'OK'
                    assert read_phys(remote, pte_pa, 8) == new_pte
                    item.update(pte_pa=hex(pte_pa), pte_before=hex(pte),
                                pte_after=hex(pte | 0x80))
                watched = (*table_pas, 0x8070a0000)
                contents = {pa: read_phys(remote, pa, 0x4000) for pa in watched}
                item['hashes_before'] = {hex(pa): hashlib.sha256(v).hexdigest()
                                         for pa, v in contents.items()}
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                for reg, val in ((0, value), (8, va)):
                    assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                assert remote.command(f'Z1,{pc + 4:x},4') == 'OK'
                remote.send('c')
                item['test_stop'] = remote.receive()
                after = registers(remote)
                item['final_pc'] = hex(after['pc'])
                assert after['x8'] == va and after['x0'] == value
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                expected = dict(contents)
                if name == 'nonzero':
                    assert after['pc'] == pc + 4, hex(after['pc'])
                    candidate = bytearray(contents[0x8070a0000])
                    candidate[0x2018:0x2020] = value.to_bytes(8, 'little')
                    expected[0x8070a0000] = bytes(candidate)
                else:
                    assert after['pc'] == pc, hex(after['pc'])
                actual = {pa: read_phys(remote, pa, 0x4000) for pa in watched}
                item['hashes_after'] = {hex(pa): hashlib.sha256(v).hexdigest()
                                        for pa, v in actual.items()}
                assert actual == expected, 'Unexpected code or page-table bytes changed'
                if name == 'nonzero':
                    # Repeat at the same native alias with an unsafe replacement.
                    # If the first emulation granted native write access, STR would
                    # now bypass validation and reach the next breakpoint.
                    assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                    for reg, val in ((32, pc), (0, 0xd5181000d5181000)):
                        assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                    remote.send('c')
                    item['repeat_stop'] = remote.receive()
                    repeat = registers(remote)
                    item['repeat_pc'] = hex(repeat['pc'])
                    assert repeat['pc'] == pc, 'Native write permission was broadened'
                    assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                    assert {pa: read_phys(remote, pa, 0x4000) for pa in watched} == expected
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
