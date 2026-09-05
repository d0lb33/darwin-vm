#!/usr/bin/env python3
"""Capture a paused 24A5430a guest's DRAM for offline crash/task inspection.

Reads only the explicitly recorded run's HMP endpoint. No debugger, guest
execution, or guest writes. Chunk names match warm_boot_postmortem.Memory.
The darwin machine maps DRAM at 0x10000000000; size comes from its launch -m.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    launch = json.loads((args.run / "launch.json").read_text())
    if launch.get("format") != "darwin-vm-qemu-launch-v1":
        parser.error("requires a recorded QEMU launch")
    argv = launch["argv"]
    if argv[argv.index("-M") + 1] != "darwin":
        parser.error("requires the darwin machine")
    size = re.fullmatch(r"([1-9][0-9]*)([MG])", argv[argv.index("-m") + 1])
    if not size:
        parser.error("unsupported RAM size syntax")
    length = int(size[1]) * (1 << (20 if size[2] == "M" else 30))
    if length > 64 << 30:
        parser.error("RAM capture exceeds 64 GiB bound")
    endpoint = argv[argv.index("-monitor") + 1]
    if not endpoint.startswith("unix:"):
        parser.error("requires an explicit UNIX monitor")
    hmp = HMP(Path(endpoint[5:].split(",", 1)[0]), timeout=60)
    if "paused" not in hmp.command("info status"):
        parser.error("source guest must already be paused")
    output = args.run.resolve() / "ram"
    if any(c in str(output) for c in ('"', '\n', '\r', '\\')):
        parser.error("capture path cannot be safely quoted for HMP")
    output.mkdir(exist_ok=False)
    report = dict(start_unix=time.time(), bytes=length, base=hex(0x10000000000),
                  chunks=[], complete=False)
    atomic_json(output / "capture.json", report)
    for offset in range(0, length, 1 << 30):
        base = 0x10000000000 + offset
        count = min(1 << 30, length - offset)
        target = output / f"{base:x}.bin"
        if "paused" not in hmp.command("info status"):
            raise RuntimeError("source resumed during capture; discard this dump")
        response = hmp.command(f'pmemsave {base:#x} {count:#x} "{target}"')
        if not target.is_file() or target.stat().st_size != count:
            raise RuntimeError(f"short capture at {base:#x}: {response}")
        with target.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        report["chunks"].append(dict(base=hex(base), bytes=count, sha256=digest))
        atomic_json(output / "capture.json", report)
        print(f"captured {offset + count:#x}/{length:#x}", flush=True)
    if "paused" not in hmp.command("info status"):
        raise RuntimeError("source resumed during capture; discard this dump")
    report.update(complete=True, end_unix=time.time())
    atomic_json(output / "capture.json", report)


if __name__ == "__main__":
    main()
