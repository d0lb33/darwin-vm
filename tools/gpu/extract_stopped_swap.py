#!/usr/bin/env python3
"""Extract bounded A408 record candidates from a stopped owned compositor VM.

Memory copies are not an RPC trace. Preserve all distinct candidates and their
physical locations; require comparison with the failing runtime log before
assigning one to a particular submission. Never modifies guest RAM.
"""
import argparse,hashlib,json,mmap,os,struct
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('trial',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    result=json.loads((a.trial/'result.json').read_text())
    if result.get('scope')!='actual backboardd boot integration; no injected scene or test-helper factory':raise ValueError('not an owned compositor trial')
    pid=int((a.trial/'qemu.pid').read_text())
    if pid<=1:raise ValueError('invalid PID')
    try:os.kill(pid,0)
    except ProcessLookupError:pass
    else:raise ValueError('owner still exists')
    source=a.trial/'managed-ram.bin'
    if source.is_symlink() or source.stat().st_size!=0x300000000:raise ValueError('expected 12 GiB DRAM file')
    a.out.mkdir(exist_ok=False);records={};hits=0
    with source.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as ram:
        signature=struct.pack('<III',0x41343038,4084,12);at=ram.find(signature)
        while at>=0:
            hits+=1
            if hits>64:raise ValueError('candidate bound exceeded')
            raw=ram[at:at+4108]
            if len(raw)==4108:
                body=raw[12:4096];sha=hashlib.sha256(body).hexdigest()
                if sha not in records:
                    name=f'a408-{sha}.bin';(a.out/name).write_bytes(body)
                    u32=lambda o:struct.unpack_from('<I',body,o)[0]
                    records[sha]=dict(file=name,sha256=sha,swap_id=u32(0x98),
                        null_flags=list(body[0xfea:0xfef]),format=hex(u32(0x6eb)),
                        width=u32(0x701),height=u32(0x705),row=u32(0x6f5),
                        descriptor_size=u32(0x709),surface_dva=hex(struct.unpack_from('<Q',body,0xf90)[0]),
                        physical_locations=[])
                records[sha]['physical_locations'].append(hex(0x10000000000+at))
            at=ram.find(signature,at+1)
    report=dict(source=str(a.trial.resolve()),scope='stopped RAM candidates, not authoritative runtime dispatch',
        signature_hits=hits,candidates=list(records.values()))
    (a.out/'index.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
