#!/usr/bin/env python3
"""Pin the owned transport QEMU/BootKC/DT while preserving the installed disk."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('installed',type=Path)
p.add_argument('boot_build',type=Path)
p.add_argument('output',type=Path)
a=p.parse_args()
m=json.loads(a.installed.read_text())
verify_backing_chain(m['disk']['backing_chain'])
for path,entry in m['qemu_inputs'].items():
    if sha256(Path(path))!=entry['sha256']:raise ValueError('changed installed input: '+path)
ledger=json.loads((a.boot_build/'ledger.json').read_text())
for flag,name in ((None,'qemu-system-aarch64'),('-bootkc','bootkc'),('-dtree','system.dtree')):
    index=0 if flag is None else m['qemu_argv'].index(flag)+1
    old=Path(m['qemu_argv'][index]).resolve();new=(a.boot_build/name).resolve()
    if not new.is_file():raise ValueError('missing boot artifact '+str(new))
    m['qemu_argv'][index]=str(new)
    for key in list(m['qemu_inputs']):
        if Path(key).resolve()==old:del m['qemu_inputs'][key]
    m['qemu_inputs'][str(new)]=dict(bytes=new.stat().st_size,sha256=sha256(new))
    if flag=='-bootkc' and sha256(new)!=ledger['output_bootkc_sha256']:raise ValueError('BootKC ledger mismatch')
    if flag=='-dtree' and sha256(new)!=ledger['output_dtree_sha256']:raise ValueError('DT ledger mismatch')
if any(key in m['qemu_env'] for key in ('DARWIN_ANS_AUX_DRIVE','DARWIN_GPU_SHM_PATH')):
    raise ValueError('uncontrolled transport backend in manifest')
m['mmio_transport']=dict(source_manifest=str(a.installed.resolve()),boot_ledger=ledger,
    scope='owned boot shim; runtime creates new RAM file; no NVMe transport',
    sptm_txm_inputs_preserved=True)
with a.output.open('x') as f:json.dump(m,f,indent=2);f.write('\n')
print(a.output)
