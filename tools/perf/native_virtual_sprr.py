#!/usr/bin/env python3
"""Native accesses after live SPRR permission changes in the integrated backend.

Boot SPTM to its SPRR enable instruction. A debugger-installed fixture primes
a native data alias, changes the permission nibble via the real dispatcher,
then performs a read, write or fetch. This tests all 16 EL permission values;
It also tests the observed PMPRR=0x40010, CONFIG=0xfb sequence and denied
changes outside its mask. It does not claim guarded-mode or AMRANGE semantics.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import struct
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
    ap.add_argument('--locked-only', action='store_true',
                    help='Run only the PMPRR/configuration-lock cases')
    ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    pc = 0xfffffff0070a37dc
    code_pa = 0x8070a37dc
    target_va, target_pa = 0xfffffff006fb8000, 0x806fb8000
    ledger_data = a.ledger.read_bytes()
    words = list(struct.unpack_from('<' + str((len(ledger_data) - 8) // 4) + 'I', ledger_data, 8))
    def smc(word):
        if word not in words:
            words.append(word)
        return 0xd4000003 | ((0xe000 + words.index(word)) << 5)
    config, pperm = smc(0xd51ef100), smc(0xd51ef1c1)
    pmprr_write = smc(0xd51ef325)  # MSR PMPRR_EL1,x5 (VHE -> EL2)
    lock_config = smc(0xd51ef106)  # MSR CONFIG_EL1,x6
    restore_pperm = smc(0xd51ef1c7)
    user_write = smc(0xd51ef1a1)
    restriction_keys = {'restriction-pmprr': (3, 2, 'SPRR_PMPRR_EL2'),
                        'restriction-umprr': (3, 0, 'SPRR_UMPRR_EL1'),
                        'restriction-amrange': (14, 3, 'SPRR_AMRANGE_EL2')}
    restriction_words = {name: smc(0xd5000000 | (3 << 19) | (6 << 16) |
                                     (15 << 12) | (crm << 8) | (op2 << 5) | 16)
                         for name, (crm, op2, _) in restriction_keys.items()}
    a.ledger = a.out / 'fixture.ledger'
    a.ledger.write_bytes(b'DVEL' + struct.pack('<I', len(words)) + struct.pack('<' + str(len(words)) + 'I', *words))
    permissions = (0, 5, 1, 3, 0, 5, 1, 0, 0, 4, 1, 3, 0, 5, 1, 3)
    names = [f'{n}-{mode}' for n in range(16) for mode in ('read', 'write', 'exec')]
    names += ['invalid-config', 'disable-after-deny', *restriction_keys]
    locked_cases = {'mask2-read': (2, 'read'), 'mask2-write': (2, 'write'),
                    'mask9-read': (9, 'read'), 'mask9-write': (9, 'write'),
                    'lock2-write': (2, 'write'), 'lock9-write': (9, 'write'),
                    'lock2-revoke': (2, 'write'), 'lock9-revoke': (9, 'write'),
                    'lock2-guard': (2, 'read'), 'lock1-other': (1, 'read'),
                    'lock-config': (2, 'read'), 'lock-mask': (2, 'read'),
                    'lock-user': (2, 'read')}
    names += locked_cases
    if a.locked_only:
        names = list(locked_cases)
    table_pas = (0x807024000, 0x807028000, 0x80702c000, 0x807110000, 0x807114000)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(), 'runs': []}
    for name in names:
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
                if name in restriction_keys:
                    # Establish unsupported state through an actual MMU-off
                    # guest MSR, since GDB system-register writes are no-ops.
                    initial = registers(remote)
                    base = 0x900000000
                    payload = struct.pack('<2I', restriction_words[name], 0xd503201f)
                    assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                    assert remote.command(f'M{base:x},8:' + payload.hex()) == 'OK'
                    assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                    for reg, val in ((16, 1), (32, base)):
                        assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                    assert remote.command(f'Z1,{base + 4:x},4') == 'OK'
                    remote.send('c')
                    remote.receive()
                    established = registers(remote)
                    field = restriction_keys[name][2]
                    assert established['pc'] == base + 4 and established[field] == 1
                    assert remote.command(f'z1,{base + 4:x},4') == 'OK'
                    for reg, val in ((16, initial['x16']), (32, initial['pc'])):
                        assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'Z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['initial_stop'] = remote.receive()
                before = registers(remote)
                assert before['pc'] == pc, hex(before['pc'])
                assert before['SPRR_CONFIG_EL2'] == 0
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                def write(pa, data):
                    for offset in range(0, len(data), 512):
                        chunk = data[offset:offset + 512]
                        assert remote.command(f'M{pa + offset:x},{len(chunk):x}:' + chunk.hex()) == 'OK'
                    assert read_phys(remote, pa, len(data)) == data
                def leaf(va):
                    table = before['TTBR1_EL2'] & 0x0000ffffffffc000
                    for level, shift in enumerate((36, 25, 14), 1):
                        index = (va >> shift) & (7 if level == 1 else 0x7ff)
                        desc = int.from_bytes(read_phys(remote, table + index * 8, 8), 'little')
                        assert desc & 3 == 3, hex(desc)
                        if level == 3:
                            return desc, table + index * 8
                        table = desc & 0x0000ffffffffc000
                (desc, desc_pa), (code_desc, _) = leaf(target_va), leaf(pc)
                locked = name in locked_cases
                if locked:
                    selected_index, _ = locked_cases[name]
                    desc = (desc & ~((3 << 6) | (3 << 53))) | ((selected_index >> 2) << 6) | ((selected_index & 3) << 53)
                    write(desc_pa, desc.to_bytes(8, 'little'))
                index = ((desc >> 53) & 3) | (((desc >> 6) & 3) << 2)
                code_index = ((code_desc >> 53) & 3) | (((code_desc >> 6) & 3) << 2)
                assert index != code_index
                special = name in ('invalid-config', 'disable-after-deny') or name in restriction_keys or locked
                nibble, mode = (0, 'read') if special else (int(name.split('-')[0]), name.split('-')[1])
                bank = (before['SPRR_PPERM_EL2'] & ~(15 << (4 * index))) | (nibble << (4 * index))
                terminal = 0xd4000003 | (0xda22 << 5)
                marker = 0x1122334455667788
                target = bytearray(struct.pack('<I', 0xd503201f) * (0x4000 // 4))
                target[:8] = struct.pack('<2I', 0xd280b4a2, terminal)  # MOV x2,#0x5a5; SMC.
                write(target_pa, target)
                first_value = int.from_bytes(target[:8], 'little')
                access_word = {'read': 0xf9400062, 'write': 0xf9000062, 'exec': 0xd61f0060}[mode]
                # CONFIG=1, prime data alias, change PPERM, then retry access.
                payload = [config, 0xf9400064, pperm, access_word, terminal]
                expected_locked = None
                if locked:
                    mode = locked_cases[name][1]
                    base_bank = before['SPRR_PPERM_EL2']
                    bank = base_bank ^ (1 << (4 * index))
                    access_word = {'read': 0xf9400062, 'write': 0xf9000062}[mode]
                    payload = [config, 0xf9400064, pmprr_write, lock_config]
                    final_bank = base_bank
                    if name.startswith('mask'):
                        payload += [access_word, terminal]
                        ok = mode == 'read'
                        expected_locked = (pc + (20 if ok else 16), final_bank,
                                           first_value if ok else marker, False)
                    elif name in ('lock2-write', 'lock9-write'):
                        payload += [pperm, access_word, terminal]
                        expected_locked = (pc + 24, bank, marker, True)
                    elif name.endswith('revoke'):
                        # Create a native writable alias, then remove that bit.
                        payload += [pperm, access_word, restore_pperm, access_word, terminal]
                        expected_locked = (pc + 28, base_bank, marker, True)
                    else:
                        if name == 'lock2-guard':
                            bank = base_bank ^ (4 << (4 * index))
                            payload += [pperm]
                        elif name == 'lock1-other':
                            payload += [pperm]
                        elif name == 'lock-config':
                            payload += [0xd2800000, config]  # Try unlocking.
                        elif name == 'lock-mask':
                            payload += [0xd2800005, pmprr_write]
                        elif name == 'lock-user':
                            bank = before['SPRR_UPERM_EL0'] ^ 1
                            payload += [user_write]
                        expected_locked = (pc + 4 * (len(payload) - 1), base_bank, marker, False)
                        payload += [terminal]
                if name == 'invalid-config' or name in restriction_keys:
                    payload = [config, terminal]
                elif name == 'disable-after-deny':
                    # Enable, prime, deny the data nibble, then disable SPRR.
                    payload = [config, 0xf9400064, pperm, 0xd2800000,
                               config, access_word, terminal]
                write(code_pa, struct.pack('<' + str(len(payload)) + 'I', *payload))
                watched = (*table_pas, 0x807068000, 0x80706c000, 0x807070000,
                           0x807074000, 0x8165a8000, 0x8070a0000)
                hashes = {hex(pa): hashlib.sha256(read_phys(remote, pa, 0x4000)).hexdigest()
                          for pa in watched}
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                for reg, val in ((0, 0xfb if name == 'invalid-config' else 1),
                                 (1, bank), (2, marker), (3, target_va), (4, marker),
                                 (5, 0x40010), (6, 0xfb), (7, before['SPRR_PPERM_EL2'])):
                    assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                remote.send('c')
                item['test_stop'] = remote.receive()
                after = registers(remote)
                allowed = bool(permissions[nibble] & {'read': 1, 'write': 2, 'exec': 4}[mode])
                expected_pc = (target_va + 4 if allowed else target_va) if mode == 'exec' else pc + (16 if allowed else 12)
                expected_x2 = (0x5a5 if mode == 'exec' else first_value) if allowed and mode != 'write' else marker
                expected_config, expected_bank = 1, bank
                if name == 'invalid-config' or name in restriction_keys:
                    expected_pc, expected_x2 = pc, marker
                    expected_config, expected_bank = 0, before['SPRR_PPERM_EL2']
                elif name == 'disable-after-deny':
                    expected_pc, expected_x2, expected_config = pc + 24, first_value, 0
                elif locked:
                    expected_pc, expected_bank, expected_x2, wrote = expected_locked
                    expected_config = 0xfb
                    allowed = name in ('mask2-read', 'mask9-read',
                                       'lock2-write', 'lock9-write')
                item.update(final_pc=hex(after['pc']), x2=hex(after['x2']),
                            index=index, descriptor=hex(desc), pperm=hex(after['SPRR_PPERM_EL2']),
                            config=hex(after['SPRR_CONFIG_EL2']), allowed=allowed)
                assert after['pc'] == expected_pc, hex(after['pc'])
                assert after['x2'] == expected_x2, hex(after['x2'])
                assert after['SPRR_CONFIG_EL2'] == expected_config
                assert after['SPRR_PPERM_EL2'] == expected_bank
                if locked:
                    assert after['SPRR_PMPRR_EL2'] == 0x40010
                    assert after['SPRR_UPERM_EL0'] == before['SPRR_UPERM_EL0']
                    item['pmprr'] = hex(after['SPRR_PMPRR_EL2'])
                    item['writes_completed'] = int(wrote)
                assert after['x4'] == (marker if name == 'invalid-config' or name in restriction_keys else first_value), 'Alias was not primed'
                if name in restriction_keys:
                    assert after[restriction_keys[name][2]] == 1
                    item['restriction_readback'] = hex(after[restriction_keys[name][2]])
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                assert hashes == {hex(pa): hashlib.sha256(read_phys(remote, pa, 0x4000)).hexdigest()
                                   for pa in watched}, 'Code or page tables changed'
                expected = bytearray(target)
                if (locked and wrote) or (not locked and mode == 'write' and allowed):
                    expected[:8] = marker.to_bytes(8, 'little')
                assert read_phys(remote, target_pa, 0x4000) == expected, 'Wrong write permission or byte range'
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
