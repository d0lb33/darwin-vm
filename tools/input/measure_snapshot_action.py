#!/usr/bin/env python3
"""Restore one known UI snapshot, perform one action, and retain its timeline.

Each invocation creates a fresh qcow2 child and QEMU process, so a delayed app
launch or stale input cannot contaminate the next trial.  It records QEMU's
source-clock input, presentation and native completion timestamps.  Sampled
PPM comparisons nominate visible changes; the screenshots remain the semantic
evidence and must be reviewed before naming the resulting screen.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_input import (HMP, gesture, idle, key_press, read_status, ready,
                          successful)  # noqa: E402


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    match = re.match(rb"P6\s+(?:#[^\n]*\s+)*(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not match or int(match[3]) != 255:
        raise ValueError(f"unsupported PPM: {path}")
    width, height = int(match[1]), int(match[2])
    pixels = data[match.end():]
    if len(pixels) != width * height * 3:
        raise ValueError(f"short PPM pixels: {path}")
    return width, height, pixels


def sample_six(path: Path) -> tuple[int, int, bytes]:
    width, height, pixels = read_ppm(path)
    out_width, out_height = (width + 5) // 6, (height + 5) // 6
    sampled = bytearray(out_width * out_height * 3)
    for y in range(out_height):
        for x in range(out_width):
            source = ((y * 6) * width + x * 6) * 3
            target = (y * out_width + x) * 3
            sampled[target:target + 3] = pixels[source:source + 3]
    return out_width, out_height, bytes(sampled)


def changed_fraction(a: bytes, b: bytes, width: int, height: int,
                     threshold: int = 8) -> float:
    if len(a) != len(b) or len(a) != width * height * 3:
        raise ValueError("frame geometry mismatch")
    y0, y1 = height // 10, height * 9 // 10
    changed = total = 0
    for pixel in range(y0 * width, y1 * width):
        offset = pixel * 3
        total += 1
        if max(abs(a[offset + channel] - b[offset + channel])
               for channel in range(3)) > threshold:
            changed += 1
    return changed / total


def process_sample(pid: int) -> dict | None:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "%cpu=,rss=,state="],
        capture_output=True, text=True, check=False,
    )
    fields = result.stdout.split()
    if result.returncode or len(fields) < 3:
        return None
    return {"host_ns": time.monotonic_ns(), "cpu_percent": float(fields[0]),
            "rss_kib": int(fields[1]), "state": fields[2]}


def parse_jit(text: str) -> dict[str, int]:
    patterns = {
        "generated_code_bytes": r"gen code size\s+(\d+)/",
        "translation_blocks": r"TB count\s+(\d+)",
        "tb_flushes": r"TB flush count\s+(\d+)",
        "tb_invalidations": r"TB invalidate count\s+(\d+)",
        "tlb_full_flushes": r"TLB full flushes\s+(\d+)",
        "tlb_partial_flushes": r"TLB partial flushes\s+(\d+)",
        "tlb_elided_flushes": r"TLB elided flushes\s+(\d+)",
    }
    result = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            result[name] = int(match[1])
    return result


def stop_owned(run: Path, pid: int) -> None:
    try:
        hmp = HMP(run / "monitor.sock")
        hmp.command("quit")
        hmp.sock.close()
    except OSError:
        pass
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    command = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True,
        text=True, check=False,
    ).stdout
    if str(run) not in command:
        raise RuntimeError(f"pid {pid} no longer identifies owned run {run}")
    os.kill(pid, 15)
    for _ in range(70):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise RuntimeError(f"owned QEMU {pid} resisted HMP quit and SIGTERM")


def parse_timeline(text: str, frames_dir: Path, baseline: bytes,
                   width: int, height: int, significant: float,
                   major: float = 0.8) -> dict:
    inputs = [
        {"epoch": int(m[1]), "seq": int(m[2]), "kind": m[3],
         "a": int(m[4]), "b": int(m[5]), "ns": int(m[6])}
        for m in re.finditer(
            r"darwin-input: timing epoch=(\d+) seq=(\d+) kind=([A-Z]) "
            r"a=(\d+) b=(\d+) c=\d+ monotonic_ns=(\d+)", text)
    ]
    if not inputs:
        raise ValueError("no source-clock input records")
    origin = inputs[0]["ns"]
    pending: dict | None = None
    completed: dict[int, dict] = {}
    for line in text.splitlines():
        match = re.match(
            r"iomfb-timing: present swap=(\d+) scanout_us=(\d+) "
            r"monotonic_ns=(\d+)", line)
        if match:
            if pending is not None:
                raise ValueError("presentation overlapped native completion")
            pending = {"swap": int(match[1]), "scanout_us": int(match[2]),
                       "present_ns": int(match[3])}
            continue
        match = re.match(
            r"iomfb-timing: complete swap=(\d+) status=(\d+) "
            r"monotonic_ns=(\d+)", line)
        if match and pending is not None:
            if int(match[1]) != pending["swap"] or int(match[2]) != 0:
                raise ValueError("native completion identity/status failure")
            pending["complete_ns"] = int(match[3])
            completed[pending["present_ns"]] = pending
            pending = None
    captures = {
        int(m[2]): (m[1], int(m[3]), int(m[4]))
        for m in re.finditer(
            r"iomfb: transition file=(\S+) present_ns=(\d+) "
            r"capture_us=(\d+) ok=(\d+)", text)
        if m[4] == "1"
    }
    rows = []
    previous = baseline
    for present_ns, (name, capture_us, _ok) in sorted(captures.items()):
        frame_width, frame_height, pixels = read_ppm(frames_dir / name)
        if (frame_width, frame_height) != (width, height):
            raise ValueError("transition thumbnail geometry mismatch")
        timing = completed.get(present_ns)
        rows.append({
            "file": name,
            "since_input_ms": (present_ns - origin) / 1e6,
            "changed_from_start": changed_fraction(baseline, pixels, width, height),
            "changed_from_previous": changed_fraction(previous, pixels, width, height),
            "scanout_us": timing["scanout_us"] if timing else None,
            "presentation_to_completion_ms": (
                (timing["complete_ns"] - present_ns) / 1e6 if timing else None),
            "capture_us": capture_us,
        })
        previous = pixels
    meaningful = [row for row in rows if row["changed_from_start"] >= significant]
    major_changes = [row for row in rows if row["changed_from_start"] >= major]
    intervals = [(later["since_input_ms"] - earlier["since_input_ms"])
                 for earlier, later in zip(rows, rows[1:])]
    return {
        "inputs": inputs,
        "frames": rows,
        "first_present_ms": rows[0]["since_input_ms"] if rows else None,
        "first_significant_visible_change_ms": (
            meaningful[0]["since_input_ms"] if meaningful else None),
        "significant_change_threshold": significant,
        "first_major_visible_change_ms": (
            major_changes[0]["since_input_ms"] if major_changes else None),
        "major_change_threshold": major,
        "largest_presentation_gaps_ms": sorted(intervals, reverse=True)[:10],
        "all_presentations_have_completion": bool(rows) and all(
            row["presentation_to_completion_ms"] is not None for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--warmup-seconds", type=float, default=2.0)
    parser.add_argument("--observe-seconds", type=float, default=30.0)
    parser.add_argument("--significant", type=float, default=0.01)
    parser.add_argument("--major", type=float, default=0.8)
    parser.add_argument("--hold-ms", type=int, default=80)
    parser.add_argument("--leave-running", action="store_true")
    parser.add_argument("--keep-runtime", action="store_true",
                        help="retain the stopped /tmp runtime for diagnosis")
    parser.add_argument("--retain-all-transitions", action="store_true",
                        help="retain every thumbnail instead of changed keyframes")
    parser.add_argument("--require-visible-change", action="store_true")
    parser.add_argument("--require-major-change", action="store_true")
    action = parser.add_subparsers(dest="action", required=True)
    tap = action.add_parser("tap")
    tap.add_argument("x", type=int); tap.add_argument("y", type=int)
    action.add_parser("home")
    swipe = action.add_parser("swipe")
    for name in ("x1", "y1", "x2", "y2"):
        swipe.add_argument(name, type=int)
    args = parser.parse_args()
    if not 0 <= args.warmup_seconds <= 60:
        parser.error("warmup must be 0..60 seconds")
    if not 1 <= args.observe_seconds <= 180:
        parser.error("observation must be 1..180 seconds")
    if not 0 < args.significant <= 1:
        parser.error("significant fraction must be in (0, 1]")
    if not args.significant < args.major <= 1:
        parser.error("major fraction must be greater than significant and <= 1")
    if not 0 <= args.hold_ms <= 2000:
        parser.error("hold must be 0..2000 ms")

    root = Path(__file__).resolve().parents[1]
    out = args.out.resolve()
    run = (Path("/tmp/dvm") / args.tag).resolve()
    out.mkdir(parents=True, exist_ok=False)
    if run.exists():
        raise RuntimeError(f"refusing to reuse runtime directory {run}")
    restore = [
        sys.executable, str(root / "restore_checkpoint.py"),
        str(args.manifest.resolve()), "--tag", args.tag, "--out", str(run),
        "--interactive", "--ready-timeout", "30", "--qemu",
        str(args.qemu.resolve()), "--model-env", "DARWIN_DCP_IOMFB_QUIET=1",
        "--model-env", "DARWIN_DCP_IOMFB_TIMING_TRACE=1",
    ]
    subprocess.run(restore, check=True)
    pid = int((run / "qemu.pid").read_text())
    should_stop = not args.leave_running
    completed_trial = False
    try:
        time.sleep(args.warmup_seconds)
        input_args = SimpleNamespace(ready_timeout=5, ack_timeout=40,
                                     frames=None, frame_wait=0, settle=0)
        before_status = ready(input_args, run / "input-status.json")
        epoch = before_status["epoch"]
        hmp = HMP(run / "monitor.sock")
        before_path = run / "before.ppm"
        hmp.command(f"screendump {json.dumps(str(before_path))} -f ppm")
        hmp.command(f"screendump {json.dumps(str(out / 'before.png'))} -f png")
        jit_before = hmp.command("info jit")
        hmp.sock.close()
        width, height, baseline = sample_six(before_path)
        log = run / "qemu.stderr.log"
        offset = log.stat().st_size
        serial_offset = (run / "serial.log").stat().st_size
        frames_dir = run / "transitions"
        (frames_dir / "transition.enable").touch()
        host_before = time.monotonic_ns()
        if args.action == "tap":
            points = [(round(args.x * 32767 / 1179),
                       round(args.y * 32767 / 2556))]
            result = gesture(input_args, run, points, args.hold_ms)
        elif args.action == "home":
            result = key_press(input_args, run, "f5", args.hold_ms)
        else:
            points = []
            for index in range(12):
                fraction = index / 11
                x = round((args.x1 + (args.x2 - args.x1) * fraction) * 32767 / 1179)
                y = round((args.y1 + (args.y2 - args.y1) * fraction) * 32767 / 2556)
                points.append((x, y))
            result = gesture(input_args, run, points, args.hold_ms)
        host_after_dispatch = time.monotonic_ns()
        samples = []
        deadline = time.monotonic() + args.observe_seconds
        while time.monotonic() < deadline:
            sample = process_sample(pid)
            if sample is None:
                raise RuntimeError("restored QEMU exited during observation")
            samples.append(sample)
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
        (frames_dir / "transition.enable").unlink(missing_ok=True)
        after_path = run / "after.ppm"
        hmp = HMP(run / "monitor.sock")
        hmp.command(f"screendump {json.dumps(str(after_path))} -f ppm")
        hmp.command(f"screendump {json.dumps(str(out / 'after.png'))} -f png")
        jit_after = hmp.command("info jit")
        hmp.sock.close()
        after_status = ready(input_args, run / "input-status.json")
        if (after_status["epoch"] != epoch or
                after_status["guest_state"] != "R" or
                after_status.get("contact_sent")):
            raise RuntimeError(f"input ownership was not preserved: {after_status}")
        with log.open("rb") as stream:
            stream.seek(offset); trace = stream.read().decode(errors="replace")
        with (run / "serial.log").open("rb") as stream:
            stream.seek(serial_offset); serial = stream.read().decode(errors="replace")
        timeline = parse_timeline(trace, frames_dir, baseline, width, height,
                                  args.significant, args.major)
        evidence_frames = out / "transitions"
        evidence_frames.mkdir()
        rows = timeline["frames"]
        retained = set()
        if rows:
            retained.update((0, len(rows) - 1))
        retained.update(index for index, row in enumerate(rows)
                        if row["changed_from_previous"] >= 0.002)
        for index, row in enumerate(rows):
            row["retained"] = args.retain_all_transitions or index in retained
            if row["retained"]:
                shutil.copy2(frames_dir / row["file"],
                             evidence_frames / row["file"])
        shutil.copy2(run / "restore-report.json", out / "restore-report.json")
        (out / "trace.log").write_text(trace)
        (out / "serial.log").write_text(serial)
        (out / "input-before.json").write_text(
            json.dumps(before_status, indent=2) + "\n")
        (out / "input-after.json").write_text(
            json.dumps(after_status, indent=2) + "\n")
        jit_before_parsed = parse_jit(jit_before)
        jit_after_parsed = parse_jit(jit_after)
        jit_delta = {key: jit_after_parsed[key] - value
                     for key, value in jit_before_parsed.items()
                     if key in jit_after_parsed}
        visible_required = args.require_visible_change or args.action == "swipe"
        acceptance = {
            "input_completed_without_failure": successful(result),
            "ownership_preserved": True,
            "native_completions_paired": (
                timeline["all_presentations_have_completion"]
                if timeline["frames"] else True),
            "visible_change_when_required": (
                not visible_required or
                timeline["first_significant_visible_change_ms"] is not None),
            "major_change_when_required": (
                not args.require_major_change or
                timeline["first_major_visible_change_ms"] is not None),
        }
        accepted = all(acceptance.values())
        report = {
            "scope": "one action from a fresh exact interaction checkpoint restore",
            "source_manifest": str(args.manifest.resolve()),
            "restore": json.loads((run / "restore-report.json").read_text()),
            "action": vars(args),
            "host": {"injection_before_ns": host_before,
                     "dispatch_observed_ns": host_after_dispatch,
                     "dispatch_observation_ms":
                         (host_after_dispatch - host_before) / 1e6,
                     "qemu_samples": samples},
            "tcg_jit": {"before": jit_before_parsed,
                        "after": jit_after_parsed, "delta": jit_delta,
                        "raw_before": jit_before, "raw_after": jit_after},
            "input_result": result,
            "input_before": before_status,
            "input_after": after_status,
            "timeline": timeline,
            "stderr_bytes": len(trace.encode()),
            "stderr_lines": trace.count("\n"),
            "serial_bytes": len(serial.encode()),
            "panic": next((line for line in serial.splitlines()
                           if "panic(cpu" in line), ""),
            "acceptance": acceptance,
            "limitations": (
                "PPM differences identify candidate visible changes, not screen semantics. "
                "QEMU presentation is earlier than VNC decode/display. Host status polling "
                "bounds dispatch observation; QEMU source timestamps carry causal timing."
            ),
        }
        # pathlib objects from argparse are not JSON values.
        report["action"]["manifest"] = str(args.manifest.resolve())
        report["action"]["out"] = str(out)
        report["action"]["qemu"] = str(args.qemu.resolve())
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        completed_trial = True
        print(json.dumps({
            "report": str(out / "report.json"),
            "restore_seconds": report["restore"]["restore_seconds"],
            "input": result,
            "first_present_ms": timeline["first_present_ms"],
            "first_significant_visible_change_ms":
                timeline["first_significant_visible_change_ms"],
            "first_major_visible_change_ms":
                timeline["first_major_visible_change_ms"],
            "presentations": len(timeline["frames"]),
            "panic": report["panic"],
            "accepted": accepted,
            "left_running": args.leave_running,
        }, indent=2))
        return 0 if accepted and not report["panic"] else 2
    finally:
        if should_stop:
            stop_owned(run, pid)
            if completed_trial and not args.keep_runtime:
                shutil.rmtree(run)


if __name__ == "__main__":
    raise SystemExit(main())
