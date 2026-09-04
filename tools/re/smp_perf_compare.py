#!/usr/bin/env python3
"""Alternate two QEMU builds on bounded, fresh-image migration samples.

Sequential A/B/B/A order. Never waits for migration completion. Every child
probe owns its QEMU and stops at the same 100-User-event milestone.
"""
import argparse
import json
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def terminate(signum, frame):
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, terminate)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--cpus", type=int, choices=[2, 4, 6], default=6)
    a = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,28}", a.tag):
        p.error("tag must be 1..28 safe filename characters")
    paths = {"base": a.baseline.resolve(), "fast": a.candidate.resolve()}
    if any(not path.is_file() for path in paths.values()):
        p.error("both executables must exist")
    out = Path("/tmp/dvm") / a.tag
    out.mkdir(exist_ok=False)
    rows = []
    for i, label in enumerate(["base", "fast", "fast", "base"]):
        tag = f"{a.tag}_{i}_{label}"
        command = [sys.executable, str(ROOT / "tools/re/smp_boot_bench.py"),
                   "--migration-sample", "--variant", f"pv{a.cpus}", "--tag", tag,
                   "--qemu", str(paths[label])]
        print(f"Starting {label}: {tag}", flush=True)
        with (out / f"{i}_{label}.log").open("w") as log:
            child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            try:
                if child.wait() != 0:
                    raise RuntimeError(f"probe failed: inspect {tag}")
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=15)
        result_path = Path("/tmp/dvm") / tag / "results.json"
        result = json.loads(result_path.read_text())
        run = result["runs"][0]
        if run.get("error") or run["user_update_count"] != 100:
            raise RuntimeError(f"incomplete comparison: inspect {result_path}")
        rows.append({"build": label, "results": str(result_path),
                     "qemu_sha256": result["qemu_sha256"], "parent": result["parent"],
                     "seconds": run["seconds"],
                     "post20_updates_per_second": run["post20_updates_per_second"]})
        assert len({r["parent"] for r in rows}) == 1
        (out / "results.json").write_text(json.dumps(rows, indent=2))
        print(f"{label}: {run['seconds']:.3f}s, "
              f"{run['post20_updates_per_second']:.3f} events/s after event 20", flush=True)
    medians = {label: statistics.median(r["seconds"] for r in rows if r["build"] == label)
               for label in paths}
    summary = {"medians": medians, "elapsed_reduction_percent":
               100 * (1 - medians["fast"] / medians["base"])}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
