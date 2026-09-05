#!/usr/bin/env python3
"""Test native VHE aliasing or the opt-in QEMU VBAR compatibility adapter."""
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


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--native', action='store_true', help='Probe HVF directly; failed aliases return nonzero')
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).resolve().with_suffix('')
    results = []
    if a.native:
        binary = a.out / 'probe'
        ent = a.out / 'entitlements.plist'
        ent.write_text('<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>')
        subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                        str(source.with_suffix('.c')), str(source.with_suffix('.S')),
                        '-framework', 'Hypervisor', '-o', str(binary)], check=True)
        subprocess.run(['codesign', '-s', '-', '--entitlements', str(ent), str(binary)], check=True)
        for hcr in (0x80000000, 0x480000000, 0x488000000):
            run = subprocess.run([str(binary), hex(hcr)], capture_output=True, text=True, timeout=6)
            results.append({'returncode': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr})
        (a.out / 'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2))
        return int(any(r['returncode'] for r in results))

    code = assemble(a.out, source.with_suffix('.S'), ['-DNATIVE_QEMU_ALIAS'])
    payload = a.out / 'payload.bin'
    payload.write_bytes(code)
    symbols = subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True)
    stop = next(int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(' _alias_stop'))
    for hcr in (0x80000000, 0x480000000, 0x488000000):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        logpath = a.out / f'{hcr:x}.stderr'
        cmd = [str(a.qemu.resolve()), '-M', 'virt,virtualization=on', '-cpu', 'host',
               '-accel', 'hvf,kernel-irqchip=off', '-m', '64M', '-display', 'none',
               '-serial', 'none', '-monitor', 'none', '-S', '-gdb', f'tcp:127.0.0.1:{port}',
               '-L', str(ROOT / 'qemu-sptm/pc-bios'), '-device',
               f'loader,file={payload.resolve()},addr=0x40200000,cpu-num=0,force-raw=on']
        item = {'hcr': hex(hcr), 'command': cmd,
                'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest()}
        remote = None
        with logpath.open('w') as log:
            child = subprocess.Popen(cmd, env=dict(os.environ, QEMU_HVF_NATIVE_HCR='1',
                                     QEMU_HVF_APPLE_BOOT_COMPAT='1'), stderr=log,
                                     stdout=subprocess.DEVNULL)
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
                for reg, value in [(0, hcr), (1, 0x40204000), (2, 0x40208000),
                                   (3, 0x4020c000), (20, 0x40203000)]:
                    assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
                remote.send('c')
                time.sleep(.05)
                remote.sock.sendall(b'\x03')
                remote.receive()
                pc = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))[32]
                values = struct.unpack('<7Q', bytes.fromhex(remote.command('m40203000,38')))
                item.update(pc=hex(pc), values=[hex(v) for v in values])
                assert pc == 0x40200000 + stop, item
                assert values[:4] == (0x40204000, 0x40208000, hcr, 0x4020c000), item
                assert values[4] == (0x4020c000 if hcr & (1 << 34) else 0x40208000), item
                assert values[5] == hcr, item
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
                results.append(item)
                (a.out / 'results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps(item), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
