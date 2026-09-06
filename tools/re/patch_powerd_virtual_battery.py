#!/usr/bin/env python3
"""Guard 24A5430a powerd's precise-info merge when physical battery is absent.

copy_powersources_info dereferences control.internal at static 0x100004ea4.
The virtual InternalBattery dictionary is sufficient without the optional
physical preciseDescription. x23 already addresses the same global page;
use that to fit a null guard without a trampoline or changing real batteries.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

OFFSET = 0x4e98
SOURCE_SHA256 = '31254642770ac63ce22a55a5717246cf2289297f478f7ab753d15b6f88436268'
BEFORE = bytes.fromhex('fc000036a80500b0089941f9021140f9')
AFTER = bytes.fromhex('fc000036e89a41f9280800b4021140f9')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    if a.output.exists():
        p.error('output must be new')
    subprocess.run(['codesign', '--verify', '--strict', str(a.source)], check=True)
    data = a.source.read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256 or \
            data[:4] != bytes.fromhex('cffaedfe') or data[OFFSET:OFFSET + len(BEFORE)] != BEFORE:
        p.error('source is not the reviewed 24A5430a powerd image')
    changed = bytearray(data)
    changed[OFFSET:OFFSET + len(AFTER)] = AFTER
    a.output.write_bytes(changed)
    a.output.chmod(0o755)
    subprocess.run(['codesign', '--force', '--sign', '-', '--timestamp=none',
                    '--preserve-metadata=identifier,entitlements,requirements,flags,runtime', str(a.output)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(a.output)], check=True)
    report = dict(source=str(a.source.resolve()), output=str(a.output.resolve()),
                  before_sha256=hashlib.sha256(data).hexdigest(),
                  after_sha256=hashlib.sha256(a.output.read_bytes()).hexdigest(),
                  offset=hex(OFFSET), before=BEFORE.hex(), after=AFTER.hex())
    a.output.with_suffix('.patch.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
