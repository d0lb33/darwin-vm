#!/usr/bin/env python3
"""Snapshot only registered IOSurface byte ranges after an owned VM has stopped.

Never labels post-submission bytes as replay inputs. No full DRAM copy, guest
mapping changes, or checkpoint semantics. Output must be a new directory.
"""
import argparse,hashlib,json,os,struct
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('trial',type=Path);p.add_argument('out',type=Path);a=p.parse_args()
    result=json.loads((a.trial/'result.json').read_text())
    if result.get('scope')!='actual backboardd boot integration; no injected scene or test-helper factory':raise ValueError('not an owned compositor trial')
    pid=int((a.trial/'qemu.pid').read_text())
    if pid<=1:raise ValueError('invalid owner PID')
    try:os.kill(pid,0)
    except ProcessLookupError:pass
    else:raise ValueError('owner PID still exists; snapshot requires completed cleanup')
    rows=[json.loads(x) for x in (a.trial/'driver-host.jsonl').read_text().splitlines()]
    submitted=any(r['op'] in ('renderSubmit','renderStageCommit','finishRenderSubmit','submit','blurSubmit') for r in rows)
    phase='post-run-final-not-replay-input' if submitted else 'stopped-before-any-host-submission'
    with (a.trial/'shared-ram.bin').open('rb') as f:header=f.read(32)
    if len(header)!=32 or header[:8]!=struct.pack('<Q',0x144564d31):raise ValueError('session header')
    ram=a.trial/'managed-ram.bin'
    if ram.is_symlink() or ram.stat().st_size!=0x300000000:raise ValueError('DRAM backing')
    a.out.mkdir(exist_ok=False);entries=[];total=0
    with ram.open('rb') as f:
        for manifest in sorted((a.trial/'managed-pages.bin.imports').glob('*.pages')):
            if manifest.is_symlink():raise ValueError('manifest symlink')
            b=manifest.read_bytes()
            if len(b)<64:raise ValueError('short manifest')
            magic,version=struct.unpack_from('<QQ',b);rid,length,offset,count=struct.unpack_from('<4Q',b,32)
            if magic!=0x31524753564d44 or version!=1 or b[16:32]!=header[16:32] or not 0<rid<=0xffffffff or not 0<length<=64*1024*1024 or offset>=16384 or count!=(length+offset+16383)//16384 or len(b)!=64+count*8:raise ValueError('manifest contract')
            pages=struct.unpack_from('<'+'Q'*count,b,64);total+=count*16384
            if len(set(pages))!=count or total>256*1024*1024 or any(v%16384 or v>=0x300000000 for v in pages):raise ValueError('page contract')
            retired=manifest.with_suffix('.retired').exists()
            # A retired allocation may already belong to something unrelated.
            if retired:continue
            target=a.out/f'surface-{rid:016x}.bin';remaining=length;skip=offset;digest=hashlib.sha256()
            with target.open('xb') as out:
                for page in pages:
                    take=min(16384-skip,remaining);f.seek(page+skip);data=f.read(take)
                    if len(data)!=take:raise ValueError('short DRAM read')
                    out.write(data);digest.update(data);remaining-=take;skip=0
            if remaining:raise ValueError('short logical snapshot')
            entries.append(dict(id=rid,file=target.name,bytes=length,sha256=digest.hexdigest(),offset=offset,page_count=count,
                file_offsets=pages,manifest_sha256=hashlib.sha256(b).hexdigest(),
                descriptors=[r['request'] for r in rows if r['op']=='surfaceImport' and r['request'].get('id')==rid]))
    report=dict(scope=phase,session=header[16:32].hex(),source=str(a.trial.resolve()),surfaces=entries,
                limitation='not an import-time/concurrent-producer snapshot; post-submission contents cannot replay initial inputs')
    (a.out/'index.json').write_text(json.dumps(report,indent=2)+'\n');print(a.out)
if __name__=='__main__':main()
