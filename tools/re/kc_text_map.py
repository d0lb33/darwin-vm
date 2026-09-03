#!/usr/bin/env python3
"""kc_text_map.py - list every fileset entry's __TEXT_EXEC range in firmware/bootkc,
and optionally attribute unslid kernel PCs to their kext.

    tools/re/kc_text_map.py [bootkc]                 # print the map
    tools/re/kc_text_map.py --pc 0xfffffff02918e3c4  # runtime PC; slide 0x20000000 assumed
The runtime-to-unslid slide is the project-standard +0x20000000 (docs/re/iomfb-dseries.md).
"""
import struct
import sys

BASE = 0xFFFFFFF007004000
SLIDE = 0x20000000


def load_map(path):
    with open(path, "rb") as f:
        data = f.read()
    ncmds = struct.unpack_from("<I", data, 16)[0]
    off = 32
    entries = []
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, off)
        if cmd == 0x80000035:  # LC_FILESET_ENTRY
            vmaddr, fileoff, name_off = struct.unpack_from("<QQI", data, off + 8)
            name = data[off + name_off:data.index(b"\0", off + name_off)].decode()
            entries.append((name, vmaddr, fileoff))
        off += size
    ranges = []
    for name, vmaddr, fileoff in entries:
        n = struct.unpack_from("<I", data, fileoff + 16)[0]
        o = fileoff + 32
        for _ in range(n):
            cmd, size = struct.unpack_from("<II", data, o)
            if cmd == 0x19:
                segname = data[o + 8:o + 24].split(b"\0", 1)[0].decode()
                va, vsize = struct.unpack_from("<QQ", data, o + 24)
                if segname == "__TEXT_EXEC" and vsize:
                    ranges.append((va, va + vsize, name))
            o += size
    ranges.sort()
    return ranges


def attribute(ranges, unslid):
    for lo, hi, name in ranges:
        if lo <= unslid < hi:
            return name, unslid - lo
    return None, 0


def main():
    args = sys.argv[1:]
    path = "firmware/bootkc"
    pcs = []
    i = 0
    while i < len(args):
        if args[i] == "--pc":
            pcs.append(int(args[i + 1], 0)); i += 2
        else:
            path = args[i]; i += 1
    ranges = load_map(path)
    if not pcs:
        for lo, hi, name in ranges:
            print("0x%x-0x%x %s" % (lo, hi, name))
        return
    for pc in pcs:
        unslid = pc - SLIDE if pc >= 0xfffffff020000000 else pc
        name, off = attribute(ranges, unslid)
        print("0x%x -> unslid 0x%x %s+0x%x" % (pc, unslid, name, off))


if __name__ == "__main__":
    main()
