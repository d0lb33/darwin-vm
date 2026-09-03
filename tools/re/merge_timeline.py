#!/usr/bin/env python3
"""merge_timeline.py - one host-time timeline from an lldb callback log, QEMU's
stderr (the DCP RPC trace) and the guest serial log.

The two logs carry no timestamps; tools/re/stamp_growth.sh records their line
count once a second, and each line is placed at the stamp where it appeared.

    tools/re/merge_timeline.py TAG [--from SECS] [--to SECS] [--grep REGEX]
reads /tmp/dvm/TAG.lldb.log, /tmp/dvm/probe/TAG.{stderr,serial}.log and
/tmp/dvm/TAG.{stderr,serial}.growth (or ~/dvm-artifacts/probe-logs copies).
"""
import argparse
import os
import re
import sys

ROOTS = ["/tmp/dvm", os.path.expanduser("~/dvm-artifacts/probe-logs")]


def find(name):
    for r in ROOTS:
        for sub in ("", "probe"):
            p = os.path.join(r, sub, name)
            if os.path.exists(p):
                return p
    return None


def load_growth(path):
    stamps = []
    for line in open(path):
        parts = line.split()
        if len(parts) == 2:
            stamps.append((float(parts[0]), int(parts[1])))
    return stamps


def line_times(growth, nlines):
    """Assign each line index the first stamp whose count covers it."""
    times = [None] * nlines
    prev = 0
    for t, n in growth:
        for i in range(prev, min(n, nlines)):
            times[i] = t
        prev = max(prev, n)
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--from", dest="t_from", type=float, default=0)
    ap.add_argument("--to", dest="t_to", type=float, default=1e9)
    ap.add_argument("--grep", default=r"TRACE AP->DCP|IOP -> AP|callback|^=== |AppleCLCD2|IOMFB|panic\(cpu|critical process")
    a = ap.parse_args()
    events = []
    lldb = find(a.tag + ".lldb.log")
    t0 = None
    if lldb:
        cur = None
        for line in open(lldb, errors="replace"):
            m = re.match(r"=== (\S+) hit=\d+/\d+ t=([\d.]+)", line)
            if m:
                cur = [float(m.group(2)), "lldb", m.group(1), ""]
                events.append(cur)
                continue
            if cur and line.startswith("progname="):
                cur[3] = line.split()[0]
    for kind in ("stderr", "serial"):
        log = find("%s.%s.log" % (a.tag, kind))
        growth = find("%s.%s.growth" % (a.tag, kind))
        if not log or not growth:
            continue
        lines = open(log, errors="replace").read().split("\n")
        times = line_times(load_growth(growth), len(lines))
        pat = re.compile(a.grep)
        for i, l in enumerate(lines):
            if times[i] and pat.search(l):
                events.append([times[i], kind, l.strip()[:150], ""])
    events.sort(key=lambda e: e[0])
    if not events:
        sys.exit("no events")
    t0 = events[0][0]
    for t, kind, text, extra in events:
        rel = t - t0
        if a.t_from <= rel <= a.t_to:
            print("%7.1f %-6s %s %s" % (rel, kind, text, extra))


if __name__ == "__main__":
    main()
