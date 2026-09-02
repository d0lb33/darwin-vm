#!/usr/bin/env python3
"""Find exact ADRP+ADD materialisations of a VA in firmware/bootkc.

kxref.py only matches the 4 KB page an ADRP names, which for a string table is
hundreds of false hits.  This pairs each ADRP with the following ADD on the
same register and prints only the sites that build the exact address.

    tools/re/kaddr.py 0xfffffff007992778 [--lo VA --hi VA] [firmware/bootkc]
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
    ap.add_argument("--window", type=int, default=8)
    a = ap.parse_args()

    with open(a.kc, "rb") as f:
        data = f.read()
    lo = a.lo or BASE
    hi = a.hi or (BASE + len(data))
    text = data[lo - BASE:hi - BASE]
    words = struct.unpack("<%dI" % (len(text) // 4), text[: len(text) // 4 * 4])

    for i, w in enumerate(words):
        if (w & 0x9F000000) != 0x90000000:
            continue
        va = lo + i * 4
        rd = w & 0x1F
        immlo = (w >> 29) & 3
        immhi = (w >> 5) & 0x7FFFF
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (va & ~0xFFF) + imm * 0x1000
        if page != (a.target & ~0xFFF):
            continue
        for j in range(i + 1, min(i + 1 + a.window, len(words))):
            v = words[j]
            # ADD Xd, Xn, #imm12 (no shift)
            if (v & 0xFFC00000) == 0x91000000 and ((v >> 5) & 0x1F) == rd:
                if page + ((v >> 10) & 0xFFF) == a.target:
                    print("0x%x  adrp/add -> 0x%x" % (va, a.target))
                break
            # LDR Xt, [Xn, #imm12*8]
            if (v & 0xFFC00000) == 0xF9400000 and ((v >> 5) & 0x1F) == rd:
                if page + (((v >> 10) & 0xFFF) * 8) == a.target:
                    print("0x%x  adrp/ldr -> [0x%x]" % (va, a.target))
                break
            # LDR Wt, [Xn, #imm12*4]
            if (v & 0xFFC00000) == 0xB9400000 and ((v >> 5) & 0x1F) == rd:
                if page + (((v >> 10) & 0xFFF) * 4) == a.target:
                    print("0x%x  adrp/ldr w -> [0x%x]" % (va, a.target))
                break


if __name__ == "__main__":
    main()
