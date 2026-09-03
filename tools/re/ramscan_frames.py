#!/usr/bin/env python3
"""Find every kernel stack that references a given set of kexts, by scanning guest RAM.

Kernel stacks are one 16 KiB page each, so a physical scan needs no VA translation:
dump RAM in chunks with the monitor's pmemsave, look for 8-byte-aligned words whose
low 40 bits fall inside a kext's slid text range (pacibsp signs the high bits of saved
LRs, hence the mask), and group the hits per 16 KiB physical page.  Pages inside the
kernelcache itself (vtables) are reported too; tell them apart by their density.

usage: ramscan_frames.py SOCK OUT [--ram-base 0x10000000000 --ram-size 0x300000000]
       [--chunk 0x40000000] [--kext RTBuddy --kext AppleDCP ...] [--workdir DIR]
The guest must be stopped (this script stops it).  Output: OUT text file.
"""
import argparse, mmap, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kc_text_map import load_map, attribute  # noqa: E402
import kmem  # noqa: E402
from ramscan_stall import frame_patterns  # noqa: E402

KSLIDE = 0x20000000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock"); ap.add_argument("out")
    ap.add_argument("--ram-base", type=lambda s: int(s, 0), default=0x10000000000)
    ap.add_argument("--ram-size", type=lambda s: int(s, 0), default=0x300000000)
    ap.add_argument("--chunk", type=lambda s: int(s, 0), default=0x40000000)
    ap.add_argument("--kext", action="append", default=[])
    ap.add_argument("--workdir", default=os.environ.get("SCRATCH", "/tmp/dvm/ramscan"))
    ap.add_argument("--bootkc", default="firmware/bootkc")
    a = ap.parse_args()
    ranges = load_map(a.bootkc)
    try:
        wanted, pats = frame_patterns(ranges, a.kext)
    except ValueError as error:
        sys.exit(str(error))
    os.makedirs(a.workdir, exist_ok=True)
    for lo, hi, name in wanted:
        slo, shi = lo + KSLIDE, hi + KSLIDE
        print("kext %s slid 0x%x-0x%x" % (name, slo, shi))
    kmem.ensure_kernel(a.sock)
    pages = {}
    t0 = time.time()
    for off in range(0, a.ram_size, a.chunk):
        size = min(a.chunk, a.ram_size - off)
        f = os.path.join(a.workdir, "chunk-%d.bin" % os.getpid())
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass
        # the size is parsed as an expression, so an unquoted /path reads as a division
        kmem.hmp(a.sock, "pmemsave 0x%x 0x%x \"%s\"" % (a.ram_base + off, size, f), timeout=600)
        if not os.path.exists(f) or os.path.getsize(f) != size:
            sys.exit("pmemsave failed at 0x%x" % (a.ram_base + off))
        with open(f, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            for pat, slo, shi, name in pats:
                for m in pat.finditer(mm):
                    p = m.start()
                    if p & 7:
                        continue
                    w = int.from_bytes(mm[p:p + 8], "little")
                    v = (w & 0xffffffffff) | 0xfffffff000000000
                    if slo <= v < shi:
                        pa = a.ram_base + off + p
                        pages.setdefault(pa & ~0x3fff, []).append((pa & 0x3fff, v))
            mm.close()
        os.unlink(f)
        print("scanned 0x%x (%d pages so far, %.0fs)" % (a.ram_base + off, len(pages), time.time() - t0), flush=True)
    with open(a.out, "w") as o:
        for pg in sorted(pages):
            hits = sorted(set(pages[pg]))
            o.write("page 0x%x (%d hits)\n" % (pg, len(hits)))
            for off, v in hits:
                r = attribute(ranges, v - KSLIDE)
                o.write("  +0x%04x  0x%x  %s+0x%x\n" % (off, v, r[0].split(".")[-1] if r and r[0] else "?", r[1] if r and r[0] else 0))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
