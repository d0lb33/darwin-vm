#!/usr/bin/env python3
"""Name the thread groups of runnable threads from a ramscan_threads.py listing.

Reads each distinct thread_group pointer's struct through the live monitor
(kmem.read_words; the vCPU is stopped in kernel context for the reads and
resumed afterwards).  The group's 32-byte name follows its id, as observed in
docs/re/boot-idle.md ("group id 10, the 32-byte name SpringBoard, refcount 5,
flags 0x102 occur consecutively"); we locate the name by scanning the first
0x80 bytes for the longest printable run rather than trusting a fixed offset.

    tools/re/thread_group_names.py SOCK threads.txt [--state-mask 0x85] [--want 0x4]

By default it reports groups of threads whose state has TH_RUN (0x4) set and
none of TH_WAIT (0x1), TH_IDLE (0x80) or TH_SUSP-less waits, i.e. threads that
are running or on a run queue.  TH_* bits: osfmk/kern/thread.h.
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmem  # noqa: E402


def group_name(sock, addr):
    words = kmem.read_words(sock, addr, 16)
    raw = b"".join(w.to_bytes(8, "little") for w in words)
    best = b""
    cur = b""
    for b in raw:
        if 32 <= b < 127:
            cur += bytes([b])
        else:
            if len(cur) > len(best):
                best = cur
            cur = b""
    if len(cur) > len(best):
        best = cur
    return best.decode() or "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock")
    ap.add_argument("threads")
    ap.add_argument("--want", type=lambda s: int(s, 0), default=0x4)
    ap.add_argument("--mask", type=lambda s: int(s, 0), default=0x81)
    a = ap.parse_args()
    rows = [l.split() for l in open(a.threads) if not l.startswith("#")]
    selected = [r for r in rows
                if (int(r[5], 16) & 0xff) & a.want and not (int(r[5], 16) & 0xff) & a.mask]
    groups = Counter(int(r[7], 16) for r in selected)
    all_groups = Counter(int(r[7], 16) for r in rows)
    kmem.ensure_kernel(a.sock)
    try:
        names = {}
        for g in set(groups) | set(all_groups):
            if g < 0xffffffe000000000:
                names[g] = "?"
                continue
            try:
                names[g] = group_name(a.sock, g)
            except SystemExit as e:
                names[g] = "unreadable"
    finally:
        kmem.hmp(a.sock, "cont")
    print("%d selected threads (state & 0x%x, not 0x%x) in %d groups; %d threads total" %
          (len(selected), a.want, a.mask, len(groups), len(rows)))
    for g, n in groups.most_common():
        print("%4d runnable / %4d total  %-32s 0x%x" % (n, all_groups[g], names[g], g))


if __name__ == "__main__":
    main()
