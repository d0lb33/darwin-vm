#!/usr/bin/env python3
"""Test real SPTM table stores and removal of cached native permissions.

Debugger fixture changes are confined to each disposable guest. Tests boot
to SPTM's first observed table invalidation, then replace only its following
instructions (and the STR source register in replacement cases).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import traceback
from arm_island_bench import ROOT, Remote, terminate
from native_sptm_tables import read_phys, registers


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
    pc = 0xfffffff0070d5d74
    code_pa = 0x8070d5d74
    table_pa, pte_pa = 0x8165a8000, 0x8165aa440
    table_va, data_va = 0xfffffff03ae3e440, 0xfffffff03b220000
    data_pa, new_pa = 0x81698c000, 0x920000000
    table_pas = (0x807024000, 0x807028000, 0x80702c000, 0x807110000, 0x807114000)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(), 'runs': []}
    for name in ('clear', 'remap-read', 'restrict-write', 'remove-self', 'guest-ro', 'unaligned'):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
               '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
               '-display', 'none', '-serial', 'none', '-monitor', 'none',
               '-dtree', str(a.dtree.resolve()), '-S', '-gdb', f'tcp:127.0.0.1:{port}',
               '-sptm', str(a.sptm.resolve())]
        for option, file in [('-bootkc', 'bootkc'), ('-txm', 'txm'),
                             ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')]:
            cmd.extend([option, str(ROOT / 'firmware' / file)])
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
        env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_SHADOW='1',
                   QEMU_HVF_VIRTUAL_LEDGER=str(a.ledger.resolve()))
        item = {'name': name, 'command': cmd, 'passed': False}
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
                assert remote.command(f'Z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['initial_stop'] = remote.receive()
                before = registers(remote)
                assert before['pc'] == pc, hex(before['pc'])
                assert before['x0'] == table_va, hex(before['x0'])
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                def write(pa, data):
                    assert remote.command(f'M{pa:x},{len(data):x}:' + data.hex()) == 'OK'
                    assert read_phys(remote, pa, len(data)) == data
                original = int.from_bytes(read_phys(remote, pte_pa, 8), 'little')
                assert original == 0x6000081698c603, hex(original)
                marker = 0x1122334455667788
                replacement, address, load_address, changed_pa = 0, table_va, data_va, pte_pa
                opcode = 0xf900001f  # Original STR xzr,[x0].
                following = 0xf9400062  # LDR x2,[x3].
                if name == 'remap-read':
                    opcode = 0xf9000001  # STR x1,[x0].
                    replacement = (original & ~0x0000ffffffffc000) | new_pa
                elif name == 'restrict-write':
                    opcode = 0xf9000001
                    replacement = original | 0x80
                    following = 0xf9000062  # STR x2,[x3].
                elif name == 'remove-self':
                    # Clear the leaf mapping the page containing this very PTE.
                    address = table_va - 0x2440 + 0x1c78
                    changed_pa = table_pa + 0x1c78
                    load_address = address
                elif name == 'guest-ro':
                    self_pa = table_pa + 0x1c78
                    self_desc = int.from_bytes(read_phys(remote, self_pa, 8), 'little')
                    assert self_desc & 0x80 == 0
                    write(self_pa, (self_desc | 0x80).to_bytes(8, 'little'))
                elif name == 'unaligned':
                    address += 1
                write(code_pa, b''.join(w.to_bytes(4, 'little') for w in
                                        (opcode, following, 0xd4000003 | (0xda22 << 5))))
                new_bytes = bytes.fromhex('abcdef1234567890')
                write(new_pa, new_bytes)
                original_data = read_phys(remote, data_pa, 0x4000)
                watched = (*table_pas, 0x807068000, 0x80706c000, 0x807070000,
                           0x807074000, table_pa, 0x8070d4000)
                contents = {pa: read_phys(remote, pa, 0x4000) for pa in watched}
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                for reg, val in ((0, address), (1, replacement), (2, marker), (3, load_address)):
                    assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['test_stop'] = remote.receive()
                after = registers(remote)
                accepted = name not in ('guest-ro', 'unaligned')
                expected_pc = pc + (8 if name == 'remap-read' else 4 if accepted else 0)
                item.update(final_pc=hex(after['pc']), x2=hex(after['x2']),
                            pte_pa=hex(changed_pa), replacement=hex(replacement))
                assert after['pc'] == expected_pc, hex(after['pc'])
                assert after['x2'] == (int.from_bytes(new_bytes, 'little')
                                       if name == 'remap-read' else marker)
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                expected = dict(contents)
                if accepted:
                    candidate = bytearray(contents[table_pa])
                    offset = changed_pa - table_pa
                    candidate[offset:offset + 8] = replacement.to_bytes(8, 'little')
                    expected[table_pa] = bytes(candidate)
                actual = {pa: read_phys(remote, pa, 0x4000) for pa in watched}
                item['hashes_before'] = {hex(pa): hashlib.sha256(v).hexdigest()
                                         for pa, v in contents.items()}
                item['hashes_after'] = {hex(pa): hashlib.sha256(v).hexdigest()
                                        for pa, v in actual.items()}
                assert actual == expected, 'Unexpected code or table bytes changed'
                assert read_phys(remote, data_pa, 0x4000) == original_data, 'Stale writable mapping survived'
                assert read_phys(remote, new_pa, 8) == new_bytes
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
        print(json.dumps(item), flush=True)
    return int(not all(r['passed'] for r in report['runs']))


if __name__ == '__main__':
    raise SystemExit(main())
