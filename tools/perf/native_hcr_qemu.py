#!/usr/bin/env python3
"""Validate QEMU HCR get/put with an MMU-on EL2 guest and permission faults.

Runs only diskless virt guests. The disabled control must reproduce the HCR
discrepancy on the affected host; enabled cases must retain native state and
real RO/NX faults after debugger inspection and resume.
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

from arm_island_bench import ROOT, Remote, assemble, terminate


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--qemu', type=Path,
                    default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    ap.add_argument('--disabled-control', action='store_true')
    ap.add_argument('--irqchip', choices=('on', 'off'), default='off')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    code = assemble(a.out, Path(__file__).with_name('native_hcr_mmu.S'),
                    ['-DNATIVE_QEMU_MMU'])
    symbols = {}
    for line in subprocess.check_output(
            ['nm', '-n', str(a.out / 'arm_island.o')], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2].startswith('_mmu_'):
            symbols[fields[2]] = int(fields[0], 16)
    assert len(code) < 0x4000
    memory = bytearray(0x20000)
    memory[:len(code)] = code
    struct.pack_into('<Q', memory, 0x4000, 0x40208003)
    for index in (32, 64):
        struct.pack_into('<Q', memory, 0x8000 + index * 8, 0x4020c003)
    for index in range(8):
        pte = (0x40200000 + index * 0x4000) | 0x703
        pte |= (3 << 53) if index else (1 << 7)
        struct.pack_into('<Q', memory, 0xc000 + (128 + index) * 8, pte)
    payload = a.out / 'payload.bin'
    payload.write_bytes(memory)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'payload_sha256': hashlib.sha256(memory).hexdigest(), 'runs': []}
    variants = [(False, 0)] if a.disabled_control else [(True, n) for n in range(3)]
    for enabled, protection in variants:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cmd = [str(a.qemu.resolve()), '-M', 'virt,virtualization=on',
               '-cpu', 'host', '-accel', f'hvf,kernel-irqchip={a.irqchip}', '-m', '64M', '-display', 'none',
               '-serial', 'none', '-monitor', f'unix:{a.out / str(protection)}.sock,server=on,wait=off', '-S',
               '-gdb', f'tcp:127.0.0.1:{port}', '-L', str(ROOT / 'qemu-sptm/pc-bios'),
               '-trace', f'enable=hvf_*,file={a.out / str(protection)}.trace',
               '-device', f'loader,file={payload.resolve()},addr=0x40200000,cpu-num=0,force-raw=on']
        env = dict(os.environ, QEMU_HVF_NATIVE_HCR='1' if enabled else '0')
        logpath = a.out / f'{protection}.stderr'
        remote = None
        item = {'enabled': enabled, 'protection': protection, 'command': cmd}
        with logpath.open('w') as log:
            child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError(logpath.read_text())
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
                initial = {}
                for r in ET.fromstring(xml).iter('reg'):
                    raw = remote.command(f'p{int(r.attrib["regnum"]):x}')
                    if raw and not raw.startswith('E'):
                        initial[r.attrib['name']] = hex(int.from_bytes(bytes.fromhex(raw), 'little'))
                (a.out / f'{protection}.initial.json').write_text(json.dumps(initial, indent=2))
                tcr = (25 | 1 << 8 | 1 << 10 | 3 << 12 | 2 << 14 |
                       25 << 16 | 1 << 30 | 1 << 32)
                values = {0: 0x488000000, 20: 0x80210000, 21: 0x40204000,
                          22: tcr, 23: 0xff,
                          24: 0x80200000 + symbols['_mmu_vectors'],
                          25: 0x80200000 + symbols['_mmu_virtual'],
                          26: protection, 32: 0x40200000, 33: 0x3c9}
                for reg, value in values.items():
                    data = value.to_bytes(4 if reg == 33 else 8, 'little').hex()
                    assert remote.command(f'P{reg:x}={data}') == 'OK'

                def run_stop():
                    remote.send('c')
                    # Tiny bounded payload, no firmware wait or disk I/O.
                    time.sleep(.05)
                    remote.sock.sendall(b'\x03')
                    remote.receive()
                    return struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))

                regs = run_stop()
                item['physical_snapshot'] = subprocess.check_output(
                    ['python3', str(ROOT / 'tools/hmp.py'), str(a.out / f'{protection}.sock'),
                     'xp /6gx 0x40210000'], text=True)
                item['registers'] = [hex(v) for v in regs]
                item['first_pc'] = hex(regs[32])
                item['guest_hcr'] = hex(regs[28])
                assert regs[32] == 0x80200000 + symbols['_mmu_wait'], item
                assert regs[28] == 0x488000000, item
                xml = ''
                while True:
                    chunk = remote.command(f'qXfer:features:read:system-registers.xml:{len(xml):x},1000')
                    xml += chunk[1:]
                    if chunk[0] == 'l':
                        break
                regnum = next(int(r.attrib['regnum']) for r in ET.fromstring(xml).iter('reg')
                              if r.attrib['name'] == 'HCR_EL2')
                observed = int.from_bytes(bytes.fromhex(remote.command(f'p{regnum:x}')), 'little')
                item['qemu_hcr'] = hex(observed)
                if not enabled:
                    item['accessor_discrepancy'] = observed != regs[28]
                    assert item['accessor_discrepancy'], item
                else:
                    assert observed == regs[28], item
                    before = bytes.fromhex(remote.command('m80210000,30'))
                    resume = 0x80200000 + symbols['_mmu_after']
                    assert remote.command('P20=' + struct.pack('<Q', resume).hex()) == 'OK'
                    regs = run_stop()
                    item['last_pc'] = hex(regs[32])
                    expected = '_mmu_fault_done' if protection else '_mmu_done'
                    assert regs[32] == 0x80200000 + symbols[expected], item
                    after = bytes.fromhex(remote.command('m80210100,58'))
                    item['before'] = [hex(v) for v in struct.unpack('<6Q', before)]
                    item['after'] = [hex(v) for v in struct.unpack('<6Q', after[:0x30])]
                    item['state_preserved'] = before == after[:0x30]
                    esr, far, fault_hcr = struct.unpack_from('<QQQ', after, 0x40)
                    item.update(guest_esr=hex(esr), guest_far=hex(far), fault_hcr=hex(fault_hcr))
                    assert item['state_preserved'], item
                    if protection:
                        assert esr >> 26 == (0x25 if protection == 1 else 0x21), item
                        assert esr & 0x3f == 0xf, item
                        assert fault_hcr == 0x488000000, item
                        assert far == (0x80200000 if protection == 1 else 0x80210100), item
                    # The attempted store must not have modified executable code.
                    checked = bytes.fromhex(remote.command('m80200000,100'))
                    assert checked == code[:0x100], item
                item['passed'] = True
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
                (a.out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
                print(json.dumps(item), flush=True)


if __name__ == '__main__':
    main()
