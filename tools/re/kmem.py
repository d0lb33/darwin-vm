#!/usr/bin/env python3
"""Read guest kernel memory through the QEMU monitor while the vCPU is in kernel context.

The gdbstub/HMP translate virtual addresses through the *current* CPU context, so a
kernel VA is only readable once the vCPU is stopped at EL1 (PC >= 0xfffffff000000000).
`ensure_kernel` loops stop / info registers / cont until that holds and leaves the guest
stopped.  Everything else is plain `x/Ngx` parsing.

usage:
  kmem.py SOCK dump ADDR NWORDS              hexdump with kext attribution (tools/re/kc_text_map.py)
  kmem.py SOCK findlr STACK LR               find saved LR word on a 16 KiB kernel stack; print x19/x20/x29 slots
  kmem.py SOCK power-async STACK LR          follow iomfb_dcp_power_async from the wait frame (see docs/re/setup-launch-runtime.md)
"""
import os, re, socket, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kc_text_map import load_map, attribute  # noqa: E402

KSLIDE = 0x20000000
KTEXT_LO, KTEXT_HI = 0xfffffff020000000, 0xfffffff02f000000


def hmp(sock, cmd, timeout=20):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(timeout); s.connect(sock)
    def until_prompt():
        buf = b""
        while not buf.rstrip().endswith(b"(qemu)"):
            try:
                c = s.recv(65536)
            except socket.timeout:
                break
            if not c:
                break
            buf += c
        return buf.decode(errors="replace")
    until_prompt(); s.sendall((cmd + "\n").encode()); out = until_prompt(); s.close()
    out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out).replace("\r", "")
    # drop the echoed command line and the prompt; keep everything else verbatim
    return "\n".join(l for l in out.split("\n") if l.strip() != "(qemu)" and not l.strip().startswith(cmd))


def ensure_kernel(sock, tries=400):
    for i in range(tries):
        hmp(sock, "stop")
        m = re.search(r"PC=([0-9a-f]+)", hmp(sock, "info registers"))
        pc = int(m.group(1), 16) if m else 0
        if pc >= 0xfffffff000000000:
            return pc
        hmp(sock, "cont"); time.sleep(0.05)
    sys.exit("never caught the vCPU in kernel context")


def read_words(sock, addr, n):
    """x/Ngx in 8-word chunks; only lines shaped like a dump row are trusted (the monitor
    echoes the typed command with ANSI edits, and those echoes must never be parsed)."""
    words = []
    while n:
        k = min(n, 8)
        out = hmp(sock, "x/%dgx 0x%x" % (k, addr))
        vals = []
        for line in out.split("\n"):
            m = re.match(r"\s*([0-9a-f]{16}):((?:\s+0x[0-9a-f]{16})+)\s*$", line)
            if m and int(m.group(1), 16) == addr + 8 * len(vals):
                vals += [int(v, 16) for v in m.group(2).split()]
        if len(vals) < k:
            sys.exit("short read at 0x%x (%d/%d words): %r" % (addr, len(vals), k, out[-300:]))
        words += vals[:k]; addr += 8 * k; n -= k
    return words


def kptr(v):
    """strip PAC / tag bits from a kernel data pointer"""
    return (v & 0x0000ffffffffffff) | 0xffff000000000000 if v else 0


def annotate(ranges, v):
    if KTEXT_LO <= v < KTEXT_HI:
        n, o = attribute(ranges, v - KSLIDE); return "%s+0x%x" % (n, o)
    if 0xffffffe000000000 <= kptr(v) < 0xfffffff000000000 and v:
        return "kdata" + ("" if v == kptr(v) else " (pac-stripped 0x%x)" % kptr(v))
    return ""


def dump(sock, ranges, addr, n, label=""):
    ws = read_words(sock, addr, n)
    if label:
        print("== %s @0x%x" % (label, addr))
    for i, w in enumerate(ws):
        print("  +0x%03x  0x%016x  %s" % (8 * i, w, annotate(ranges, w)))
    return ws


def read_cstr(sock, addr, maxlen=64):
    ws = read_words(sock, addr, (maxlen + 7) // 8)
    b = b"".join(w.to_bytes(8, "little") for w in ws)
    return b.split(b"\0")[0].decode(errors="replace")


def os_string(sock, p):
    """OSString/OSSymbol: vptr, retainCount, flags:14/length:18 at +0xc, char* at +0x10 (PAC-signed)."""
    p = kptr(p); ws = read_words(sock, p, 3)
    length = (ws[1] >> 46) & 0x3ffff  # +0xc upper 18 bits of the u32 at +0xc -> bits 46.. of word 1
    return read_cstr(sock, kptr(ws[2]), min(max(length, 1), 64))


def main():
    if len(sys.argv) < 3:
        print(__doc__); return 2
    sock, cmd = sys.argv[1], sys.argv[2]
    ranges = load_map(os.environ.get("BOOTKC", "firmware/bootkc"))
    pc = ensure_kernel(sock); print("vCPU stopped in kernel at PC=0x%x" % pc)
    if cmd == "dump":
        dump(sock, ranges, int(sys.argv[3], 0), int(sys.argv[4], 0)); return 0
    stack, lr = int(sys.argv[3], 0), int(sys.argv[4], 0)
    ws = read_words(sock, stack, 0x4000 // 8)
    # pacibsp signs the saved x30 with SP as modifier, so only the low VA bits are stable
    M = 0xffffffffff
    hits = [i for i, w in enumerate(ws) if (w & M) == (lr & M)]
    print("LR 0x%x (low 40 bits) found at %s" % (lr, ["0x%x" % (stack + 8 * i) for i in hits]))
    for other in (0xfffffff02a0d4b74, 0xfffffff02a0d5b3c, 0xfffffff02a0b7b64):
        oh = ["0x%x" % (stack + 8 * i) for i, w in enumerate(ws) if (w & M) == (other & M)]
        print("  other return 0x%x at %s" % (other, oh))
    if not hits:
        return 1
    i = hits[0]
    # frame of the callee that saved this LR: stp x20,x19,[sp,-0x20]!; stp x29,x30,[sp,#0x10]
    x20, x19, x29 = ws[i - 3], ws[i - 2], ws[i - 1]
    print("  callee frame: x20=0x%x x19=0x%x x29=0x%x" % (x20, x19, x29))
    if cmd == "findlr":
        return 0
    obj = kptr(x19)
    o = dump(sock, ranges, obj, 0x12, "iomfb_dcp_power_async")
    owner = kptr(o[0x28 // 8]); print("owner (+0x28) = 0x%x" % owner)
    if owner:
        dump(sock, ranges, owner + 0x430, 4, "owner+0x430..0x448 (expect +0x438 == obj)")
    notifier = kptr(o[0x68 // 8]); print("notifier (+0x68) = 0x%x" % notifier)
    if notifier:
        nw = dump(sock, ranges, notifier, 8, "_IOServiceNotifier")
        matching = kptr(nw[3])  # +0x18
        mw = dump(sock, ranges, matching, 6, "matching OSDictionary")
        entries, count = kptr(mw[3]), mw[4] & 0xffffffff
        print("  dictionary entries @0x%x count=%d" % (entries, count))
        ew = read_words(sock, entries, 2 * min(count, 8))
        for k in range(min(count, 8)):
            key, val = ew[2 * k], ew[2 * k + 1]
            try:
                ks = os_string(sock, key)
            except SystemExit:
                ks = "?"
            try:
                vs = os_string(sock, val)
            except SystemExit:
                vs = "(non-string 0x%x)" % val
            print("    %-24s = %r" % (ks, vs))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def fpwalk(sock, ranges, fp, stack_lo, stack_hi, max_frames=40):
    """Walk an AArch64 frame-pointer chain: [fp] = caller fp, [fp+8] = signed LR.
    Prints each return address (kext-attributed) and the 8 words just below the frame,
    which is where the function's callee-saved registers were pushed."""
    for n in range(max_frames):
        if not (stack_lo <= fp < stack_hi):
            print("  fp 0x%x leaves the stack; stop" % fp); return
        below = read_words(sock, fp - 0x40, 10)
        nfp, lr = kptr(below[8]), (below[9] & 0xffffffffff) | 0xfffffff000000000
        r = attribute(ranges, lr - KSLIDE); name = ("%s+0x%x" % (r[0].split(".")[-1], r[1])) if r and r[0] else "?"
        print("  frame %2d fp=0x%x lr=0x%x %s" % (n, fp, lr, name))
        print("           below: " + " ".join("%016x" % w for w in below[:8]))
        if nfp <= fp: print("  chain ends"); return
        fp = nfp
