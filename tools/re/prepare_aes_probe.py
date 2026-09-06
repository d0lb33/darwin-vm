#!/usr/bin/env python3
"""Expose native AES matching in a disposable diagnostic device tree.

24A5430a AppleS8000AES personality matches aes,s8000. Its raw DT points to
dart-sio/mapper-aes (phandle 0x71, SID 1). This is driver diagnosis only:
the existing catch-all MMIO is NOT a functional AES accelerator.
"""
import argparse
from pathlib import Path
import sys
import struct

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import dt_fixup as dt


def decode(data):
    """Preserve binary properties: printable random-seed is not a C string."""
    def node(offset):
        result = dt.ADTNode()
        result.flags = {}
        result.records = {}
        count, children = struct.unpack_from('<II', data, offset)
        offset += 8
        for _ in range(count):
            start = offset
            name = data[offset:offset + 32].split(b'\0')[0].decode()
            length, = struct.unpack_from('<I', data, offset + 32)
            result.flags[name] = length & 0x80000000
            length &= 0x7fffffff
            offset += 36
            result.props[name] = data[offset:offset + length]
            offset += (length + 3) & ~3
            result.records[name] = (result.props[name], data[start:offset])
        for _ in range(children):
            child, offset = node(offset)
            result.children.append(child)
        return result, offset
    result, end = node(0)
    encoded = encode(result)
    if end != len(data) or encoded != data:
        first = next((i for i, (a, b) in enumerate(zip(encoded, data)) if a != b), None)
        raise ValueError(f'device tree does not round-trip exactly: parsed={end}, input={len(data)}, first difference={first}')
    return result


def encode(node):
    output = struct.pack('<II', len(node.props), len(node.children))
    for name, value in node.props.items():
        original = node.records.get(name)
        if original and original[0] == value:
            output += original[1]
            continue
        output += name.encode().ljust(32, b'\0')
        output += struct.pack('<I', len(value) | node.flags.get(name, 0))
        output += value.ljust((len(value) + 3) & ~3, b'\0')
    return output + b''.join(encode(c) for c in node.children)


def child(node, name):
    return next(c for c in node.children
                if c.props['name'].rstrip(b'\0') == name.encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ungated', action='store_true',
                        help='Diagnostic native no-clock-gate/no-power-gate configuration for always-on virtual devices')
    args = parser.parse_args()
    raw = decode(args.raw.read_bytes())
    prepared = decode(args.prepared.read_bytes())
    for name, compatible in [('aes', 'aes,s8000'), ('dart-sio', 'dart,t8110')]:
        source = child(child(raw, 'arm-io'), name)
        target = child(child(prepared, 'arm-io'), name)
        if source.props.get('compatible', b'').rstrip(b'\0') != compatible.encode():
            parser.error(f'unexpected raw {name} compatibility')
        for prop in ('reg', 'interrupts', 'AAPL,phandle'):
            if source.props[prop] != target.props[prop]:
                parser.error(f'prepared {name}/{prop} differs from raw input')
        target.props['compatible'] = source.props['compatible']
    source = child(child(child(raw, 'arm-io'), 'dart-sio'), 'mapper-aes')
    target = child(child(child(prepared, 'arm-io'), 'dart-sio'), 'mapper-aes')
    if source.props.get('compatible', b'').rstrip(b'\0') != b'iommu-mapper':
        parser.error('unexpected AES mapper compatibility')
    for prop in ('reg', 'AAPL,phandle'):
        if source.props[prop] != target.props[prop]:
            parser.error(f'prepared mapper-aes/{prop} differs from raw input')
    target.props['compatible'] = source.props['compatible']
    if args.ungated:
        # AppleT8140 9712930/9712970 tests presence of these properties;
        # clock/power requests return unsupported instead of waiting for PMGR.
        # This isolates AES registers, not a claim of PMGR emulation.
        arm_io = child(prepared, 'arm-io')
        arm_io.props['no-clock-gate'] = b''
        arm_io.props['no-power-gate'] = b''
    dart_id = 0
    for node in child(prepared, 'arm-io').children:
        if node.props.get('device_type', b'').rstrip(b'\0') == b'dart' and 'compatible' in node.props:
            node.props['dart-id'] = struct.pack('<I', dart_id)
            dart_id += 1
    with args.out.open('xb') as output:
        output.write(encode(prepared))
    print(f'Diagnostic only: AES driver exposed in {args.out}; AES MMIO remains unmodelled')


if __name__ == '__main__':
    main()
