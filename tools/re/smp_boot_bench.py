#!/usr/bin/env python3
"""Compare wall-clock system boot on fresh disk children, sequentially.

Three repetitions of stock 1 CPU, virtual-platform 2 CPUs,
and virtual-platform 6 CPUs. Measures launch to Early boot complete; not UI.
Each owned VM stops at the marker, on panic, or after 60 seconds.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", args.tag):
        parser.error("invalid tag")
    out = Path("/tmp/dvm") / args.tag
    out.mkdir(exist_ok=False)
    qemu = ROOT / "qemu-sptm/build/qemu-system-aarch64"
    fw = ROOT / "firmware"
    parent = Path("/tmp/dvm/data-seed/persistent-parent.qcow2").resolve()
    dt = Path("/tmp/dvm/data-seed/dt_nvme_welcome.bin")
    tc = Path.home() / "dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin"
    pv_kernel = Path("/tmp/dvm/SMP_PV.bootkc")
    for path in [qemu, parent, dt, tc, pv_kernel, fw / "bootkc"]:
        if not path.is_file():
            parser.error(f"missing input: {path}")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DARWIN_", "GXFSTAT_"))}
    env.update(DARWIN_DCP_EPIC="all", DARWIN_DCP_REPLY="1", DARWIN_DCP_IOMFB="4",
        DARWIN_DCP_IOMFB_OUT="A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000",
        DARWIN_DCP_IOMFB_CB="D120::4,D586:9b040000fc090000:4")
    report = {"marker": "Early boot complete", "poll_seconds": .02,
              "qemu_sha256": hashlib.file_digest(qemu.open("rb"), "sha256").hexdigest(),
              "parent": str(parent), "runs": []}
    variants = {"stock1": (1, False), "pv2": (2, True), "pv6": (6, True)}
    for number, name in enumerate(["stock1", "pv2", "pv6", "pv6", "pv2", "stock1",
                                    "pv2", "stock1", "pv6"]):
        cpus, pv = variants[name]
        tag = f"{number}_{name}"
        disk = out / f"{tag}.qcow2"
        serial = out / f"{tag}.serial.log"
        subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
                        "-b", str(parent), str(disk)], check=True, stdout=subprocess.DEVNULL)
        cmd = [str(qemu), "-M", "darwin", "-smp", str(cpus), "-accel", "tcg,thread=multi",
               "-bootkc", str(pv_kernel if pv else fw / "bootkc"), "-dtree", str(dt),
               "-tc", str(tc), "-ramdisk", str(fw / "ramdisk.dmg"),
               "-sptm", str(fw / "sptm"), "-txm", str(fw / "txm"), "-m", "12G",
               "-display", "none", "-serial", f"file:{serial}",
               "-fb", "1179x2556", "-fbmode", "graphics",
               "-drive", f"if=none,id=ans,file={disk},format=qcow2",
               "-args", "rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort"]
        run_env = dict(env, **({"DARWIN_SMP_PV": "1"} if pv else {}))
        row = {"variant": name, "command": cmd, "host_load": os.getloadavg(), "seconds": None}
        with (out / f"{tag}.stderr.log").open("w") as err:
            start = time.monotonic()
            proc = subprocess.Popen(cmd, env=run_env, stdout=subprocess.DEVNULL, stderr=err)
            try:
                while time.monotonic() - start < 60:
                    text = serial.read_bytes() if serial.exists() else b""
                    if b"panic(cpu" in text:
                        row["error"] = "guest panic"
                        break
                    if b"Early boot complete" in text and b"BSD root: disk1s1" in text:
                        row["seconds"] = time.monotonic() - start
                        break
                    if proc.poll() is not None:
                        row["error"] = f"QEMU exited {proc.returncode}"
                        break
                    time.sleep(.02)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=10)
        report["runs"].append(row)
        (out / "results.json").write_text(json.dumps(report, indent=2))
        print(f"{tag}: {row['seconds']} seconds; {row.get('error', 'no panic before milestone')}", flush=True)
        if row["seconds"] is None:
            raise RuntimeError("incomplete trial; inspect saved logs")
    report["medians"] = {v: statistics.median(r["seconds"] for r in report["runs"]
                                               if r["variant"] == v) for v in variants}
    (out / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["medians"], indent=2), flush=True)


if __name__ == "__main__":
    main()
