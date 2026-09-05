#!/usr/bin/env python3
"""Locate SPRR register accesses in executable Mach-O segments, without edits.

Addresses are unslid image VAs, not runtime PCs. Segment scanning can include
literal pools, so each result is a candidate requiring control-flow inspection.
Outer fileset executable segments cover the kernel and embedded kext text.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def scan(path):
    data = path.read_bytes()
    if struct.unpack_from('<I', data)[0] != 0xfeedfacf:
        raise ValueError('Expected thin 64-bit Mach-O')
    sites = []
    p = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, p)
        if size < 8 or p + size > len(data):
            raise ValueError('Invalid load command')
        if kind == 0x19:
            name, va, _, offset, length, _, prot = struct.unpack_from(
                '<16sQQQQII', data, p + 8)
            if prot & 4:
                if offset % 4 or length % 4 or offset + length > len(data):
                    raise ValueError('Invalid executable segment')
                for pos in range(offset, offset + length, 4):
                    word = struct.unpack_from('<I', data, pos)[0]
                    if word & 0xffd00000 != 0xd5100000:
                        continue
                    op0, op1 = (word >> 19) & 3, (word >> 16) & 7
                    crn, crm, op2 = (word >> 12) & 15, (word >> 8) & 15, (word >> 5) & 7
                    if (op0, op1, crn) != (3, 6, 15):
                        continue
                    if crm not in (1, 3, 4, 5, 6, 14, 15):
                        continue
                    sites.append({'segment': name.rstrip(b'\0').decode(),
                                  'offset': hex(pos), 'va': hex(va + pos - offset),
                                  'word': hex(word), 'read': bool(word & 0x200000),
                                  'rt': word & 31, 'encoding': [op0, op1, crn, crm, op2]})
        p += size
    return {'image': str(path.resolve()),
            'sha256': hashlib.sha256(data).hexdigest(), 'sites': sites}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('images', type=Path, nargs='+')
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    results = [scan(p) for p in a.images]
    with a.out.open('x') as out:
        json.dump(results, out, indent=2)
    for r in results:
        print(f"{r['image']}: {len(r['sites'])} candidates")


if __name__ == '__main__':
    main()
