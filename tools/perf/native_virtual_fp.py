#!/usr/bin/env python3
"""Check all VHE FPEN settings at the real SPTM LDNP boundary under HVF.

A test entry writes CPTR_EL2 through the real dispatcher and branches to the
original SPTM entry. The SPTM instruction stream is unchanged; debugger
breakpoints surround LDNP without skipping it or changing its input memory.
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
import traceback
import xml.etree.ElementTree as ET
from arm_island_bench import ROOT, Remote, terminate
from native_sptm_tables import read_phys, registers


def feature(remote, name):
    data = ''
    while True:
        reply = remote.command(f'qXfer:features:read:{name}:{len(data):x},1000')
        assert reply[0] in 'ml', reply
        data += reply[1:]
        if reply[0] == 'l':
            # QEMU's target.xml uses xi:include without declaring xmlns:xi.
            return ET.fromstring(data.replace('xi:include', 'include'))


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
    ledger = bytearray(a.ledger.read_bytes())
    assert ledger[:4] == b'DVEL'
    count = struct.unpack_from('<I', ledger, 4)[0]
    assert len(ledger) == 8 + count * 4 and count < 4096
    # MSR CPTR_EL2,x16, followed by BR x17 to the original SPTM entry.
    ledger += struct.pack('<I', 0xd51c1150)
    struct.pack_into('<I', ledger, 4, count + 1)
    test_ledger = a.out / 'ledger.bin'
    test_ledger.write_bytes(ledger)
    entry = a.out / 'entry.bin'
    entry.write_bytes(struct.pack('<II', 0xd4000003 | ((0xe000 + count) << 5),
                                  0xd61f0220))
    pc = 0xfffffff0070a3f84  # LDNP q2, q3, [x1], word 0xac400c22
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(), 'runs': []}
    for fpen in range(4):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
               '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
               '-display', 'none', '-serial', 'none', '-monitor', 'none',
               '-dtree', str(a.dtree.resolve()), '-S', '-gdb', f'tcp:127.0.0.1:{port}',
               '-sptm', str(a.sptm.resolve()), '-device',
               f'loader,file={entry.resolve()},addr=0x900000000,force-raw=on']
        for option, file in [('-bootkc', 'bootkc'), ('-txm', 'txm'),
                             ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')]:
            cmd.extend([option, str(ROOT / 'firmware' / file)])
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
        env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_SHADOW='1',
                   QEMU_HVF_VIRTUAL_LEDGER=str(test_ledger.resolve()))
        item = {'fpen': fpen, 'command': cmd, 'passed': False}
        remote = None
        with (a.out / f'fpen{fpen}.stderr').open('w') as log:
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
                initial = registers(remote)
                cptr = (initial['CPTR_EL2'] & ~(3 << 20)) | (fpen << 20)
                item['cptr'] = hex(cptr)
                def put(number, value):
                    assert remote.command(f'P{number:x}=' + value.to_bytes(8, 'little').hex()) == 'OK'
                put(16, cptr)
                put(17, initial['pc'])
                put(32, 0x900000000)
                assert remote.command(f'Z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['initial_stop'] = remote.receive()
                before = registers(remote)
                item['before'] = {k: hex(v) for k, v in before.items()}
                assert before['pc'] == pc, hex(before['pc'])
                assert before['CPTR_EL2'] == cptr, 'Guest CPTR write/readback mismatch'
                includes = [r.attrib['href'] for r in feature(remote, 'target.xml')
                            if r.tag.endswith('include')]
                item['features'] = includes
                fp_feature = next(n for n in includes if 'sve' in n or 'fpu' in n)
                number = 0
                fpregs = {}
                for r in feature(remote, fp_feature).iter('reg'):
                    number = int(r.attrib.get('regnum', number))
                    fpregs[r.attrib['name']] = number
                    number += 1
                def vector(n):
                    name = f'v{n}' if f'v{n}' in fpregs else f'z{n}'
                    return bytes.fromhex(remote.command(f'p{fpregs[name]:x}'))[:16]
                original = vector(2) + vector(3)
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                assert read_phys(remote, 0x8070a3f84, 4) == bytes.fromhex('220c40ac')
                source = before['x1'] - 0xfffffff000000000 + 0x800000000
                assert 0x800000000 <= source < 0xa00000000
                expected = read_phys(remote, source, 32)
                assert expected != original, 'Fixture cannot distinguish an omitted load'
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                assert remote.command(f'Z1,{pc + 4:x},4') == 'OK'
                remote.send('c')
                item['test_stop'] = remote.receive()
                after = registers(remote)
                actual = vector(2) + vector(3)
                item.update(final_pc=hex(after['pc']), expected=expected.hex(),
                            actual=actual.hex(), original=original.hex())
                assert after['CPTR_EL2'] == cptr
                if fpen in (1, 3):
                    assert after['pc'] == pc + 4, hex(after['pc'])
                    assert actual == expected, 'Native SIMD load returned wrong data'
                else:
                    assert after['pc'] == pc, hex(after['pc'])
                    assert actual == original, 'Denied SIMD load changed registers'
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
        print(json.dumps({k: v for k, v in item.items() if k != 'before'}), flush=True)
    return int(not all(r['passed'] for r in report['runs']))


if __name__ == '__main__':
    raise SystemExit(main())
