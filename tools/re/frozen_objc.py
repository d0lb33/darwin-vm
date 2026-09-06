#!/usr/bin/env python3
"""Read native Objective-C ivars through an explicit frozen task's pmap."""
import argparse
import json
from pathlib import Path
import struct

from warm_boot_postmortem import Memory

POINTER = 0x0000ffffffffffff


class ObjC:
    def __init__(self, memory, root):
        self.memory, self.root, self.classes = memory, root, {}

    def read(self, address, size):
        return self.memory.user(self.root, address & POINTER, size)

    def u64(self, address):
        return int.from_bytes(self.read(address, 8), 'little')

    def string(self, address):
        return self.read(address, 256).split(b'\0')[0].decode('ascii')

    def class_info(self, cls):
        cls &= POINTER & ~7
        if cls in self.classes:
            return self.classes[cls]
        data = self.u64(cls + 0x20) & POINTER & ~7
        flags = int.from_bytes(self.read(data, 4), 'little')
        ro = self.u64(data + 8) if flags & 0x80000000 else data
        if ro & 1:
            ro = self.u64(ro & ~7)
        ro &= POINTER & ~7
        raw = self.read(ro, 0x48)
        name = self.string(int.from_bytes(raw[0x18:0x20], 'little'))
        if not name or not name.isprintable():
            raise ValueError('invalid class name')
        info = dict(name=name, address=hex(cls), superclass=hex(self.u64(cls + 8) & POINTER),
                    size=struct.unpack_from('<I', raw, 8)[0], ivars=[])
        ivars = int.from_bytes(raw[0x30:0x38], 'little') & POINTER
        if ivars:
            entry_size, count = struct.unpack('<II', self.read(ivars, 8))
            if entry_size != 32 or count > 512:
                raise ValueError('unsupported ivar list')
            for i in range(count):
                entry = self.read(ivars + 8 + i * entry_size, 32)
                offset_ptr, name_ptr, type_ptr, alignment, size = struct.unpack('<QQQII', entry)
                try:
                    type_name = self.string(type_ptr)
                except ValueError:
                    type_name = '<unmapped>'
                try:
                    ivar_name = self.string(name_ptr)
                except ValueError:
                    ivar_name = f'<unmapped-name:{name_ptr & POINTER:#x}>'
                info['ivars'].append(dict(name=ivar_name, type=type_name,
                    offset=int.from_bytes(self.read(offset_ptr, 4), 'little'), size=size))
        self.classes[cls] = info
        return info

    def name(self, object_address):
        try:
            return self.class_info(self.u64(object_address))['name']
        except (ValueError, UnicodeError):
            return None

    def object(self, address):
        cls = self.u64(address) & POINTER & ~7
        info = self.class_info(cls)
        result = dict(address=hex(address), class_name=info['name'], ivars=[])
        seen = set()
        while cls and cls not in seen:
            seen.add(cls)
            info = self.class_info(cls)
            for ivar in info['ivars']:
                row = dict(ivar, owner=info['name'])
                raw = self.read(address + ivar['offset'], min(ivar['size'], 64))
                row['raw'] = raw.hex()
                if (ivar['type'].startswith(('@', '#')) or ivar['type'] == '<unmapped>') and len(raw) == 8:
                    ptr = int.from_bytes(raw, 'little') & POINTER
                    row.update(pointer=hex(ptr), class_name=self.name(ptr) if ptr else None)
                result['ivars'].append(row)
            cls = int(info['superclass'], 16)
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('run', type=Path)
    p.add_argument('--root', required=True, type=lambda s: int(s, 0))
    p.add_argument('--object', action='append', required=True, type=lambda s: int(s, 0))
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    argv = json.loads((a.run / 'launch.json').read_text())['argv']
    endpoint = argv[argv.index('-monitor') + 1]
    objc = ObjC(Memory(Path(endpoint[5:].split(',', 1)[0]), a.run / 'ram'), a.root)
    rows = [objc.object(address) for address in a.object]
    a.output.write_text(json.dumps(rows, indent=2) + '\n')
    for row in rows:
        print(row['address'], row['class_name'])
        for ivar in row['ivars']:
            if ivar.get('class_name'):
                print(hex(ivar['offset']), ivar['name'], ivar['pointer'], ivar['class_name'])


if __name__ == '__main__':
    main()
