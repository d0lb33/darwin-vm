#!/usr/bin/env python3
"""Enumerate the DCP link-protocol FourCC method names in a kernelcache.

The names ('A401', 'D563', ...) are u32 immediates, never strings: the
compiler materialises them with a MOVZ Wd,#lo / MOVK Wd,#hi,LSL 16 pair, so
grepping bootkc for "A401" finds nothing.  This scans every 4-byte aligned
word for that pair and prints the ones whose big-endian bytes look like an
Apple DCP method name (letter A-Z followed by three digits).

    tools/re/fourcc_scan.py [firmware/bootkc] [--lo VA] [--hi VA] [--letters AD]

Default range is the IOMobileGraphicsFamily-DCP __TEXT_EXEC,
0xfffffff00a0b2080-0xfffffff00a0dc230 (docs/re/iomfb-link.md); pass --lo/--hi 0
for the whole file.  Address mapping is the flat file_offset = VA - BASE that
the same document establishes and spot-checks.

Re-run this against any new kernelcache before trusting the tables in
docs/re/iomfb-dseries.md.
"""
import argparse
import struct

BASE = 0xFFFFFFF007004000
DCP_TEXT_LO = 0xFFFFFFF00A0B2080
DCP_TEXT_HI = 0xFFFFFFF00A0DC230


def movz_w(word):
    """(rd, imm16) if word is MOVZ Wd,#imm16 (no shift), else None."""
    if (word & 0xFFE00000) != 0x52800000:
        return None
    return (word & 0x1F, (word >> 5) & 0xFFFF)


def movk_w_lsl16(word):
    """(rd, imm16) if word is MOVK Wd,#imm16,LSL 16, else None."""
    if (word & 0xFFE00000) != 0x72A00000:
        return None
    return (word & 0x1F, (word >> 5) & 0xFFFF)


def name_of(val):
    """Big-endian rendering, which is how the AP's own tracer prints it
    (0xa0ce4a8-0xa0ce4c4 unpacks the u32 byte-by-byte, MSB first)."""
    b = struct.pack(">I", val)
    return b.decode("latin1")


def plausible(name, letters):
    return (name[0] in letters and name[1].isdigit()
            and name[2].isdigit() and name[3].isdigit())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kc", nargs="?", default="firmware/bootkc")
    ap.add_argument("--lo", type=lambda s: int(s, 0), default=DCP_TEXT_LO)
    ap.add_argument("--hi", type=lambda s: int(s, 0), default=DCP_TEXT_HI)
    ap.add_argument("--letters", default="AD")
    ap.add_argument("--window", type=int, default=6,
                    help="how many instructions after the MOVZ a MOVK may appear")
    args = ap.parse_args()

    with open(args.kc, "rb") as f:
        data = f.read()

    lo, hi = args.lo, args.hi
    if lo == 0:
        lo, hi = BASE, BASE + len(data)
    off_lo, off_hi = lo - BASE, hi - BASE
    text = data[off_lo:off_hi]

    words = struct.unpack("<%dI" % (len(text) // 4), text[: len(text) // 4 * 4])
    hits = {}
    for i, w in enumerate(words):
        z = movz_w(w)
        if not z:
            continue
        rd, lo16 = z
        for j in range(i + 1, min(i + 1 + args.window, len(words))):
            k = movk_w_lsl16(words[j])
            if not k:
                continue
            if k[0] != rd:
                continue
            val = (k[1] << 16) | lo16
            nm = name_of(val)
            if plausible(nm, args.letters):
                hits.setdefault(nm, []).append(lo + i * 4)
            else:
                # A jump-table switch never materialises its base name: the
                # compiler builds -base and does `add w16, w1, w8; cmp w16,#n`
                # so the range check falls out of one subtraction.  0xbbcbcfd0
                # is -"D400".  Report those as "NAME+" to flag a range whose
                # members are only reachable through the table.
                nm2 = name_of((-val) & 0xFFFFFFFF)
                if plausible(nm2, args.letters):
                    hits.setdefault(nm2 + "+", []).append(lo + i * 4)
            break

    for nm in sorted(hits):
        addrs = " ".join("0x%x" % a for a in hits[nm])
        print("%-5s 0x%08x  %s"
              % (nm, struct.unpack(">I", nm[:4].encode())[0], addrs))
    print("# %d distinct names" % len(hits))


if __name__ == "__main__":
    main()
