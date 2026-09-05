#!/usr/bin/env python3
"""Install staged restore-ramdisk helpers into one fresh qcow2 child.

The template is a disk-only restore launch description.  This tool deliberately
rejects migration RAM input, starts no debugger, and preserves all boot/model
arguments (including the template trust cache).  It only substitutes the
prepared ramdisk, a new child ANS disk, and isolated monitor/UART endpoints.

Success requires the guest's own ``DVM_GRAPHICS_PROBE_INSTALLED`` marker in
QEMU's serial log; a successful serial-client process is not sufficient.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from typing import Any

SAFE_TAG = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DEFAULT_INSTALLER = "/libexec/dvm-graphics-install.sh"
INSTALL_MARKER = b"DVM_GRAPHICS_PROBE_INSTALLED"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def load_boot_command():
    """Use the shared restore sanitiser, not a second launch rewriter."""
    tools_dir = Path(__file__).resolve().parents[1]
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        from warm_boot_probe import boot_command
    except ImportError as error:
        raise RuntimeError(
            "tools/warm_boot_probe.py is required for restore argv sanitising"
        ) from error
    return boot_command


def option_values(argv: list[str], option: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] == option:
            if index + 1 >= len(argv):
                raise ValueError(f"template option {option} has no value")
            values.append(argv[index + 1])
            index += 2
        else:
            index += 1
    return values


def rewrite_ramdisk(argv: list[str], ramdisk: Path) -> list[str]:
    values = option_values(argv, "-ramdisk")
    if len(values) != 1:
        raise ValueError("restore template must contain exactly one -ramdisk")
    result = list(argv)
    result[result.index("-ramdisk") + 1] = str(ramdisk)
    return result


def ans_drive_path(argv: list[str]) -> Path:
    matches: list[Path] = []
    for spec in option_values(argv, "-drive"):
        parts = spec.split(",")
        if "id=ans" not in parts:
            continue
        files = [part[5:] for part in parts if part.startswith("file=")]
        if len(files) != 1:
            raise ValueError("ANS -drive must contain exactly one file= component")
        matches.append(Path(files[0]).resolve())
    if len(matches) != 1:
        raise ValueError("template must contain exactly one id=ans -drive")
    return matches[0]


def validate_template(template: dict[str, Any], parent: Path) -> tuple[list[str], dict[str, str]]:
    if template.get("format") != "darwin-vm-qemu-launch-v1":
        raise ValueError("template format is not darwin-vm-qemu-launch-v1")
    argv = template.get("argv")
    env = template.get("env")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("template argv is not a non-empty string list")
    if not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(value, str)
                                            for key, value in env.items()):
        raise ValueError("template env is not a string map")
    if any(option in argv for option in ("-incoming", "-loadvm")):
        raise ValueError("template contains -incoming RAM state; fresh-child install refuses it")
    boot_args = option_values(argv, "-args")
    if len(boot_args) != 1 or "rd=md0" not in shlex.split(boot_args[0]):
        raise ValueError("template is not a restore launch with rd=md0")
    if not option_values(argv, "-ramdisk"):
        raise ValueError("restore template has no -ramdisk")
    if ans_drive_path(argv) != parent.resolve():
        raise ValueError("template ANS disk does not equal --parent")
    return list(argv), dict(env)


def input_paths(argv: list[str], parent: Path, ramdisk: Path, template: Path) -> list[Path]:
    """Pin the QEMU executable and file-valued boot inputs used by this child."""
    paths = {template.resolve(), parent.resolve(), ramdisk.resolve(), Path(argv[0]).resolve()}
    for option in ("-bootkc", "-dtree", "-tc", "-sptm", "-txm"):
        paths.update(Path(value).resolve() for value in option_values(argv, option))
    missing = sorted(path for path in paths if not path.is_file())
    if missing:
        raise FileNotFoundError("template input missing: " + ", ".join(map(str, missing)))
    return sorted(paths)


def pin_inputs(argv: list[str], parent: Path, ramdisk: Path, template: Path) -> dict[str, dict[str, object]]:
    return {
        str(path): {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in input_paths(argv, parent, ramdisk, template)
    }


def wait_for_file(path: Path, proc: subprocess.Popen[bytes], deadline: float) -> None:
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited {proc.returncode} before creating {path.name}")
        time.sleep(0.10)
    raise TimeoutError(f"timed out waiting for {path}")


def wait_for_serial(path: Path, pattern: re.Pattern[bytes], start: int,
                    proc: subprocess.Popen[bytes], deadline: float) -> tuple[bool, int]:
    position = start
    tail = b""
    while time.monotonic() < deadline:
        if path.exists():
            with path.open("rb") as serial:
                serial.seek(position)
                block = serial.read()
            position += len(block)
            tail = (tail + block)[-65536:]
            if pattern.search(tail):
                return True, position
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited {proc.returncode} before serial condition")
        time.sleep(0.20)
    return False, position


def hmp_command(path: Path, command: str, timeout: float = 8.0) -> str:
    """Issue one HMP command to an endpoint created by this tool."""
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(max(0.1, deadline - time.monotonic()))
        client.connect(str(path))
        data = bytearray()
        while not bytes(data).rstrip().endswith(b"(qemu)"):
            chunk = client.recv(65536)
            if not chunk:
                raise ConnectionError("HMP closed before its greeting")
            data.extend(chunk)
        client.sendall(command.encode("ascii") + b"\n")
        data = bytearray()
        while not bytes(data).rstrip().endswith(b"(qemu)"):
            client.settimeout(max(0.1, deadline - time.monotonic()))
            chunk = client.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
    return data.decode(errors="replace")


def quit_cleanly(proc: subprocess.Popen[bytes], monitor: Path, hmp_log: Path) -> None:
    answer = hmp_command(monitor, "quit")
    hmp_log.write_text("quit\n" + answer)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("QEMU ignored HMP quit") from error
    if proc.returncode != 0:
        raise RuntimeError(f"QEMU exited {proc.returncode} after HMP quit")


def retain_failure(proc: subprocess.Popen[bytes] | None, monitor: Path,
                   mode: str, hmp_log: Path) -> str:
    if not proc or proc.poll() is not None:
        return "already-exited"
    command = "stop" if mode == "pause" else "quit"
    try:
        answer = hmp_command(monitor, command)
        with hmp_log.open("a") as log:
            log.write(f"{command}\n{answer}\n")
    except (OSError, TimeoutError) as error:
        with hmp_log.open("a") as log:
            log.write(f"{command} failed: {error}\n")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return "terminated-after-hmp-failure"
    if mode == "pause":
        return "paused"
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return "terminated-after-quit-timeout"
    return "quit"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True,
                        help="existing darwin-vm-qemu-launch-v1 restore launch JSON")
    parser.add_argument("--parent", type=Path, required=True,
                        help="immutable parent qcow2 named by the template ANS drive")
    parser.add_argument("--ramdisk", type=Path, required=True,
                        help="prepared small restore ramdisk containing the installer")
    parser.add_argument("--out", type=Path, required=True,
                        help="new artifact directory; it must not already exist")
    parser.add_argument("--tag", required=True, help="unique safe run tag")
    parser.add_argument("--timeout", type=int, default=180,
                        help="whole-run deadline in seconds (1..180)")
    parser.add_argument("--installer", default=DEFAULT_INSTALLER,
                        help="prepared /libexec/*.sh guest installer path")
    parser.add_argument("--install-marker", default=INSTALL_MARKER.decode("ascii"),
                        help="exact final success marker emitted by the guest installer")
    parser.add_argument("--shell-marker", default=r"(?:^|[\r\n])# ?",
                        help="bytes regex proving the restore shell is ready")
    parser.add_argument("--on-failure", choices=("quit", "pause"), default="quit",
                        help="retain failed writable child after quitting or pausing owned QEMU")
    args = parser.parse_args()
    if not SAFE_TAG.fullmatch(args.tag):
        parser.error("--tag must match [A-Za-z0-9_.-]{1,64}")
    if not 1 <= args.timeout <= 180:
        parser.error("--timeout must be in 1..180")
    if not re.fullmatch(r"/libexec/[A-Za-z0-9_-]+\.sh", args.installer):
        parser.error("--installer must name a shell script directly under /libexec")
    if not re.fullmatch(r"DVM_[A-Z0-9_]{1,96}", args.install_marker):
        parser.error("--install-marker must be a DVM_ uppercase identifier")
    return args


def main() -> int:
    args = parse_args()
    install_command = "sh " + args.installer
    template_path = args.template.resolve()
    parent = args.parent.resolve()
    ramdisk = args.ramdisk.resolve()
    out = args.out.resolve()
    if out.exists():
        raise RuntimeError(f"refusing to overwrite existing output directory: {out}")
    if not template_path.is_file() or not parent.is_file() or not ramdisk.is_file():
        raise FileNotFoundError("--template, --parent, and --ramdisk must be regular files")
    try:
        shell_pattern = re.compile(args.shell_marker.encode("ascii"))
    except (UnicodeEncodeError, re.error) as error:
        raise ValueError(f"invalid --shell-marker: {error}") from error

    if parent.stat().st_mode & 0o222:
        raise ValueError("parent must be read-only before creating an installer child")
    if len(os.fsencode(str(out / "monitor.sock"))) >= 104:
        raise ValueError("output directory is too long for a macOS UNIX socket")
    template = json.loads(template_path.read_text())
    source_argv, template_env = validate_template(template, parent)
    source_argv = rewrite_ramdisk(source_argv, ramdisk)
    boot_command = load_boot_command()
    qemu_img = shutil.which("qemu-img")
    if not qemu_img:
        raise RuntimeError("qemu-img is required to create the fresh child")

    out.mkdir(parents=True)
    child = out / "disk.qcow2"
    monitor = out / "monitor.sock"
    uart = out / "uart.sock"
    serial = out / "serial.log"
    deadline = time.monotonic() + args.timeout
    proc: subprocess.Popen[bytes] | None = None
    result: dict[str, object] = {
        "tag": args.tag,
        "template": str(template_path),
        "parent": str(parent),
        "ramdisk": str(ramdisk),
        "timeout": args.timeout,
        "installer_command": install_command,
        "install_marker": args.install_marker,
        "success": False,
    }
    try:
        # Pin before the child is made, so evidence names only the exact bytes used.
        atomic_json(out / "input-hashes.json", {
            "template_launch_json_sha256": sha256(template_path),
            "inputs": pin_inputs(source_argv, parent, ramdisk, template_path),
        })
        (out / "template.launch.json").write_text(template_path.read_text())
        with (out / "qemu-img.log").open("wb") as image_log:
            subprocess.run(
                [qemu_img, "create", "-f", "qcow2", "-F", "qcow2", "-b",
                 str(parent), str(child)], check=True, timeout=max(1, int(deadline - time.monotonic())),
                stdout=image_log, stderr=subprocess.STDOUT,
            )
        command = boot_command(source_argv, out)
        if any(item in command for item in ("-incoming", "-gdb", "-S", "-s")):
            raise RuntimeError("sanitised command retained forbidden RAM/debug pause option")
        if not any(item.startswith("if=none,id=ans,file=") and str(child) in item
                   for item in command):
            raise RuntimeError("sanitised command did not select fresh child disk")
        model_env = dict(template_env)
        model_env["DARWIN_TOUCH_EVENTS"] = str(out / "events.jsonl")
        host_env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("DARWIN_", "GXFSTAT_", "DVM_"))}
        host_env.update(model_env)
        atomic_json(out / "launch.json", {
            "format": "darwin-vm-qemu-launch-v1", "argv": command, "env": model_env,
        })
        with (out / "qemu.stderr.log").open("wb") as stderr:
            proc = subprocess.Popen(command, env=host_env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=stderr,
                                    start_new_session=True)
        (out / "qemu.pid").write_text(f"{proc.pid}\n")
        wait_for_file(monitor, proc, deadline)
        wait_for_file(uart, proc, deadline)
        ready, serial_position = wait_for_serial(serial, shell_pattern, 0, proc, deadline)
        if not ready:
            raise TimeoutError("restore shell marker did not arrive before deadline")
        result["shell_marker_offset"] = serial_position

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("deadline expired before installer command")
        with (out / "serial-command.stdout.log").open("wb") as stdout, \
             (out / "serial-command.stderr.log").open("wb") as stderr:
            serial_client = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "serial.py"),
                 str(uart), "send", install_command, "--secs", "8", "--idle", "1",
                 "--prompt-timeout", str(min(10, max(1, int(remaining)))),
                 "--echo-timeout", str(min(10, max(1, int(remaining)))),
                 "--log", str(out / "uart.console.log")],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                timeout=max(1, remaining), check=False,
            )
        result["serial_client_returncode"] = serial_client.returncode
        if serial_client.returncode != 0:
            raise RuntimeError("serial client did not establish and echo the installer command")
        installed, serial_position = wait_for_serial(
            serial, re.compile(rb"(?:^|[\r\n])" + re.escape(args.install_marker.encode("ascii")) +
                               rb"(?:[\r\n]|$)"), serial_position, proc, deadline)
        result["install_marker_offset"] = serial_position
        if not installed:
            raise TimeoutError("guest installer marker absent from QEMU serial before deadline")

        quit_cleanly(proc, monitor, out / "hmp.log")
        proc = None
        child.chmod(0o444)
        result.update(success=True, child_mode=oct(stat.S_IMODE(child.stat().st_mode)),
                      outcome="installed-marker-confirmed-and-child-sealed")
        atomic_json(out / "result.json", result)
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except BaseException as error:
        result.update(error=f"{type(error).__name__}: {error}",
                      outcome="failed-child-retained")
        result["failure_action"] = retain_failure(
            proc, monitor, args.on_failure, out / "hmp.log")
        atomic_json(out / "result.json", result)
        print(json.dumps(result, sort_keys=True), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
