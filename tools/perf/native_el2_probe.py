#!/usr/bin/env python3
"""Independent Apple EL2 execution probe. Other hosts explicitly retain TCG."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--out', type=Path, required=True)
ap.add_argument('--high-ipa', action='store_true', help='Also verify the Darwin RAM address and a too-small IPA negative control')
ap.add_argument('--hcr-roundtrip', action='store_true', help='Compare resume with no HCR write, API readback write, and guest value write')
a = ap.parse_args()
a.out.mkdir(exist_ok=False)
report = {'host': platform.platform(), 'production_accelerator': 'tcg',
          'native_ios_validated': False, 'runs': []}
if platform.system() != 'Darwin' or platform.machine() != 'arm64':
    report['eligibility'] = 'TCG fallback: host is not Apple Silicon macOS'
elif int(platform.mac_ver()[0].split('.')[0]) < 15:
    report['eligibility'] = 'TCG fallback: macOS 15 or newer required for guest EL2'
else:
    source = Path(__file__).resolve().with_suffix('')
    binary = a.out / 'native_el2_probe'
    entitlements = a.out / 'entitlements.plist'
    entitlements.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>
''')
    subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-mmacosx-version-min=15.0',
                    str(source.with_suffix('.c')), str(source.with_suffix('.S')),
                    '-framework', 'Hypervisor', '-o', str(binary)], check=True)
    subprocess.run(['codesign', '-s', '-', '--entitlements', str(entitlements),
                    str(binary)], check=True)
    report['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    for el in (1, 2):
        for case in range(8 if el == 2 else 4):
            count = 1000000 if case == 0 else 10000 if case == 4 else 1
            run = subprocess.run([str(binary), str(el), str(case), str(count)],
                                 text=True, capture_output=True, timeout=6)
            if run.returncode == 77:
                report['eligibility'] = 'TCG fallback: guest EL2 unavailable'
                break
            if run.returncode:
                raise RuntimeError(f'EL{el} case {case}: {run.returncode}: {run.stderr}')
            result = json.loads(run.stdout)
            if case == 0:
                assert result['done'] == 0x600d and result['checksum'] == 3 * count
                assert result['current_el'] == 4 * el and result['guest_exceptions'] == 0
            if case == 4:
                assert result['done'] == 0x600d and result['checksum'] == count * (count + 1) // 2
                assert result['guest_exceptions'] == 2 * count
            report['runs'].append(result)
            print(json.dumps(result), flush=True)
            (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    if a.hcr_roundtrip and 'eligibility' not in report:
        report['hcr_roundtrip'] = []
        for mode in range(3):
            run = subprocess.run([str(binary), '2', '5', '1', str(mode)],
                                 text=True, capture_output=True, timeout=6, check=True)
            result = [json.loads(line) for line in run.stdout.splitlines()]
            report['hcr_roundtrip'].append(result)
            print(json.dumps(result[-1]), flush=True)
            (a.out / 'results.json').write_text(json.dumps(report, indent=2))
    if a.high_ipa and 'eligibility' not in report:
        report['high_ipa'] = []
        for bits, address in ((36, '0x10000000000'), (42, '0x10000000000'),
                              (36, '0x800000000'), (40, '0x800000000')):
            run = subprocess.run([str(binary), '2', '0', '1000000', str(bits), address],
                                 text=True, capture_output=True, timeout=6)
            item = {'ipa_bits': bits, 'address': address, 'returncode': run.returncode,
                    'stdout': run.stdout, 'stderr': run.stderr}
            report['high_ipa'].append(item)
            (a.out / 'results.json').write_text(json.dumps(report, indent=2))
            if bits == 36 and address == '0x10000000000':
                assert run.returncode == 1 and 'hv_vm_map' in run.stderr, item
            elif bits == 42 and run.returncode:
                assert run.returncode == 1 and 'hv_vm_config_set_ipa_size' in run.stderr, item
                item['outcome'] = 'Requested IPA size unavailable; a lower guest RAM base is required'
            else:
                assert run.returncode == 0, item
                value = json.loads(run.stdout)
                assert value['checksum'] == 3000000 and value['current_el'] == 8, item
            print(json.dumps(item), flush=True)
(a.out / 'results.json').write_text(json.dumps(report, indent=2))
