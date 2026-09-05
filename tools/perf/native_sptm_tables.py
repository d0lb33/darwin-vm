#!/usr/bin/env python3
"""Capture real SPTM translation tables before its first MMU enable under TCG.

Firmware-specific breakpoint; no guest patches, disks, or register writes.
The owned vCPU stays stopped throughout collection and exits afterward.
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
import xml.etree.ElementTree as ET
from arm_island_bench import ROOT, Remote, terminate


def registers(remote, extra_prefixes=()):
    raw = bytes.fromhex(remote.command('g'))
    values = struct.unpack_from('<33Q', raw)
    result = {f'x{i}' if i < 31 else 'sp' if i == 31 else 'pc': v
              for i, v in enumerate(values)}
    result['pstate'] = struct.unpack_from('<I', raw, 33 * 8)[0]
    xml = ''
    while True:
        chunk = remote.command(f'qXfer:features:read:system-registers.xml:{len(xml):x},1000')
        xml += chunk[1:]
        if chunk[0] == 'l':
            break
    for reg in ET.fromstring(xml).iter('reg'):
        name = reg.attrib['name']
        if name.startswith(('TCR', 'TTBR', 'SCTLR', 'HCR', 'MAIR', 'VBAR',
                            'SPRR', 'GXF', 'CURRENT', 'ID_AA64MMFR',
                            'CPTR', 'CPACR', 'DCZID', 'CTRR', 'CTXR',
                            'ACC_CTRR', 'ACC_CTXR') + extra_prefixes):
            result[name] = int.from_bytes(bytes.fromhex(remote.command(
                f'p{int(reg.attrib["regnum"]):x}')), 'little')
    return result


def read_phys(remote, address, size):
    value = bytearray()
    for off in range(0, size, 1024):
        chunk = remote.command(f'm{address + off:x},{min(1024, size - off):x}')
        if chunk.startswith('E'):
            raise RuntimeError(f'Physical read failed at {address + off:#x}: {chunk}')
        value.extend(bytes.fromhex(chunk))
    assert len(value) == size
    return bytes(value)


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--pc', type=lambda x: int(x, 0), default=0x8070a3740)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'max', '-accel', 'tcg',
           '-m', '8G', '-display', 'none', '-monitor', 'none',
           '-serial', f'file:{a.out.resolve() / "serial.log"}', '-S',
           '-gdb', f'tcp:127.0.0.1:{port}', '-dtree', str(a.dtree.resolve())]
    for option, name in (('-bootkc', 'bootkc'), ('-sptm', 'sptm'), ('-txm', 'txm'),
                         ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')):
        cmd.extend((option, str(ROOT / 'firmware' / name)))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
    report = {'command': cmd, 'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256((ROOT / 'firmware/sptm').read_bytes()).hexdigest(),
              'dtree_sha256': hashlib.sha256(a.dtree.read_bytes()).hexdigest(), 'pages': []}
    remote = None
    with (a.out / 'stderr.log').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stderr=log, stdout=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
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
            assert remote.command(f'Z1,{a.pc:x},4') == 'OK'
            remote.send('c')
            try:
                report['stop'] = remote.receive()
            except socket.timeout:
                remote.sock.sendall(b'\x03')
                report['stop'] = remote.receive()
            state = registers(remote)
            report['state'] = {k: hex(v) for k, v in state.items()}
            assert state['pc'] == a.pc, f'Unexpected stop at {state["pc"]:#x}'
            assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
            instruction = read_phys(remote, a.pc, 4)
            assert instruction == bytes.fromhex('001018d5'), instruction.hex()
            # This capture deliberately supports only the observed 16 KiB
            # regime. Reject other layouts rather than mis-decoding tables.
            tcr = state['TCR_EL2'] if state['pstate'] & 0xc == 8 else state['TCR_EL1']
            assert (tcr >> 14) & 3 == 2 and (tcr >> 30) & 3 == 1, hex(tcr)
            assert not (tcr >> 59) & 1, 'LPA2 descriptor layout unsupported'
            bank = 2 if state['pstate'] & 0xc == 8 else 1
            queue = []
            for half in (0, 1):
                tsz = (tcr >> (16 if half else 0)) & 63
                levels = (64 - tsz - 14 + 10) // 11
                assert 1 <= levels <= 4
                root = state[f'TTBR{half}_EL{bank}'] & 0x0000ffffffffc000
                if root:
                    queue.append((root, 4 - levels))
            seen = set()
            while queue:
                pa, level = queue.pop(0)
                if (pa, level) in seen:
                    continue
                assert len(seen) < 4096, 'Table capture exceeded 64 MiB bound'
                assert 0x800000000 <= pa < 0xa00000000, f'Table outside guest RAM: {pa:#x}'
                seen.add((pa, level))
                data = read_phys(remote, pa, 0x4000)
                name = f'table-{pa:x}.bin'
                (a.out / name).write_bytes(data)
                entries = [(i, v) for i, v in enumerate(struct.unpack('<2048Q', data)) if v & 1]
                report['pages'].append({'pa': hex(pa), 'level': level, 'file': name,
                                        'sha256': hashlib.sha256(data).hexdigest(),
                                        'valid_entries': len(entries)})
                if level < 3:
                    for _, value in entries:
                        if value & 3 == 3:
                            queue.append((value & 0x0000ffffffffc000, level + 1))
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
    print(json.dumps(report, indent=2))
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
