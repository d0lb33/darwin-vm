#!/usr/bin/env python3
"""Decode a complete, contiguous export from QEMU's own serial log."""
import argparse
import hashlib
from pathlib import Path
import re
import zlib


def decode(raw):
    begin = re.findall(rb'^DVM_FILE_BEGIN (\d+)\r?$', raw, re.M)
    end = re.findall(rb'^DVM_FILE_END (\d+) ([0-9a-f]{8})\r?$', raw, re.M)
    if len(begin) != 1 or len(end) != 1:
        raise ValueError('requires exactly one begin/end record')
    output = bytearray()
    for offset, data in re.findall(rb'^DVM_FILE_HEX ([0-9a-f]+) ([0-9a-f]+)\r?$', raw, re.M):
        if int(offset, 16) != len(output):
            raise ValueError('non-contiguous export')
        output.extend(bytes.fromhex(data.decode('ascii')))
    if len(output) != int(begin[0]) or len(output) != int(end[0][0]):
        raise ValueError('export size mismatch')
    if zlib.crc32(output) != int(end[0][1], 16):
        raise ValueError('export CRC mismatch')
    return bytes(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('serial', type=Path)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    output = decode(a.serial.read_bytes().replace(b'\r\r\n', b'\r\n'))
    with a.output.open('xb') as file:
        file.write(output)
    print(len(output), hashlib.sha256(output).hexdigest(), a.output)


if __name__ == '__main__':
    main()
