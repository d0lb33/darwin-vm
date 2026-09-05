#!/usr/bin/env python3
"""Add synthetic ANS NSID 6 without re-encoding any other DT property.

The tuple is an experiment, not a claim about physical T8140 hardware. Parsing
keeps opaque values opaque (especially random-seed); generic string conversion
has previously changed that property's length and caused an SPTM panic.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

EXPECTED = [(1, 1, 0), (2, 2, 0x800), (3, 3, 0x20), (4, 4, 2),
            (5, 5, 2), (6, 8, 0x100), (7, 13, 0x8000)]


def properties(data):
    result = {}

    def node(at, parent):
        nprops, children = struct.unpack_from('<II', data, at)
        at += 8
        entries = []
        name = None
        for _ in range(nprops):
            start = at
            key = data[at:at + 32].split(b'\0', 1)[0].decode('ascii')
            size = struct.unpack_from('<I', data, at + 32)[0] & 0x7fffffff
            at += 36
            value = data[at:at + size]
            if len(value) != size:
                raise ValueError('truncated property')
            at += (size + 3) & ~3
            if at > len(data):
                raise ValueError('truncated padding')
            entries.append((key, start, at, value))
            if key == 'name':
                name = value.rstrip(b'\0').decode('ascii')
        if name is None:
            raise ValueError('missing node name')
        path = parent + '/' + name
        for key, start, end, value in entries:
            identity = (path, key)
            if identity in result:
                raise ValueError('duplicate property')
            result[identity] = (start, end, value)
        for _ in range(children):
            at = node(at, path)
        return at

    end = node(0, '')
    if any(data[end:]):
        raise ValueError('nonzero trailing data')
    return result


def extend(data):
    before = properties(data)
    targets = [k for k in before if k[0].endswith('/arm-io/ans') and k[1] == 'namespaces']
    if len(targets) != 1:
        raise ValueError('expected exactly one ANS namespaces property')
    key = targets[0]
    start, end, value = before[key]
    if list(struct.iter_unpack('<III', value)) != EXPECTED:
        raise ValueError('unexpected namespace list; refusing to guess')
    flags = struct.unpack_from('<I', data, start + 32)[0] & 0x80000000
    value += struct.pack('<III', 8, 6, 0)
    output = data[:start + 32] + struct.pack('<I', len(value) | flags) + value + data[end:]
    after = properties(output)
    if before.keys() != after.keys():
        raise ValueError('tree structure changed')
    for identity in before:
        if identity != key:
            a, b, _ = before[identity]
            x, y, _ = after[identity]
            if data[a:b] != output[x:y]:
                raise ValueError(f'unrelated property changed: {identity}')
    return output


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    original = a.source.read_bytes()
    patched = extend(original)
    with a.output.open('xb') as f:
        f.write(patched)
    print(json.dumps(dict(source=str(a.source.resolve()), output=str(a.output.resolve()),
        source_sha256=hashlib.sha256(original).hexdigest(),
        output_sha256=hashlib.sha256(patched).hexdigest(),
        appended=[8, 6, 0], other_properties_byte_identical=True), indent=2))


if __name__ == '__main__':
    main()
