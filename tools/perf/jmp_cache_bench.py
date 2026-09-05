#!/usr/bin/env python3
"""Checksummed EL0 indirect calls over small/large executable working sets.

This isolates dispatch-cache cost without iOS boot, devices, or TLB shootdowns.
It is a diagnostic workload, not a gaming or migration speed estimate.
"""
import argparse
import json
from pathlib import Path
import statistics
from arm_island_bench import ROOT, assemble, run

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--baseline', type=Path, required=True)
ap.add_argument('--candidate', type=Path, required=True)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--repeat', type=int, default=5)
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
bootstrap = (Path(__file__).with_name('arm_island.S')).read_text().split('.org 0x100\n')[0]
source = bootstrap + '''.org 0x100
ready:
    mrs x20, cntvct_el0
    mrs x21, cntfrq_el0
    mov x9, #0x1234
    mov x12, #0
    sub x1, x1, #1
    adr x26, functions
loop:
    add x12, x12, #97
    and x12, x12, x1
    add x25, x26, x12, lsl #4
    blr x25
    subs x0, x0, #1
    b.ne loop
    isb
    mrs x22, cntvct_el0
    sub x22, x22, x20
    b done
.org 0x1000
done:
    b done
.org 0x10000
table:
    .quad 0
    .quad 0x40000701
    .quad 0x40000741
.org 0x20000
functions:
.rept 8192
    add x9, x9, x12
    ror x9, x9, #7
    ret
    nop
.endr
'''
src = a.out / 'bench.S'; src.write_text(source)
payload = a.out / 'bench.bin'; payload.write_bytes(assemble(a.out, src))
rows = []
for rep in range(a.repeat):
    for size in (32, 512, 8192):
        pair = []
        order = [('old', a.baseline), ('new', a.candidate)]
        if rep % 2:
            order.reverse()
        for name, qemu in order:
            r = run(qemu.resolve(), payload, size, 1000000, 'tcg', a.out / f'{rep}_{size}_{name}', extra_env={'QEMU_TCG_JMP_PROFILE': '0'})
            r.update(build=name, functions=size, repetition=rep)
            rows.append(r); pair.append(r)
            print(rep, size, name, r['seconds'], flush=True)
        assert pair[0]['checksum'] == pair[1]['checksum'], pair
medians = {}
for size in (32, 512, 8192):
    med = {name: statistics.median(r['seconds'] for r in rows if r['functions'] == size and r['build'] == name) for name in ('old', 'new')}
    med['speedup'] = med['old'] / med['new']; medians[size] = med
(a.out / 'results.json').write_text(json.dumps({'runs': rows, 'medians': medians}, indent=2) + '\n')
print(json.dumps(medians, indent=2))
