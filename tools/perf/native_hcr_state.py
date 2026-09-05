#!/usr/bin/env python3
"""Run bounded, direct-HVF controls for EL2 state preservation across exits."""
import argparse
import json
from pathlib import Path
import subprocess

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
source = Path(__file__).resolve().with_suffix('')
binary = a.out / 'native_hcr_state'
ent = a.out / 'entitlements.plist'
ent.write_text('<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>')
subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                str(source.with_suffix('.c')), str(source.with_suffix('.S')),
                '-framework', 'Hypervisor', '-o', str(binary)], check=True)
subprocess.run(['codesign', '-s', '-', '--entitlements', str(ent), str(binary)], check=True)
results = []
failed = False
for hcr in ('0x80000000', '0x480000000', '0x488000000'):
    for kind in range(2):
        for mode in range(9):
            if not kind and mode == 3:
                continue
            run = subprocess.run([str(binary), str(kind), str(mode), hcr], capture_output=True,
                                 text=True, timeout=6)
            item = {'hcr': hcr, 'exit_kind': kind, 'mode': mode, 'returncode': run.returncode,
                    'stdout': run.stdout, 'stderr': run.stderr}
            if run.returncode == 0:
                item['result'] = json.loads(run.stdout)
                result = item['result']
                regs = result['registers']
                item['preserved'] = all(r['before'] == r['after'] for r in regs)
                item['valid_exits'] = (result['first_ec'] == (23 if kind == 0 else 36)
                                       and result['second_ec'] == 23)
                if mode in (0, 3, 6, 7, 8):
                    item['control_passed'] = item['preserved'] and item['valid_exits']
                    if mode in (7, 8):
                        item['control_passed'] &= regs[0]['api'] == regs[0]['before']
                    failed |= not item['control_passed']
                else:
                    # Accessor modes characterize the API; do not require a
                    # framework defect to exist on every host/OS release.
                    item['api_hcr_changed_guest'] = regs[0]['before'] != regs[0]['after']
                    failed |= not item['valid_exits']
                    failed |= any(r['before'] != r['after'] for r in regs[1:])
            else:
                failed = True
            results.append(item)
            (a.out / 'results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(item), flush=True)
raise SystemExit(1 if failed else 0)
