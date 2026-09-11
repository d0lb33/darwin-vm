#!/usr/bin/env python3
"""Create and restore interaction-ready checkpoints without repeating iOS boot.

Capture is deliberately strict: the native input helper must be ready and
idle, no contact may be held, and two framebuffer captures must match. A fresh
run also requires the last complete A484 transition to be ON; a restored run
may explicitly record the narrower stable-visible-frame witness when no new
A484 transition exists. The underlying checkpoint tool then terminates the
exact verified QEMU process and seals its disk.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_common import HMP, SAFE_TAG, atomic_json, pid_alive  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def endpoint(argv: list[str], option: str) -> Path:
    try:
        value = argv[argv.index(option) + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"launch has no {option} endpoint") from error
    if not value.startswith("unix:"):
        raise RuntimeError(f"{option} is not a UNIX endpoint: {value}")
    return Path(value.removeprefix("unix:").split(",", 1)[0]).resolve()


def ans_disk(argv: list[str]) -> Path:
    for index, option in enumerate(argv[:-1]):
        if option != "-drive":
            continue
        parts = argv[index + 1].split(",")
        if "id=ans" not in parts:
            continue
        for part in parts:
            if part.startswith("file="):
                return Path(part.removeprefix("file=")).resolve()
    raise RuntimeError("launch has no ANS disk")


def idle_input(status: dict) -> bool:
    return (status.get("guest_state") == "R" and status.get("inflight") == 0 and
            status.get("queue_len") == 0 and status.get("wire_pending", 0) == 0 and
            status.get("wheel_pending", 0) == 0 and
            not status.get("contact_sent", False) and
            not status.get("btn_down", False))


def wait_stable_input(path: Path, timeout: float,
                      stable_seconds: float = 0.25) -> tuple[dict, dict]:
    deadline = time.monotonic() + timeout
    first = None
    first_time = 0.0
    last = None
    while time.monotonic() < deadline:
        try:
            last = read_json(path)
        except (OSError, ValueError):
            last = None
        now = time.monotonic()
        if last and idle_input(last):
            identity = (last.get("epoch"), last.get("guest_pid"))
            if first is None or identity != (first.get("epoch"),
                                              first.get("guest_pid")):
                first, first_time = last, now
            elif now - first_time >= stable_seconds:
                return first, last
        else:
            first = None
        time.sleep(0.05)
    raise RuntimeError(f"input did not remain idle for {stable_seconds}s: {last}")


def last_display_power(text: str) -> bool | None:
    matches = re.findall(
        r"iomfb: A484 display power \d+ -> (\d+) \(flags [^)]+\)", text
    )
    if not matches:
        return None
    if matches[-1] not in ("0", "1"):
        raise RuntimeError(f"unsupported A484 display power {matches[-1]}")
    return matches[-1] == "1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def capture(args: argparse.Namespace) -> None:
    run = args.run.resolve()
    launch_path = run / "launch.json"
    status_path = run / "input-status.json"
    pid_path = run / "qemu.pid"
    serial_path = run / "serial.log"
    launch = read_json(launch_path)
    if launch.get("format") != "darwin-vm-qemu-launch-v1":
        raise RuntimeError("unsupported launch manifest")
    pid = int(pid_path.read_text().strip())
    if not pid_alive(pid):
        raise RuntimeError(f"QEMU pid {pid} is not alive")
    monitor = endpoint(launch["argv"], "-monitor")
    hmp = HMP(monitor)
    if "running" not in hmp.command("info status"):
        raise RuntimeError("source VM must be running")
    if args.marker_regex:
        serial_text = serial_path.read_text(errors="replace")
        if not re.search(args.marker_regex, serial_text, re.MULTILINE):
            raise RuntimeError(
                "requested guest marker is absent; refusing to pause the VM"
            )

    first, second = wait_stable_input(status_path, args.input_idle_timeout)
    if (first.get("epoch"), first.get("guest_pid")) != (
            second.get("epoch"), second.get("guest_pid")):
        raise RuntimeError("input ownership changed during the capture gate")

    stderr = run / ("stderr.log" if (run / "stderr.log").exists()
                    else "qemu.stderr.log")
    power = last_display_power(stderr.read_text(errors="replace"))
    if power is not True and not (power is None and
                                  args.accept_visible_frame_power):
        raise RuntimeError(
            "display is not proven ON by the last complete A484 transition; "
            f"observed {power}; --accept-visible-frame-power records the "
            "narrower current-frame witness for a restored session"
        )
    power_evidence = ("last-complete-A484-ON" if power is True else
                      "stable-visible-restored-frame-no-post-restore-A484")

    frame1 = run / f".{args.tag}.checkpoint-frame-1.png"
    frame2 = run / f".{args.tag}.checkpoint-frame-2.png"
    for frame in (frame1, frame2):
        answer = hmp.command(f"screendump {frame} -f png")
        if not frame.is_file() or frame.stat().st_size < 1024:
            raise RuntimeError(f"screendump failed: {answer}")
        if frame == frame1:
            time.sleep(args.stable_seconds)
    frame_hash = sha256(frame1)
    if sha256(frame2) != frame_hash:
        raise RuntimeError("framebuffer changed during the stability gate")

    create = Path(__file__).resolve().parents[1] / "create_checkpoint.py"
    command = [
        sys.executable, str(create), "--tag", args.tag,
        "--monitor", str(monitor), "--pid-file", str(pid_path),
        "--launch-manifest", str(launch_path), "--disk", str(ans_disk(launch["argv"])),
        "--serial-log", str(serial_path), "--out", str(args.out.resolve()),
    ]
    if args.marker_regex:
        command += ["--marker-regex", args.marker_regex]
    subprocess.run(command, check=True)

    evidence = args.out.resolve() / "evidence"
    visible = evidence / "interaction-source.png"
    shutil.copy2(frame2, visible)
    atomic_json(args.out.resolve() / "interaction.json", {
        "format": "darwin-vm-interaction-checkpoint-v1",
        "source_run": str(run),
        "source_pid_terminated": pid,
        "input": second,
        "display_power_on": power is True,
        "display_evidence": power_evidence,
        "stable_seconds": args.stable_seconds,
        "visible_frame": {"path": str(visible), "sha256": frame_hash},
    })
    print(json.dumps({
        "manifest": str(args.out.resolve() / "manifest.json"),
        "interaction": str(args.out.resolve() / "interaction.json"),
        "visible_frame_sha256": frame_hash,
    }, indent=2))


def restore(args: argparse.Namespace) -> None:
    tool = Path(__file__).resolve().parents[1] / "restore_checkpoint.py"
    command = [
        sys.executable, str(tool), str(args.manifest.resolve()),
        "--tag", args.tag, "--out", str(args.out.resolve()), "--interactive",
        "--ready-timeout", str(args.ready_timeout),
    ]
    if args.vnc_port is not None:
        command += ["--vnc-port", str(args.vnc_port)]
    if args.qemu is not None:
        command += ["--qemu", str(args.qemu.resolve())]
    for value in args.model_env:
        command += ["--model-env", value]
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("capture", help="capture one visible, idle interaction state")
    make.add_argument("--run", type=Path, required=True)
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--tag", required=True)
    make.add_argument("--marker-regex", default="")
    make.add_argument("--stable-seconds", type=float, default=0.5)
    make.add_argument("--input-idle-timeout", type=float, default=5.0)
    make.add_argument(
        "--accept-visible-frame-power", action="store_true",
        help="for a restore with no new A484 transition, record stable visible "
             "pixels as a narrower capture gate",
    )

    load = sub.add_parser("restore", help="restore as a persistent interactive session")
    load.add_argument("manifest", type=Path)
    load.add_argument("--out", type=Path, required=True)
    load.add_argument("--tag", required=True)
    load.add_argument("--ready-timeout", type=float, default=30.0)
    load.add_argument("--vnc-port", type=int)
    load.add_argument("--qemu", type=Path)
    load.add_argument("--model-env", action="append", default=[],
                      metavar="KEY=VALUE",
                      help="explicit DARWIN_/GXFSTAT_ restore override")

    args = parser.parse_args()
    if not SAFE_TAG.fullmatch(args.tag):
        parser.error("invalid tag")
    if getattr(args, "stable_seconds", 0.5) <= 0:
        parser.error("--stable-seconds must be positive")
    if getattr(args, "input_idle_timeout", 5.0) <= 0:
        parser.error("--input-idle-timeout must be positive")
    if getattr(args, "ready_timeout", 30.0) <= 0:
        parser.error("--ready-timeout must be positive")
    if args.command == "capture":
        capture(args)
    else:
        restore(args)


if __name__ == "__main__":
    main()
