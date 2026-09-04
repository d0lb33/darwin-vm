#!/usr/bin/env python3
"""Prove concurrent EL0 execution on every requested CPU in a real iOS boot.

Starts an owned restore-shell VM, runs one shell compute loop per vCPU,
captures all vCPUs at the same debugger stop, and requires distinct user
stacks on every CPU. Stops only its guest loops and its owned QEMU process.
"""
import argparse
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time

from smp_trace import Remote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import serial


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cpus", type=int, choices=range(2, 7), default=2)
    p.add_argument("--tag", required=True)
    a = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", a.tag):
        p.error("invalid tag")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    uart = f"/tmp/dvm/{a.tag}.uart"
    stop = Path(f"/tmp/dvm/{a.tag}.stop")
    log_path = Path(f"/tmp/dvm/probe/{a.tag}.serial.log")
    proc = subprocess.Popen([str(ROOT / "tools/probe.sh"), "--tag", a.tag,
        "--secs", "60", "--bootkc", "/tmp/dvm/SMP_PV.bootkc", "--uart-socket", uart,
        "--stop-file", str(stop), "--", "-smp", str(a.cpus),
        "-accel", "tcg,thread=multi", "-gdb", f"tcp:127.0.0.1:{port}"],
        cwd=ROOT, env=dict(os.environ, DARWIN_SMP_PV="1"))
    r = u = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            text = log_path.read_text(errors="replace") if log_path.exists() else ""
            if "panic(cpu" in text:
                raise RuntimeError("guest panicked before shell")
            if "can't access tty" in text:
                break
            time.sleep(.1)
        else:
            raise RuntimeError("guest did not reach shell")
        r = Remote(port)
        r.send("c")
        u = serial.connect(uart)
        with open(f"/tmp/dvm/{a.tag}.console.log", "w") as log:
            assert serial.wait_for_prompt(u, re.compile(r"# "), 5, .003, None, log)
            command = ('echo SMP_NPROC_BEGIN; /bin/nproc; echo SMP_NPROC_END; '
                       'pids=""; for i in ' + " ".join(str(i) for i in range(a.cpus)) +
                       '; do /bin/sh -c "while :; do :; done" & pids="$pids $!"; done; '
                       'echo SMP_WORK_READY $pids')
            serial.send_command(u, command, .003)
            assert serial.wait_for_text(u, re.compile(r"\nSMP_WORK_READY [0-9 ]+\r*\n"),
                                        10, None, log)
            log.flush()
            console = Path(log.name).read_text()
            block = re.search(r"\nSMP_NPROC_BEGIN\n(.*?)\nSMP_NPROC_END", console, re.S)
            counts = re.findall(r"(?m)^(\d+)$", block[1]) if block else []
            assert counts == [str(a.cpus)], console
            simultaneous = False
            for sample in range(60):
                time.sleep(.1)
                r.sock.sendall(b"\x03")
                r.receive()
                states = []
                for cpu in range(a.cpus):
                    assert r.command(f"Hg{cpu+1:x}") == "OK"
                    raw = bytes.fromhex(r.command("g"))
                    registers = struct.unpack_from("<33Q", raw)
                    pstate = struct.unpack_from("<I", raw, 264)[0]
                    states.append((cpu, registers[32], registers[31], pstate))
                print(f"sample {sample}: " + " ".join(
                    f"cpu={cpu} pc={pc:#x} sp={sp:#x} pstate={ps:#x}"
                    for cpu, pc, sp, ps in states), flush=True)
                if (all(ps & 15 == 0 and 0 < pc < 0x10000000000
                        for _, pc, _, ps in states)
                        and len({sp for _, _, sp, _ in states}) == a.cpus):
                    simultaneous = True
                    r.send("c")
                    break
                r.send("c")
            assert simultaneous, "did not observe concurrent userspace on every CPU"
            serial.send_command(u, 'kill $pids; wait; echo SMP_WORK_STOPPED', .003)
            assert serial.wait_for_text(u, re.compile(r"\nSMP_WORK_STOPPED\r*\n"),
                                        5, None, log)
            assert "panic(cpu" not in log_path.read_text(errors="replace")
            print(f"PASS: {a.cpus} online CPUs, concurrent EL0 execution with distinct stacks, "
                  "workloads stopped and shell responsive", flush=True)
        stop.write_text(f"SMP userspace passed on {a.cpus} CPUs\n")
        proc.wait(timeout=10)
    finally:
        if r:
            r.sock.close()
        if u:
            u.close()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
