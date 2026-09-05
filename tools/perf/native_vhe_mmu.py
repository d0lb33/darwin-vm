#!/usr/bin/env python3
"""Test distinct low/high VHE translations on direct HVF or QEMU TCG.

Native runs report unsupported behavior as failures, not successful emulation.
TCG controls verify the same page tables and architectural instruction stream.
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
from arm_island_bench import ROOT, Remote, assemble, terminate


def run_tcg(a, source):
    code = assemble(a.out, source.with_suffix('.S'), ['-DNATIVE_QEMU_VHE_MMU'])
    symbols = {line.split()[-1]: int(line.split()[0], 16) for line in
               subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True).splitlines()
               if len(line.split()) == 3}
    gpa, high_base = 0x40200000, 0xffffff8000000000
    data = bytearray(0x40000)
    data[:len(code)] = code
    def put(offset, value):
        struct.pack_into('<Q', data, offset, value)
    for bank in range(2):
        l1 = 0x4000 + bank * 0xc000
        l2, l3 = l1 + 0x4000, l1 + 0x8000
        put(l1, gpa + l2 + 3)
        put(l2 + (gpa >> 25) * 8, gpa + l3 + 3)
        for i in range(16):
            put(l3 + (((gpa >> 14) & 0x7ff) + i) * 8,
                (gpa + i * 0x4000) | 0x703 | ((3 << 53) if i else (1 << 7)))
        put(l3 + (((gpa >> 14) & 0x7ff) + 12) * 8,
            (gpa + 0x30000 + bank * 0x4000) | 0x703 | (3 << 53))
    put(0x30000, 0x1234)
    put(0x34000, 0xabcd)
    payload = a.out / 'payload.bin'
    payload.write_bytes(data)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'payload_sha256': hashlib.sha256(data).hexdigest(), 'cases': []}
    for hcr in (0x480000000, 0x488000000):
        for high in (False, True):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            tag = f'{hcr:x}-{int(high)}'
            cmd = [str(a.qemu.resolve()), '-M', 'virt,virtualization=on', '-cpu', 'max',
                   '-accel', 'tcg', '-m', '64M', '-display', 'none', '-serial', 'none',
                   '-monitor', 'none', '-S', '-gdb', f'tcp:127.0.0.1:{port}',
                   '-L', str(ROOT / 'qemu-sptm/pc-bios'), '-device',
                   f'loader,file={payload.resolve()},addr={gpa:#x},cpu-num=0,force-raw=on']
            item = {'hcr': hex(hcr), 'high': high, 'command': cmd}
            remote = None
            with (a.out / f'{tag}.stderr').open('w') as log:
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
                    tcr = (25 | 1 << 8 | 1 << 10 | 3 << 12 | 2 << 14 | 25 << 16 |
                           1 << 24 | 1 << 26 | 3 << 28 | 1 << 30 | 1 << 32)
                    base = gpa | (high_base if high else 0)
                    regs = [(0, hcr), (20, gpa + 0x20000), (21, gpa + 0x4000),
                            (22, gpa + 0x10000), (23, tcr),
                            (24, gpa + symbols['_vmmu_vectors']),
                            (25, base + symbols['_vmmu_entry']), (26, base + 0x30000), (27, 0)]
                    for reg, value in regs:
                        assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
                    assert remote.command('P21=c9030000') == 'OK'
                    remote.send('c')
                    time.sleep(.05)
                    remote.sock.sendall(b'\x03')
                    remote.receive()
                    pc = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))[32]
                    values = struct.unpack('<5Q', bytes.fromhex(remote.command(f'm{gpa + 0x20000:x},28')))
                    item.update(pc=hex(pc), values=[hex(v) for v in values],
                                passed=pc == base + symbols['_vmmu_success'] and
                                values == (0, 0, 0, hcr, 0xabcd if high else 0x1234))
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
                    report['cases'].append(item)
                    (a.out / 'results.json').write_text(json.dumps(report, indent=2))
                    print(json.dumps(item), flush=True)
    return int(any(not r['passed'] for r in report['cases']))


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--tcg', action='store_true')
    ap.add_argument('--el1-control', action='store_true', help='Use ordinary EL1 translation, without nested EL2')
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).resolve().with_suffix('')
    if a.tcg and a.el1_control:
        ap.error('--el1-control is a direct-HVF experiment')
    if a.tcg:
        return run_tcg(a, source)
    binary, ent = a.out / 'probe', a.out / 'entitlements.plist'
    ent.write_text('<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>')
    # Match the TCG fixture's base so both exercise identical table indices.
    definitions = ['-DNATIVE_EL1_MMU'] if a.el1_control else []
    subprocess.run(['clang', *definitions, '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                    '-DVMMU_GPA=0x40200000ull', str(source.with_suffix('.c')), str(source.with_suffix('.S')),
                    '-framework', 'Hypervisor', '-o', str(binary)], check=True)
    subprocess.run(['codesign', '-s', '-', '--entitlements', str(ent), str(binary)], check=True)
    report = {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'cases': []}
    for hcr in ((0,) if a.el1_control else (0x80000000, 0x480000000, 0x488000000)):
        for mode in ((0,) if a.el1_control else range(4)):
            for high in range(2):
                cmd = [str(binary.resolve()), hex(hcr), str(high), str(mode)]
                run = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
                item = {'command': cmd, 'returncode': run.returncode, 'stderr': run.stderr}
                try:
                    item.update(json.loads(run.stdout))
                except ValueError:
                    item.update(stdout=run.stdout, passed=False)
                report['cases'].append(item)
                print(json.dumps(item), flush=True)
                (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    return int(any(not r['passed'] for r in report['cases']))


if __name__ == '__main__':
    raise SystemExit(main())
