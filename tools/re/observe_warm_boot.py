#!/usr/bin/env python3
"""Resume one explicitly named paused warm probe for a bounded observation.

Only new log bytes count as milestones. Always freeze the owned VM on exit;
preserve the initial boot result and write continuation evidence separately.
"""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--stop-on", action="append", default=[])
    parser.add_argument("--already-running", action="store_true",
                        help="bound a diagnostic run resumed through its debugger")
    args = parser.parse_args()
    if not args.label.replace("-", "").replace("_", "").isalnum():
        parser.error("label must be alphanumeric with optional dashes/underscores")
    if not 1 <= args.seconds <= 600:
        parser.error("seconds must be 1..600")
    launch = json.loads((args.run / "launch.json").read_text())
    if launch.get("format") != "darwin-vm-qemu-launch-v1":
        parser.error("expected a recorded QEMU launch")
    output = args.run / args.label
    output.mkdir(exist_ok=False)
    argv = launch["argv"]
    endpoint = argv[argv.index("-monitor") + 1]
    if not endpoint.startswith("unix:"):
        parser.error("expected an explicit UNIX monitor endpoint")
    hmp = HMP(Path(endpoint[5:].split(",", 1)[0]), timeout=10)
    status = hmp.command("info status")
    expected = "running" if args.already_running else "paused"
    if expected not in status:
        parser.error(f"VM must be {expected} before observation: {status}")
    stderr = "stderr.log" if (args.run / "stderr.log").exists() else "qemu.stderr.log"
    streams = {name: (args.run / name).open("rb")
               for name in ("serial.log", stderr)}
    offsets = {name: stream.seek(0, 2) for name, stream in streams.items()}
    pending = {name: b"" for name in streams}
    report = dict(start_unix=time.time(), start_offsets=offsets, events=[],
                  stop_reason="deadline", elapsed=0)
    start = time.monotonic()
    next_progress = 30
    try:
        if not args.already_running:
            hmp.command("cont")
        done = False
        while time.monotonic() - start < args.seconds and not done:
            elapsed = time.monotonic() - start
            for name, stream in streams.items():
                lines = (pending[name] + stream.read()).split(b"\n")
                pending[name] = lines.pop()
                for raw in lines:
                    line = raw.decode(errors="replace").rstrip()
                    matches = [marker for marker in args.stop_on + [
                        "panic(cpu", "rebooting due to critical process crashes",
                        "rejected unsupported"] if marker in line]
                    if matches:
                        event = dict(seconds=round(elapsed, 3), file=name,
                                     markers=matches, line=line)
                        report["events"].append(event)
                        print(json.dumps(event), flush=True)
                        report["stop_reason"] = "matched milestone"
                        done = True
            if elapsed >= next_progress:
                print(f"{elapsed:.1f}s: waiting for {args.stop_on}", flush=True)
                next_progress += 30
            time.sleep(.2)
    finally:
        hmp.command("stop")
        report["elapsed"] = time.monotonic() - start
        report["status"] = hmp.command("info status")
        report["end_offsets"] = {name: stream.tell()
                                 for name, stream in streams.items()}
        hmp.command(f"screendump {output}/final.png -f png")
        for stream in streams.values():
            stream.close()
        atomic_json(output / "result.json", report)
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
