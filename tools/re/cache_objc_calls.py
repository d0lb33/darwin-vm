#!/usr/bin/env python3
"""Resolve ARM64 shared-cache selector stubs in an ipsw disassembly.

Only accepts the observed ADRP x1; ADD x1,x1,#imm; B objc_msgSend
shape. Does not infer selectors from nearest-symbol labels.
"""
import argparse
from pathlib import Path
import re
import struct


class Cache:
    def __init__(self, directory):
        self.maps = []
        for path in directory.glob('dyld_shared_cache_arm64e*'):
            with path.open('rb') as f:
                header = f.read(4096)
                if header[:4] != b'dyld':
                    continue
                offset, count = struct.unpack_from('<II', header, 16)
                f.seek(offset)
                for _ in range(count):
                    va, size, fileoff, _, _ = struct.unpack('<QQQII', f.read(32))
                    self.maps.append((va, va + size, fileoff, path))

    def read(self, address, length):
        for lo, hi, offset, path in self.maps:
            if lo <= address and address + length <= hi:
                with path.open('rb') as f:
                    f.seek(offset + address - lo)
                    return f.read(length)
        raise ValueError('unmapped cache address')

    def selector(self, address):
        adrp, add, branch = struct.unpack('<III', self.read(address, 12))
        if adrp & 0x9f00001f != 0x90000001 or add & 0xffc003ff != 0x91000021 or branch >> 26 != 5:
            raise ValueError('not a direct selector stub')
        imm = ((adrp >> 5 & 0x7ffff) << 2) | (adrp >> 29 & 3)
        if imm & 0x100000:
            imm -= 0x200000
        target = (address & ~0xfff) + (imm << 12) + (add >> 10 & 0xfff)
        return self.read(target, 256).split(b'\0')[0].decode('ascii')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('cache_directory', type=Path)
    p.add_argument('disassembly', type=Path)
    selection=p.add_mutually_exclusive_group(required=True)
    selection.add_argument('--selector')
    selection.add_argument('--all-selectors',action='store_true')
    a = p.parse_args()
    cache = Cache(a.cache_directory)
    resolved, function = {}, ''
    for line in a.disassembly.read_text().splitlines():
        if line.endswith(':') and not line.startswith('0x'):
            function = line
        match = re.search(r'\b(?:bl|b)\s+(0x[0-9a-f]+)', line)
        if not match:
            continue
        address = int(match[1], 16)
        if address not in resolved:
            try:
                resolved[address] = cache.selector(address)
            except (ValueError, UnicodeError):
                resolved[address] = None
        if a.all_selectors and resolved[address]:
            print(line+' ; selector='+resolved[address])
        elif not a.all_selectors and resolved[address] == a.selector:
            print(function, line, sep='\n')


if __name__ == '__main__':
    main()
