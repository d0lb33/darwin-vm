#!/usr/bin/env python3
"""Boot the supported iOS restore shell with experimental multicore TCG.

Build this worktree's QEMU first. The original firmware is never modified.
Two CPUs are the tested parallel-work default; six-CPU boot is also verified.
Exit QEMU with Ctrl-A, X. Suspend, hotplug and checkpoint restore are unsupported.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cpus", type=int, choices=range(2, 7), default=2)
    p.add_argument("--check", action="store_true", help="prepare kernel and print command without booting")
    a = p.parse_args()
    fw = ROOT / "firmware"
    qemu = ROOT / "qemu-sptm/build/qemu-system-aarch64"
    for path in [qemu, *(fw / f for f in ["bootkc", "dtree", "ramdisk.tc", "ramdisk.dmg", "sptm", "txm"])]:
        if not path.is_file():
            p.error(f"missing required input: {path}")
    output = Path("/tmp/dvm")
    output.mkdir(parents=True, exist_ok=True)
    kernel = output / "SMP_PV.bootkc"
    subprocess.run([sys.executable, str(ROOT / "tools/re/smp_pv_patch.py"),
                    str(fw / "bootkc"), str(kernel)], check=True)
    command = [str(qemu), "-M", "darwin", "-smp", str(a.cpus),
               "-accel", "tcg,thread=multi", "-m", "8G", "-bootkc", str(kernel),
               "-dtree", str(fw / "dtree"), "-tc", str(fw / "ramdisk.tc"),
               "-ramdisk", str(fw / "ramdisk.dmg"), "-sptm", str(fw / "sptm"),
               "-txm", str(fw / "txm"), "-display", "none", "-serial", "mon:stdio",
               "-args", "rd=md0 serial=3 -v wdt=-1 wlan-olyhal-abort"]
    print("Experimental virtual CPU power management; suspend/hotplug unsupported.", flush=True)
    if a.check:
        print("DARWIN_SMP_PV=1 " + shlex.join(command))
        return
    os.execve(qemu, command, dict(os.environ, DARWIN_SMP_PV="1"))


if __name__ == "__main__":
    main()
