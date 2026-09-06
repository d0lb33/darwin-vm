#!/usr/bin/env python3
"""Preserve small diagnostic records; exclude disks, RAM, binaries and shaders."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re

ALLOWED={'.json','.jsonl','.log','.txt','.md','.py','.sh','.m','.c','.cpp','.h','.tsv',
         '.stdout','.stderr','.exit','.plist','.tbd','.ll','.png','.command',
         '.nm-u','.otool-l','.disass','.csv','.bgra','.inc','.s'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reference-streams',action='store_true',help='include captured LIBREF streams (skip full-library uploads)')
    p.add_argument('--mmio-frames',action='store_true',help='include owned 16 MiB transport RAM and bounded luma/blur frames, never full guest RAM')
    p.add_argument('sources',type=Path,nargs='+')
    a=p.parse_args()
    a.output.mkdir(exist_ok=False)
    entries=[]
    for source in a.sources:
        if source.is_symlink():
            raise ValueError('source must not be a symlink')
        paths=sorted(source.rglob('*')) if source.is_dir() else [source]
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            stream=a.reference_streams and path.name in ('guest-requests.bin','host-responses.bin')
            compressed=False
            if path.name in ('stderr.log.gz','serial.log.gz','wire.log.gz'):
                ledger=path.with_name('compressed-logs.json')
                if ledger.is_file():
                    compressed=any(x['compressed']==path.name and x['compressed_sha256']==hashlib.sha256(path.read_bytes()).hexdigest() for x in json.loads(ledger.read_text()))
            mmio=False
            if a.mmio_frames:
                if path.name=='shared-ram.bin' and path.stat().st_size==16*1024*1024:
                    with path.open('rb') as f:mmio=f.read(4)==b'1MVD'
                elif path.name=='managed-pages.bin' and path.stat().st_size==32+759*8:
                    # Fixed kernel registration record; never the DRAM backend.
                    mmio=True
                elif path.name=='managed-final-buffer.bin' and path.stat().st_size==759*16384:
                    ledger=path.with_name('managed-verification.json')
                    if ledger.exists():
                        mmio=hashlib.sha256(path.read_bytes()).hexdigest()==json.loads(ledger.read_text()).get('resource_snapshot_sha256')
                elif path.name=='output-ram.bin' and path.stat().st_size==16*1024*1024 and (path.parent/'plan.json').is_file():
                    # Final-only output of the resident host presentation probe.
                    with path.open('rb') as f:
                        f.seek(0x300000)
                        mmio=f.read(16)==bytes.fromhex('4d5644ff535250ff524c42ff210000ff')
                elif re.fullmatch(r'binary-(request|reply)-[0-9]+\.bin',path.name):
                    data=path.read_bytes()
                    mmio=(len(data)==28672 and data[:4]==b'DVB1') or (len(data)==272 and data[:4]==b'DVR1')
                elif re.fullmatch(r'blur-(request|reply)-[0-9]+x[0-9]+\.bin',path.name) and path.stat().st_size<2*1024*1024:
                    from blur_peer import request,reply
                    data=path.read_bytes()
                    try:
                        (request if path.name.startswith('blur-request') else reply)(data)
                        mmio=True
                    except ValueError:
                        pass
            if stream:
                request=path.with_name('guest-requests.bin').read_bytes()
                if not request.startswith(b'LIBREF ') and request:
                    continue # legacy captures can contain the full Apple library
            if (path.suffix not in ALLOWED and not stream and not mmio and not compressed) or path.stat().st_size>16*1024*1024:
                continue
            relative=Path(source.name)/(path.relative_to(source) if source.is_dir() else Path(path.name))
            target=a.output/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                raise ValueError(f'collision: {target}')
            shutil.copyfile(path,target)
            data=target.read_bytes()
            entries.append(dict(path=str(relative),source=str(path.resolve()),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    (a.output/'index.json').write_text(json.dumps(dict(files=entries,reference_streams=a.reference_streams,mmio_frames=a.mmio_frames,excluded='disks, full guest RAM, executables, libraries, full shader upload captures, files over 16 MiB, and unlisted extensions'),indent=2)+'\n')
    print(f'preserved {len(entries)} records, {sum(e["bytes"] for e in entries)} bytes in {a.output}')


if __name__=='__main__':
    main()
