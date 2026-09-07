#!/usr/bin/env python3
"""List the strings a kernelcache code range materialises with ADRP+ADD.

The inverse of kaddr.py: walk [lo, hi) in firmware/bootkc, pair each ADRP
with the next ADD on the same register, and print the target when it points
at printable text. Useful to see which device-tree property names a driver
function looks up before reading its disassembly.

    tools/re/kstrrefs.py 0xfffffff00975dc00 0xfffffff00975e700 [firmware/bootkc]
"""
import struct
import sys

BASE = 0xFFFFFFF007004000


def main():
    lo, hi = int(sys.argv[1], 0), int(sys.argv[2], 0)
    path = sys.argv[3] if len(sys.argv) > 3 else "firmware/bootkc"
    data = open(path, "rb").read()
    words = struct.unpack_from("<%dI" % ((hi - lo) // 4), data, lo - BASE)
    pending = {}
    for i, w in enumerate(words):
        pc = lo + 4 * i
        if (w & 0x9f000000) == 0x90000000:  # ADRP
            rd = w & 31
            immlo = (w >> 29) & 3
            immhi = (w >> 5) & 0x7ffff
            imm = ((immhi << 2) | immlo)
            if imm & (1 << 20):
                imm -= 1 << 21
            pending[rd] = (pc & ~0xfff) + (imm << 12)
        elif (w & 0xff800000) == 0x91000000:  # ADD (immediate, 64-bit)
            rn, rd = (w >> 5) & 31, w & 31
            imm = (w >> 10) & 0xfff
            if (w >> 22) & 1:
                imm <<= 12
            if rn in pending:
                target = pending[rn] + imm
                off = target - BASE
                if 0 <= off < len(data):
                    s = data[off:off + 96].split(b"\0")[0]
                    if len(s) >= 3 and all(32 <= c < 127 for c in s):
                        print("0x%x  x%d -> 0x%x  %r" % (pc, rd, target, s.decode()))
                pending.pop(rn, None)
        elif (w & 0xffffffff) >> 26 == 0x25:  # BL: registers survive, keep pending
            pass


if __name__ == "__main__":
    main()
