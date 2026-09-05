#!/usr/bin/env python3
"""Preserve small diagnostic records; exclude disks, RAM, binaries and shaders."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ALLOWED={'.json','.jsonl','.log','.txt','.md','.py','.sh','.m','.c','.tsv',
         '.stdout','.stderr','.exit','.plist','.tbd','.ll','.png','.command',
         '.nm-u','.otool-l','.disass'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
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
            if path.suffix not in ALLOWED or path.stat().st_size>16*1024*1024:
                continue
            relative=Path(source.name)/(path.relative_to(source) if source.is_dir() else Path(path.name))
            target=a.output/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                raise ValueError(f'collision: {target}')
            shutil.copyfile(path,target)
            data=target.read_bytes()
            entries.append(dict(path=str(relative),source=str(path.resolve()),bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    (a.output/'index.json').write_text(json.dumps(dict(files=entries,excluded='disks, RAM, executables, libraries, files over 16 MiB, and unlisted extensions'),indent=2)+'\n')
    print(f'preserved {len(entries)} records, {sum(e["bytes"] for e in entries)} bytes in {a.output}')


if __name__=='__main__':
    main()
