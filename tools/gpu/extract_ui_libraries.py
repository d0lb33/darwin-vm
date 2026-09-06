#!/usr/bin/env python3
"""Copy only selected UI shader resources from an already safely mounted guest image."""
import argparse, hashlib, json, plistlib
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('mount', type=Path)
p.add_argument('out', type=Path)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=False)
def digest(data): return hashlib.sha256(data).hexdigest()
version = a.mount / 'System/Library/CoreServices/SystemVersion.plist'
v = plistlib.loads(version.read_bytes())
assert v['ProductBuildVersion'] == '24A5430a' and v['ProductVersion'] == '27.0', v
paths = ['System/Library/Frameworks/QuartzCore.framework/default.metallib',
         'System/Library/PrivateFrameworks/RenderBox.framework/default.metallib',
         'System/Library/PrivateFrameworks/RenderBox.framework/archive.metallib']
records = []
for rel in paths:
    src = a.mount / rel
    data = src.read_bytes()
    assert len(data) > 88, (rel, 'empty/compressed extraction')
    dst = a.out / (src.parent.name + '.' + src.name)
    dst.write_bytes(data)
    records.append(dict(guest_path='/' + rel, output=str(dst.resolve()), bytes=len(data),
                        sha256=digest(data), header=data[:88].hex()))
(a.out / 'SystemVersion.plist').write_bytes(version.read_bytes())
(a.out / 'provenance.json').write_text(json.dumps(dict(version=v, libraries=records), indent=2)+'\n')
print(json.dumps(records, indent=2))
