#!/usr/bin/env python3
"""oskcdata.py - find XNU os_reason / crashinfo kcdata blobs in a guest RAM dump
and print why a process died.

This is the instrument that named the SpringBoard crash. When a process aborts,
XNU builds an `os_reason` as a kcdata buffer holding the namespace, the code and
- for anything that called abort_with_reason, which includes every _objc_fatal,
every dyld failure and every os_crash - a human-readable description. launchd
logs that at a level the serial console filters out, and ReportCrash writes its
.ips into a tmpfs that never reaches a disk the host can read. The kernel's copy
is the durable one.

Pair it with tools/snap_at_marker.sh (freeze the guest inside the failure
window) and tools/guest_memgrep.py (copy RAM out).

kcdata layout, XNU osfmk/kern/kern_cdata.h: a buffer is a chain of items, each
`u32 type, u32 size, u64 flags` followed by `size` bytes, starting with an item
whose type is the buffer magic and ending with KCDATA_TYPE_BUFFER_END. We scan
for the magic rather than following any pointer, because we have no symbols.

usage:
    oskcdata.py <dump-file-or-dir>...            # scan and print every blob
    oskcdata.py <file> --at 0x22f5d070           # parse one known offset
"""
import argparse
import os
import struct
import sys

MAGICS = {
    0x53A20900: "OS_REASON",
    0xDEB7CAF3: "CRASHINFO",
    0x59A25807: "STACKSHOT",
    0xDE17A59A: "DELTA_STACKSHOT",
}

ITEM = {
    0x00000000: "INVALID",
    0x00000001: "STRING_DESC",
    0x00000002: "UINT32_DESC",
    0x00000003: "UINT64_DESC",
    0xFFFFFFFF: "BUFFER_END",
    # bsd/sys/reason.h
    0x1001: "EXIT_REASON_SNAPSHOT",
    0x1002: "EXIT_REASON_USER_DESC",
    0x1003: "EXIT_REASON_USER_PAYLOAD",
    0x1004: "EXIT_REASON_CODESIGNING_INFO",
    0x1005: "EXIT_REASON_WORKLOOP_ID",
    0x1006: "EXIT_REASON_DISPATCH_QUEUE_NO",
    # task crashinfo range 0x800.., only the ones we have actually seen
    0x0847: "PROC_NAME",
}

# bsd/sys/reason.h OS_REASON_* . Only the low numbers are load-bearing here;
# the rest are carried so a future hit prints a name instead of a bare integer.
NAMESPACE = {
    0: "NONE", 1: "JETSAM", 2: "SIGNAL", 3: "CODESIGNING", 4: "HANGTRACER",
    5: "TEST", 6: "DYLD", 7: "LIBXPC", 8: "OBJC", 9: "EXEC", 10: "SPRINGBOARD",
    11: "TCC", 12: "REPORTCRASH", 13: "COREANIMATION", 14: "AGGREGATED",
    15: "RUNNINGBOARD", 16: "SKYWALK", 17: "SETTINGS", 18: "LIBSYSTEM",
    19: "FOUNDATION", 20: "WATCHDOG", 21: "METAL", 22: "WATCHKIT",
    23: "GUARD", 24: "ANALYTICS", 25: "SANDBOX", 26: "SECURITY",
}


def scan(path):
    """Yield (offset, magic-name) for every kcdata buffer start in the file."""
    pats = {struct.pack("<I", m): n for m, n in MAGICS.items()}
    with open(path, "rb") as f:
        prev, base = b"", 0
        while True:
            buf = f.read(1 << 26)
            if not buf:
                return
            data = prev + buf
            for p, n in pats.items():
                s = 0
                while True:
                    k = data.find(p, s)
                    if k < 0:
                        break
                    yield base - len(prev) + k, n
                    s = k + 1
            base += len(buf)
            prev = data[-8:]


def walk(buf, start, limit=512):
    """Walk kcdata items from the buffer-begin item at `start`."""
    pos = start
    for _ in range(limit):
        if pos + 16 > len(buf):
            return
        t, sz, fl = struct.unpack_from("<IIQ", buf, pos)
        if sz > (1 << 20):
            return
        yield t, sz, buf[pos + 16:pos + 16 + sz]
        if t == 0xFFFFFFFF:
            return
        pos = (pos + 16 + sz + 3) & ~3


def render(path, off, tag, out=sys.stdout):
    with open(path, "rb") as f:
        f.seek(off)
        buf = f.read(1 << 16)
    lines = []
    interesting = False
    for t, sz, data in walk(buf, 0):
        name = ITEM.get(t, "0x%x" % t)
        if t == 0x1001 and sz >= 20:
            ns, code, flags = struct.unpack_from("<IQQ", data, 0)
            lines.append("  %-26s namespace=%d (%s) code=0x%x flags=0x%x"
                         % (name, ns, NAMESPACE.get(ns, "?"), code, flags))
            interesting = True
        elif sz and all(c in (0, 9, 10) or 32 <= c < 127
                        for c in data[:min(sz, 64)]):
            txt = data.split(b"\0")[0].decode("ascii", "replace")
            txt = txt.replace("\n", " ")
            if txt:
                lines.append("  %-26s %r" % (name, txt[:500]))
                interesting = True
        if t == 0xFFFFFFFF or (t == 0 and sz == 0 and len(lines) > 2):
            break
    if not interesting:
        return False
    print("=== %s %s @ 0x%x ===" % (os.path.basename(path), tag, off), file=out)
    for l in lines:
        print(l, file=out)
    print(file=out)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--at", type=lambda s: int(s, 0), default=None)
    a = ap.parse_args()

    files = []
    for p in a.paths:
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))]
        else:
            files.append(p)

    if a.at is not None:
        render(files[0], a.at, "at")
        return 0

    n = 0
    for f in files:
        for off, tag in scan(f):
            if render(f, off, tag):
                n += 1
    print("blobs printed: %d" % n, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
