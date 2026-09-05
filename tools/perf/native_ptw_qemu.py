#!/usr/bin/env python3
"""Compare the explicit-I/O ARM walker under HVF with real TCG accesses.

Diskless, one vCPU; every case uses fresh RAM. Failed accesses must retain
their original descriptor and data. HVF-only injected I/O failures exercise
the callback boundary, not an architecturally different permission policy.
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

GPA = 0x40200000
LOW = 0x80000000
HIGH = 0xffffff8080000000
TCR = 25 | 1 << 8 | 1 << 10 | 3 << 12 | 2 << 14 | 25 << 16 | 1 << 24 | 1 << 26 | 3 << 28 | 1 << 30 | 1 << 32


def cases():
    for el in (1, 0):
        for high in (False, True):
            for kind in ('rw-read', 'rw-write', 'ro-write', 'rx-fetch', 'nx-fetch',
                         'priv-read', 'invalid', 'af-clear', 'ha-read',
                         'hd-write', 'read-error', 'cas-error'):
                yield el, high, kind


def payload(code, el, high, kind):
    data = bytearray(0x40000)
    data[:len(code)] = code
    def put(off, val):
        struct.pack_into('<Q', data, off, val)
    # Separate TTBR trees. Both map the fixture; only the selected tree maps
    # the target. Swapping TTBR0/1 therefore cannot accidentally pass.
    for bank in range(2):
        l1 = 0x4000 + bank * 0x10000
        l2, l3, target = l1 + 0x4000, l1 + 0x8000, l1 + 0xc000
        put(l1, GPA + l2 + 3)
        put(l2 + (GPA >> 25) * 8, GPA + l3 + 3)
        put(l2 + (LOW >> 25) * 8, GPA + target + 3)
        for i in range(16):
            flags = 0x7c3 if i == 0 else 0x743 | (3 << 53)
            put(l3 + (((GPA >> 14) & 0x7ff) + i) * 8,
                GPA + i * 0x4000 | flags)
    leaf_off = (0x20000 if high else 0x10000)
    leaf = GPA + 0x30000 | 0x743 | (3 << 53)
    access, fault, cas = 0, 0, 0
    tcr = TCR
    if kind in ('rw-write', 'ro-write', 'hd-write'):
        access = 1
    if kind == 'ro-write':
        leaf |= 1 << 7
        fault = 0xf
    if kind in ('rx-fetch', 'nx-fetch'):
        access = 2
        if kind == 'rx-fetch':
            leaf = (leaf & ~(3 << 53)) | 1 << 7
        else:
            fault = 0xf
    if kind == 'priv-read':
        leaf &= ~(1 << 6)
        fault = 0xf if el == 0 else 0
    if kind == 'invalid':
        leaf = 0
        fault = 7
    if kind in ('af-clear', 'ha-read', 'cas-error'):
        leaf &= ~(1 << 10)
        if kind == 'af-clear':
            fault = 0xb
        else:
            tcr |= 1 << 39
            cas = 1
    if kind == 'hd-write':
        leaf |= (1 << 51) | (1 << 7)
        tcr |= (1 << 39) | (1 << 40)
        cas = 1
    if kind in ('read-error', 'cas-error'):
        fault = 0x17
    put(leaf_off, leaf)
    # A load marker and a real executable target (mov x16,#0xace; ret).
    put(0x30000, 0xd65f03c0d28159d0)
    expected_leaf = leaf
    if not fault and cas:
        expected_leaf |= 1 << 10
        if kind == 'hd-write':
            expected_leaf &= ~(1 << 7)
    return data, leaf_off, access, fault, cas, tcr, expected_leaf


def run_case(a, code, symbols, case):
    el, high, kind = case
    data, leaf_off, access, fault, cas, tcr, expected_leaf = payload(code, *case)
    if a.host_features and kind in ('ha-read', 'hd-write', 'cas-error'):
        fault = 0xf if kind == 'hd-write' else 0xb
        cas = 0
        expected_leaf = struct.unpack_from('<Q', data, leaf_off)[0]
    name = f'el{el}-{int(high)}-{kind}'
    path = a.out / f'{name}.bin'
    path.write_bytes(data)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'virt', '-cpu', 'max' if a.tcg else 'host',
           '-accel', 'tcg' if a.tcg else 'hvf,kernel-irqchip=off', '-m', '64M',
           '-display', 'none', '-serial', 'none', '-monitor', 'none', '-S',
           '-gdb', f'tcp:127.0.0.1:{port}', '-L', str(ROOT / 'qemu-sptm/pc-bios'),
           '-device', f'loader,file={path.resolve()},addr={GPA:#x},cpu-num=0,force-raw=on']
    item = {'name': name, 'command': cmd, 'expected_fsc': fault,
            'payload_sha256': hashlib.sha256(data).hexdigest()}
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
    if not a.tcg:
        env['QEMU_HVF_PTW_PROBE'] = '1'
    remote = None
    with (a.out / f'{name}.stderr').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stderr=log, stdout=subprocess.DEVNULL)
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
            regs = {0: HIGH if high else LOW, 1: access, 2: el, 3: tcr,
                    4: GPA + 0x4000, 5: GPA + 0x14000, 6: 0x801005, 7: 0xff,
                    8: 0 if a.host_features else 2,
                    9: 0,
                    12: GPA + leaf_off if kind == 'read-error' else 0,
                    13: int(kind == 'cas-error'), 15: 0xcafe, 20: GPA + 0x28000}
            for reg, val in regs.items():
                assert remote.command(f'P{reg:x}=' + struct.pack('<Q', val).hex()) == 'OK'
            assert remote.command('P21=c5030000') == 'OK'
            remote.send('c')
            time.sleep(.04)
            remote.sock.sendall(b'\x03')
            remote.receive()
            pc = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))[32]
            assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
            values = struct.unpack('<7Q', bytes.fromhex(remote.command(f'm{GPA + 0x28000:x},38')))
            leaf = struct.unpack('<Q', bytes.fromhex(remote.command(f'm{GPA + leaf_off:x},8')))[0]
            marker = struct.unpack('<Q', bytes.fromhex(remote.command(f'm{GPA + 0x30000:x},8')))[0]
            expected_marker = 0xcafe if access == 1 and not fault and a.tcg else 0xd65f03c0d28159d0
            passed = (pc == GPA + symbols['_ptw_stop'] and values[0] == int(not fault)
                      and values[3] & 0x3f == fault and leaf == expected_leaf
                      and marker == expected_marker)
            if a.tcg:
                if fault:
                    passed &= values[4] == regs[0]
                elif access == 0:
                    passed &= values[1] == marker
                elif access == 2:
                    passed &= values[1] == 0xace
            else:
                passed &= values[4] == 3 and values[5] == cas
                if a.host_features:
                    passed &= values[6] & 15 == 0
                if not fault:
                    passed &= values[1] == GPA + 0x30000 and bool(values[2] & (1 << access))
            item.update(pc=hex(pc), values=[hex(v) for v in values], leaf=hex(leaf),
                        expected_leaf=hex(expected_leaf), marker=hex(marker), passed=bool(passed))
        except Exception as exc:
            item.update(passed=False, error=str(exc))
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
    return item


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--tcg', action='store_true')
    ap.add_argument('--host-features', action='store_true',
                    help='Control: retain native HAFDBS=0 instead of modeling updates')
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    if a.tcg and a.host_features:
        ap.error('--host-features is an HVF control')
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).with_suffix('.S')
    code = assemble(a.out, source, ['-DPTW_TCG'] if a.tcg else [])
    symbols = {p[-1]: int(p[0], 16) for line in
               subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True).splitlines()
               if len(p := line.split()) == 3}
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(), 'cases': []}
    for case in cases():
        if a.tcg and case[2] in ('read-error', 'cas-error'):
            continue
        item = run_case(a, code, symbols, case)
        report['cases'].append(item)
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(item), flush=True)
    return int(any(not x['passed'] for x in report['cases']))


if __name__ == '__main__':
    raise SystemExit(main())
