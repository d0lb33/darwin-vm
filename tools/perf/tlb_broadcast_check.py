#!/usr/bin/env python3
"""Check synchronized executable remaps on six TCG CPUs, using TLBI ranges."""
import argparse
import hashlib
import json
import signal
from pathlib import Path
from arm_island_bench import ROOT, assemble, run, terminate

signal.signal(signal.SIGTERM, terminate)
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--qemu', type=Path, default=ROOT / 'qemu-sptm/build-fast/qemu-system-aarch64')
ap.add_argument('--negative-control', action='store_true',
                help='also verify that omitting TLBI makes the test fail')
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
results = []
for mapping, definitions in [('block', []), ('page', ['-DSMALL_PAGES'])]:
    code = assemble(a.out, Path(__file__).with_suffix('.S'), definitions)
    payload = a.out / f'{mapping}.bin'
    payload.write_bytes(code)
    for label, operand in [('2pages', 0), ('64pages', 31 << 39),
                           ('2048pages', (31 << 39) | (1 << 44)),
                           ('65536pages', (31 << 39) | (2 << 44))]:
        def inspect(remote, result):
            slots = bytes.fromhex(remote.command('m40214000,180'))
            acknowledgments = [int.from_bytes(slots[i * 64:i * 64 + 8], 'little')
                               for i in range(6)]
            assert acknowledgments == [1000] * 6, acknowledgments
            result['acknowledgments'] = acknowledgments
        r = run(a.qemu.resolve(), payload, operand, 1000, 'tcg',
                a.out / f'{mapping}_{label}', extra_args=['-smp', '6'], inspect=inspect)
        assert int(r['checksum'], 16) == 9000, r
        r['payload_sha256'] = hashlib.sha256(code).hexdigest()
        r['mapping'] = mapping
        r['range'] = label
        results.append(r)
        print(f'PASS {mapping}/{label}: 1000 remaps acknowledged by all six CPUs; '
              f'{r["seconds"]:.6f}s', flush=True)
negative_control = None
if a.negative_control:
    code = assemble(a.out, Path(__file__).with_suffix('.S'),
                    ['-DSMALL_PAGES', '-DOMIT_TLBI'])
    payload = a.out / 'no_tlbi.bin'
    payload.write_bytes(code)
    try:
        run(a.qemu.resolve(), payload, 0, 1000, 'tcg', a.out / 'no_tlbi',
            extra_args=['-smp', '6'])
    except TimeoutError:
        negative_control = 'failed to complete without TLBI, as expected'
        print('PASS negative control: stale executable mappings prevent completion')
    else:
        raise AssertionError('test completed without TLBI; invalid negative control')
(a.out / 'results.json').write_text(json.dumps({
    'qemu_sha256': hashlib.sha256(a.qemu.read_bytes()).hexdigest(),
    'negative_control': negative_control, 'runs': results}, indent=2))
