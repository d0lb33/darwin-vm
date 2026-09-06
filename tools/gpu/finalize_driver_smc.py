#!/usr/bin/env python3
"""Pin the guarded native-SMC migration of an existing driver/input disk.

No disk writes: verify the installer result and unchanged parent chain, then
publish a manifest for disposable normal-boot children.
"""
import argparse
import copy
import json
from pathlib import Path
import plistlib
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import atomic_json, qcow2_backing_chain, sha256, verify_backing_chain

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('baseline', type=Path)
p.add_argument('migration', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
if a.output.exists():
    p.error('output must be new')
m = json.loads(a.baseline.read_text())
result = json.loads((a.migration/'install/result.json').read_text())
if not result.get('success') or result.get('install_marker') != 'DVM_NATIVE_BATTERY_INSTALLED':
    raise ValueError('missing successful guarded SMC install')
if Path(result['parent']).resolve() != Path(m['disk']['path']).resolve():
    raise ValueError('migration did not use retained driver parent')
verify_backing_chain(m['disk']['backing_chain'])
for path, record in m['qemu_inputs'].items():
    if sha256(Path(path)) != record['sha256']:
        raise ValueError('changed pinned input: '+path)
for path, digest in json.loads((a.migration/'inputs.json').read_text()).items():
    if sha256(Path(path)) != digest:
        raise ValueError('changed migration input: '+path)
before = plistlib.loads((a.migration/'payload/nb-launchd-before').read_bytes())
after = plistlib.loads((a.migration/'payload/nb-launchd-after').read_bytes())
expected = copy.deepcopy(before)
del expected['LaunchDaemons']['/System/Library/LaunchDaemons/com.apple.dvm-power-pv-service.plist']
if expected != after:
    raise ValueError('migration changed unrelated launchd jobs')
disk = (a.migration/'install/disk.qcow2').resolve()
if disk.stat().st_mode & 0o222:
    raise ValueError('migration disk is writable')
chain = qcow2_backing_chain(Path(shutil.which('qemu-img')), disk)
if chain[1:] != m['disk']['backing_chain']:
    raise ValueError('migration changed disk lineage')
m['disk'] = dict(path=str(disk), backing_chain=chain)
argv = m['qemu_argv']
argv[argv.index('-drive')+1] = f'if=none,id=ans,file={disk},format=qcow2'
m['battery_source'] = 'emulated-smc'
m['driver_smc_migration'] = dict(baseline=str(a.baseline.resolve()),
    installer=str(a.migration.resolve()), result=result,
    original_powerd_sha256=sha256(a.migration/'payload/nb-powerd-after'),
    launchd_cache_sha256=sha256(a.migration/'payload/nb-launchd-after'),
    retained_parent_verified=True, runtime_validated=False)
atomic_json(a.output, m)
print(a.output)
