#!/usr/bin/env python3
"""Post-trial hash verification of every immutable parent and pinned boot input."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256,verify_backing_chain
p=argparse.ArgumentParser(description=__doc__);p.add_argument('manifest',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
m=json.loads(a.manifest.read_text());verify_backing_chain(m['disk']['backing_chain'])
for name,entry in m['qemu_inputs'].items():
    if sha256(Path(name))!=entry['sha256']:raise ValueError('changed pinned input: '+name)
r=dict(verified=True,manifest=str(a.manifest.resolve()),backing_chain=m['disk']['backing_chain'],qemu_inputs=m['qemu_inputs'],
       scope='all immutable disk ancestors and pinned QEMU/firmware inputs unchanged after trial; disposable runtime child excluded')
a.out.write_text(json.dumps(r,indent=2)+'\n');print('Verified',len(m['disk']['backing_chain']),'ancestors and',len(m['qemu_inputs']),'boot inputs.')
