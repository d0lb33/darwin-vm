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


def fileset_text_ranges(data, names):
    """__TEXT_EXEC ranges of fileset entries whose name contains any of names."""
    ranges = []
    pos = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, pos)
        if kind == 0x80000035:  # LC_FILESET_ENTRY
            vmaddr, fileoff, name_off = struct.unpack_from('<QQI', data, pos + 8)
            name = data[pos + name_off:pos + size].split(b'\0')[0].decode()
            if any(n in name for n in names):
                p = fileoff + 32
                for _ in range(struct.unpack_from('<I', data, fileoff + 16)[0]):
                    k, sz = struct.unpack_from('<II', data, p)
                    if k == 0x19 and b'TEXT_EXEC' in data[p + 8:p + 24]:
                        sva, svs = struct.unpack_from('<QQ', data, p + 24)
                        ranges.append((name, sva, sva + svs))
                    p += sz
        pos += size
    return ranges


def rewrite(data, vbar_compat=False, virtual_el2=False, ledger_in=None,
            segments=False, native_zva=False, exclude_kexts=()):
    """Rewrite privileged instructions.

    ledger_in: existing ledger words (list) whose indices are preserved so
    several images (SPTM and the kernelcache) share one ledger.
    segments: scan every word of __TEXT_EXEC / __TEXT_BOOT_EXEC segments
    instead of instruction-flagged sections. The kernelcache is a Mach-O
    fileset whose top-level segments carry no section attributes.
    """
    assert struct.unpack_from('<I', data)[0] == 0xfeedfacf, 'Expected thin Mach-O 64'
    result = bytearray(data)
    records = []
    virtual_ops = {w: i for i, w in enumerate(ledger_in or [])}
    excluded = fileset_text_ranges(data, exclude_kexts) if exclude_kexts else []
    pos = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, pos)
        if kind == 0x19:
            nsects = struct.unpack_from('<I', data, pos + 64)[0]
            segname = data[pos + 8:pos + 24].rstrip(b'\0').decode()
            ranges = []
            if segments:
                if 'TEXT_EXEC' in segname or segname == '__TEXT_BOOT_EXEC':
                    vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, pos + 24)
                    ranges.append((vmaddr, filesize, fileoff))
            else:
                for n in range(nsects):
                    sec = pos + 72 + n * 80
                    address, length, offset = struct.unpack_from('<QQI', data, sec + 32)
                    flags = struct.unpack_from('<I', data, sec + 64)[0]
                    if flags & 0x80000400:
                        ranges.append((address, length, offset))
            for address, length, offset in ranges:
                assert offset % 4 == 0 and length % 4 == 0 and offset + length <= len(data)
                for p in range(offset, offset + length, 4):
                    word = struct.unpack_from('<I', data, p)[0]
                    if virtual_el2:
                        site = address + p - offset
                        if any(lo <= site < hi for _, lo, hi in excluded):
                            # Left native: the FIPS integrity self-test hashes
                            # the corecrypto kext text (panic "FIPS Kernel POST
                            # Failed" @corecrypto_kext.c:362 with it patched).
                            continue
                        if native_zva and (word & 0xffffffe0) == 0xd50b7420:
                            # DC ZVA executes natively under the shadow's alias
                            # rights; the kernel zeroes pages one line at a time.
                            continue
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
    ap.add_argument('--ledger-in', type=Path,
                    help='Existing ledger to extend; its indices are preserved')
    ap.add_argument('--segments', action='store_true',
                    help='Scan whole __TEXT_EXEC segments (Mach-O fileset kernelcache)')
    ap.add_argument('--native-zva', action='store_true',
                    help='Leave DC ZVA native (kernelcache); SPTM keeps the emulated path')
    ap.add_argument('--exclude-kext', action='append', default=[],
                    help='Leave fileset entries whose name contains this unpatched')
    a = ap.parse_args()
    data = a.input.read_bytes()
    if a.virtual_el2 != bool(a.ledger) or (a.virtual_el2 and a.vbar_compat):
        ap.error('--virtual-el2 requires --ledger and excludes --vbar-compat')
    ledger_in = []
    if a.ledger_in:
        raw = a.ledger_in.read_bytes()
        assert raw[:4] == b'DVEL'
        count = struct.unpack_from('<I', raw, 4)[0]
        assert len(raw) == 8 + 4 * count
        ledger_in = list(struct.unpack_from('<' + str(count) + 'I', raw, 8))
    result, records = rewrite(data, a.vbar_compat, a.virtual_el2, ledger_in, a.segments,
                              a.native_zva, a.exclude_kext)
    ledger_info = {}
    if a.virtual_el2:
        words = {i: w for i, w in enumerate(ledger_in)}
        words.update({r['index']: int(r['original'], 16) for r in records})
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
