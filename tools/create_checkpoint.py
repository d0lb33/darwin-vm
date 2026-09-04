#!/usr/bin/env python3
"""Create a RAM/device migration stream paired with an immutable ANS disk."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from checkpoint_common import (
    HMP, SAFE_TAG, atomic_json, parse_migration_status, parse_pc, pid_alive,
    process_argv_env, qcow2_backing_chain, qemu_input_files,
    serial_hex_clock_bounds, sha256, wait_pid_exit,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--monitor", type=Path, required=True)
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--launch-manifest", type=Path, required=True)
    parser.add_argument("--disk", type=Path, required=True)
    parser.add_argument("--serial-log", type=Path, required=True)
    parser.add_argument("--marker-regex", default="")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    if not SAFE_TAG.fullmatch(args.tag):
        parser.error("--tag must use 1-64 letters, digits, dots, underscores or dashes")
    out = (args.out or Path("/tmp/dvm/checkpoints") / args.tag).resolve()
    state = out / "vmstate.bin"
    manifest_path = out / "manifest.json"
    evidence = out / "evidence"

    pid = int(args.pid_file.read_text(encoding="ascii").strip())
    if not pid_alive(pid):
        raise RuntimeError(f"source QEMU pid {pid} is not alive")
    live_argv, live_environ = process_argv_env(pid)
    launch = json.loads(args.launch_manifest.read_text())
    if launch.get("format") != "darwin-vm-qemu-launch-v1":
        raise RuntimeError("unsupported launch manifest format")
    argv = launch["argv"]
    environ = launch.get("env", {})
    if not live_argv or "qemu-system-aarch64" not in Path(live_argv[0]).name:
        raise RuntimeError(f"pid {pid} is not qemu-system-aarch64")
    if live_argv != argv:
        raise RuntimeError("live QEMU argv differs from the recorded launch manifest")
    live_model_env = {
        key: value for key, value in live_environ.items()
        if key.startswith("DARWIN_") or key.startswith("GXFSTAT_")
    }
    if live_model_env != environ:
        raise RuntimeError(
            "live QEMU Darwin-model environment differs from the launch manifest"
        )
    monitor = args.monitor.resolve()
    disk = args.disk.resolve()
    monitor_paths = [
        Path(item.removeprefix("unix:").split(",", 1)[0]).resolve()
        for item in argv if item.startswith("unix:")
    ]
    disk_paths = []
    for item in argv:
        parts = item.split(",")
        if "id=ans" not in parts:
            continue
        disk_paths.extend(Path(part[5:]).resolve() for part in parts
                          if part.startswith("file="))
    if monitor not in monitor_paths:
        raise RuntimeError(f"pid {pid} does not own monitor {monitor}")
    if disk not in disk_paths:
        raise RuntimeError(f"pid {pid} does not use disk {disk}")

    # Do not consume the tag with an empty directory when ownership or argv
    # validation fails.  From here onward the exact source has been verified.
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    evidence.mkdir()

    hmp = HMP(monitor)
    source_rss_kib = int(subprocess.check_output(
        ["ps", "-p", str(pid), "-o", "rss="], text=True
    ).strip())
    started = time.monotonic()
    hmp.command("stop")
    status = hmp.command("info status")
    if "paused" not in status:
        raise RuntimeError(f"QEMU did not pause: {status}")
    registers = hmp.command("info registers")
    pc = parse_pc(registers)
    (evidence / "pre-checkpoint-registers.txt").write_text(
        registers + "\n", encoding="utf-8"
    )
    (evidence / "qtree.txt").write_text(hmp.command("info qtree") + "\n")
    (evidence / "mtree.txt").write_text(hmp.command("info mtree") + "\n")
    (evidence / "cpus.txt").write_text(hmp.command("info cpus") + "\n")
    (evidence / "block.txt").write_text(hmp.command("info block") + "\n")

    serial_text = args.serial_log.read_text(errors="replace")
    _, source_clock_last = serial_hex_clock_bounds(serial_text)
    marker = None
    if args.marker_regex:
        matches = list(re.finditer(args.marker_regex, serial_text, re.MULTILINE))
        if not matches:
            raise RuntimeError("the requested guest marker is absent from the serial log")
        match = matches[-1]
        line_start = serial_text.rfind("\n", 0, match.start()) + 1
        line_end = serial_text.find("\n", match.end())
        if line_end < 0:
            line_end = len(serial_text)
        marker = {
            "regex": args.marker_regex,
            "match": match.group(0),
            "line": serial_text[line_start:line_end],
            "character_offset": match.start(),
        }
        (evidence / "guest-marker.txt").write_text(
            marker["line"] + "\n", encoding="utf-8"
        )

    # ANS commands are synchronous, but explicitly flush the backend as an
    # independent storage witness before freezing its qcow2 generation.
    flush = hmp.command('qemu-io ans "flush"')
    (evidence / "block-flush.txt").write_text(flush + "\n")
    hmp.command("migrate_set_parameter max-bandwidth 0")
    hmp.command(f"migrate -d file:{state}")
    deadline = time.monotonic() + args.timeout
    migration = ""
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            raise RuntimeError("source QEMU exited during migration")
        migration = hmp.command("info migrate -a")
        migration_status = parse_migration_status(migration)
        if migration_status == "completed":
            break
        if migration_status in {"failed", "cancelled"}:
            raise RuntimeError(migration)
        time.sleep(0.5)
    else:
        raise RuntimeError("migration did not finish before --timeout")
    migration_seconds = time.monotonic() - started
    (evidence / "migration-status.txt").write_text(migration + "\n")
    if not state.is_file() or state.stat().st_size < 4096:
        raise RuntimeError("migration reported completion but the state file is missing/short")
    # The stream contains guest RAM, Apple CPU implementation-defined state,
    # and SEP/SKS material.  It is a local secret, not a shareable build log.
    state.chmod(0o600)

    # Quit through the monitor, then wait only for the PID whose argv and
    # socket were verified above.  No process-name matching is used.
    hmp.command("quit")
    if not wait_pid_exit(pid, 30):
        raise RuntimeError(f"source QEMU pid {pid} did not exit after HMP quit")
    # Freeze the disk generation immediately after the verified source exits,
    # before any analysis or hashing that could itself fail.
    disk.chmod(0o444)

    qemu = Path(argv[0]).resolve()
    analyzer = qemu.parent.parent / "scripts" / "analyze-migration.py"
    if analyzer.is_file():
        analyzed = subprocess.run(
            ["python3", str(analyzer), "-f", str(state), "-d", "desc"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        (evidence / "migration-description.txt").write_text(
            analyzed.stdout, encoding="utf-8"
        )
    qemu_img = qemu.with_name("qemu-img")
    check = subprocess.run(
        [str(qemu_img), "check", "-f", "qcow2", str(disk)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    ).stdout
    (evidence / "disk-check.txt").write_text(check, encoding="utf-8")
    disk_chain = qcow2_backing_chain(qemu_img, disk)
    if disk_chain[0]["format"] != "qcow2" or len(disk_chain) < 2:
        raise RuntimeError("checkpoint disk is not a qcow2 child overlay")

    inputs = {}
    for path in [qemu, *qemu_input_files(argv)]:
        if path.is_file():
            inputs[str(path)] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    filtered_env = {
        key: value for key, value in environ.items()
        if key.startswith("DARWIN_") or key.startswith("GXFSTAT_")
    }
    total_seconds = time.monotonic() - started
    manifest = {
        "format": "darwin-vm-external-checkpoint-v1",
        "tag": args.tag,
        "created_unix": time.time(),
        "source_pid_terminated": pid,
        "source_pc": pc,
        "guest_marker": marker,
        "source_serial_clock_last": source_clock_last,
        "source_serial_log": {
            "path": str(args.serial_log.resolve()),
            "bytes": args.serial_log.stat().st_size,
            "sha256": sha256(args.serial_log),
        },
        "qemu_argv": argv,
        "qemu_env": filtered_env,
        "qemu_inputs": inputs,
        "vmstate": {
            "path": str(state),
            "bytes": state.stat().st_size,
            "sha256": sha256(state),
        },
        "disk": {
            "path": str(disk),
            "bytes": disk.stat().st_size,
            "sha256": disk_chain[0]["sha256"],
            "mode": oct(disk.stat().st_mode & 0o777),
            "qemu_img_check": str(evidence / "disk-check.txt"),
            "backing_chain": disk_chain,
        },
        "timing": {
            "migration_seconds": round(migration_seconds, 3),
            "checkpoint_total_seconds": round(total_seconds, 3),
            "source_rss_kib_at_stop": source_rss_kib,
        },
        "witnesses": {
            "migration_status": str(evidence / "migration-status.txt"),
            "state_file_hash": sha256(state),
            "explicit_ans_flush": str(evidence / "block-flush.txt"),
            "source_process_exited": True,
        },
        "inventory": {
            "qtree": str(evidence / "qtree.txt"),
            "mtree": str(evidence / "mtree.txt"),
            "cpus": str(evidence / "cpus.txt"),
            "block": str(evidence / "block.txt"),
        },
    }
    atomic_json(manifest_path, manifest)
    print(json.dumps({
        "manifest": str(manifest_path), "source_pc": pc,
        "migration_seconds": round(migration_seconds, 3),
        "checkpoint_total_seconds": round(total_seconds, 3),
        "vmstate_bytes": state.stat().st_size,
        "disk_bytes": disk.stat().st_size,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
