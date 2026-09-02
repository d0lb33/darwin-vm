#!/usr/bin/env python3
"""bplist_carve.py - recover binary property lists out of a guest RAM dump.

Why: on this machine /private/var is a tmpfs, so ReportCrash's output never
reaches a disk the host can read - but it is still in guest RAM, and it is a
*binary* plist, not the JSON that `.ips` files use on disk. Searching the dump
for JSON keys finds nothing; searching for "bplist00" and parsing finds the
report.

A binary plist ends in a 32-byte trailer: 5 unused bytes, sortVersion,
offsetIntSize, objectRefSize, numObjects(8), topObject(8), offsetTableOffset(8),
all big-endian. That is enough structure to find the end of an object whose
start we already know, which is what carve() does - we cannot simply hand
plistlib a slice, because plistlib reads the trailer from the last 32 bytes of
whatever it is given.

usage:
    bplist_carve.py <dump-file> [--near OFFSET] [--window BYTES] [--max N]
                                [--grep TEXT]
"""
import argparse
import io
import plistlib
import struct
import sys

MAGIC = b"bplist00"


def try_end(buf, start, end):
    """Is there a plausible trailer in buf[end-32:end] for a plist at start?"""
    if end - start < 40:
        return False
    t = buf[end - 32:end]
    if t[:5] != b"\0\0\0\0\0":
        return False
    off_size, ref_size = t[6], t[7]
    if not (1 <= off_size <= 8) or not (1 <= ref_size <= 8):
        return False
    num, top, table = struct.unpack(">QQQ", t[8:32])
    if num == 0 or num > 1 << 22 or top >= num:
        return False
    if table < 8 or start + table + num * off_size != end - 32:
        return False
    return True


def carve(buf, start, window):
    limit = min(len(buf), start + window)
    # The trailer's offset table sits immediately before it, so walk candidate
    # ends. Step 1 byte: plists here are small enough that this is cheap.
    for end in range(start + 40, limit):
        if try_end(buf, start, end):
            try:
                return plistlib.load(io.BytesIO(buf[start:end]), fmt=plistlib.FMT_BINARY), end - start
            except Exception:
                continue
    return None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--near", type=lambda s: int(s, 0), default=None,
                    help="only consider plists starting within --window before this offset")
    ap.add_argument("--window", type=lambda s: int(s, 0), default=1 << 22)
    ap.add_argument("--max", type=int, default=5)
    ap.add_argument("--grep", default=None, help="only print plists whose repr contains this")
    a = ap.parse_args()

    with open(a.dump, "rb") as f:
        if a.near is not None:
            lo = max(0, a.near - a.window)
            f.seek(lo)
            buf = f.read(a.window * 2)
            base = lo
        else:
            buf = f.read()
            base = 0

    found = 0
    pos = 0
    while found < a.max:
        k = buf.find(MAGIC, pos)
        if k < 0:
            break
        pos = k + 1
        obj, n = carve(buf, k, a.window)
        if obj is None:
            continue
        text = repr(obj)
        if a.grep and a.grep not in text:
            continue
        print("=== bplist at 0x%x, %d bytes ===" % (base + k, n))
        print(text[:20000])
        print()
        found += 1
    print("plists: %d" % found, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
