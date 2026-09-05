#!/usr/bin/env python3
"""Read a paused guest's t8110 DART mapping through HMP, without host LLDB.

Uses the same 16K PTE and TTBR geometry documented in darwin_dart.c.
Only reads registers and RAM. Outputs the payload plus a page-walk witness.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--monitor', type=Path, required=True)
    for name in ('mmio', 'sid', 'dva', 'size'):
        ap.add_argument('--' + name, type=lambda x: int(x, 0), required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    if not 0 <= a.sid < 256 or not 0 < a.size <= 0x100000:
        ap.error('invalid stream or read length')
    hmp = HMP(a.monitor)
    if 'paused' not in hmp.command('info status'):
        raise RuntimeError('pause the guest before reading DART state')

    def read(address, size):
        words = (size + 3) // 4
        text = hmp.command('xp/%dwx 0x%x' % (words, address))
        values = re.findall(r'(?<![\da-fA-F])0x([\da-fA-F]{8})(?![\da-fA-F])', text)
        if len(values) != words:
            raise RuntimeError('unexpected physical read: ' + text)
        return b''.join(int(x, 16).to_bytes(4, 'little') for x in values)[:size]

    tcr = int.from_bytes(read(a.mmio + 0x1000 + a.sid * 4, 4), 'little')
    ttbr = int.from_bytes(read(a.mmio + 0x1400 + a.sid * 4, 4), 'little')
    if not tcr & 1 or tcr & 2 or not ttbr & 1:
        raise RuntimeError('requires an enabled translated t8110 stream')
    levels = 4 if tcr & 8 else 3
    va_bits = 14 + 11 * (levels - 1)
    output, walks = bytearray(), []
    while len(output) < a.size:
        dva = (a.dva + len(output)) & ((1 << va_bits) - 1)
        table = (ttbr >> 2) << 14
        entries = []
        for level in range(levels - 2, -1, -1):
            address = table + ((dva >> (14 + 11 * level)) & 0x7ff) * 8
            pte = int.from_bytes(read(address, 8), 'little')
            entries.append({'level': level, 'address': hex(address), 'pte': hex(pte)})
            if not pte & 1:
                raise RuntimeError('unmapped DVA: ' + json.dumps(entries))
            table = ((pte >> 10) & ((1 << 28) - 1)) << 14
        pa = table | (dva & 0x3fff)
        count = min(a.size - len(output), 0x4000 - (dva & 0x3fff))
        output.extend(read(pa, count))
        walks.append({'dva': hex(a.dva + len(output) - count), 'pa': hex(pa),
                      'bytes': count, 'entries': entries})
    a.out.write_bytes(output)
    report = {'mmio': hex(a.mmio), 'sid': a.sid, 'tcr': hex(tcr),
              'ttbr': hex(ttbr), 'walks': walks, 'bytes': len(output),
              'sha256': hashlib.sha256(output).hexdigest()}
    a.out.with_suffix(a.out.suffix + '.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
