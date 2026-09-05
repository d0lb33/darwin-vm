#!/usr/bin/env python3
"""Verify a restricted virtual EL2-at-EL1 contract and its TCG control."""
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
import xml.etree.ElementTree as ET
from arm_island_bench import ROOT, Remote, assemble, terminate


def tcg_control(a, source):
    code = assemble(a.out, source.with_suffix('.S'), ['-DVEL_TCG_CONTROL'] + (['-DVEL_TGE'] if a.tge else []))
    symbols = {line.split()[-1]: int(line.split()[0], 16) for line in
               subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True).splitlines()
               if len(line.split()) == 3}
    gpa, va = 0x40200000, 0xffffff8080000000
    data = bytearray(0x34000)
    data[:len(code)] = code
    def put(offset, value):
        struct.pack_into('<Q', data, offset, value)
    put(0x14000, gpa + 0x18003)
    for i in ((gpa >> 25) & 0x7ff, (va >> 25) & 0x7ff):
        put(0x18000 + i * 8, gpa + 0x1c003)
    # The VA offset starts at zero; TCG's physical loader uses a 2MB offset.
    for i in range(13):
        attributes = (1 << 7) if i < 4 else (3 << 53)
        if i == 3:
            attributes = 0xc0 | (1 << 53)
        if i == 9:
            attributes |= 0x40
        # The TCG-only setup at physical page 4 also needs execution.
        if i == 4:
            attributes = 1 << 7
        descriptor = (gpa + i * 0x4000) | 0x703 | attributes
        put(0x1c000 + i * 8, descriptor)
        put(0x1c000 + (((gpa >> 14) & 0x7ff) + i) * 8, descriptor)
    put(0x30000, 0xface)
    payload = a.out / 'payload.bin'
    payload.write_bytes(data)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'virt,virtualization=on', '-cpu', 'max',
           '-accel', 'tcg', '-m', '64M', '-display', 'none', '-serial', 'none', '-monitor', 'none',
           '-S', '-gdb', f'tcp:127.0.0.1:{port}', '-L', str(ROOT / 'qemu-sptm/pc-bios'),
           '-device', f'loader,file={payload.resolve()},addr={gpa:#x},cpu-num=0,force-raw=on']
    report = {'command': cmd, 'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'payload_sha256': hashlib.sha256(data).hexdigest()}
    remote = None
    with (a.out / 'tcg.stderr').open('w') as log:
        child = subprocess.Popen(cmd, stderr=log, stdout=subprocess.DEVNULL,
                                 env={k: v for k, v in os.environ.items()
                                      if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))})
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError('QEMU exited at startup')
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
            xml = ''
            while True:
                chunk = remote.command(f'qXfer:features:read:system-registers.xml:{len(xml):x},1000')
                xml += chunk[1:]
                if chunk[0] == 'l':
                    break
            (a.out / 'system-registers.xml').write_text(xml)
            regnums = {r.attrib['name']: int(r.attrib['regnum']) for r in ET.fromstring(xml).iter('reg')}
            def write(reg, value, size=8):
                assert remote.command(f'P{reg:x}=' + value.to_bytes(size, 'little').hex()) == 'OK'
            write(33, 0x3c9, 4)
            tcr = (25 | 1 << 8 | 1 << 10 | 3 << 12 | 2 << 14 | 25 << 16 |
                   1 << 24 | 1 << 26 | 3 << 28 | 1 << 30 | 1 << 32)
            # ARM's GDB system-register setter is a no-op. Execute setup MSRs;
            # an OK packet is not evidence that an architectural register changed.
            for reg, value in [(0, 0x488000000 if a.tge else 0x480000000), (1, gpa + 0x14000), (2, tcr),
                               (3, va + symbols['_vel_vectors1']), (4, va + symbols['_vel_vectors2']),
                               (5, va + 0x2fff0), (6, va + 0x2bff0), (7, va), (11, 0x80027ff0),
                               (14, 0x777), (18, int(a.illegal)), (19, int(a.sp0)), (20, va + 0x20000), (26, va + 0x30000), (28, 0),
                               (32, gpa + symbols['_vel_tcg_setup'])]:
                write(reg, value)
            remote.send('c')
            time.sleep(.05)
            remote.sock.sendall(b'\x03')
            remote.receive()
            pc = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))[32]
            report['pc'] = hex(pc)
            report['system'] = {name: remote.command(f'p{regnums[name]:x}') for name in
                                ('HCR_EL2', 'SCTLR_EL1', 'SCTLR_EL2', 'TCR_EL1', 'TCR_EL2',
                                 'TTBR0_EL1', 'TTBR1_EL1', 'TTBR0_EL2', 'TTBR1_EL2',
                                 'VBAR_EL1', 'VBAR_EL2', 'ESR_EL2', 'ELR_EL2', 'FAR_EL2')}
            assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
            values = struct.unpack('<48Q', bytes.fromhex(remote.command(f'm{gpa + 0x20000:x},180')))
            expected = (8, va + symbols['_vel_vectors2'], 0x480000000, 0xa0000000, 4, va + 0x2fff0,
                        0x02000000, va + symbols['_vel_hcr1_native'], 0xa00003c5, 0, 0, 0,
                        8, 0x600003c5, va + symbols['_vel_after_hvc'], 0x5a000071,
                        va + 0x2bff0, 0xface, va + 0x2fff0, 0x60000000, 1 << 22, 0,
                        0x56000072, 0x80000000 + symbols['_vel_user_after'], 0x200003c0,
                        0x20000000, 0xabe, 0x80027ff0, 0x56000073,
                        0x80000000 + symbols['_vel_user_after2'], 0x200003c0,
                        0x20000000, 0x80027ff0, 4,
                        0x02000000, 0x80000000 + symbols['_vel_user_priv'], 0x200003c0,
                        0x9200000f, 0x80000000 + symbols['_vel_user_data'], 0x200003c0, va + 0x30000, 0x80027ff0 if a.sp0 else va + 0x2bff0, 0, 0, 0, 0, 0, 0)
            if a.tge:
                expected = list(expected)
                expected[:22] = [0] * 22
                expected[0:3] = [8, va + symbols['_vel_vectors2'], 0x488000000]
                expected[20], expected[33] = 1 << 22, 8
                if a.illegal:
                    expected[42:] = [0x3a000000, va + symbols['_vel_illegal_target'],
                                     0xa01003c8 if a.sp0 else 0xa01003c9, 8, va + 0x2bff0, 0x777]
                expected = tuple(expected)
            report.update(pc=hex(pc), values=[hex(v) for v in values],
                          expected=[hex(v) for v in expected],
                          passed=pc == va + symbols['_vel_done'] and values == expected)
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
            print(json.dumps(report), flush=True)
    return int(not report['passed'])


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--tcg', action='store_true')
    ap.add_argument('--sp0', action='store_true', help='TCG control with native SPSel=0 at virtual EL2')
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    ap.add_argument('--tge', action='store_true')
    ap.add_argument('--illegal', action='store_true', help='TCG control of an illegal TGE return to EL1')
    a = ap.parse_args()
    if a.illegal and not a.tge:
        ap.error('--illegal requires --tge')
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).resolve().with_suffix('')
    if a.tcg:
        return tcg_control(a, source)
    binary, ent = a.out / 'probe', a.out / 'entitlements.plist'
    ent.write_text('<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>')
    inputs = [source.with_suffix('.c'), source.with_suffix('.S'), ROOT / 'qemu-sptm/target/arm/apple-sprr.h']
    subprocess.run(['clang', *(['-DVEL_TGE'] if a.tge else []), '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                    '-I', str(inputs[2].parent), str(inputs[0]), str(inputs[1]),
                    '-framework', 'Hypervisor', '-o', str(binary)], check=True)
    subprocess.run(['codesign', '-s', '-', '--entitlements', str(ent), str(binary)], check=True)
    report = {'tge': a.tge, 'sources': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'cases': []}
    modes = ('normal', 'sp0', 'illegal', 'illegal-sp0', 'no-tge-route', 'no-bank-switch') if a.tge else ('normal', 'sp0', 'no-revoke', 'no-bank-switch')
    for mode in modes:
        cmd = [str(binary.resolve())] + ([] if mode == 'normal' else [mode])
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=7)
        item = {'mode': mode, 'command': cmd, 'returncode': run.returncode, 'stderr': run.stderr}
        try:
            item.update(json.loads(run.stdout))
            item['verified'] = (run.returncode == 0 and item['passed']) if mode in ('normal', 'sp0', 'illegal', 'illegal-sp0') else (
                run.returncode == 1 and not item['passed'] and 'result[' in run.stderr)
        except ValueError:
            item.update(stdout=run.stdout, verified=False)
        report['cases'].append(item)
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(item), flush=True)
    return int(any(not item['verified'] for item in report['cases']))


if __name__ == '__main__':
    raise SystemExit(main())
