#!/usr/bin/env python3
"""Verify SPRR leaf policy using native HVF accesses, aliases and revocation."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import subprocess

# Independent expected access sets: nibble -> EL and guarded permissions.
EL = ('', 'rx', 'r', 'rw', '', 'rx', 'r', '', '', 'x', 'r', 'rw', '', 'rx', 'r', 'rw')
GL = ('', '', '', '', 'rx', 'rx', 'rx', 'rx', 'r', 'r', 'r', 'r', 'rw', 'rw', 'rw', 'rw')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--el1-control', action='store_true')
    ap.add_argument('--high-va', action='store_true')
    a = ap.parse_args()
    if a.high_va and not a.el1_control:
        ap.error('--high-va requires --el1-control; native EL2 VHE is not supported')
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).resolve().with_suffix('')
    root = source.parents[2]
    inputs = [source.with_suffix('.c'), source.with_suffix('.S'),
              root / 'qemu-sptm/target/arm/apple-sprr.h']
    binary, ent = a.out / 'probe', a.out / 'entitlements.plist'
    ent.write_text('<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>')
    definitions = ['-DNATIVE_SPRR_EL1'] if a.el1_control else []
    if a.high_va:
        definitions.append('-DSPRR_VA=0xffffff8080000000ull')
    subprocess.run(['clang', *definitions, '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                    '-I', str(inputs[2].parent), str(inputs[0]), str(inputs[1]),
                    '-framework', 'Hypervisor', '-o', str(binary)], check=True)
    subprocess.run(['codesign', '-s', '-', '--entitlements', str(ent), str(binary)], check=True)
    report = {'el1_control': a.el1_control, 'high_va': a.high_va, 'sources': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in inputs},
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'cases': []}
    for pperm, index, guarded, op in itertools.product(
            (0xfedcba9876543210, 0x2020a52a302abaf5), range(16), range(2), range(3)):
        cmd = [str(binary.resolve()), hex(pperm), str(index), str(guarded), str(op)]
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
        item = {'command': cmd, 'returncode': run.returncode, 'stderr': run.stderr}
        try:
            item.update(json.loads(run.stdout))
            rights = (GL if guarded else EL)[(pperm >> (4 * index)) & 15]
            expected = sum(1 << bit for bit, char in enumerate('rwx') if char in rights)
            # First/op, other alias R/W/X, first/R, primed/op, revoked/op, restored/op.
            denied = 2 + 3 * ('rwx'[op] not in rights) + ('r' not in rights)
            item['verified'] = (run.returncode == 0 and item['passed'] and
                                item['permissions'] == expected and item['accesses'] == 8 and
                                item['denied'] == denied)
        except (ValueError, KeyError):
            item.update(stdout=run.stdout, verified=False)
        report['cases'].append(item)
        (a.out / 'results.json').write_text(json.dumps(report, indent=2))
        if not item['verified']:
            print(json.dumps(item), flush=True)
    passed = sum(item['verified'] for item in report['cases'])
    print(f'{passed}/{len(report["cases"])} cases passed; '
          f'{sum(item.get("accesses", 0) for item in report["cases"])} native accesses')
    return int(passed != len(report['cases']))


if __name__ == '__main__':
    raise SystemExit(main())
