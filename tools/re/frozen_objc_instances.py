#!/usr/bin/env python3
"""Find heap instances of one exact Objective-C class in a frozen task.

Uses the task's physical 16 KiB page tables, not the selected CPU's EL0 map.
Instance candidates may include stale allocations; callers must verify their
retained object graph before interpreting them as active UI state.
"""
import argparse
import json
from pathlib import Path
import struct

from warm_boot_postmortem import Memory

MASK = 0x0000ffffffffc000
HEAPS = ((0x100000000, 0x110000000), (0x7000000000, 0x8000000000))


def mappings(memory, root):
    def walk(table, level, prefix):
        shift = {1: 36, 2: 25, 3: 14}[level]
        count = 128 if level == 1 else 2048
        raw = memory.physical(table, count * 8)
        for i, (entry,) in enumerate(struct.iter_unpack('<Q', raw)):
            va = prefix + (i << shift)
            end = va + (1 << shift)
            if not entry & 1 or not any(va < hi and lo < end for lo, hi in HEAPS):
                continue
            if entry & 3 == 1 or level == 3:
                if level == 3 and entry & 3 != 3:
                    continue
                yield va, (entry & MASK) & ~((1 << shift) - 1), 1 << shift
            else:
                yield from walk(entry & MASK, level + 1, va)
    yield from walk(root & 0x0000fffffffffc00, 1, 0)


def instances(memory, root, cls, size):
    needle = cls.to_bytes(8, 'little')[1:6]
    hits = []
    for base, data in memory.maps:
        offset = data.find(needle)
        while offset >= 0:
            start = offset - 1
            if start >= 0 and start % 8 == 0 and data[start] & 0xf8 == cls & 0xf8:
                hits.append(base + start)
            offset = data.find(needle, offset + 1)
    pages = {}
    for pa in hits:
        pages.setdefault(pa & ~0x3fff, []).append(pa)
    for va, pa, length in mappings(memory, root):
        if length == 0x4000:
            matches = pages.get(pa, [])
        else:
            matches = [h for h in hits if pa <= h < pa + length]
        for h in matches:
            address = va + h - pa
            try:
                raw = memory.user(root, address, size)
                isa = int.from_bytes(raw[:8], 'little')
                if (isa & 0x0000fffffffffff8) == cls:
                    yield dict(address=hex(address), physical=hex(h), isa=hex(isa), raw=raw.hex())
            except ValueError:
                continue


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--root', required=True, type=lambda s: int(s, 0))
    p.add_argument('--class-address', required=True, type=lambda s: int(s, 0))
    p.add_argument('--size', type=lambda s: int(s, 0), default=0x280)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    if not 8 <= a.size <= 0x4000:
        p.error('size must be 8..16384')
    argv = json.loads((a.run / 'launch.json').read_text())['argv']
    endpoint = argv[argv.index('-monitor') + 1]
    memory = Memory(Path(endpoint[5:].split(',', 1)[0]), a.run / 'ram')
    result = list(instances(memory, a.root, a.class_address, a.size))
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print('candidate instances:', len(result))
    for row in result:
        print(row['address'], row['isa'])


if __name__ == '__main__':
    main()
