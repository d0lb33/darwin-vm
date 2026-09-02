#!/usr/bin/env python3
"""Disassemble a VA range out of firmware/bootkc.

The kernelcache is an MH_FILESET; for the ranges we care about (the
IOMobileGraphicsFamily-DCP __TEXT_EXEC, 0xfffffff00a0b2080-0xfffffff00a0dc230)
the mapping is the flat

    file_offset = VA - 0xfffffff007004000

documented and spot-checked at link_send_message's prologue in
docs/re/iomfb-link.md.

    tools/re/kdis.py 0xfffffff00a0d05ac 0xfffffff00a0d0680 [firmware/bootkc]
"""
import sys
import capstone

BASE = 0xFFFFFFF007004000


def main():
    lo = int(sys.argv[1], 0)
    hi = int(sys.argv[2], 0)
    kc = sys.argv[3] if len(sys.argv) > 3 else "firmware/bootkc"
    with open(kc, "rb") as f:
        f.seek(lo - BASE)
        data = f.read(hi - lo)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    for i in md.disasm(data, lo):
        print("0x%x  %-8s %s" % (i.address, i.mnemonic, i.op_str))


if __name__ == "__main__":
    main()
