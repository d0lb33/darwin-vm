#!/usr/bin/env python3
"""Decode the bounded, native -aes_spew trace; never expose key/IV bytes.

T8140 AppleS8000AES version 5, address-width 42. Native command builders:
KEY 93efcbc, IV 93ef884, DATA 93f0568, STORE_IV 93f0368,
FLAG 93f02d4/93f04d4. Addresses are static in the 24A5430a bootkc.
Unknown or incomplete commands are errors, not guessed packet boundaries.
"""
import argparse
import json
from pathlib import Path
import re


def decode(words):
    result = []
    offset = 0
    while offset < len(words):
        h = words[offset]
        if not 0 <= h <= 0xffffffff:
            raise ValueError(f'word {offset}: not a uint32')
        opcode = h >> 28
        item = dict(word=offset, header=f'{h:08x}')
        if opcode == 1:
            selector, size = (h >> 24) & 7, (h >> 22) & 3
            if size == 3:
                raise ValueError(f'word {offset}: unknown key size')
            if h & (1 << 21):
                raise ValueError(f'word {offset}: wrapped key layout not supported')
            key_bytes = (16, 24, 32)[size]
            count = 1 if selector else 1 + key_bytes // 4
            item.update(command='KEY', context=(h >> 27) & 1,
                        selector=selector, key_bytes=key_bytes,
                        encrypt=bool(h & (1 << 20)), mode=(h >> 16) & 3,
                        function=(h >> 18) & 3)
        elif opcode == 2:
            count = 5
            item.update(command='IV', context=(h >> 26) & 3)
        elif opcode == 5:
            count = 4
            item.update(command='DATA', key_context=(h >> 27) & 1,
                        iv_context=(h >> 25) & 3, bytes=h & 0xffffff)
        elif opcode == 6:
            count = 2
            item.update(command='STORE_IV')
        elif opcode == 8:
            count = 1
            # Bit 27 distinguishes the native final interrupt-bearing flag
            # from the preceding 80000100 barrier. Preserve unknown bits.
            item.update(command='FLAG', interrupt=bool(h & (1 << 27)),
                        tag=h & 0xff, other_bits=f'{h & 0x07ffff00:08x}')
        else:
            raise ValueError(f'word {offset}: unsupported opcode {opcode:x}')
        if offset + count > len(words):
            raise ValueError(f'word {offset}: truncated {item["command"]}, need {count} words')
        body = words[offset:offset + count]
        if any(not 0 <= w <= 0xffffffff for w in body):
            raise ValueError(f'word {offset}: payload is not uint32')
        if opcode == 5:
            item.update(source=f'0x{(((body[1] >> 16) & 0x3ff) << 32) | body[2]:x}',
                        destination=f'0x{((body[1] & 0x3ff) << 32) | body[3]:x}')
        elif opcode == 6:
            item['destination'] = f'0x{((h & 0x3ff) << 32) | body[1]:x}'
        result.append(item)
        offset += count
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('serial', type=Path)
    args = parser.parse_args()
    words = [int(m, 16) for m in re.findall(
        rb'AES_COMMAND: ([0-9a-fA-F]{8})(?:\r?\n|$)', args.serial.read_bytes())]
    if not words:
        parser.error('no complete AES_COMMAND records')
    try:
        print(json.dumps(decode(words), indent=2))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
