#!/usr/bin/env python3
"""Decode an array of chained-fixup pointers out of firmware/bootkc.

A kernelcache __DATA_CONST pointer is not stored as a VA: it is a packed
dyld chained-fixup entry whose low 32 bits are the *file offset* of the
target.  Adding the flat 0xfffffff007004000 base recovers the VA -- the same
relationship kdis.py uses, spot-checked in docs/re/iomfb-link.md (the
__auth_got slot at 0xfffffff00808f7c0 decodes to link_rpc_lookup at
0xfffffff00a0d0538, which the D400 switch independently confirms).

    tools/re/kptr.py 0xfffffff008089f48 8 [firmware/bootkc]
"""
import struct
import sys

BASE = 0xFFFFFFF007004000


def main():
    va = int(sys.argv[1], 0)
    n = int(sys.argv[2], 0)
    kc = sys.argv[3] if len(sys.argv) > 3 else "firmware/bootkc"
    d = open(kc, "rb").read()
    for i in range(n):
        a = va + i * 8
        raw = struct.unpack("<Q", d[a - BASE:a - BASE + 8])[0]
        low = raw & 0xFFFFFFFF
        print("[%2d] 0x%x raw=0x%016x -> 0x%x" % (i, a, raw, BASE + low))


if __name__ == "__main__":
    main()
