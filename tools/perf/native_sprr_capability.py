#!/usr/bin/env python3
"""Observe native HVF PMPRR/configuration accesses without firmware or emulation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--vmapple-entitlement', action='store_true',
                    help='Observe whether an ad-hoc private vmapple entitlement is accepted')
    a = ap.parse_args()
    a.out.mkdir(exist_ok=False)
    source = Path(__file__).resolve().parent
    binary = a.out / 'native_sprr_capability'
    entitlements = a.out / 'entitlements.plist'
    private_key = ('<key>com.apple.private.hypervisor.vmapple</key><true/>'
                   if a.vmapple_entitlement else '')
    entitlements.write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                           '<plist version="1.0"><dict>'
                           '<key>com.apple.security.hypervisor</key><true/>' +
                           private_key + '</dict></plist>\n')
    subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-mmacosx-version-min=15.0', str(source / 'native_el2_probe.c'),
                    str(source / 'native_sprr_capability.S'), '-framework', 'Hypervisor',
                    '-o', str(binary)], check=True)
    subprocess.run(['codesign', '-s', '-', '--entitlements', str(entitlements),
                    str(binary)], check=True)
    report = {'vmapple_entitlement_requested': a.vmapple_entitlement,
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'sources': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                          for name in ('native_el2_probe.c', 'native_sprr_capability.S')},
              'runs': []}
    names = ['pmprr-el1-read', 'pmprr-el1-write-read', 'pmprr-el2-read',
             'pmprr-el2-write-read', 'config-el1-write-read', 'config-el2-write-read',
             'pmprr-el12-read', 'current-el-control']
    for el in (1, 2):
        # A harmless native control runs first, so a signature rejection does
        # not get misreported as evidence about a protected register.
        for case in (7, 0, 1, 2, 3, 4, 5, 6):
            name = names[case]
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
            if result.returncode:
                return 1
            if case == 7:
                assert item.get('native_operation_completed') and item['register_value'] == 4 * el
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
