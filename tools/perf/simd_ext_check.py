#!/usr/bin/env python3
"""Check EXT against byte-array semantics, including aliases and upper lanes.

Uses the existing EL0/MMU harness. No iOS image. --sve-bytes additionally checks
zeroing of SVE lanes above the AdvSIMD result with nonzero canaries.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import signal

from arm_island_bench import ROOT, assemble, run, terminate


def payload_source(vector_bytes):
    sve = vector_bytes > 16
    bootstrap = Path(__file__).with_name('arm_island.S').read_text().split('.org 0x100')[0]
    if sve:
        bootstrap = '.arch armv8-a+sve\n' + bootstrap.replace(
            'msr cpacr_el1, x15', 'orr x15, x15, #0x30000\n    msr cpacr_el1, x15\n'
            f'    mov x15, #{vector_bytes // 16 - 1}\n    msr zcr_el1, x15')
    lines = [bootstrap, '.org 0x100', 'ready:', 'isb', 'mrs x20, cntvct_el0',
             'mrs x21, cntfrq_el0', 'b tests', '.org 0x1000', 'done: b done',
             '.org 0x2000', 'tests:', 'adr x15, output']
    if sve:
        lines.append('ptrue p0.b')
    rng = random.Random(0x455854)
    seeds = [bytes(rng.randrange(1, 256) for _ in range(3 * vector_bytes)) for _ in range(8)]
    expected = bytearray()
    cases = []
    aliases = [(0, 1, 2), (1, 1, 2), (2, 1, 2), (0, 1, 1), (1, 1, 1)]
    for pattern, seed in enumerate(seeds):
        for width in (8, 16):
            for offset in range(width):
                for rd, rn, rm in aliases:
                    lines.append(f'adr x14, seed{pattern}')
                    for reg in range(3):
                        if sve:
                            lines += [f'ld1b {{z{reg}.b}}, p0/z, [x14]', f'add x14, x14, #{vector_bytes}']
                        else:
                            lines.append(f'ldr q{reg}, [x14], #16')
                    lines.append(f'ext v{rd}.{width}b, v{rn}.{width}b, v{rm}.{width}b, #{offset}')
                    for reg in range(3):
                        if sve:
                            lines += [f'st1b {{z{reg}.b}}, p0, [x15]', f'add x15, x15, #{vector_bytes}']
                        else:
                            lines.append(f'str q{reg}, [x15], #16')
                    regs = [seed[i * vector_bytes:(i + 1) * vector_bytes] for i in range(3)]
                    value = (regs[rn][:width] + regs[rm][:width])[offset:offset + width]
                    regs[rd] = value + bytes(vector_bytes - width)
                    expected.extend(b''.join(regs))
                    cases.append(dict(pattern=pattern, width=width, offset=offset, rd=rd, rn=rn, rm=rm))
    lines += ['isb', 'mrs x22, cntvct_el0', 'sub x22, x22, x20', 'mov x0, #0',
              'mov x9, #0x600d', 'b done', '.org 0x10000', 'table:', '.quad 0',
              '.quad 0x40000701', '.quad 0x40000741', '.org 0x20000']
    for i, seed in enumerate(seeds):
        lines += [f'seed{i}:', '.byte ' + ','.join(str(b) for b in seed)]
    lines += ['.org 0x30000', 'output:', f'.space {len(expected)}']
    return '\n'.join(lines) + '\n', expected, cases


def fp_trap_source():
    bootstrap = Path(__file__).with_name('arm_island.S').read_text().split('.org 0x100')[0]
    bootstrap = bootstrap.replace('msr cpacr_el1, x15',
        'msr cpacr_el1, xzr\n    adr x15, vectors\n    msr vbar_el1, x15')
    return bootstrap + '''
.org 0x100
ready:
    isb
    mrs x20, cntvct_el0
    mrs x21, cntfrq_el0
    ext v0.16b, v1.16b, v2.16b, #3
    b .
.org 0x1000
done: b done
.org 0x1400
handler:
    mrs x9, esr_el1
    lsr x9, x9, #26
    mrs x10, elr_el1
    mrs x22, cntvct_el0
    sub x22, x22, x20
    mov x0, #0
    adr x15, done
    mov x16, #0x40000000
    add x15, x15, x16
    msr elr_el1, x15
    eret
.org 0x1800
vectors:
.rept 16
    b handler
    .space 124
.endr
.org 0x10000
table:
    .quad 0
    .quad 0x40000701
    .quad 0x40000741
'''


def main():
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, default=ROOT/'qemu-sptm/build-fast/qemu-system-aarch64')
    parser.add_argument('--accel', choices=['tcg', 'hvf'], default='tcg')
    parser.add_argument('--sve-bytes', type=int, choices=[16, 32, 64], default=16)
    parser.add_argument('--dump-code', action='store_true')
    parser.add_argument('--vector-ext', action='store_true', help='enable the experimental nonzero-offset vector lowering')
    parser.add_argument('--fp-trap', action='store_true', help='require EXT to trap when FP/SIMD access is disabled')
    args = parser.parse_args()
    if args.accel == 'hvf' and args.sve_bytes > 16:
        parser.error('SVE checks use TCG; this Mac does not expose SVE through HVF')
    args.out.mkdir(exist_ok=False)
    source, expected, cases = payload_source(args.sve_bytes)
    src = args.out/'ext_check.S'
    src.write_text(source)
    payload = args.out/'ext_check.bin'
    payload.write_bytes(assemble(args.out, src))

    def inspect(remote, result):
        if args.fp_trap:
            fault_pc = int.from_bytes(bytes.fromhex(remote.command('pa')), 'little')
            assert result['checksum'] == '0x7', result
            assert fault_pc == 0x8020010c, hex(fault_pc)
            result.update(verified_fp_access_trap=True, fault_pc=hex(fault_pc))
            return
        actual = bytearray()
        for off in range(0, len(expected), 1024):
            length = min(1024, len(expected) - off)
            actual.extend(bytes.fromhex(remote.command(f'm{0x80230000 + off:x},{length:x}')))
        if actual != expected:
            i = next(i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1])
            raise AssertionError(f'byte {i}, case {cases[i // (3 * args.sve_bytes)]}: '
                                 f'got {actual[i]:02x}, expected {expected[i]:02x}')
        result.update(verified_cases=len(cases), vector_bytes=args.sve_bytes,
                      checked_bytes=len(expected), output_sha256=hashlib.sha256(actual).hexdigest())

    if args.fp_trap:
        src.write_text(fp_trap_source())
        payload.write_bytes(assemble(args.out, src))
    extra = ['-d', 'out_asm', '-D', str(args.out/'host-code.log')] if args.dump_code else []
    cpu = 'host' if args.accel == 'hvf' else f'max,sve-max-vq={args.sve_bytes // 16}'
    result = run(args.qemu.resolve(), payload, 0, 1, args.accel, args.out/'check',
                 cpu=cpu, extra_args=extra, inspect=inspect,
                 extra_env={'QEMU_ARM_TCG_VECTOR_EXT': '1' if args.vector_ext else '0'})
    result['qemu_sha256'] = hashlib.sha256(args.qemu.read_bytes()).hexdigest()
    (args.out/'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
