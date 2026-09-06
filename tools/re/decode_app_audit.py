#!/usr/bin/env python3
"""Decode native restore-shell, read-only plist exports for migration/app audit."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import plistlib
import re

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('serial', type=Path)
ap.add_argument('--out', type=Path, required=True)
a = ap.parse_args()
a.out.mkdir(parents=True, exist_ok=False)
records, current, chunks = [], None, []
for line in a.serial.read_text(errors='replace').replace('\r', '').splitlines():
    if line.startswith('DVM_FILE_BEGIN:/mnt'):
        if current:
            raise RuntimeError('incomplete preceding export')
        current, chunks = line.split(':', 1)[1], []
    elif line == 'DVM_FILE_END' and current:
        data = base64.b64decode(''.join(chunks), validate=True)
        value = plistlib.loads(data)
        destination = a.out / f'{len(records):03d}-{Path(current).name}'
        destination.write_bytes(data)
        records.append(dict(guest_path=current, local_path=str(destination),
                            sha256=hashlib.sha256(data).hexdigest(), plist=value))
        current, chunks = None, []
    elif current and re.fullmatch(r'[A-Za-z0-9+/=]+', line):
        chunks.append(line)
if current:
    raise RuntimeError('last export is incomplete')
apps = [dict(path=r['guest_path'], identifier=r['plist'].get('CFBundleIdentifier'),
             executable=r['plist'].get('CFBundleExecutable'),
             version=r['plist'].get('CFBundleVersion'))
        for r in records if r['guest_path'].endswith('.app/Info.plist')]
report = dict(exports=records, installed_app_metadata=apps)
(a.out/'audit.json').write_text(json.dumps(report, indent=2, default=str)+'\n')
print(json.dumps(dict(exports=len(records), app_metadata=len(apps),
                     identifiers=[r['identifier'] for r in apps]), indent=2))
