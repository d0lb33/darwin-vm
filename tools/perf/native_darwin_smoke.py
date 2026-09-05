#!/usr/bin/env python3
"""Diskless, bounded native adapter and RAM boundary tests on the Darwin board."""
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
from native_dram_tree import properties

signal.signal(signal.SIGTERM, terminate)
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--dtree', type=Path, required=True)
ap.add_argument('--accel', choices=['hvf', 'tcg'], default='hvf')
ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
code = assemble(a.out, Path(__file__).with_name('native_el2_probe.S'), ['-DNATIVE_DARWIN_UART'])
payload = a.out / 'payload.bin'
payload.write_bytes(code)
tree = a.dtree.read_bytes()
_, nodes = properties(tree)
armio = next(p for path, p in nodes.items() if path.endswith('/arm-io'))
uart = next(p for path, p in nodes.items() if path.endswith('/arm-io/uart0'))
iobase = struct.unpack_from('<Q', tree, armio['ranges'][0] + 8)[0]
uartbase = iobase + struct.unpack_from('<Q', tree, uart['reg'][0])[0]
base = 0x800040000
results = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
           'payload_sha256': hashlib.sha256(code).hexdigest(), 'uart': hex(uartbase), 'runs': []}
for case, enabled in (((8, True), (9, False), (8, False)) if a.accel == 'hvf' else ((9, False),)):
    tag = f'{case}-{int(enabled)}'
    serial = (a.out / f'{tag}.serial').resolve()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host' if a.accel == 'hvf' else 'max',
           '-accel', 'hvf,nested-virt=on,ipa-bits=40' if a.accel == 'hvf' else 'tcg', '-m', '8G', '-display', 'none',
           '-serial', f'file:{serial}', '-monitor', 'none', '-S', '-gdb', f'tcp:127.0.0.1:{port}',
           '-dtree', str(a.dtree.resolve()), '-bootkc', str(ROOT / 'firmware/bootkc'),
           '-sptm', str(ROOT / 'firmware/sptm'), '-txm', str(ROOT / 'firmware/txm'),
           '-tc', str(ROOT / 'firmware/ramdisk.tc'), '-ramdisk', str(ROOT / 'firmware/ramdisk.dmg'),
           '-device', f'loader,file={payload.resolve()},addr={base:#x},force-raw=on']
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DARWIN_', 'GXFSTAT_', 'QEMU_HVF_'))}
    if enabled:
        env['QEMU_HVF_APPLE_BOOT_COMPAT'] = '1'
    if case == 9:
        env['DARWIN_UNIMP_DEBUG'] = '1'
    remote = None
    with (a.out / f'{tag}.stderr').open('w') as log:
        child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
        try:
            end = time.monotonic() + 10
            while time.monotonic() < end:
                if child.poll() is not None:
                    raise RuntimeError((a.out / f'{tag}.stderr').read_text())
                try:
                    remote = Remote(port)
                    break
                except ConnectionRefusedError:
                    time.sleep(.02)
            assert remote is not None, 'GDB startup timeout'
            remote.sock.settimeout(5)
            remote.command('?')
            for reg, value in ((0, 10000), (1, case), (16, uartbase), (32, base)):
                assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
            start = time.monotonic()
            remote.send('c')
            while time.monotonic() - start < (3 if enabled or case == 9 else .2):
                if serial.exists() and b'OK\n' in serial.read_bytes():
                    break
                time.sleep(.01)
            remote.sock.sendall(b'\x03')
            remote.receive()
            regs = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
            done = serial.exists() and b'OK\n' in serial.read_bytes()
            item = {'case': case, 'adapter_enabled': enabled, 'done': done,
                    'checksum': regs[2], 'marker': regs[4], 'current_el': regs[5],
                    'pc': hex(regs[32]), 'command': cmd}
            results['runs'].append(item)
            (a.out / 'results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(item), flush=True)
            if enabled or case == 9:
                assert done and regs[4] == 0x600d and regs[5] == 8, item
                assert regs[2] == (50005000 if case == 8 else 3 * 0x1234), item
                if case == 9:
                    log.flush()
                    trace = (a.out / f'{tag}.stderr').read_text()
                    assert 'unimp: write 0x210059010' in trace, 'Adjacent MMIO callback bypassed'
                    assert 'unimp: read  0x210059010' in trace, 'Adjacent MMIO read callback bypassed'
            else:
                assert not done, 'Negative control unexpectedly completed'
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
