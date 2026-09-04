#!/usr/bin/env python3
"""Boot the persistent iOS system with SMP on a fresh disposable disk child.

Stops ten seconds after Early boot complete, on panic, or after 90 seconds.
Uses the established display-device configuration without modifying it.
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tag", required=True)
    p.add_argument("--cpus", type=int, choices=range(2, 7), default=6)
    a = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", a.tag):
        p.error("invalid tag")
    parent = Path("/tmp/dvm/data-seed/persistent-parent.qcow2")
    dt = Path("/tmp/dvm/data-seed/dt_nvme_welcome.bin")
    tc = Path.home() / "dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin"
    kc = Path("/tmp/dvm/SMP_PV.bootkc")
    child = Path(f"/tmp/dvm/{a.tag}.qcow2")
    for path in [parent, dt, tc, kc]:
        if not path.exists():
            p.error(f"missing input: {path}")
    if child.exists():
        p.error("disk child already exists; use a new tag")
    subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
                    "-b", str(parent.resolve()), str(child)], check=True)
    stop = Path(f"/tmp/dvm/{a.tag}.stop")
    serial = Path(f"/tmp/dvm/probe/{a.tag}.serial.log")
    env = dict(os.environ, DARWIN_SMP_PV="1", DARWIN_DCP_EPIC="all",
               DARWIN_DCP_REPLY="1", DARWIN_DCP_IOMFB="4",
               DARWIN_DCP_IOMFB_OUT="A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000",
               DARWIN_DCP_IOMFB_CB="D120::4,D586:9b040000fc090000:4")
    proc = subprocess.Popen([str(ROOT / "tools/probe.sh"), "--tag", a.tag,
        "--secs", "90", "--bootkc", str(kc), "--dtree", str(dt), "--tc", str(tc),
        "--mem", "12G", "--stop-file", str(stop), "--bootargs",
        "rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort",
        "--", "-smp", str(a.cpus), "-accel", "tcg,thread=multi", "-fb", "1179x2556",
        "-fbmode", "graphics", "-drive", f"if=none,id=ans,file={child},format=qcow2"],
        cwd=ROOT, env=env)
    reached = None
    held = False
    try:
        while proc.poll() is None:
            text = serial.read_text(errors="replace") if serial.exists() else ""
            if "panic(cpu" in text:
                stop.write_text("SMP system panic\n")
                break
            if reached is None and "Early boot complete" in text:
                reached = time.monotonic()
                print("System reached Early boot complete", flush=True)
            if reached is not None and time.monotonic() - reached >= 10:
                held = True
                stop.write_text("SMP system passed Early boot complete + 10 seconds\n")
                break
            time.sleep(.25)
        proc.wait(timeout=10)
        text = serial.read_text(errors="replace")
        assert held and "panic(cpu" not in text, "system boot failed or ended before observation window"
        assert "BSD root: disk1s1" in text, "did not boot from the system disk"
        print(f"PASS: iOS system disk boot on {a.cpus} CPUs, Early boot complete, zero panics; "
              f"disposable disk: {child}", flush=True)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
