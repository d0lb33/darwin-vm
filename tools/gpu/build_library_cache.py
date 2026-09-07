#!/usr/bin/env python3
"""Provision unchanged MTLB files by digest for explicitly selected guest libraries.

Inputs must already be MTLB slices (extract_air.py handles fat resources).
This is host provisioning, not proof of guest loading or shader execution.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('out', type=Path)
    p.add_argument('libraries', type=Path, nargs='+')
    a = p.parse_args()
    records = []
    for source in a.libraries:
        data = source.read_bytes()
        if not 88 <= len(data) <= 12*1024*1024 or data[:4] != b'MTLB' or struct.unpack_from('<Q', data, 16)[0] != len(data):
            raise ValueError('requires an unchanged bounded MTLB slice: ' + str(source))
        records.append((data, dict(source=str(source.resolve()), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())))
    a.out.mkdir(exist_ok=False)
    for data, record in records:
        (a.out/(record['sha256']+'.metallib')).write_bytes(data)
    (a.out/'manifest.json').write_text(json.dumps(dict(libraries=[r for _, r in records]), indent=2)+'\n')
    print(a.out)


if __name__ == '__main__':
    main()
