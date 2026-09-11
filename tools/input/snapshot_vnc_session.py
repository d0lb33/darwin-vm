#!/usr/bin/env python3
"""Start or stop a persistent noVNC session from an interaction checkpoint."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, atomic_json, pid_alive, process_argv_env  # noqa: E402


DEFAULT_WEBSOCKIFY = Path(
    "/Users/jdolbe1/dvm-artifacts/research/input-reliability-20260907/"
    "viewer-venv/bin/websockify"
)
DEFAULT_WEB_ROOT = Path(
    "/Users/jdolbe1/dvm-artifacts/research/input-reliability-20260907/novnc"
)


def wait_port(port: int, process: subprocess.Popen, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"viewer exited with status {process.returncode}")
        with socket.socket() as connection:
            connection.settimeout(.2)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(.1)
    raise TimeoutError(f"viewer did not listen on 127.0.0.1:{port}")


def require_free_port(port: int) -> None:
    with socket.socket() as connection:
        connection.settimeout(.2)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"127.0.0.1:{port} is already in use")


def start(args: argparse.Namespace) -> None:
    run = args.run.resolve()
    if run.exists():
        raise RuntimeError(f"refusing to reuse {run}")
    for port in (args.vnc_port, args.web_port):
        require_free_port(port)
    if not args.websockify.is_file() or not os.access(args.websockify, os.X_OK):
        raise RuntimeError(f"websockify is unavailable: {args.websockify}")
    if not (args.web_root / "vnc.html").is_file():
        raise RuntimeError(f"no vnc.html in {args.web_root}")
    snapshot = Path(__file__).resolve().with_name("snapshot_session.py")
    restore = [
        sys.executable, str(snapshot), "restore", str(args.manifest.resolve()),
        "--out", str(run), "--tag", args.tag, "--qemu",
        str(args.qemu.resolve()), "--vnc-port", str(args.vnc_port),
        "--model-env", "DARWIN_DCP_IOMFB_QUIET=1",
        "--model-env", "DARWIN_DCP_IOMFB_TIMING_TRACE=1",
    ]
    subprocess.run(restore, check=True)
    viewer_argv = [
        str(args.websockify.resolve()), "--web", str(args.web_root.resolve()),
        f"127.0.0.1:{args.web_port}", f"127.0.0.1:{args.vnc_port}",
    ]
    viewer_log = (run / "viewer.log").open("wb")
    viewer = subprocess.Popen(viewer_argv, stdin=subprocess.DEVNULL,
                              stdout=viewer_log, stderr=subprocess.STDOUT,
                              start_new_session=True)
    try:
        wait_port(args.web_port, viewer)
    except Exception:
        try:
            HMP(run / "monitor.sock").command("quit")
        finally:
            viewer.terminate()
        raise
    finally:
        viewer_log.close()
    url = (f"http://127.0.0.1:{args.web_port}/vnc.html?autoconnect=true"
           "&resize=scale&shared=true")
    live_viewer_argv, _ = process_argv_env(viewer.pid)
    atomic_json(run / "vnc-session.json", {
        "format": "darwin-vm-snapshot-vnc-session-v1",
        "qemu_pid": int((run / "qemu.pid").read_text()),
        "viewer_pid": viewer.pid, "viewer_argv": live_viewer_argv,
        "vnc_port": args.vnc_port, "web_port": args.web_port, "url": url,
        "no_fixed_session_deadline": True,
    })
    print(json.dumps({"run": str(run), "url": url,
                      "qemu_pid": int((run / "qemu.pid").read_text()),
                      "viewer_pid": viewer.pid}, indent=2))


def stop(args: argparse.Namespace) -> None:
    run = args.run.resolve()
    session = json.loads((run / "vnc-session.json").read_text())
    if session.get("format") != "darwin-vm-snapshot-vnc-session-v1":
        raise RuntimeError("unsupported session manifest")
    qemu_pid = int(session["qemu_pid"])
    launch = json.loads((run / "launch.json").read_text())
    if pid_alive(qemu_pid):
        live_argv, _ = process_argv_env(qemu_pid)
        if live_argv != launch["argv"]:
            raise RuntimeError("QEMU PID no longer matches this session")
        HMP(run / "monitor.sock").command("quit")
    viewer_pid = int(session["viewer_pid"])
    if pid_alive(viewer_pid):
        live_argv, _ = process_argv_env(viewer_pid)
        if live_argv != session["viewer_argv"]:
            raise RuntimeError("viewer PID no longer matches this session")
        os.kill(viewer_pid, 15)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not pid_alive(qemu_pid) and not pid_alive(viewer_pid):
            print(json.dumps({"stopped": True, "run": str(run)}, indent=2))
            return
        time.sleep(.1)
    raise RuntimeError("session processes did not stop within 10 seconds")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("start")
    begin.add_argument("manifest", type=Path)
    begin.add_argument("--run", type=Path, required=True)
    begin.add_argument("--tag", required=True)
    begin.add_argument("--qemu", type=Path, required=True)
    begin.add_argument("--vnc-port", type=int, default=5989)
    begin.add_argument("--web-port", type=int, default=6089)
    begin.add_argument("--websockify", type=Path, default=DEFAULT_WEBSOCKIFY)
    begin.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    end = commands.add_parser("stop")
    end.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    (start if args.command == "start" else stop)(args)


if __name__ == "__main__":
    main()
