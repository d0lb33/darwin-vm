#!/usr/bin/env python3
"""Bounded, owned virtual-platform XNU SMP probe with per-CPU register capture."""
import argparse
import os
from pathlib import Path
import socket
import struct
import subprocess
import time
import re

from smp_trace import Remote

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True)
    p.add_argument("--cpus", type=int, choices=range(2, 7), default=2)
    p.add_argument("--bootkc", default="/tmp/dvm/SMP_PV.bootkc")
    p.add_argument("--seconds", type=float, default=2)
    p.add_argument("--break", dest="breakpoint", type=lambda x: int(x, 0))
    a = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", a.tag):
        p.error("invalid tag")
    if not 0 < a.seconds < 10:
        p.error("--seconds must be between zero and ten")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, DARWIN_SMP_PV="1", DARWIN_SMP_DEBUG="1", NO_WATCHDOG="1")
    proc = subprocess.Popen([str(ROOT / "tools/probe.sh"), "--tag", a.tag,
        "--secs", "12", "--bootkc", a.bootkc, "--", "-smp", str(a.cpus),
        "-accel", "tcg,thread=multi", "-S", "-gdb", f"tcp:127.0.0.1:{port}"],
        cwd=ROOT, env=env)
    r = None
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                r = Remote(port)
                break
            except ConnectionRefusedError:
                time.sleep(.05)
        assert r
        if a.breakpoint:
            assert r.command(f"Z1,{a.breakpoint:x},4") == "OK"
        r.send("c")
        if not a.breakpoint:
            time.sleep(a.seconds)
            r.sock.sendall(b"\x03")
        print("stop:", r.receive(), flush=True)
        for cpu in range(a.cpus):
            assert r.command(f"Hg{cpu+1:x}") == "OK"
            regs = struct.unpack_from("<33Q", bytes.fromhex(r.command("g")))
            print(f"CPU {cpu}: " + " ".join(f"x{i}={v:#x}" for i, v in enumerate(regs)), flush=True)
            print("PC memory:", r.command(f"m{regs[32]:x},40"), flush=True)
            if regs[32] == 0xFFFFFFF0070F75A8:
                reply = r.command(f"m{regs[3]:x},400")
                if not reply.startswith("E"):
                    print("SPTM panic:", bytes.fromhex(reply).split(b"\0")[0].decode(errors="replace"), flush=True)
                reply = r.command(f"m{regs[3]-256:x},500")
                if not reply.startswith("E"):
                    print("SPTM panic context:", re.findall(rb"[ -~]{8,}", bytes.fromhex(reply)), flush=True)
            xml = ""
            offset = 0
            while True:
                chunk = r.command(f"qXfer:features:read:system-registers.xml:{offset:x},1000")
                if not chunk or chunk[0] not in "lm":
                    break
                xml += chunk[1:]
                offset += len(chunk) - 1
                if chunk[0] == "l":
                    break
            for name, number in re.findall(r'<reg name="([^"]+)"[^>]*regnum="([^"]+)"', xml):
                if re.search(r"ESR_|ELR_|FAR_|SPSR_|CPACR|CPTR|SCTLR|VBAR|HCR_EL|CTRR|CTXR", name, re.I):
                    print(name, r.command(f"p{int(number):x}"), flush=True)
        # Leave stopped for probe.sh's verdict, then owned process cleanup.
        proc.wait(timeout=20)
    finally:
        if r:
            r.sock.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
