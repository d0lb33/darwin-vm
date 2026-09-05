#!/usr/bin/env python3
"""Adapt AGTCNTVOFF_EL2 and optionally VBAR_EL1 in a disposable Mach-O.

Only instruction sections are scanned. The output is a cold-boot experiment,
not a replacement for normal firmware or a complete Apple timer model.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def rewrite(data, vbar_compat=False, virtual_el2=False):
    assert struct.unpack_from('<I', data)[0] == 0xfeedfacf, 'Expected thin Mach-O 64'
    result = bytearray(data)
    records = []
    virtual_ops = {}
    pos = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, pos)
        if kind == 0x19:
            nsects = struct.unpack_from('<I', data, pos + 64)[0]
            for n in range(nsects):
                sec = pos + 72 + n * 80
                address, length, offset = struct.unpack_from('<QQI', data, sec + 32)
                flags = struct.unpack_from('<I', data, sec + 64)[0]
                if not flags & 0x80000400:
                    continue
                assert offset % 4 == 0 and length % 4 == 0 and offset + length <= len(data)
                for p in range(offset, offset + length, 4):
                    word = struct.unpack_from('<I', data, p)[0]
                    if virtual_el2:
                        selected = ((word & 0xffc00000) == 0xd5000000 and
                                    ((word >> 19) & 3) != 0)
                        selected |= ((word & 0xfff8f01f) == 0xd500401f and
                                     (word & 0xfffffeff) != 0xd50040bf)
                        selected |= ((word & 0xfffff000) == 0xd69f0000 or
                                     (word & 0xfffffff0) == 0x00201420 or
                                     word == 0x00201400)
                        if not selected:
                            continue
                        index = virtual_ops.setdefault(word, len(virtual_ops))
                        assert index < 4096, 'Virtual instruction ledger overflow'
                        immediate = 0xe000 + index
                        replacement = 0xd4000003 | (immediate << 5)
                        struct.pack_into('<I', result, p, replacement)
                        records.append({'offset': hex(p), 'address': hex(address + p - offset),
                                        'original': hex(word), 'replacement': hex(replacement),
                                        'operation': 'virtual-el2', 'index': index})
                        continue
                    opcode = word & ~0x20001f
                    if opcode == 0xd519f980:
                        operation, immediate = 'AGTCNTVOFF_EL2', 0xd100
                    elif vbar_compat and opcode == 0xd518c000:
                        operation, immediate = 'VBAR_EL1', 0xd200
                    else:
                        continue
                    read = bool(word & 0x200000)
                    immediate |= (32 if read else 0) | (word & 31)
                    replacement = 0xd4000003 | (immediate << 5)
                    struct.pack_into('<I', result, p, replacement)
                    records.append({'offset': hex(p), 'address': hex(address + p - offset),
                                    'original': hex(word), 'replacement': hex(replacement),
                                    'operation': operation, 'read': read, 'rt': word & 31})
        pos += size
    assert records, 'No selected instruction sites found'
    return result, records


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--vbar-compat', action='store_true',
                    help='Also adapt VBAR_EL1, requiring QEMU_HVF_NATIVE_HCR=1')
    ap.add_argument('--virtual-el2', action='store_true',
                    help='Rewrite privileged operations for the virtual EL2 backend')
    ap.add_argument('--ledger', type=Path, help='Required binary instruction ledger for --virtual-el2')
    a = ap.parse_args()
    data = a.input.read_bytes()
    if a.virtual_el2 != bool(a.ledger) or (a.virtual_el2 and a.vbar_compat):
        ap.error('--virtual-el2 requires --ledger and excludes --vbar-compat')
    result, records = rewrite(data, a.vbar_compat, a.virtual_el2)
    ledger_info = {}
    if a.virtual_el2:
        words = {r['index']: int(r['original'], 16) for r in records}
        ledger = b'DVEL' + struct.pack('<I', len(words)) + b''.join(
            struct.pack('<I', words[i]) for i in range(len(words)))
        with a.ledger.open('xb') as out:
            out.write(ledger)
        ledger_info = {'ledger': str(a.ledger.resolve()),
                       'ledger_sha256': hashlib.sha256(ledger).hexdigest()}
    with a.output.open('xb') as out:
        out.write(result)
    print(json.dumps({'input': str(a.input.resolve()), 'output': str(a.output.resolve()),
                      'input_sha256': hashlib.sha256(data).hexdigest(),
                      'output_sha256': hashlib.sha256(result).hexdigest(),
                      **ledger_info,
                      'sites': records}, indent=2))


if __name__ == '__main__':
    main()
