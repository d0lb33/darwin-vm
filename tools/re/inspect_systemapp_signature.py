#!/usr/bin/env python3
"""Read the current SystemAppMigrator signature-check path, without guest writes."""
import argparse,json
from pathlib import Path
from inspect_migration_processes import DemandMemory
from warm_boot_postmortem import inspect
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--monitor',required=True)
p.add_argument('--out',type=Path,required=True)
p.add_argument('--proc',type=lambda v:int(v,0),required=True)
a=p.parse_args()
m=DemandMemory(Path(a.monitor),a.out/'pages')
raw=m.kernel(a.proc,0x800)
if raw[0x55c:0x56d].split(b'\0')[0] != b'com.apple.migrat':
    raise RuntimeError('wrong process')
d=inspect(m,'com.apple.migrat',0,raw);root=int(d['root'],16)
# Match the exact SystemAppMigrator call and next instruction at static 0x6c98.
code=m.user(root,0x104432c98,8)
if code.hex() != '6d0a0094f70300aa':raise RuntimeError('different image slide or code')
for t in d['threads']:
    if '0x104432c9c' in t.get('frames',[]) and t.get('pc')=='0x24df696a8':
        ptr=int(t['registers']['x0'],16)
        t['getattrlist_path']=m.user(root,ptr,1024).split(b'\0',1)[0].decode('utf-8','replace')
(a.out/'signature.json').write_text(json.dumps(d,indent=2)+'\n')
print(json.dumps([t.get('getattrlist_path') for t in d['threads'] if 'getattrlist_path' in t]))
