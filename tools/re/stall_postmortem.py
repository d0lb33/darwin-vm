#!/usr/bin/env python3
"""One-shot post-mortem of a frozen guest: which kernel threads are parked in a set of
kexts, what each is waiting on, and the frame chain of each such stack.

Runs ramscan_threads.py and ramscan_frames.py (both dump guest RAM through the
monitor), keeps the sparse pages (kernel stacks, not vtables), re-reads each such page,
lists its (fp, lr) pairs, and joins the stack's VA (recovered from its own frame
pointers) to the thread table for the wait_event.

usage: stall_postmortem.py SOCK TAG [--kext driver.RTBuddy --kext AppleDCP --kext IOMobileGraphicsFamily]
Outputs land in /tmp/dvm/TAG.{threads,ramscan,postmortem}.txt.
"""
import argparse, os, re, struct, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmem  # noqa: E402
from kc_text_map import load_map, attribute  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def attr(ranges, v):
    v = (v & 0xffffffffff) | 0xfffffff000000000
    r = attribute(ranges, v - kmem.KSLIDE)
    return "%s+0x%x" % (r[0].split(".")[-1], r[1]) if r and r[0] else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock"); ap.add_argument("tag")
    ap.add_argument("--kext", action="append", default=None)
    ap.add_argument("--max-hits", type=int, default=80)
    ap.add_argument("--reuse", action="store_true", help="reuse existing threads/frames files")
    ap.add_argument("--frames-file", default=None)
    a = ap.parse_args()
    kexts = a.kext or ["driver.RTBuddy", "AppleDCP", "IOMobileGraphicsFamily"]
    ranges = load_map("firmware/bootkc")
    thr = "/tmp/dvm/%s.threads.txt" % a.tag
    frm = a.frames_file or "/tmp/dvm/%s.ramscan.txt" % a.tag
    out = frm.replace(".txt", "") + ".postmortem.txt"
    if not (a.reuse and os.path.exists(thr)):
        subprocess.run([sys.executable, os.path.join(HERE, "ramscan_threads.py"), a.sock, thr], check=True)
    if not (a.reuse and os.path.exists(frm)):
        cmd = [sys.executable, os.path.join(HERE, "ramscan_frames.py"), a.sock, frm]
        for k in kexts:
            cmd += ["--kext", k]
        subprocess.run(cmd, check=True)
    rows = [tuple(int(x, 16) for x in l.split()) for l in open(thr) if not l.startswith("#")]
    bystack = {r[3]: r for r in rows}
    pages = []
    for blk in open(frm).read().split("page ")[1:]:
        head, *lines = blk.strip().split("\n")
        pa = int(head.split()[0], 16); n = int(re.search(r"\((\d+) hits", head).group(1))
        if n <= a.max_hits:
            pages.append(pa)
    kmem.ensure_kernel(a.sock)
    o = open(out, "w")
    for pa in pages:
        f = "/tmp/dvm/ramscan/pm_%x.bin" % pa
        kmem.hmp(a.sock, "pmemsave 0x%x 0x4000 \"%s\"" % (pa, f), timeout=60)
        d = open(f, "rb").read(); os.unlink(f)
        ws = [struct.unpack_from("<Q", d, i)[0] for i in range(0, 0x4000, 8)]
        fps = [kmem.kptr(w) for w in ws if 0xffffffdf00000000 <= kmem.kptr(w) < 0xfffffff000000000]
        vas = [x & ~0x3fff for x in fps]
        va = max(set(vas), key=vas.count) if vas else 0
        t = bystack.get(va)
        hdr = "page 0x%x stack VA 0x%x" % (pa, va)
        if t:
            hdr += "  thread_pa=0x%x wait_event=0x%x continuation=%s" % (t[0], t[1], attr(ranges, t[2]) if t[2] else "0")
        else:
            hdr += "  (no thread has this kernel_stack)"
        frames = []
        for i in range(len(ws) - 1):
            fp = kmem.kptr(ws[i]); lr = (ws[i + 1] & 0xffffffffff) | 0xfffffff000000000
            if (fp & ~0x3fff) == va and kmem.KTEXT_LO <= lr < kmem.KTEXT_HI and (fp & 0x3fff) > 8 * i:
                frames.append("  +0x%04x fp->+0x%04x lr=%s" % (8 * i, fp & 0x3fff, attr(ranges, lr)))
        if not any(any(k.split(".")[-1] in fr for k in kexts) for fr in frames):
            continue
        print(hdr); o.write(hdr + "\n")
        for fr in frames:
            print(fr); o.write(fr + "\n")
    o.close()
    print("wrote", out)


if __name__ == "__main__":
    main()
