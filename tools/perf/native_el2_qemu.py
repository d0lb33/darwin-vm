#!/usr/bin/env python3
"""Run the independently written EL2 payload through QEMU's real HVF backend."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time
from arm_island_bench import ROOT, assemble, Remote, terminate

signal.signal(signal.SIGTERM, terminate)
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
code = assemble(a.out, Path(__file__).with_name('native_el2_probe.S'),
                ['-DNATIVE_QEMU_UART'])
payload = a.out / 'payload.bin'
payload.write_bytes(code)
symbols = subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True)
stop_offset = next(int(line.split()[0], 16) for line in symbols.splitlines()
                   if line.endswith(' _native_probe_stop'))
base = 0x40200000
results = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
           'payload_sha256': hashlib.sha256(code).hexdigest(), 'runs': []}
for case in range(5):
    serial = (a.out / f'{case}.serial').resolve()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'virt,virtualization=on', '-cpu', 'host',
           '-accel', 'hvf', '-m', '64M', '-display', 'none', '-serial', f'file:{serial}',
           '-monitor', 'none', '-S', '-gdb', f'tcp:127.0.0.1:{port}',
           '-L', str(ROOT / 'qemu-sptm/pc-bios'), '-device',
           f'loader,file={payload.resolve()},addr={base:#x},cpu-num=0,force-raw=on']
    remote = None
    with (a.out / f'{case}.stderr').open('w') as log:
        child = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log)
        try:
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if child.poll() is not None:
                    raise RuntimeError((a.out / f'{case}.stderr').read_text())
                try:
                    remote = Remote(port)
                    break
                except ConnectionRefusedError:
                    time.sleep(.02)
            if remote is None:
                raise RuntimeError('QEMU GDB startup timeout')
            remote.sock.settimeout(5)
            remote.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.command('?')
            count = 1000000 if case == 0 else 10000 if case == 4 else 1
            for reg, value in [(0, count), (1, case)]:
                assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
            start = time.monotonic()
            remote.send('c')
            while time.monotonic() - start < 5:
                if serial.exists() and b'OK\n' in serial.read_bytes():
                    break
                time.sleep(.01)
            remote.sock.sendall(b'\x03')
            reply = remote.receive()
            elapsed = time.monotonic() - start
            regs = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
            (a.out / f'{case}.registers.json').write_text(json.dumps(
                {f'x{i}' if i < 31 else 'sp' if i == 31 else 'pc': hex(v)
                 for i, v in enumerate(regs)}, indent=2))
            assert serial.exists() and b'OK\n' in serial.read_bytes(), regs
            assert regs[32] == base + stop_offset and regs[4] == 0x600d, (reply, regs)
            assert regs[5] == 8, regs
            if case == 0:
                assert regs[2] == 3 * count and regs[7] == 0
            if case == 4:
                assert regs[2] == count * (count + 1) // 2 and regs[7] == 2 * count
            result = {'case': case, 'iterations': count, 'current_el': regs[5],
                      'checksum': regs[2], 'guest_exceptions': regs[7],
                      'esr': hex(regs[8]), 'pc': hex(regs[32]),
                      'host_seconds_including_polling_and_debugger': elapsed, 'command': cmd}
            results['runs'].append(result)
            print(json.dumps(result), flush=True)
            (a.out / 'results.json').write_text(json.dumps(results, indent=2))
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
