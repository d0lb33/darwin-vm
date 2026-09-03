#!/usr/bin/env python3
"""Summarize an lldb callback log written by setup_gate_callbacks.py /
sb_setup_path_callbacks.py: hits per label, per process, with the register
values that matter, in hit order.

    python3 tools/re/lldb_hits_summary.py /tmp/dvm/<TAG>.lldb.log [--full]
"""
import re
import sys
from collections import Counter, OrderedDict

path = sys.argv[1]
full = "--full" in sys.argv
hits = []
cur = None
for line in open(path, errors="replace"):
    m = re.match(r"=== (\S+) hit=(\d+)/(\d+)(?: t=([\d.]+))? ===", line)
    if m:
        cur = {"label": m.group(1), "n": int(m.group(2)), "limit": int(m.group(3)),
               "t": float(m.group(4) or 0), "progname": "?", "regs": {}, "fields": {}, "lr_static": "?"}
        hits.append(cur)
        continue
    if cur is None:
        continue
    m = re.match(r"progname=(\S+) breakpoint=(\d+) thread=(0x[0-9a-f]+)", line)
    if m:
        cur["progname"], cur["thread"] = m.group(1), m.group(3)
        continue
    if line.startswith("registers "):
        for kv in line.split()[1:]:
            k, _, v = kv.partition("=")
            cur["regs"][k] = v
        continue
    m = re.match(r"lr_static=(\S+)", line)
    if m:
        cur["lr_static"] = m.group(1)
        continue
    m = re.match(r"field (\S+)@0x[0-9a-f]+=(\S+)", line)
    if m:
        cur["fields"][m.group(1)] = m.group(2)

proofs = sum(1 for l in open(path, errors="replace") if "COMMAND_LIST_PROOF" in l)
print("file=%s proofs=%d hits=%d" % (path, proofs, len(hits)))
by_label = OrderedDict()
for h in hits:
    by_label.setdefault(h["label"], []).append(h)
print("\n%-36s %5s  %s" % ("label", "hits", "processes"))
for label, hs in by_label.items():
    procs = Counter(h["progname"] for h in hs)
    print("%-36s %5d  %s" % (label, len(hs), ", ".join("%s x%d" % kv for kv in procs.most_common())))

interesting = ("w0", "x0", "x2", "w21", "x21")
print("\nordered hits (label, process, thread, lr_static, key registers, fields):")
t0 = hits[0]["t"] if hits and hits[0]["t"] else 0
for h in hits if full else hits[:400]:
    regs = " ".join("%s=%s" % (k, h["regs"][k]) for k in interesting if k in h["regs"])
    fields = " ".join("%s=%s" % kv for kv in h["fields"].items())
    print("%7.1fs %-34s %-22s %s lr=%s %s %s" % (
        (h["t"] - t0) if h["t"] else 0, h["label"], h["progname"], h.get("thread", "?"),
        h["lr_static"], regs, fields))
if not full and len(hits) > 400:
    print("... %d more (use --full)" % (len(hits) - 400))
