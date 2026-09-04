#!/usr/bin/env python3
"""kthread_peek.py - read a guest kernel `thread` struct from a frozen guest and
name every kernel-text pointer in it (continuation, wait event, etc.).

The thread_t is the `tpidr_el1` value lldb callbacks record for a hit.  The
guest must be frozen with its gdbstub listening (probe.sh --keep after a
setup_gate_probe.sh run).  Kernel PCs are attributed with kc_text_map.py.

    tools/re/kthread_peek.py 0xffffffe2ba3fb830 [--port 1234] [--size 0x800]
"""
import argparse
import os
import subprocess
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from kc_text_map import load_map, attribute  # noqa: E402

KTEXT_LO = 0xfffffff020000000   # runtime kernel/kext text lives above the +0x20000000 slide
KTEXT_HI = 0xfffffff02f000000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("thread", type=lambda s: int(s, 0))
    ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--size", type=lambda s: int(s, 0), default=0x800)
    ap.add_argument("--bootkc", default="firmware/bootkc")
    a = ap.parse_args()
    out = tempfile.NamedTemporaryFile(delete=False, suffix=".bin").name
    cmd = tempfile.NamedTemporaryFile("w", delete=False, suffix=".lldb")
    cmd.write("gdb-remote %d\nmemory read --force --binary --outfile %s 0x%x 0x%x\nquit\n" %
              (a.port, out, a.thread, a.thread + a.size))
    cmd.close()
    subprocess.run(["lldb", "-b", "-s", cmd.name], capture_output=True, text=True)
    data = open(out, "rb").read()
    if len(data) < 16:
        sys.exit("read failed (%d bytes); is the guest frozen with the gdbstub free?" % len(data))
    ranges = load_map(a.bootkc)
    print("thread 0x%x: %d bytes" % (a.thread, len(data)))
    for off in range(0, len(data) - 7, 8):
        v = struct.unpack_from("<Q", data, off)[0]
        if KTEXT_LO <= v < KTEXT_HI:
            name, foff = attribute(ranges, v - 0x20000000)
            print("  +0x%03x = 0x%x  %s+0x%x" % (off, v, name, foff))
        elif 0xffffffe000000000 <= v < 0xfffffff000000000 and off < 0x200:
            print("  +0x%03x = 0x%x  (kernel data ptr)" % (off, v))


if __name__ == "__main__":
    main()
