#!/usr/bin/env python3
"""Enumerate XNU thread structures by scanning guest RAM for their constant words.

Every `struct thread` in this kernelcache carries 0x2010002030100000 at +0x1a0,
0xfeedfacefeedfad3 at +0x1d0 and 0x2020a52a302abae6 at +0x1d8 (observed on several
live threads; see docs/re/setup-launch-runtime.md).  Fields printed: +0x18 wait_event,
+0xe0 continuation, +0xf0 kernel_stack (a VA; the stack is one 16 KiB page).

usage: ramscan_threads.py SOCK OUT [--ram-base --ram-size --chunk --workdir]
"""
import argparse, mmap, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmem  # noqa: E402

SIG = (0x1a0, (0x2010002030100000).to_bytes(8, "little"))
CHECKS = ((0x1d0, 0xfeedfacefeedfad3), (0x1d8, 0x2020a52a302abae6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock"); ap.add_argument("out")
    ap.add_argument("--ram-base", type=lambda s: int(s, 0), default=0x10000000000)
    ap.add_argument("--ram-size", type=lambda s: int(s, 0), default=0x300000000)
    ap.add_argument("--chunk", type=lambda s: int(s, 0), default=0x40000000)
    ap.add_argument("--workdir", default="/tmp/dvm/ramscan")
    a = ap.parse_args()
    os.makedirs(a.workdir, exist_ok=True)
    kmem.ensure_kernel(a.sock)
    pat = re.compile(re.escape(SIG[1]))
    rows = []; t0 = time.time()
    for off in range(0, a.ram_size, a.chunk):
        size = min(a.chunk, a.ram_size - off)
        f = os.path.join(a.workdir, "tchunk-%d.bin" % os.getpid())
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass
        kmem.hmp(a.sock, "pmemsave 0x%x 0x%x \"%s\"" % (a.ram_base + off, size, f), timeout=600)
        if not os.path.exists(f) or os.path.getsize(f) != size:
            sys.exit("pmemsave failed at 0x%x" % (a.ram_base + off))
        with open(f, "rb") as fh:
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            for m in pat.finditer(mm):
                base = m.start() - SIG[0]
                if base < 0 or base & 7 or base + 0x200 > len(mm):
                    continue
                if all(int.from_bytes(mm[base + o:base + o + 8], "little") == v for o, v in CHECKS):
                    w = lambda o: int.from_bytes(mm[base + o:base + o + 8], "little")
                    rows.append((a.ram_base + off + base, w(0x18), w(0xe0), w(0xf0), w(0x10)))
            mm.close()
        os.unlink(f)
        print("scanned 0x%x: %d threads so far (%.0fs)" % (a.ram_base + off, len(rows), time.time() - t0), flush=True)
    with open(a.out, "w") as o:
        o.write("# thread_pa wait_event continuation kernel_stack +0x10\n")
        for r in rows:
            o.write("0x%x 0x%x 0x%x 0x%x 0x%x\n" % r)
    print("wrote", a.out, len(rows), "threads")


if __name__ == "__main__":
    main()
