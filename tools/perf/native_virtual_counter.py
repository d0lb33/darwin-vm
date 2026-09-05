#!/usr/bin/env python3
"""Test counter rate, shared epoch, pause behavior and rejected offset contexts."""
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
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    code = assemble(a.out, Path(__file__).with_suffix('.S'))
    names = subprocess.check_output(['nm', '-n', str(a.out / 'arm_island.o')], text=True)
    symbols = {line.split()[-1]: int(line.split()[0], 16)
               for line in names.splitlines() if len(line.split()) == 3}
    words = {}
    for off in range(0, len(code), 4):
        word = struct.unpack_from('<I', code, off)[0]
        if (word & 0xffc00000) == 0xd5000000 and (word >> 19) & 3:
            index = words.setdefault(word, len(words))
            struct.pack_into('<I', code, off, 0xd4000003 | ((0xe000 + index) << 5))
    payload, ledger = a.out / 'payload.bin', a.out / 'ledger.bin'
    payload.write_bytes(code)
    ledger.write_bytes(b'DVEL' + struct.pack('<I', len(words)) + b''.join(
        struct.pack('<I', w) for w in words))
    base = 0x900000000
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'payload_sha256': hashlib.sha256(code).hexdigest(), 'runs': []}
    for name, offsets in [('common', (0, 0)), ('arm-offset', (1, 0)),
                          ('apple-offset', (0, 1)), ('write-ss', (0, 0))]:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
               '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
               '-display', 'none', '-serial', 'none', '-monitor', 'none',
               '-dtree', str(a.dtree.resolve()), '-S', '-gdb', f'tcp:127.0.0.1:{port}']
        for option, file in [('-bootkc', 'bootkc'), ('-sptm', 'sptm'), ('-txm', 'txm'),
                             ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')]:
            cmd.extend([option, str(ROOT / 'firmware' / file)])
        cmd.extend(['-device', f'loader,file={payload.resolve()},addr={base:#x},force-raw=on'])
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
        env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_LEDGER=str(ledger.resolve()))
        item = {'name': name, 'command': cmd, 'offsets': offsets, 'passed': False}
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
                def put(reg, value):
                    assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
                put(32, base + (symbols['_counter_write_ss'] if name == 'write-ss' else 0))
                put(0, offsets[0])
                put(1, offsets[1])
                put(20, 100000000)
                start = time.monotonic()
                remote.send('c')
                item['stop'] = remote.receive()
                elapsed = time.monotonic() - start
                regs = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
                item['registers'] = [hex(v) for v in regs]
                item['elapsed'] = elapsed
                assert regs[2:4] == offsets, 'Offset write/readback mismatch'
                if name == 'common':
                    assert regs[32] == base + symbols['_counter_done'], hex(regs[32])
                    assert regs[4:6] == (3, 3) and regs[10] == 24000000
                    assert list(regs[11:20]) == sorted(regs[11:20]) and regs[11] > 0
                    ticks = regs[16] - regs[12]
                    item['observed_hz'] = ticks / elapsed
                    assert 0.80 * 24000000 < item['observed_hz'] < 1.20 * 24000000
                    before = regs[19]
                    time.sleep(.1)
                    put(32, base + symbols['_counter_sample'])
                    put(20, 0)
                    remote.send('c')
                    remote.receive()
                    after = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
                    item['paused_counter_delta'] = after[11] - before
                    assert after[32] == base + symbols['_counter_done']
                    assert 0 <= item['paused_counter_delta'] < 240000
                elif name == 'write-ss':
                    assert regs[32] == base + symbols['_counter_write_ss'], hex(regs[32])
                    assert regs[11] == 0, 'Counter read ran after denied write'
                else:
                    assert regs[32] == base + symbols['_counter_route'], hex(regs[32])
                    assert regs[11] == 0, 'Counter read ran after rejected redirection'
                item['passed'] = True
            except Exception as exc:
                item['error'] = str(exc)
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
