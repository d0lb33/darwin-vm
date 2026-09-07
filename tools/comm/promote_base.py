#!/usr/bin/env python3
"""Select a verified cellular-plan candidate as the default; preserve rollback.

Only replaces the manifest. Every disk and pinned boot input stays immutable.
Run bounded cold-boot validation before invoking this publication step.
This tool verifies inputs and lineage; it does not certify runtime behavior.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate', type=Path)
    p.add_argument('default', type=Path)
    p.add_argument('backup', type=Path)
    a = p.parse_args()
    candidate = json.loads(a.candidate.read_text())
    current = json.loads(a.default.read_text())
    if 'cellular_service_installation' not in candidate:
        p.error('candidate lacks cellular service installation provenance')
    if candidate['disk']['backing_chain'][1:] != current['disk']['backing_chain']:
        p.error('candidate must derive directly from the current immutable base')
    # This service needs neither kernel changes nor a different hardware setup.
    for key in ('qemu_env', 'battery_source'):
        if candidate.get(key) != current.get(key):
            p.error('unexpected change to ' + key)
    def fixed_args(m):
        v = list(m['qemu_argv'])
        for key in ('-tc', '-drive'):
            v[v.index(key) + 1] = '<updated>'
        return v
    if fixed_args(candidate) != fixed_args(current):
        p.error('unexpected boot configuration change')
    verify_backing_chain(candidate['disk']['backing_chain'])
    for name, item in candidate['qemu_inputs'].items():
        if sha256(Path(name)) != item['sha256']:
            p.error('changed boot input: ' + name)
    original = a.default.read_bytes()
    with a.backup.open('xb') as f:
        f.write(original)
    candidate['source_manifest'] = str(a.backup.resolve())
    temporary = a.default.with_name(a.default.name + '.cellular-new')
    with temporary.open('x') as f:
        json.dump(candidate, f, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    if a.default.read_bytes() != original:
        p.error('default changed concurrently; backup retained, no replacement')
    os.replace(temporary, a.default)
    print('Default updated; rollback manifest:', a.backup)


if __name__ == '__main__':
    main()
