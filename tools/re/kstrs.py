#!/usr/bin/env python3
"""List the C string literals a VA range references.

Both DCP kexts are stripped (nsyms=0), so the only way to name a function is
by the strings it prints.  This disassembles a range, resolves every ADRP+ADD
pair, and prints the ones that land on a printable NUL-terminated string.

    tools/re/kstrs.py 0xfffffff00917a634 0xfffffff00917a688 [firmware/bootkc]
"""
import sys
import capstone

BASE = 0xFFFFFFF007004000


def cstr(d, va, maxlen=160):
    o = va - BASE
    if o < 0 or o >= len(d):
        return None
    end = d.find(b"\0", o, o + maxlen)
    if end < 0:
        return None
    s = d[o:end]
    if len(s) < 4:
        return None
    if not all(0x20 <= c < 0x7F or c in (9, 10) for c in s):
        return None
    return s.decode("latin1")


def main():
    lo = int(sys.argv[1], 0)
    hi = int(sys.argv[2], 0)
    kc = sys.argv[3] if len(sys.argv) > 3 else "firmware/bootkc"
    d = open(kc, "rb").read()
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    pages = {}
    for i in md.disasm(d[lo - BASE:hi - BASE], lo):
        if i.mnemonic == "adrp":
            rd, page = i.op_str.split(",")
            pages[rd.strip()] = int(page.strip()[1:], 0)
        elif i.mnemonic == "add":
            p = [o.strip() for o in i.op_str.split(",")]
            if len(p) == 3 and p[2].startswith("#") and p[1] in pages:
                va = pages[p[1]] + int(p[2][1:], 0)
                s = cstr(d, va)
                if s:
                    print("0x%x  -> 0x%x  %r" % (i.address, va, s))
        elif i.mnemonic in ("bl", "b"):
            print("0x%x  %s %s" % (i.address, i.mnemonic, i.op_str))


if __name__ == "__main__":
    main()
