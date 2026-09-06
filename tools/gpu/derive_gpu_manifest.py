#!/usr/bin/env python3
"""Derive isolated GPU boot inputs while preserving the verified disk lineage."""
import argparse
import copy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from checkpoint_common import sha256, verify_backing_chain

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--qemu',type=Path)
    p.add_argument('--bootkc',type=Path)
    p.add_argument('--dtree',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();m=json.loads(a.source.read_text())
    verify_backing_chain(m['disk']['backing_chain'])
    for path,entry in m['qemu_inputs'].items():
        if sha256(Path(path))!=entry['sha256']:raise ValueError('changed input: '+path)
    before=copy.deepcopy(m)
    for flag,path in ((None,a.qemu),('-bootkc',a.bootkc),('-dtree',a.dtree)):
        if path is None:continue
        index=0 if flag is None else m['qemu_argv'].index(flag)+1
        old=m['qemu_argv'][index];path=path.resolve()
        del m['qemu_inputs'][old]
        m['qemu_argv'][index]=str(path)
        m['qemu_inputs'][str(path)]=dict(bytes=path.stat().st_size,sha256=sha256(path))
    for flag in ('-sptm','-txm','-tc','-drive'):
        assert m['qemu_argv'][m['qemu_argv'].index(flag)+1]==before['qemu_argv'][before['qemu_argv'].index(flag)+1]
    assert m['disk']==before['disk'] and m['qemu_env']==before['qemu_env']
    m['derived_gpu_source']=str(a.source.resolve())
    with a.output.open('x') as f:json.dump(m,f,indent=2);f.write('\n')
    print(a.output)

if __name__=='__main__':main()
