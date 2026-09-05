#!/usr/bin/env python3
"""Alternate preserved baseline/candidate builds on identical EL0 SIMD loops."""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import statistics

from arm_island_bench import assemble, run, terminate


def main():
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--iterations', type=int, default=10000000)
    parser.add_argument('--dump-code', action='store_true')
    parser.add_argument('--candidate-vector-ext', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20 or not 1 <= args.iterations <= 100000000:
        parser.error('invalid repetition or iteration count')
    args.out.mkdir(exist_ok=False)
    builds = {'baseline': args.baseline.resolve(), 'candidate': args.candidate.resolve()}
    original = Path(__file__).with_name('arm_island.S').read_text()
    programs = {'mixed_neon': original}
    # Dependent EXT chains exercise both widths and each half of the 128-bit
    # inputs. Zero and eight-byte offsets are simpler scalar-path controls.
    for width, offset in [(8, 0), (8, 3), (16, 0), (16, 3), (16, 8), (16, 11)]:
        body = '\n'.join(f'    ext v{i % 2}.{width}b, v{i % 2}.{width}b, v{1 - i % 2}.{width}b, #{offset}' for i in range(8))
        prefix, rest = original.split('neon_loop:\n')
        _, suffix = rest.split('    subs x0, x0, #1', 1)
        programs[f'ext_{width}b_{offset}'] = prefix + 'neon_loop:\n' + body + '\n    subs x0, x0, #1' + suffix
    payloads = {}
    for label, source in programs.items():
        folder = args.out/label
        folder.mkdir()
        src = folder/'code.S'
        src.write_text(source)
        payloads[label] = folder/'code.bin'
        payloads[label].write_bytes(assemble(folder, src))
    report = {'iterations': args.iterations, 'build_sha256': {
        label: hashlib.sha256(path.read_bytes()).hexdigest() for label, path in builds.items()},
        'payload_sha256': {label: hashlib.sha256(path.read_bytes()).hexdigest() for label, path in payloads.items()},
        'runs': []}
    for repeat in range(args.repeat):
        for label, payload in payloads.items():
            pair = []
            for build in (list(builds) if repeat % 2 == 0 else list(reversed(builds))):
                row = run(builds[build], payload, 2, args.iterations, 'tcg',
                          args.out/label/f'{repeat}_{build}', extra_args=(
                              ['-d', 'op,op_opt,out_asm', '-D', str(args.out/label/f'{repeat}_{build}.code.log')]
                              if args.dump_code else []),
                          extra_env={'QEMU_ARM_TCG_VECTOR_EXT': '1' if build == 'candidate' and args.candidate_vector_ext else '0'})
                row.update(build=build, workload=label, repeat=repeat)
                report['runs'].append(row)
                pair.append(row)
                print(f'{repeat} {label} {build}: {row["seconds"]:.6f}s', flush=True)
            assert pair[0]['checksum'] == pair[1]['checksum'], label
            (args.out/'results.json').write_text(json.dumps(report, indent=2))
    report['medians'] = {}
    for label in payloads:
        med = {build: statistics.median(row['seconds'] for row in report['runs']
               if row['build'] == build and row['workload'] == label) for build in builds}
        med['speedup'] = med['baseline'] / med['candidate']
        report['medians'][label] = med
    (args.out/'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report['medians'], indent=2))


if __name__ == '__main__':
    main()
