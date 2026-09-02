#!/usr/bin/env python3
"""guest_memgrep.py - pull a frozen guest's RAM out through the QEMU monitor and
search it for text.

Why this exists
---------------
iOS mounts /private/var as a tmpfs on this machine (dt_fixup.py -ephemeral-data),
so everything userspace writes there - ReportCrash's .ips reports, os_log's
staging files, daemon scratch - lives in *guest RAM* and never touches a disk we
can mount on the host. When a process dies and the only serial evidence is
launchd's one-line "rebooting due to critical process crashes", the actual crash
report is still sitting in guest physical memory. Freeze the guest and read it.

This is the read-only counterpart to tools/hmp.py: hmp.py's socket timeout is 20
seconds, which is far too short for a multi-gigabyte transfer, and its output
parsing is line-oriented. Here we stream `pmemsave` chunks to a file instead.

usage:
    guest_memgrep.py <monitor.sock> dump  <outfile> [--base HEX] [--size BYTES]
    guest_memgrep.py <monitor.sock> chunk <outdir> --base HEX --size BYTES --step BYTES

The filename MUST be quoted in the HMP command: the monitor evaluates the size
as an arithmetic expression and greedily eats the following "/tmp/..." as a
division, giving "invalid char 't' in expression".

`dump` writes one file; `chunk` writes one file per step so a long search can
start before the whole transfer finishes. The guest should already be stopped
(`hmp.py <sock> stop`) or the snapshot will be inconsistent.

The DRAM base is not hardcoded: pass it, or let the script read it from the
monitor's `info mtree`, because it comes from the device tree's /chosen
dram-base and differs between trees (darwin.c: info->dram_base).
"""
import argparse
import os
import re
import socket
import sys
import time


class Monitor:
    def __init__(self, path, timeout=3600):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(timeout)
        self.s.connect(path)
        self._read_prompt()

    def _read_prompt(self):
        buf = b""
        while not buf.rstrip().endswith(b"(qemu)"):
            chunk = self.s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors="replace")

    def cmd(self, text):
        self.s.sendall((text + "\n").encode())
        return self._read_prompt()


def find_dram(mon):
    """Read the guest DRAM base/size out of `info mtree` rather than assuming it."""
    out = mon.cmd("info mtree -f")
    best = None
    for line in out.splitlines():
        # e.g. "  0000000800000000-0000000fffffffff (prio 0, ram): dram"
        m = re.search(r"([0-9a-f]{8,16})-([0-9a-f]{8,16}) \(prio \d+, ram\): (\S+)", line)
        if not m:
            continue
        lo, hi, name = int(m.group(1), 16), int(m.group(2), 16), m.group(3)
        if "tag" in name:            # MTE tag RAM is a shadow, not guest DRAM
            continue
        size = hi - lo + 1
        if best is None or size > best[1]:
            best = (lo, size, name)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock")
    ap.add_argument("mode", choices=["dump", "chunk", "info"])
    ap.add_argument("out", nargs="?")
    ap.add_argument("--base", default=None, help="guest physical base, hex")
    ap.add_argument("--size", default=None, help="bytes to read (decimal or 0x hex)")
    ap.add_argument("--step", default=str(1 << 30), help="chunk size, default 1 GiB")
    a = ap.parse_args()

    mon = Monitor(a.sock)
    found = find_dram(mon)
    if a.mode == "info":
        print("dram:", found)
        return 0
    if not found:
        print("could not find guest DRAM in `info mtree`; pass --base/--size", file=sys.stderr)
        if not (a.base and a.size):
            return 1
    base = int(a.base, 16) if a.base else found[0]
    size = int(a.size, 0) if a.size else found[1]
    step = int(a.step, 0)

    if a.mode == "dump":
        # pmemsave appends nothing, so one call per chunk into separate files and
        # concatenate; QEMU has no "append" mode.
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "wb") as dst:
            off = 0
            while off < size:
                n = min(step, size - off)
                tmp = a.out + ".part"
                mon.cmd('pmemsave 0x%x %d "%s"' % (base + off, n, tmp))
                with open(tmp, "rb") as src:
                    while True:
                        b = src.read(1 << 22)
                        if not b:
                            break
                        dst.write(b)
                os.unlink(tmp)
                off += n
                print("  %5.1f%%  0x%012x" % (100.0 * off / size, base + off), flush=True)
        return 0

    if a.mode == "chunk":
        os.makedirs(a.out, exist_ok=True)
        off = 0
        while off < size:
            n = min(step, size - off)
            path = os.path.join(a.out, "%012x.bin" % (base + off))
            t0 = time.time()
            mon.cmd('pmemsave 0x%x %d "%s"' % (base + off, n, path))
            print("%s  %.1fs" % (path, time.time() - t0), flush=True)
            off += n
        return 0


if __name__ == "__main__":
    sys.exit(main())
