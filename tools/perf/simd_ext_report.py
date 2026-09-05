#!/usr/bin/env python3
"""Preserve compact SIMD EXT experiment evidence outside disposable logs."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--ios', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    evidence = {}

    def read(path):
        data = path.read_bytes()
        evidence[str(path)] = hashlib.sha256(data).hexdigest()
        return json.loads(data)

    report = {'host': 'Apple M5 Max, macOS 27.0 26A5421a, 18 CPUs, 128 GiB',
              'base_commits': {'parent': '3a6f4b4', 'qemu': 'a122a9d'},
              'candidate_option': 'QEMU_ARM_TCG_VECTOR_EXT=1',
              'comparisons': {}, 'checks': {}, 'evidence_sha256': evidence}
    keep = ('workload', 'build', 'repeat', 'seconds', 'host_seconds',
            'checksum', 'vector_ext_option')
    for mode in ('default', 'vector'):
        result = read(args.artifacts/f'final_compare_{mode}/results.json')
        report['comparisons'][mode] = {key: result[key] for key in
            ('iterations', 'build_sha256', 'payload_sha256', 'medians')}
        report['comparisons'][mode]['runs'] = [
            {key: row[key] for key in keep} for row in result['runs']]
    for name in ('final_default16', 'final_default64', 'final_vector16',
                 'final_vector32', 'final_vector64', 'final_fp',
                 'check_baseline16', 'check_baseline64_retry', 'check_hvf16'):
        result = read(args.artifacts/name/'results.json')
        report['checks'][name] = {key: val for key, val in result.items() if key != 'command'}
    report['ios_summary'] = read(args.ios/'summary.json')
    report['ios_runs'] = read(args.ios/'results.json')
    for row in report['ios_runs']:
        path = Path(row['results'])
        result = read(path)['runs'][0]
        row.update(early_boot_seconds=result['early_boot_seconds'],
                   user_update_count=result['user_update_count'],
                   stop_reason=result['stop_reason'], host_load=result['host_load'])
        serial = path.parent/'0_pv6.serial.log'
        row['xnu_panics'] = serial.read_bytes().count(b'panic(cpu')
        evidence[str(serial)] = hashlib.sha256(serial.read_bytes()).hexdigest()
        assert row['user_update_count'] == 100 and row['xnu_panics'] == 0
    report['limitations'] = [
        'The independent display VM and ordinary host load were not controlled.',
        'Migration stopped at a metadata milestone; full boot/setup time is not measured.',
        'Synthetic EXT-only loops do not establish whole-VM speedups.',
        'Windows was not built or executed in this session.',
    ]
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(args.out)


if __name__ == '__main__':
    main()
