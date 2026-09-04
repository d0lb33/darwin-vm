#!/usr/bin/env python3
"""Compare wall-clock system boot on fresh disk children, sequentially.

Three repetitions of stock 1 CPU, virtual-platform 2 CPUs,
and virtual-platform 6 CPUs. Measures launch to Early boot complete; not UI.
Each owned VM stops at the marker, on panic, or after 60 seconds.

--migration-sample instead measures one partial first-boot metadata window
per configuration using the pre-first-boot marker image. It stops after 100
unique User-volume dir-stats events, 200 total events, panic, or 180 seconds.
Events are progress proxies, not byte counts or a percentage of full migration.
--variant selects one configuration for a bounded repeat.
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
    parser.add_argument("--migration-sample", action="store_true",
                        help="stop after 100 User-volume dir-stats updates; never wait for completion")
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--variant", choices=["stock1", "pv2", "pv6"],
                        help="run one selected configuration")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", args.tag):
        parser.error("invalid tag")
    out = Path("/tmp/dvm") / args.tag
    out.mkdir(exist_ok=False)
    qemu = ROOT / "qemu-sptm/build/qemu-system-aarch64"
    fw = ROOT / "firmware"
    parent = (args.parent or Path("/tmp/dvm/data-seed/rebuild/marker.qcow2" if args.migration_sample
                                  else "/tmp/dvm/data-seed/persistent-parent.qcow2")).resolve()
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
    report = {"marker": "100 User-volume dir-stats updates" if args.migration_sample else "Early boot complete", "poll_seconds": .02,
              "qemu_sha256": hashlib.file_digest(qemu.open("rb"), "sha256").hexdigest(),
              "parent": str(parent), "runs": []}
    variants = {"stock1": (1, False), "pv2": (2, True), "pv6": (6, True)}
    order = ["stock1", "pv2", "pv6"] if args.migration_sample else [
        "stock1", "pv2", "pv6", "pv6", "pv2", "stock1", "pv2", "stock1", "pv6"]
    if args.variant:
        order = [args.variant]
    for number, name in enumerate(order):
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
        events = []
        seen = set()
        user_events = []
        progress_pattern = re.compile(rb"set_dir_stats:\d+: (disk1s[25]) setting dir-stats for ino (\d+) ")
        with (out / f"{tag}.stderr.log").open("w") as err:
            start = time.monotonic()
            proc = subprocess.Popen(cmd, env=run_env, stdout=subprocess.DEVNULL, stderr=err)
            try:
                while time.monotonic() - start < (180 if args.migration_sample else 60):
                    text = serial.read_bytes() if serial.exists() else b""
                    if b"panic(cpu" in text:
                        row["error"] = "guest panic"
                        break
                    elapsed = time.monotonic() - start
                    if b"Early boot complete" in text and b"BSD root: disk1s1" in text:
                        row.setdefault("early_boot_seconds", elapsed)
                        if not args.migration_sample:
                            row["seconds"] = elapsed
                            break
                    if args.migration_sample:
                        for m in progress_pattern.finditer(text):
                            key = (m[1].decode(), int(m[2]))
                            if key not in seen:
                                seen.add(key)
                                event = {"volume": key[0], "inode": key[1], "seconds": elapsed}
                                events.append(event)
                                if key[0] == "disk1s5":
                                    user_events.append(event)
                                    if len(user_events) % 20 == 0:
                                        print(f"{tag}: {len(user_events)} User updates at {elapsed:.3f}s", flush=True)
                        if len(user_events) >= 100 or len(events) >= 200:
                            row["seconds"] = elapsed
                            row["stop_reason"] = "partial metadata work limit"
                            break
                    if proc.poll() is not None:
                        row["error"] = f"QEMU exited {proc.returncode}"
                        break
                    time.sleep(.02)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait(timeout=10)
        if args.migration_sample:
            row["metadata_events"] = events
            row["user_update_count"] = len(user_events)
            if len(user_events) >= 20:
                span = user_events[-1]["seconds"] - user_events[0]["seconds"]
                row["user_updates_per_second"] = (len(user_events) - 1) / span if span > 0 else None
                row["active_sample_seconds"] = span
            if len(user_events) >= 40:
                steady_span = user_events[-1]["seconds"] - user_events[19]["seconds"]
                row["post20_updates_per_second"] = (len(user_events) - 20) / steady_span
                row["post20_sample_seconds"] = steady_span
            row["stop_reason"] = row.get("stop_reason", row.get("error", "180-second partial-run deadline"))
        if row["seconds"] is None:
            row.setdefault("error", "work milestone not reached within bounded run")
        report["runs"].append(row)
        (out / "results.json").write_text(json.dumps(report, indent=2))
        print(f"{tag}: {row['seconds']} seconds; {row.get('error', 'no panic before milestone')}", flush=True)
        if row["seconds"] is None and not args.migration_sample:
            raise RuntimeError("incomplete trial; inspect saved logs")
    if args.migration_sample:
        print(json.dumps([{k: v for k, v in r.items() if k not in ("command", "metadata_events")}
                          for r in report["runs"]], indent=2), flush=True)
        return
    report["medians"] = {v: statistics.median(r["seconds"] for r in report["runs"]
                                               if r["variant"] == v) for v in set(order)}
    (out / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["medians"], indent=2), flush=True)


if __name__ == "__main__":
    main()
