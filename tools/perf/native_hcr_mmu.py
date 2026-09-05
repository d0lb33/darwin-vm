#!/usr/bin/env python3
"""Exercise an HCR access helper across an MMU-on guest with RX/NX checks."""
import argparse
import json
from pathlib import Path
import subprocess

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--pre-access', type=lambda s: int(s, 0), default=0)
ap.add_argument('--cancel', action='store_true')
ap.add_argument('--shift', type=lambda s: int(s, 0), default=0, choices=(0, 0x200000))
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
source = Path(__file__).resolve().with_suffix('')
binary = a.out / 'native_hcr_mmu'
ent = a.out / 'entitlements.plist'
ent.write_text('<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>')
subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                *(['-DNATIVE_QEMU_MMU'] if a.cancel else []),
                f'-DMMU_SHIFT={a.shift}', str(source.with_suffix('.c')), str(source.with_suffix('.S')),
                '-framework', 'Hypervisor', '-o', str(binary)], check=True)
subprocess.run(['codesign', '-s', '-', '--entitlements', str(ent), str(binary)], check=True)
results = []
for mode in range(3):
    for protection in range(3):
        run = subprocess.run([str(binary), str(mode), str(protection), str(a.pre_access)], capture_output=True,
                             text=True, timeout=6)
        item = {'mode': mode, 'protection': protection, 'returncode': run.returncode,
                'stdout': run.stdout, 'stderr': run.stderr}
        if run.stdout:
            item['result'] = json.loads(run.stdout)
        results.append(item)
        (a.out / 'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(item), flush=True)
raise SystemExit(1 if any(r['returncode'] for r in results) else 0)
