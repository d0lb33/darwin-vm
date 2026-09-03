#!/usr/bin/env python3
"""pc_sampler.py - a poor man's profiler for the single TCG vCPU.

Stops the guest through the QEMU monitor, records PC / PSTATE / the current
instruction, resumes, and repeats.  Written on 2026-09-03 to find out where
the vCPU is while backboardd's render-server thread sits in a blocking
mach_msg receive with queued messages for ~100 s (docs/re/setup-launch-runtime.md).

    tools/pc_sampler.py <monitor.sock> --secs 120 --interval 0.5 --out /tmp/dvm/X.samples

Each output line: host_time  EL  PC  instruction.  Summarise with --report.
"""
import argparse
import re
import socket
import time


def hmp(path, command, timeout=20):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    buf = b""
    while not buf.rstrip().endswith(b"(qemu)"):
        buf += s.recv(65536)
    s.sendall(command.encode() + b"\n")
    buf = b""
    while not buf.rstrip().endswith(b"(qemu)"):
        buf += s.recv(65536)
    s.close()
    out = []
    for line in buf.decode(errors="replace").replace("\r", "").split("\n"):
        if "\x1b[" in line or line.strip() == "(qemu)":
            continue
        out.append(line)
    return "\n".join(out)


def sample(sock):
    hmp(sock, "stop")
    regs = hmp(sock, "info registers")
    pc = re.search(r"PC=([0-9a-f]+)", regs)
    el = re.search(r"PSTATE=[0-9a-f]+ [^ ]+ (EL[0-9][th])", regs)
    pc = int(pc.group(1), 16) if pc else 0
    insn = hmp(sock, "x/1i 0x%x" % pc)
    insn = insn.strip().split("\n")[-1].strip() if insn else "?"
    hmp(sock, "cont")
    return pc, (el.group(1) if el else "?"), insn


def classify(pc):
    if pc >= 0xfffffff000000000:
        return "kernel"
    if 0x180000000 <= pc < 0x340000000:
        return "user-cache"
    if pc >= 0x100000000:
        return "user-image"
    return "user-low"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sock")
    ap.add_argument("--secs", type=float, default=120)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    t_end = time.time() + a.secs
    n = 0
    with open(a.out, "w") as f:
        while time.time() < t_end:
            try:
                pc, el, insn = sample(a.sock)
            except Exception as e:
                f.write("%.3f error %s\n" % (time.time(), e))
                break
            f.write("%.3f %s 0x%x %s %s\n" % (time.time(), el, pc, classify(pc), insn.replace("\n", " ")))
            f.flush()
            n += 1
            time.sleep(a.interval)
    if a.report:
        from collections import Counter
        cls = Counter(); wfi = 0; top = Counter()
        for line in open(a.out):
            parts = line.split()
            if len(parts) < 4 or parts[1] == "error":
                continue
            cls[parts[3]] += 1
            if "wfi" in line or "wfe" in line:
                wfi += 1
            top[parts[2]] += 1
        print("samples=%d wfi=%d classes=%s" % (n, wfi, dict(cls)))
        print("top pcs:", top.most_common(12))


if __name__ == "__main__":
    main()
