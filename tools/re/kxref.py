#!/usr/bin/env python3
"""Find BL/B and ADRP+ADD references to a VA inside firmware/bootkc.

    tools/re/kxref.py 0xfffffff00a0d0538 [--lo VA --hi VA] [firmware/bootkc]

Same flat mapping as tools/re/kdis.py (file_offset = VA - 0xfffffff007004000).
Default search range is the whole file, which takes a couple of seconds.
"""
import argparse
import struct

BASE = 0xFFFFFFF007004000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", type=lambda s: int(s, 0))
    ap.add_argument("kc", nargs="?", default="firmware/bootkc")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=0)
    a = ap.parse_args()

    with open(a.kc, "rb") as f:
        data = f.read()
    lo = a.lo or BASE
    hi = a.hi or (BASE + len(data))
    text = data[lo - BASE:hi - BASE]
    words = struct.unpack("<%dI" % (len(text) // 4), text[: len(text) // 4 * 4])

    tgt_page = a.target & ~0xFFF
    for i, w in enumerate(words):
        va = lo + i * 4
        # B (0x14000000) / BL (0x94000000), imm26 << 2, sign extended
        if (w & 0x7C000000) == 0x14000000:
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            if va + imm * 4 == a.target:
                print("0x%x  %s 0x%x" % (va, "bl" if w & 0x80000000 else "b",
                                         a.target))
        # ADRP Xd, page
        if (w & 0x9F000000) == 0x90000000:
            immlo = (w >> 29) & 3
            immhi = (w >> 5) & 0x7FFFF
            imm = (immhi << 2) | immlo
            if imm & (1 << 20):
                imm -= 1 << 21
            page = (va & ~0xFFF) + imm * 0x1000
            if page == tgt_page:
                print("0x%x  adrp x%d, 0x%x   (page of the target)"
                      % (va, w & 0x1F, page))


if __name__ == "__main__":
    main()
