#!/usr/bin/env python3
"""Make a disposable low-DRAM tree for HVF bring-up, preserving all other bytes.

xnuboot_sptm.c constructs the loaded memory-map and boot_args from dram-base.
This does not claim that firmware hardcoded physical addresses are relocated.
Never use it to resume a snapshot made with a different physical layout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def properties(data, offset=0, parent=''):
    count, children = struct.unpack_from('<II', data, offset)
    offset += 8
    props = {}
    for _ in range(count):
        name = data[offset:offset + 32].split(b'\0')[0].decode('ascii')
        size = struct.unpack_from('<I', data, offset + 32)[0] & 0x7fffffff
        start = offset + 36
        if start + size > len(data):
            raise ValueError('Truncated device-tree property')
        props[name] = (start, size)
        offset = start + ((size + 3) & ~3)
    start, size = props['name']
    name = data[start:start + size].split(b'\0')[0].decode('ascii')
    path = f'{parent}/{name}'
    nodes = {path: props}
    for _ in range(children):
        offset, subtree = properties(data, offset, path)
        nodes.update(subtree)
    return offset, nodes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0x800000000)
    a = ap.parse_args()
    data = a.input.read_bytes()
    _, nodes = properties(data)
    chosen = next(p for path, p in nodes.items() if path.endswith('/chosen'))
    offset, size = chosen['dram-base']
    assert size == 8
    old = struct.unpack_from('<Q', data, offset)[0]
    size_offset, size_size = chosen['dram-size']
    assert size_size == 8
    ram_size = struct.unpack_from('<Q', data, size_offset)[0]
    assert a.base >= 0x800000000 and a.base % 0x4000 == 0
    assert a.base + ram_size <= 1 << 36, 'Bring-up uses the default 36-bit HVF IPA'
    result = bytearray(data)
    struct.pack_into('<Q', result, offset, a.base)
    with a.output.open('xb') as out:
        out.write(result)
    print(json.dumps({'input': str(a.input.resolve()), 'output': str(a.output.resolve()),
                      'input_sha256': hashlib.sha256(data).hexdigest(),
                      'output_sha256': hashlib.sha256(result).hexdigest(),
                      'property_offset': hex(offset), 'old_base': hex(old),
                      'new_base': hex(a.base), 'ram_size': hex(ram_size)}, indent=2))


if __name__ == '__main__':
    main()
