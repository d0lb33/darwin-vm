#!/usr/bin/env python3
"""Bounded warm-disk cold boot using a recorded launch and a fresh child disk.

Owns only the new process/endpoints; stops on SKS rejection or kernel panic.
Records elapsed milestones, final CPU state and framebuffer before shutdown.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--launch-template", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.tag):
        parser.error("invalid tag")
    if not 1 <= args.seconds <= 600:
        parser.error("seconds must be in 1..600")
    out = Path("/tmp/dvm") / args.tag
    out.mkdir(exist_ok=False)
    source = json.loads(args.launch_template.read_text())
    monitor = f"/tmp/dvm/{args.tag}.sock"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    command = []
    argv, i = source["argv"], 0
    while i < len(argv):
        key = argv[i]
        if key in ("-monitor", "-qmp", "-gdb", "-chardev"):
            i += 2
            continue
        if key == "-S":
            i += 1
            continue
        if key in ("-incoming", "-loadvm"):
            parser.error("template must be a cold boot, not a RAM restore")
        replacements = {
            "-drive": f"if=none,id=ans,file={out}/disk.qcow2,format=qcow2",
            "-serial": f"file:{out}/serial.log",
        }
        if key in replacements:
            command += [key, replacements[key]]
            i += 2
        else:
            command.append(key)
            i += 1
    command += ["-monitor", f"unix:{monitor},server=on,wait=off",
                "-gdb", f"tcp:127.0.0.1:{port}"]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("DARWIN_", "DVM_", "GXFSTAT_"))}
    env.update(source["env"])
    env["DARWIN_SKS_REQUEST_DEBUG_CODE"] = "0x0f"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
                    "-b", str(args.parent.resolve()), str(out / "disk.qcow2")],
                   check=True, stdout=subprocess.DEVNULL)
    report = {"argv": command, "env": {k: v for k, v in env.items()
              if k.startswith("DARWIN_")}, "gdb_port": port,
              "qemu_sha256": hashlib.sha256(Path(command[0]).read_bytes()).hexdigest(),
              "parent": str(args.parent.resolve()), "events": []}
    (out / "launch.json").write_text(json.dumps(report, indent=2))
    patterns = ["Early boot complete", "record-kind 2 class 3",
                "record-kind 2 class 4", "dcp: state", "sks op19 returns",
                "AP DRIVER START", "SpringBoard", "backboardd",
                "rejected unsupported", "panic(cpu"]
    seen, sizes = set(), {}
    next_status = 30
    reason = "observation deadline"
    def cancelled(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, cancelled)
    signal.signal(signal.SIGINT, cancelled)
    with (out / "stderr.log").open("w") as log:
        start = time.monotonic()
        proc = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL,
                                stderr=log)
        (out / "qemu.pid").write_text(str(proc.pid))
        print(f"{args.tag}: PID {proc.pid}, GDB {port}", flush=True)
        try:
            while time.monotonic() - start < args.seconds:
                elapsed = time.monotonic() - start
                for filename in ("serial.log", "stderr.log"):
                    path = out / filename
                    text = path.read_text(errors="replace") if path.exists() else ""
                    sizes[filename] = len(text)
                    for pattern in patterns:
                        if pattern in text and pattern not in seen:
                            seen.add(pattern)
                            event = {"seconds": elapsed, "file": filename,
                                     "marker": pattern,
                                     "line": next(s for s in text.splitlines()
                                                  if pattern in s)}
                            report["events"].append(event)
                            print(json.dumps(event), flush=True)
                if {"rejected unsupported", "panic(cpu"} & seen:
                    reason = "device rejection or kernel panic"
                    break
                if proc.poll() is not None:
                    reason = f"QEMU exited {proc.returncode}"
                    break
                if elapsed >= next_status:
                    print(f"{elapsed:.1f}s: log bytes {sizes}", flush=True)
                    next_status += 30
                time.sleep(.1)
        finally:
            report.update(elapsed=time.monotonic() - start, stop_reason=reason)
            (out / "result.json").write_text(json.dumps(report, indent=2))
            if proc.poll() is None:
                try:
                    for name, cmd in [("stop", "stop"), ("cpus", "info cpus"),
                                      ("registers", "info registers"),
                                      ("framebuffer", f"screendump {out}/framebuffer.ppm")]:
                        result = subprocess.run(["python3", str(ROOT / "tools/hmp.py"),
                                                 monitor, cmd], capture_output=True,
                                                text=True, timeout=5)
                        (out / f"{name}.txt").write_text(result.stdout + result.stderr)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
    print(f"{reason}; saved {out}/result.json", flush=True)


if __name__ == "__main__":
    main()
