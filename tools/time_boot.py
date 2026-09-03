#!/usr/bin/env python3
"""time_boot.py - how long does a boot take, in host wall-clock seconds?

probe.sh answers "where did it get to"; this answers "how fast did it get
there", which is what any accelerator claim has to be measured against. It
starts qemu, polls the serial log for a marker, and prints the elapsed time.

usage:
  tools/time_boot.py --qemu <path> --dtree <file> [--marker "can't access tty"]
                     [--timeout 120] [--tag NAME] [--repeat N]
                     [-- QEMU_ARGS...]

Written for docs/re/hvf-acceleration.md, where the TCG boot time is the
denominator of every speedup number.
"""
import argparse
import os
import signal
import statistics
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def one_run(args, i):
    serial = f"/tmp/dvm/probe/{args.tag}{i}.serial.log"
    err = f"/tmp/dvm/probe/{args.tag}{i}.stderr.log"
    os.makedirs("/tmp/dvm/probe", exist_ok=True)
    for f in (serial, err):
        if os.path.exists(f):
            os.unlink(f)

    cmd = [
        args.qemu, "-M", "darwin",
        "-bootkc", args.bootkc,
        "-dtree", args.dtree,
        "-tc", args.tc,
        "-ramdisk", args.ramdisk,
        "-args", args.bootargs,
        "-display", "none",
        "-serial", "file:" + serial,
        "-m", args.mem,
    ]
    if os.path.exists(os.path.join(REPO, "firmware/sptm")):
        cmd += ["-sptm", os.path.join(REPO, "firmware/sptm"),
                "-txm", os.path.join(REPO, "firmware/txm")]
    cmd += args.qemu_args

    marker = args.marker.encode()
    with open(err, "wb") as ef:
        t0 = time.monotonic()
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=ef,
                             start_new_session=True)
        hit = None
        while time.monotonic() - t0 < args.timeout:
            try:
                with open(serial, "rb") as f:
                    if marker in f.read():
                        hit = time.monotonic() - t0
                        break
            except FileNotFoundError:
                pass
            if p.poll() is not None:
                break
            time.sleep(0.02)
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        p.wait(timeout=10)
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qemu", default=os.path.join(
        REPO, "qemu-sptm/build/qemu-system-aarch64"))
    ap.add_argument("--dtree", default="/tmp/dvm/dt_base.bin")
    ap.add_argument("--bootkc", default=os.path.join(REPO, "firmware/bootkc"))
    ap.add_argument("--ramdisk", default=os.path.join(REPO, "firmware/ramdisk.dmg"))
    ap.add_argument("--tc", default=os.path.join(REPO, "firmware/ramdisk.tc"))
    ap.add_argument("--mem", default="8G")
    ap.add_argument("--bootargs",
                    default="rd=md0 serial=3 -v wdt=-1 wlan-olyhal-abort")
    ap.add_argument("--marker", default="can't access tty")
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--tag", default="timeboot")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("qemu_args", nargs=argparse.REMAINDER,
                    help="arguments after -- are passed directly to QEMU")
    args = ap.parse_args()

    # argparse.REMAINDER normally consumes the separator, but accepting an
    # explicit leading one keeps this robust when called through wrappers.
    if args.qemu_args[:1] == ["--"]:
        args.qemu_args = args.qemu_args[1:]

    times = []
    for i in range(args.repeat):
        t = one_run(args, i)
        if t is None:
            print(f"run {i}: marker never appeared within {args.timeout}s")
        else:
            print(f"run {i}: {t:.2f} s to \"{args.marker}\"")
            times.append(t)
    if times:
        print(f"min {min(times):.2f} s  median {statistics.median(times):.2f} s  "
              f"mean {statistics.fmean(times):.2f} s  max {max(times):.2f} s  "
              f"({len(times)} runs)")
    return 0 if times else 1


if __name__ == "__main__":
    sys.exit(main())
