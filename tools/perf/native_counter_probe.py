#!/usr/bin/env python3
"""Read native HVF counter capabilities without firmware or timer emulation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).resolve().parent
    binary = a.out / 'native_counter_probe'
    entitlements = a.out / 'entitlements.plist'
    entitlements.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>
''')
    subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-mmacosx-version-min=15.0', str(source / 'native_el2_probe.c'),
                    str(source / 'native_counter_probe.S'), '-framework', 'Hypervisor',
                    '-o', str(binary)], check=True)
    subprocess.run(['codesign', '-s', '-', '--entitlements', str(entitlements),
                    str(binary)], check=True)
    report = {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'sources': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                          for name in ('native_el2_probe.c', 'native_counter_probe.S')},
              'runs': []}
    names = ['ordinary', 'redirect-read', 'redirect-write', 'apple-counter',
             'counter-ss', 'frequency', 'virtual-counter', 'physical-counter']
    for el in (1, 2):
        for case, name in enumerate(names):
            cmd = [str(binary), str(el), str(case), '1']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
            item = {'command': cmd, 'case_name': name, 'returncode': result.returncode,
                    'stderr': result.stderr}
            if result.returncode == 0:
                item.update(json.loads(result.stdout))
                item['native_operation_completed'] = (item['guest_exceptions'] == 0 and
                                                       item['done'] == 0x600d and
                                                       item['exit_reason'] == 1 and
                                                       item['exit_ec'] == 0x17)
            else:
                item['stdout'] = result.stdout
            report['runs'].append(item)
            (a.out / 'results.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(item), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
