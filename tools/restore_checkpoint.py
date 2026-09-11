#!/usr/bin/env python3
"""Restore a darwin-vm checkpoint into a new QEMU and fresh qcow2 child."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path

from checkpoint_common import (
    HMP, SAFE_TAG, atomic_json, parse_migration_status, parse_pc, pid_alive,
    restore_argv, sha256, serial_hex_clock_bounds, sptm_panic_message,
    verify_backing_chain, selected_cpu_index,
)


BOOT_PATTERNS = ("Darwin Kernel Version", "Darwin Bootstrapper Version", "launchd[1]")


def parse_model_env_overrides(values: list[str]) -> dict[str, str]:
    result = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or not re.fullmatch(r"(?:DARWIN_|GXFSTAT_)[A-Za-z0-9_]+", key):
            raise ValueError("--model-env requires a DARWIN_/GXFSTAT_ KEY=VALUE")
        result[key] = value
    return result


def checkpoint_source_cpu(manifest: dict) -> int:
    index = manifest.get("source_cpu_index")
    if index is None:
        # Earlier v1 captures preserved this choice in the CPU inventory.
        inventory = manifest.get("inventory", {}).get("cpus")
        index = selected_cpu_index(Path(inventory).read_text()) if inventory else 0
    if type(index) is not int or index < 0:
        raise RuntimeError("invalid source CPU index")
    return index


def activate_paused_disks(qmp_path: Path) -> None:
    """Acquire incoming disk ownership without running a guest instruction.

    QEMU's HMP/QMP cont activates migrated block nodes, but gdbstub continue
    calls vm_start directly (gdbstub/system.c). A first LLDB continue would
    otherwise abort on BDRV_O_INACTIVE at the first ANS write.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(30)
        connection.connect(str(qmp_path))
        with connection.makefile("rwb") as stream:
            greeting = json.loads(stream.readline())
            if "QMP" not in greeting:
                raise RuntimeError(f"invalid QMP greeting: {greeting}")
            for index, command in enumerate((
                {"execute": "qmp_capabilities"},
                {"execute": "blockdev-set-active", "arguments": {"active": True}},
            )):
                command["id"] = index
                stream.write(json.dumps(command).encode() + b"\n")
                stream.flush()
                while True:
                    line = stream.readline()
                    if not line:
                        raise RuntimeError("QMP closed before block activation")
                    response = json.loads(line)
                    if response.get("id") != index:
                        continue
                    if "error" in response:
                        raise RuntimeError(f"QMP {command['execute']}: {response['error']}")
                    if "return" not in response:
                        raise RuntimeError(f"invalid QMP response: {response}")
                    break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--gdb-port", type=int)
    parser.add_argument("--observe-seconds", type=int, default=120)
    parser.add_argument("--leave-paused", action="store_true",
                        help="load and verify the exact checkpoint PC without executing; "
                             "attach debugger probes before resuming")
    parser.add_argument("--interactive", action="store_true",
                        help="restore as a persistent interactive input-development session; "
                             "creates run-local QMP/monitor sockets and waits for an idle helper")
    parser.add_argument("--ready-timeout", type=float, default=30.0,
                        help="interactive native-input readiness deadline in seconds")
    parser.add_argument("--vnc-port", type=int,
                        help="interactive-only localhost VNC TCP port (for example 5989)")
    parser.add_argument("--qemu-data-dir", type=Path,
                        help="QEMU data directory containing keymaps (used with --vnc-port)")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--qemu", type=Path,
                        help="explicit compatible QEMU override for development replay; "
                             "records both binary hashes, never changes the checkpoint")
    parser.add_argument("--model-env", action="append", default=[], metavar="KEY=VALUE",
                        help="explicit model-only environment override for development replay")
    parser.add_argument("--display", choices=("none", "cocoa", "cocoa,zoom-to-fit=on",
                                              "cocoa,zoom-to-fit=on,show-cursor=on", "sdl"),
                        help="override only the host display backend on restore")
    args = parser.parse_args()
    try:
        model_env_overrides = parse_model_env_overrides(args.model_env)
    except ValueError as error:
        parser.error(str(error))
    if not SAFE_TAG.fullmatch(args.tag):
        parser.error("--tag must use 1-64 letters, digits, dots, underscores or dashes")
    if args.observe_seconds < 0:
        parser.error("--observe-seconds must be non-negative")
    if args.ready_timeout <= 0:
        parser.error("--ready-timeout must be positive")
    if args.interactive and args.leave_paused:
        parser.error("--interactive and --leave-paused are mutually exclusive")
    if args.vnc_port is not None:
        if not args.interactive:
            parser.error("--vnc-port requires --interactive")
        if not 5900 <= args.vnc_port <= 65535:
            parser.error("--vnc-port must be in 5900..65535")
    elif args.qemu_data_dir is not None:
        parser.error("--qemu-data-dir requires --vnc-port")
    if args.leave_paused:
        args.observe_seconds = 0
    if args.interactive:
        args.observe_seconds = 0

    manifest = json.loads(args.manifest.read_text())
    if manifest.get("format") != "darwin-vm-external-checkpoint-v1":
        raise RuntimeError("unsupported checkpoint manifest format")
    source_cpu_index = checkpoint_source_cpu(manifest)
    state = Path(manifest["vmstate"]["path"])
    source_disk = Path(manifest["disk"]["path"])
    if sha256(state) != manifest["vmstate"]["sha256"]:
        raise RuntimeError("VM-state hash mismatch")
    if sha256(source_disk) != manifest["disk"]["sha256"]:
        raise RuntimeError("immutable checkpoint disk hash mismatch")
    if source_disk.stat().st_mode & 0o222:
        raise RuntimeError("checkpoint disk has regained write permission")
    verify_backing_chain(manifest["disk"]["backing_chain"])

    out = (args.out or args.manifest.resolve().parent / "restores" / args.tag).resolve()
    if args.interactive:
        monitor = out / "monitor.sock"
        uart = out / "uart.sock"
        qmp = out / "qmp.sock"
    else:
        monitor = Path("/tmp/dvm") / f"{args.tag}.restore.sock"
        uart = Path("/tmp/dvm") / f"{args.tag}.restore.uart.sock"
        qmp = Path("/tmp/dvm") / f"{args.tag}.restore.qmp.sock"
    pid_file = out / "qemu.pid"
    serial = out / "serial.log"
    stderr = out / "qemu.stderr.log"
    disk = out / "disk.qcow2"
    report_path = out / "restore-report.json"
    for path in (monitor, uart, qmp):
        if path.exists():
            raise RuntimeError(f"refusing to reuse existing socket path {path}")

    source_qemu = Path(manifest["qemu_argv"][0]).resolve()
    qemu = (args.qemu or source_qemu).resolve()
    qemu_img = qemu.with_name("qemu-img")
    expected_qemu = manifest["qemu_inputs"].get(str(source_qemu), {}).get("sha256")
    actual_qemu = sha256(qemu)
    if not args.qemu and expected_qemu and actual_qemu != expected_qemu:
        raise RuntimeError("QEMU binary differs from the checkpoint source")
    for name, expected in manifest["qemu_inputs"].items():
        path = Path(name)
        if args.qemu and path.resolve() == source_qemu:
            continue
        if not path.is_file() or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"QEMU input differs from checkpoint source: {path}")
    # Preserve the restore tag when immutable inputs fail verification.
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    subprocess.run([
        str(qemu_img), "create", "-f", "qcow2", "-F", "qcow2",
        "-b", str(source_disk), str(disk),
    ], check=True)

    argv = restore_argv(
        manifest["qemu_argv"], source_disk, disk, monitor, serial, uart,
        state, args.gdb_port,
    )
    argv[0] = str(qemu)
    if args.display is not None:
        # Host presentation only; preserve every guest machine/CPU argument.
        if "-display" in argv:
            argv[argv.index("-display") + 1] = args.display
        else:
            argv += ["-display", args.display]
    if args.leave_paused or args.interactive:
        argv += ["-qmp", f"unix:{qmp},server=on,wait=off"]
    if args.vnc_port is not None:
        data_dir = (args.qemu_data_dir or
                    Path(__file__).resolve().parents[1] / "qemu-sptm" / "pc-bios")
        data_dir = data_dir.resolve()
        if not (data_dir / "keymaps" / "en-us").is_file():
            raise RuntimeError(f"QEMU data directory has no en-us keymap: {data_dir}")
        argv += ["-L", str(data_dir)]
        argv += ["-vnc", f"127.0.0.1:{args.vnc_port - 5900}"]
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("DARWIN_") and not key.startswith("GXFSTAT_")
    }
    env.update(manifest.get("qemu_env", {}))
    env.update(model_env_overrides)
    input_status = out / "input-status.json"
    if args.interactive:
        env["DARWIN_TOUCH_EVENTS"] = str(out / "events.jsonl")
        if env.get("DARWIN_INPUT_UART", "0") != "0":
            env["DARWIN_INPUT_STATUS"] = str(input_status)
        if env.get("DARWIN_DCP_TRANSITION_TRACE_DIR"):
            transitions = out / "transitions"
            transitions.mkdir()
            env["DARWIN_DCP_TRANSITION_TRACE_DIR"] = str(transitions)
    # A restored VM can itself become a checkpoint source. Record the same
    # exact launch schema used by fresh boots, without unrelated host env.
    atomic_json(out / "launch.json", {
        "format": "darwin-vm-qemu-launch-v1", "argv": argv,
        "env": {key: value for key, value in env.items()
                if key.startswith(("DARWIN_", "GXFSTAT_"))},
    })
    started = time.monotonic()
    stderr_file = stderr.open("wb")
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=stderr_file,
                            env=env, start_new_session=True)
    pid_file.write_text(f"{proc.pid}\n", encoding="ascii")
    try:
        monitor_deadline = time.monotonic() + 120
        while not monitor.exists():
            if proc.poll() is not None:
                detail = stderr.read_text(errors="replace") if stderr.exists() else ""
                raise RuntimeError(
                    f"restored QEMU exited with status {proc.returncode} before "
                    f"creating its monitor: {detail[-2000:]}"
                )
            if time.monotonic() >= monitor_deadline:
                raise TimeoutError(f"timed out waiting for {monitor}")
            time.sleep(0.1)
        hmp = HMP(monitor)
        deadline = time.monotonic() + 120
        while True:
            if proc.poll() is not None:
                raise RuntimeError(f"restored QEMU exited with status {proc.returncode}")
            status = hmp.command("info status")
            migration = hmp.command("info migrate -a")
            migration_status = parse_migration_status(migration)
            if "paused" in status and migration_status == "completed":
                break
            if migration_status in {"failed", "cancelled"}:
                raise RuntimeError(migration)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "restored QEMU did not finish incoming migration in the "
                    f"paused state: {status}\n{migration}"
                )
            time.sleep(0.2)
        hmp.command(f"cpu {source_cpu_index}")
        if selected_cpu_index(hmp.command("info cpus")) != source_cpu_index:
            raise RuntimeError("restored VM does not have the source witness CPU")
        registers = hmp.command("info registers")
        restored_pc = parse_pc(registers)
        (out / "pre-resume-registers.txt").write_text(registers + "\n")
        if restored_pc != manifest["source_pc"]:
            raise RuntimeError(
                f"restored PC {restored_pc} != checkpoint PC {manifest['source_pc']}"
            )
        if args.leave_paused:
            activate_paused_disks(qmp)
            if ("paused" not in hmp.command("info status") or
                    parse_pc(hmp.command("info registers")) != restored_pc):
                raise RuntimeError("guest advanced during paused block activation")
        stderr_before_resume = stderr.stat().st_size if stderr.exists() else 0
        serial_before_resume = serial.stat().st_size if serial.exists() else 0
        restore_seconds = time.monotonic() - started
        interactive_ready = None
        if not args.leave_paused:
            hmp.command("cont")
            if args.interactive and env.get("DARWIN_INPUT_UART", "0") != "0":
                ready_deadline = time.monotonic() + args.ready_timeout
                last_status = None
                while time.monotonic() < ready_deadline:
                    if proc.poll() is not None:
                        raise RuntimeError(
                            f"restored QEMU exited with status {proc.returncode}"
                        )
                    try:
                        last_status = json.loads(input_status.read_text())
                    except (OSError, ValueError):
                        last_status = None
                    if (last_status and last_status.get("guest_state") == "R" and
                            last_status.get("inflight") == 0 and
                            last_status.get("queue_len") == 0 and
                            last_status.get("wire_pending", 0) == 0 and
                            last_status.get("wheel_pending", 0) == 0 and
                            not last_status.get("contact_sent", False)):
                        interactive_ready = {
                            key: last_status.get(key) for key in (
                                "epoch", "guest_pid", "guest_state", "inflight",
                                "queue_len", "wire_pending", "contact_sent",
                                "timeouts", "dispatch_failed",
                            )
                        }
                        break
                    time.sleep(0.05)
                else:
                    raise RuntimeError(
                        "native input helper did not become idle after restore: "
                        f"{last_status}"
                    )
            if args.interactive:
                ready_frame = out / "ready.png"
                answer = hmp.command(f"screendump {ready_frame} -f png")
                if not ready_frame.is_file() or ready_frame.stat().st_size < 1024:
                    raise RuntimeError(f"interactive ready screendump failed: {answer}")
        peak_rss_kib = 0
        observation_started = time.monotonic()
        observe_deadline = time.monotonic() + args.observe_seconds
        serial_scan_offset = serial_before_resume
        serial_scan_tail = b""
        panic_seen = False
        while time.monotonic() < observe_deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"restored QEMU exited with status {proc.returncode}")
            rss = subprocess.check_output(
                ["ps", "-p", str(proc.pid), "-o", "rss="], text=True
            ).strip()
            if rss:
                peak_rss_kib = max(peak_rss_kib, int(rss))
            if serial.exists() and serial.stat().st_size > serial_scan_offset:
                with serial.open("rb") as serial_file:
                    serial_file.seek(serial_scan_offset)
                    new_serial = serial_file.read()
                serial_scan_offset += len(new_serial)
                scan = serial_scan_tail + new_serial
                if b"panic(cpu" in scan:
                    panic_seen = True
                    break
                serial_scan_tail = scan[-32:]
            time.sleep(min(0.5, observe_deadline - time.monotonic()))
        observed_seconds = time.monotonic() - observation_started
        hmp.command("stop")
        after_registers = hmp.command("info registers")
        after_pc = parse_pc(after_registers)
        sptm_panic = sptm_panic_message(hmp, after_registers)
        (out / "post-observation-registers.txt").write_text(after_registers + "\n")
        serial_bytes = serial.read_bytes()[serial_before_resume:] if serial.exists() else b""
        serial_text = serial_bytes.decode(errors="replace")
        repeated = [pattern for pattern in BOOT_PATTERNS if pattern in serial_text]
        qemu_bytes = stderr.read_bytes()[stderr_before_resume:] if stderr.exists() else b""
        qemu_text = qemu_bytes.decode(errors="replace")
        serial_clock_first, serial_clock_last = serial_hex_clock_bounds(serial_text)
        source_clock_last = manifest.get("source_serial_clock_last")
        traffic = {
            "ans_reads": len(re.findall(r"^ans\([^\n]*\): READ\s", qemu_text,
                                        re.MULTILINE)),
            "ans_writes": len(re.findall(r"^ans\([^\n]*\): WRITE\s", qemu_text,
                                         re.MULTILINE)),
            "sks_replies": len(re.findall(r"^sep\([^\n]*\): sks code .* replied with ",
                                          qemu_text, re.MULTILINE)),
            "dcp_messages": len(re.findall(r"^(?:dcp:|afk\()", qemu_text,
                                           re.MULTILINE)),
            "iomfb_messages": len(re.findall(r"^iomfb:", qemu_text,
                                             re.MULTILINE)),
        }
        first_panic = next((line for line in serial_text.splitlines()
                            if "panic(cpu" in line), "")
        acceptance = {
            "different_qemu_process": proc.pid != manifest["source_pid_terminated"],
            "exact_pc_before_resume": restored_pc == manifest["source_pc"],
            "execution_progressed": after_pc != restored_pc,
            "no_repeated_boot_banner": not repeated,
            "no_xnu_panic": not first_panic,
            "no_sptm_panic": not sptm_panic,
            "post_resume_serial_activity": bool(serial_bytes),
            "serial_clock_continued": (
                source_clock_last is not None and serial_clock_first is not None and
                serial_clock_last is not None and
                serial_clock_first >= source_clock_last and
                serial_clock_last >= serial_clock_first
            ),
            "ans_read_and_write_traffic": bool(traffic["ans_reads"] and
                                               traffic["ans_writes"]),
            "sks_completed_request": bool(traffic["sks_replies"]),
            "dcp_and_iomfb_traffic": bool(traffic["dcp_messages"] and
                                          traffic["iomfb_messages"]),
        }
        report = {
            "format": "darwin-vm-restore-report-v1",
            "tag": args.tag,
            "source_manifest": str(args.manifest.resolve()),
            "qemu_binary": {
                "source_sha256": expected_qemu, "restore_sha256": actual_qemu,
                "override": args.qemu is not None,
                "development_replay": actual_qemu != expected_qemu,
            },
            "qemu_pid": proc.pid,
            "monitor": str(monitor),
            "qmp": str(qmp) if args.leave_paused or args.interactive else None,
            "uart": str(uart),
            "disk_child": str(disk),
            "source_pc": manifest["source_pc"],
            "source_cpu_index": source_cpu_index,
            "model_env_overrides": model_env_overrides,
            "qemu_env": {key: value for key, value in env.items()
                         if key.startswith(("DARWIN_", "GXFSTAT_"))},
            "restored_pc": restored_pc,
            "pc_after_observation": after_pc,
            "pc_match_witness": restored_pc == manifest["source_pc"],
            "execution_progress_witness": after_pc != restored_pc,
            "restore_seconds": round(restore_seconds, 3),
            "peak_rss_kib": peak_rss_kib,
            "disk_child_bytes": disk.stat().st_size,
            "observation_seconds_requested": args.observe_seconds,
            "left_paused": args.leave_paused,
            "observation_seconds_actual": round(observed_seconds, 3),
            "observation_ended_on_panic": panic_seen,
            "interactive": args.interactive,
            "interactive_ready": interactive_ready,
            "ready_frame": ({
                "path": str(out / "ready.png"),
                "sha256": sha256(out / "ready.png"),
            } if args.interactive else None),
            "vnc_port": args.vnc_port,
            "qemu_alive": pid_alive(proc.pid),
            "serial_bytes_before_resume": serial_before_resume,
            "serial_bytes_after_resume": len(serial_bytes),
            "qemu_stderr_bytes_before_resume": stderr_before_resume,
            "qemu_stderr_bytes_after_resume": len(qemu_bytes),
            "serial_clock": {
                "source_last": source_clock_last,
                "restore_first": serial_clock_first,
                "restore_last": serial_clock_last,
            },
            "repeated_boot_patterns": repeated,
            "first_panic": first_panic,
            "sptm_panic": sptm_panic,
            "traffic_counts": traffic,
            "acceptance_witnesses": acceptance,
            "argv": argv,
        }
        atomic_json(report_path, report)
        summary = {
            "report": str(report_path), "qemu_pid": proc.pid,
            "restored_pc": restored_pc,
            "restore_seconds": round(restore_seconds, 3),
            "serial_bytes": report["serial_bytes_after_resume"],
            "repeated_boot_patterns": repeated,
            "first_panic": report["first_panic"],
            "sptm_panic": sptm_panic,
            "acceptance_witnesses": acceptance,
            "guest_left_running": not (args.leave_paused or first_panic or sptm_panic),
        }
        if first_panic or sptm_panic:
            # A panicked restore is not useful to leave consuming host memory.
            # Reap only the child whose argv and PID this invocation owns.
            hmp.command("quit")
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=10)
            print(json.dumps(summary, indent=2))
            return 1
        if not args.leave_paused:
            hmp.command("cont")
        print(json.dumps(summary, indent=2))
        return 0
    except Exception:
        # Reap only the exact child this invocation created.
        if proc.poll() is None:
            try:
                HMP(monitor, timeout=5).command("quit")
            except Exception:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        raise
    finally:
        stderr_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
