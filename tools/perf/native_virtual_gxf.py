#!/usr/bin/env python3
"""Exercise actual GENTER/GEXIT dispatch and native guarded permissions.

A disposable debugger fixture replaces SPTM's initial SPRR setup. Each case
boots a fresh owned VM. No runtime GDB system-register writes are relied on.
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
    ap.add_argument('--control-only', action='store_true')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    pc, code_pa = 0xfffffff0070a37dc, 0x8070a37dc
    entry, entry_pa = pc + 0x100, code_pa + 0x100
    target, target_pa = 0xfffffff006fb8000, 0x806fb8000
    marker, gl_stack = 0x1122334455667788, 0xfffffff007122000
    data = a.ledger.read_bytes()
    words = list(struct.unpack_from('<' + str((len(data) - 8) // 4) + 'I', data, 8))
    def smc(word):
        if word not in words:
            words.append(word)
        return 0xd4000003 | ((0xe000 + words.index(word)) << 5)
    config, pperm, mask, lock = map(smc, (0xd51ef100, 0xd51ef1c9, 0xd51ef325, 0xd51ef106))
    gxf, entry_write, enter, leave = map(smc, (0xd51ef140, 0xd51ef82a, 0x00201420, 0x00201400))
    enters = {n: smc(0x00201420 | n) for n in (*range(16), 16, 31)}
    esr_read = smc(0xd53efba2)
    protect, seal, illegal = map(smc, (0xd51ef1cd, 0xd51ef10e, 0xd51ef1cf))
    elr_read, tpidr_read, vhe_read, icache = map(smc, (0xd53efbc2, 0xd53efb22, 0xd53efac2, 0xd508751f))
    pan_clear, pan_set = map(smc, (0xd500409f, 0xd500419f))
    pmu2, pmu12, pmuvhe, pmu2_read, pmu12_read, pmu_enable, pmc0, pmc1 = map(
        smc, (0xd51ef8b2, 0xd519f852, 0xd51ef8f2, 0xd53ef8a2,
              0xd539f842, 0xd519f000, 0xd53af002, 0xd53af104))
    pmu2_read4, pmu12_read4 = map(smc, (0xd53ef8a4, 0xd539f844))
    vmsa, vmsa_read, vmsa_clear, vbar, vbar_vhe, ttbr1, sctlr_off = map(
        smc, (0xd51cf152, 0xd53cf1a2, 0xd51cf1bf,
              0xd51cc00c, 0xd518c00c, 0xd51c202c, 0xd51c100c))
    lower_config, lower_entry, lower_abort, lower_config_read, lower_entry_read, lower_abort_read, lower_enable, lower_target = map(
        smc, (0xd51eff3f, 0xd51eff5f, 0xd51eff7f,
              0xd53eff22, 0xd53eff44, 0xd53eff66, 0xd51eff20, 0xd51eff4c))
    terminal = 0xd4000003 | (0xda22 << 5)
    ledger = a.out / 'fixture.ledger'
    ledger.write_bytes(b'DVEL' + struct.pack('<I', len(words)) + struct.pack('<' + str(len(words)) + 'I', *words))
    names = ['enter', 'return-sp0', 'return-sp1', 'nested', 'disabled', 'outside',
             'entry-noexec', 'stale-el-read', 'stale-el-write',
             'stale-gl-read', 'stale-gl-write', 'protect-a', 'protect-b',
             'protect-return', 'protect-normal', 'protect-invalid',
             'lock-gl', 'unlock-gl', 'guard-elr-before', 'guard-tpidr-before',
             'guard-vhe-before', 'guard-elr-after', 'guard-elr-read',
             'icache-el', 'icache-gl', 'icache-code',
             'pan-clear', 'pan-clear-denied', 'pan-set',
             'pmu-gl2', 'pmu-gl12', 'pmu-vhe', 'pmu-normal',
             'pmu-invalid', 'pmu-enable', 'pmu-frozen',
             'vmsa-set', 'vmsa-normal', 'vmsa-clear', 'vmsa-vbar-free',
             'vmsa-vbar-locked', 'vmsa-vbar-vhe', 'vmsa-ttbr1', 'vmsa-mmu',
             'lower-zero', 'lower-enable', 'lower-target']
    names += [f'enter-imm-{n}' for n in range(16)]
    names += [f'enter-wide-{n}' for n in (16, 31)]
    names += [f'enter-raw-{n}' for n in (0, 15, 16, 31)]
    if not a.control_only:
        names += [f'perm-{n}-{access}' for n in range(16) for access in ('read', 'write', 'exec')]
    permissions = (0, 0, 0, 0, 5, 5, 5, 5, 1, 1, 1, 1, 3, 3, 3, 3)
    report = {'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
              'sptm_sha256': hashlib.sha256(a.sptm.read_bytes()).hexdigest(),
              'runs': []}
    for name in names:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        cmd = [str(a.qemu.resolve()), '-M', 'darwin', '-cpu', 'host',
               '-accel', 'hvf,ipa-bits=40,kernel-irqchip=off', '-m', '8G',
               '-display', 'none', '-serial', 'none', '-monitor', 'none',
               '-dtree', str(a.dtree.resolve()), '-S', '-gdb', f'tcp:127.0.0.1:{port}',
               '-sptm', str(a.sptm.resolve())]
        for option, f in (('-bootkc', 'bootkc'), ('-txm', 'txm'),
                          ('-tc', 'ramdisk.tc'), ('-ramdisk', 'ramdisk.dmg')):
            cmd += [option, str(ROOT / 'firmware' / f)]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('QEMU_HVF_', 'DARWIN_', 'GXFSTAT_'))}
        env.update(QEMU_HVF_VIRTUAL_EL2='1', QEMU_HVF_VIRTUAL_SHADOW='1',
                   QEMU_HVF_VIRTUAL_LEDGER=str(ledger.resolve()),
                   # Denied accesses stop here rather than being delivered as
                   # guest aborts; the matrix checks the walk's decision.
                   QEMU_HVF_VIRTUAL_STOP_ON_FAULT='1')
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
                assert remote is not None
                remote.sock.settimeout(5)
                remote.command('?')
                assert remote.command(f'Z1,{pc:x},4') == 'OK'
                remote.send('c')
                remote.receive()
                before = registers(remote, ('SPSR_GL', 'ASPSR_GL', 'ELR_GL', 'ESR_GL'))
                assert before['pc'] == pc and before['SPRR_CONFIG_EL2'] == 0
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                def write(pa, content):
                    for off in range(0, len(content), 512):
                        block = content[off:off + 512]
                        assert remote.command(f'M{pa + off:x},{len(block):x}:' + block.hex()) == 'OK'
                    assert read_phys(remote, pa, len(content)) == content
                def instructions(pa, payload):
                    write(pa, struct.pack('<' + str(len(payload)) + 'I', *payload))
                access, nibble = 'read', 15
                if name.startswith('perm-'):
                    _, n, access = name.split('-')
                    nibble = int(n)
                elif name.startswith('stale-el'):
                    nibble, access = 3, name.rsplit('-', 1)[1]
                elif name.startswith('stale-gl'):
                    nibble, access = 12, name.rsplit('-', 1)[1]
                elif name == 'entry-noexec':
                    nibble = 8
                elif name == 'pan-clear-denied':
                    nibble = 3
                elif name.startswith('enter-raw-'):
                    nibble, access = 7, 'exec'
                bank = (before['SPRR_PPERM_EL2'] & ~0xf0) | (nibble << 4)
                # This fixture's target uses SPRR index 1; verify the PTE.
                table = before['TTBR1_EL2'] & 0x0000ffffffffc000
                for shift in (36, 25, 14):
                    idx = (target >> shift) & (7 if shift == 36 else 0x7ff)
                    desc = int.from_bytes(read_phys(remote, table + idx * 8, 8), 'little')
                    assert desc & 3 == 3
                    table = desc & 0x0000ffffffffc000
                assert ((desc >> 53) & 3) | (((desc >> 6) & 3) << 2) == 1
                target_bytes = bytearray(struct.pack('<I', 0xd503201f) * (0x4000 // 4))
                target_bytes[:8] = struct.pack('<2I', 0xd280b4a2, terminal)
                if name.startswith('enter-raw-'):
                    target_bytes[:4] = struct.pack('<I', 0x00201420 | int(name.rsplit('-', 1)[1]))
                write(target_pa, target_bytes)
                first_value = int.from_bytes(target_bytes[:8], 'little')
                access_word = {'read': 0xf9400062, 'write': 0xf9000062, 'exec': 0xd61f0060}[access]
                caller = [config, pperm, mask, lock, gxf, entry_write]
                if name.startswith('stale-el'):
                    caller += [0xf9400064]  # Prime a native alias in EL.
                enter_pc = pc + len(caller) * 4
                caller += [enter, terminal]
                guarded = [terminal]
                expected_pc, expected_g, wrote, x2 = entry, 1, False, marker
                expected_bank, expected_config = bank, 0xfb
                expected_esr = 0xfe010000
                protected_bank = (0x2020a52a302abae6 if name in ('protect-b', 'lock-gl', 'unlock-gl')
                                  else 0x2020a52a302acae6)
                initial_pstate = 0xa00003c8 | (name == 'return-sp1')
                if name.startswith('enter-imm-'):
                    immediate = int(name.rsplit('-', 1)[1])
                    caller[-2] = enters[immediate]
                    guarded = [esr_read, terminal]
                    expected_pc, x2 = entry + 4, 0xfe010000 | immediate
                    expected_esr = x2
                elif name.startswith('enter-wide-'):
                    caller[-2] = enters[int(name.rsplit('-', 1)[1])]
                    expected_pc, expected_g = enter_pc, 0
                elif name.startswith('enter-raw-'):
                    guarded = [0xd61f0060]
                    expected_pc = target
                elif name.startswith('perm-'):
                    guarded = [access_word, terminal]
                    allowed = bool(permissions[nibble] & {'read': 1, 'write': 2, 'exec': 4}[access])
                    expected_pc = (target + 4 if allowed else target) if access == 'exec' else entry + (4 if allowed else 0)
                    wrote = access == 'write' and allowed
                    x2 = (0x5a5 if access == 'exec' else first_value) if allowed and access != 'write' else marker
                elif name.startswith('return-'):
                    # Observe incoming GL SP, change it, then return to EL SP.
                    guarded = [0x910003eb, 0x9100019f, leave]
                    expected_pc, expected_g = enter_pc + 4, 0
                elif name == 'nested':
                    guarded = [enter, terminal]
                elif name in ('disabled', 'outside'):
                    caller = [config, pperm, mask, lock, enter if name == 'disabled' else leave, terminal]
                    expected_pc, expected_g = pc + 16, 0
                elif name == 'entry-noexec':
                    expected_pc = target
                elif name.startswith('stale-el'):
                    guarded = [access_word, terminal]
                elif name.startswith('stale-gl'):
                    guarded = [0xf9000062, leave]  # Prime writable GL alias.
                    caller[-1:] = [access_word, terminal]
                    expected_pc, expected_g, wrote = enter_pc + 4, 0, True
                elif name in ('protect-a', 'protect-b', 'protect-return'):
                    guarded = [protect, leave if name == 'protect-return' else terminal]
                    expected_bank = protected_bank
                    expected_pc = enter_pc + 4 if name == 'protect-return' else entry + 4
                    expected_g = 0 if name == 'protect-return' else 1
                elif name == 'protect-normal':
                    caller = caller[:6] + [protect, enter, terminal]
                    expected_pc, expected_g = pc + 24, 0
                elif name == 'protect-invalid':
                    guarded = [illegal, terminal]
                elif name in ('lock-gl', 'unlock-gl'):
                    guarded = [protect, seal, illegal if name == 'lock-gl' else lock, terminal]
                    expected_pc, expected_bank, expected_config = entry + 8, protected_bank, 0xff
                elif name in ('guard-elr-before', 'guard-tpidr-before', 'guard-vhe-before'):
                    read_word = {'guard-elr-before': elr_read, 'guard-tpidr-before': tpidr_read,
                                 'guard-vhe-before': vhe_read}[name]
                    caller = caller[:6] + [read_word, enter, terminal]
                    expected_pc, expected_g = pc + 24, 0
                elif name == 'guard-elr-after':
                    guarded = [leave]
                    caller[-1:] = [elr_read, terminal]
                    expected_pc, expected_g = enter_pc + 4, 0
                elif name == 'guard-elr-read':
                    guarded = [elr_read, terminal]
                    expected_pc, x2 = entry + 4, enter_pc + 4
                elif name == 'icache-el':
                    caller = caller[:6] + [icache, terminal]
                    expected_pc, expected_g = pc + 28, 0
                elif name == 'icache-gl':
                    guarded = [icache, terminal]
                    expected_pc = entry + 4
                elif name == 'icache-code':
                    # Execute, replace through the checked STR64 path, then
                    # maintain the cache and execute the new instruction.
                    # Use the real pre-SPRR writable/executable mapping;
                    # guarded permissions never grant simultaneous W+X.
                    caller = [0x94000061, 0xf9000230, icache,
                              0x9400005e, terminal]
                    instructions(entry_pa + 0x84, [0xd2802462, 0xd65f03c0])
                    expected_pc, x2, expected_g = pc + 16, 0x456, 0
                    expected_bank, expected_config = before['SPRR_PPERM_EL2'], 0
                elif name.startswith('pan-'):
                    guarded = [pan_set if name == 'pan-set' else pan_clear,
                               0xf9400062 if name == 'pan-clear-denied' else terminal]
                    # PAN set is an ordinary PSTATE immediate now (mirrored to
                    # the physical PSTATE); both forms complete.
                    expected_pc = entry + 4
                elif name in ('pmu-gl2', 'pmu-gl12', 'pmu-vhe'):
                    write_word = {'pmu-gl2': pmu2, 'pmu-gl12': pmu12, 'pmu-vhe': pmuvhe}[name]
                    read_other = pmu2_read4 if name == 'pmu-gl12' else pmu12_read4
                    guarded = [write_word, pmu12_read if name == 'pmu-gl12' else pmu2_read,
                               read_other, terminal]
                    expected_pc, x2 = entry + 12, 0x3030000ffff00
                elif name == 'pmu-normal':
                    caller = caller[:6] + [pmu2, enter, terminal]
                    expected_pc, expected_g = pc + 24, 0
                elif name in ('pmu-invalid', 'pmu-enable'):
                    guarded = [pmu2 if name == 'pmu-invalid' else pmu_enable, terminal]
                    if name == 'pmu-enable':
                        # PMCR0 activation is stored (the kernel enables its
                        # cycle counters); active PMC reads then follow the
                        # virtual clock.
                        expected_pc = entry + 4
                elif name == 'pmu-frozen':
                    guarded = [pmu2, pmc0, pmc1, pmc0, pmc1, terminal]
                    expected_pc, x2 = entry + 20, 0
                elif name == 'vmsa-set':
                    guarded = [vmsa, vmsa_read, terminal]
                    expected_pc, x2 = entry + 8, 0x8000000000000010
                elif name == 'vmsa-normal':
                    caller = caller[:6] + [vmsa, enter, terminal]
                    expected_pc, expected_g = pc + 24, 0
                elif name.startswith('vmsa-'):
                    test_word = {'vmsa-clear': vmsa_clear, 'vmsa-vbar-free': vbar,
                                 'vmsa-vbar-locked': vbar, 'vmsa-vbar-vhe': vbar_vhe,
                                 'vmsa-ttbr1': ttbr1, 'vmsa-mmu': sctlr_off}[name]
                    guarded = [vmsa, test_word, terminal]
                    expected_pc = entry + (8 if name == 'vmsa-vbar-free' else 4)
                elif name == 'lower-zero':
                    guarded = [lower_config, lower_entry, lower_abort,
                               lower_config_read, lower_entry_read, lower_abort_read, terminal]
                    expected_pc, x2 = entry + 24, 0
                elif name.startswith('lower-'):
                    guarded = [lower_enable if name == 'lower-enable' else lower_target, terminal]
                instructions(code_pa, caller)
                instructions(entry_pa, guarded)
                watched = (0x807024000, 0x807028000, 0x80702c000,
                           0x807110000, 0x807114000, 0x807068000,
                           0x807070000, 0x8165a8000, 0x8070a0000)
                hashes = {hex(p): hashlib.sha256(read_phys(remote, p, 0x4000)).hexdigest() for p in watched}
                replacement = struct.pack('<2I', 0xd2808ac2, 0xd65f03c0)
                if name == 'icache-code':
                    expected_code = bytearray(read_phys(remote, 0x8070a0000, 0x4000))
                    off = entry_pa + 0x84 - 0x8070a0000
                    expected_code[off:off + 8] = replacement
                    hashes[hex(0x8070a0000)] = hashlib.sha256(expected_code).hexdigest()
                assert remote.command('Qqemu.PhyMemMode:0') == 'OK'
                assert remote.command('P21=' + initial_pstate.to_bytes(4, 'little').hex()) == 'OK'
                for reg, val in ((0, 1), (2, marker), (3, target), (4, marker),
                                 (5, 0x40010), (6, 0xfb), (9, bank),
                                 (10, target if name == 'entry-noexec' else entry),
                                 (11, marker), (12, gl_stack), (13, protected_bank),
                                 (14, 0xff), (15, (protected_bank if name == 'lock-gl' else bank) ^ 4),
                                 (16, int.from_bytes(replacement, 'little')),
                                 (17, entry + 0x84),
                                 (18, (0x8000000000000010 | (name in ('vmsa-vbar-locked', 'vmsa-vbar-vhe')))
                                  if name.startswith('vmsa-') else 0x3030000ffff00 | (name == 'pmu-invalid')),
                                 (31, before['sp'])):
                    assert remote.command(f'P{reg:x}=' + val.to_bytes(8, 'little').hex()) == 'OK'
                assert remote.command(f'z1,{pc:x},4') == 'OK'
                remote.send('c')
                remote.receive()
                after = registers(remote, ('SP_', 'SPSR_GL', 'ASPSR_GL', 'ELR_GL', 'ESR_GL', 'PMCR', 'VMSA'))
                item.update(state={k: hex(v) for k, v in after.items()},
                            expected_pc=hex(expected_pc), expected_g=expected_g,
                            nibble=nibble, access=access, writes_completed=int(wrote))
                assert after['pc'] == expected_pc, hex(after['pc'])
                assert after['CURRENTG'] == expected_g
                assert after['x2'] == x2
                assert after['SPRR_PPERM_EL2'] == expected_bank
                assert after['SPRR_CONFIG_EL2'] == expected_config
                if name not in ('disabled', 'outside', 'protect-normal', 'icache-el', 'icache-code', 'pmu-normal', 'vmsa-normal') and not name.endswith('-before') and not name.startswith('enter-wide-'):
                    assert after['GXF_CONFIG_EL2'] == 1
                    assert after['ELR_GL2'] == enter_pc + 4
                    assert after['SPSR_GL2'] == initial_pstate
                    assert after['ASPSR_GL2'] == 0 and after['ESR_GL2'] == expected_esr
                if name.startswith('enter-wide-'):
                    for reg in ('ELR_GL2', 'SPSR_GL2', 'ASPSR_GL2', 'ESR_GL2'):
                        assert after[reg] == before[reg]
                if name.startswith('enter-raw-'):
                    raw = 0x00201420 | int(name.rsplit('-', 1)[1])
                    assert f'unadapted instruction 0x{raw:08x}' in (a.out / f'{name}.stderr').read_text()
                if name.startswith('return-'):
                    assert after['pstate'] == initial_pstate
                    assert after['sp'] == before['sp']
                    assert after['x11'] == 0, hex(after['x11'])
                    assert after['SP_GL2'] == gl_stack
                if name.startswith('stale-el'):
                    assert after['x4'] == first_value
                if name.startswith('icache-'):
                    assert 'Virtual shadow completed native IC IALLU' in (a.out / f'{name}.stderr').read_text()
                if name.startswith('pan-'):
                    assert bool(after['pstate'] & (1 << 22)) == (name == 'pan-set')
                if name.startswith('pmu-'):
                    # pmu-enable stores the activation (0x1); every other
                    # case leaves the counters disabled.
                    assert after['PMCR0_EL1'] == (1 if name == 'pmu-enable' else 0)
                    assert after['PMCR1_GL2'] == (0x3030000ffff00 if name in ('pmu-gl2', 'pmu-vhe', 'pmu-frozen') else 0)
                    # GDB redirects its GL1 name to GL2 in this VHE context.
                    # The actual lower-bank MRS above checks bank separation.
                    assert after['PMCR1_GL1'] == after['PMCR1_GL2']
                    if name in ('pmu-frozen', 'pmu-gl2', 'pmu-gl12', 'pmu-vhe'):
                        assert after['x4'] == 0
                if name.startswith('vmsa-'):
                    expected_lock = 0 if name == 'vmsa-normal' else 0x8000000000000010 | (name in ('vmsa-vbar-locked', 'vmsa-vbar-vhe'))
                    assert after['VMSA_LOCK_EL2'] == expected_lock
                    assert after['VBAR_EL2'] == (gl_stack if name == 'vmsa-vbar-free' else before['VBAR_EL2'])
                    for reg in ('SCTLR_EL2', 'TTBR1_EL2', 'TTBR0_EL2', 'TCR_EL2'):
                        assert after[reg] == before[reg]
                    if name not in ('vmsa-set', 'vmsa-vbar-free'):
                        assert 'Virtual VMSA lock denied' in (a.out / f'{name}.stderr').read_text()
                if name == 'lower-zero':
                    assert after['x4'] == 0 and after['x6'] == 0
                assert remote.command('Qqemu.PhyMemMode:1') == 'OK'
                assert hashes == {hex(p): hashlib.sha256(read_phys(remote, p, 0x4000)).hexdigest() for p in watched}
                if wrote:
                    target_bytes[:8] = marker.to_bytes(8, 'little')
                assert read_phys(remote, target_pa, 0x4000) == target_bytes
                item['passed'] = True
            except Exception as exc:
                item.update(error=str(exc), traceback=traceback.format_exc())
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
        print(json.dumps({'name': name, 'passed': item['passed'], 'error': item.get('error')}), flush=True)
    return int(not all(r['passed'] for r in report['runs']))


if __name__ == '__main__':
    raise SystemExit(main())
