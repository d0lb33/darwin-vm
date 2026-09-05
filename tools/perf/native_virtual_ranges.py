#!/usr/bin/env python3
"""Real-dispatcher controls for inactive CTRR/CTXR bounds (not activation).

Synthetic MMU-off guests establish a control value, read it back, then attempt
one bound write. Active/locked cases never access memory in the protected
range, and must stop before changing the bound. This does not validate range
permission enforcement or lock-write hardware behavior.
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
from arm_island_bench import ROOT, Remote, terminate
from native_virtual_fp import feature


def sysreg(key, rt, read=False):
    op0, op1, crn, crm, op2 = key
    return (0xd5000000 | (int(read) << 21) | (op0 << 19) | (op1 << 16) |
            (crn << 12) | (crm << 8) | (op2 << 5) | rt)


def main():
    signal.signal(signal.SIGTERM, terminate)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtree', type=Path, required=True)
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    # Encodings from scripts/darwin/sysregs.py, also in the M5 field dump.
    banks = [('CTRR_C', (2, 0), (1, 0), 1),
             ('CTRR_D', (2, 1), (1, 2), 1),
             ('CTXR_A', (6, 2), (4, 2), 1 << 62),
             ('CTXR_B', (6, 3), (4, 4), 1 << 62),
             ('CTXR_C', (6, 4), (4, 6), 1 << 62),
             ('CTXR_D', (6, 5), (5, 0), 1 << 62)]
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(), 'runs': []}
    base, marker = 0x900000000, 0xdeaddead
    for bank, ctl, lower, active in banks:
        for upper in (False, True):
            key = (3, 0, 11, lower[0], lower[1] + int(upper))
            words = [sysreg((3, 4, 1, 1, 0), 3),
                     sysreg((3, 0, 11, *ctl), 0),
                     sysreg((3, 0, 11, *ctl), 4, True),
                     sysreg(key, 1), sysreg(key, 2, True)]
            stem = f'{bank}-{"upper" if upper else "lower"}'
            ledger, payload = a.out / f'{stem}.ledger', a.out / f'{stem}.bin'
            ledger.write_bytes(b'DVEL' + struct.pack('<I', len(words)) +
                               struct.pack('<5I', *words))
            payload.write_bytes(struct.pack('<6I', *(
                [0xd4000003 | ((0xe000 + i) << 5) for i in range(5)] +
                [0xd4000003 | (0xda22 << 5)])))
            variants = [('inactive', 0, 0x12345fff),
                        ('active', active, 0x12345fff),
                        ('locked', (1 << 63) | active, 0x12345fff),
                        ('reserved-address', 0, (1 << 42) | 0x12345fff)]
            for variant, control, address in variants:
                name = f'{stem}-{variant}'
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    port = sock.getsockname()[1]
                cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
                       '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
                       '-display', 'none', '-serial', 'none', '-monitor', 'none',
                       '-dtree', str(a.dtree.resolve()), '-S', '-gdb', f'tcp:127.0.0.1:{port}']
                for option, file in [('-bootkc', 'bootkc'), ('-sptm', 'sptm'),
                                     ('-txm', 'txm'), ('-tc', 'ramdisk.tc'),
                                     ('-ramdisk', 'ramdisk.dmg')]:
                    cmd.extend([option, str(ROOT / 'firmware' / file)])
                cmd.extend(['-device', f'loader,file={payload.resolve()},addr={base:#x},force-raw=on'])
                env = {k: v for k, v in os.environ.items()
                       if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
                env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_LEDGER=str(ledger.resolve()))
                item = {'name': name, 'command': cmd, 'control': hex(control),
                        'address': hex(address), 'passed': False}
                remote = None
                with (a.out / f'{name}.stderr').open('w') as log:
                    child = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=log)
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
                        assert remote is not None, 'Debugger startup timeout'
                        remote.sock.settimeout(5)
                        remote.command('?')
                        for reg, value in ((32, base), (0, control), (1, address),
                                           (2, marker), (3, 0x488000000)):
                            assert remote.command(f'P{reg:x}=' + struct.pack('<Q', value).hex()) == 'OK'
                        remote.send('c')
                        item['stop'] = remote.receive()
                        regs = struct.unpack_from('<33Q', bytes.fromhex(remote.command('g')))
                        item.update(pc=hex(regs[32]), readback=hex(regs[2]),
                                    control_readback=hex(regs[4]))
                        name_in_xml = f'{bank}_{"UPR" if upper else "LWR"}_EL2'
                        field = next(r for r in feature(remote, 'system-registers.xml').iter('reg')
                                     if r.attrib['name'] == name_in_xml)
                        stored = int.from_bytes(bytes.fromhex(remote.command(
                            f'p{int(field.attrib["regnum"]):x}')), 'little')
                        item['stored_bound'] = hex(stored)
                        assert regs[4] == control, 'Control fixture was not established'
                        if variant == 'inactive':
                            assert regs[32] == base + 20 and regs[2] == 0x12345000
                            assert stored == 0x12345000
                        else:
                            assert regs[32] == base + 12 and regs[2] == marker
                            assert stored == 0, 'Rejected write changed register backing'
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
                print(json.dumps({k: v for k, v in item.items() if k != 'command'}), flush=True)
    return int(not all(r['passed'] for r in report['runs']))


if __name__ == '__main__':
    raise SystemExit(main())
