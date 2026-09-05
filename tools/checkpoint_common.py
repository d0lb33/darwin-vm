#!/usr/bin/env python3
"""Shared helpers for darwin-vm external checkpoint tooling."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import struct
import sys
import time
import ctypes
import ctypes.util
import subprocess
from pathlib import Path


SAFE_TAG = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def sha256(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(chunk):
            h.update(data)
    return h.hexdigest()


def qcow2_backing_chain(qemu_img: Path, disk: Path) -> list[dict[str, object]]:
    """Return a hash-pinned description of every file backing *disk*."""
    raw = subprocess.check_output([
        str(qemu_img), "info", "--backing-chain", "--output=json", str(disk),
    ], text=True)
    info = json.loads(raw)
    if not isinstance(info, list) or not info:
        raise RuntimeError("qemu-img returned an empty or invalid backing chain")
    chain: list[dict[str, object]] = []
    for entry in info:
        path = Path(entry["filename"]).resolve()
        if not path.is_file():
            raise RuntimeError(f"backing-chain member is not a file: {path}")
        chain.append({
            "path": str(path),
            "format": entry["format"],
            "virtual_size": entry["virtual-size"],
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    if Path(chain[0]["path"]) != disk.resolve():
        raise RuntimeError("qemu-img backing chain does not begin with selected disk")
    return chain


def verify_backing_chain(chain: list[dict[str, object]]) -> None:
    if not chain:
        raise RuntimeError("checkpoint manifest has no backing chain")
    for entry in chain:
        path = Path(str(entry["path"]))
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise RuntimeError(f"disk-chain member missing or resized: {path}")
        if sha256(path) != entry["sha256"]:
            raise RuntimeError(f"disk-chain member hash mismatch: {path}")


def wait_for_path(path: Path, deadline: float) -> None:
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for {path}")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_pid_exit(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


class HMP:
    def __init__(self, path: Path, timeout: float = 30.0):
        self.path = path
        self.timeout = timeout

    @staticmethod
    def _read_prompt(sock: socket.socket) -> str:
        data = bytearray()
        while not bytes(data).rstrip().endswith(b"(qemu)"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
        return data.decode(errors="replace")

    def command(self, command: str) -> str:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(str(self.path))
            self._read_prompt(sock)
            sock.sendall(command.encode() + b"\n")
            raw = self._read_prompt(sock)
        lines = []
        for line in raw.replace("\r", "").splitlines():
            if "\x1b[" in line or line.strip() == "(qemu)":
                continue
            lines.append(line)
        return "\n".join(lines).strip()


def selected_cpu_index(cpus: str) -> int:
    """HMP's register witness belongs to the starred CPU, not always CPU 0."""
    matches = re.findall(r"^\s*\* CPU #(\d+):", cpus, re.MULTILINE)
    if len(matches) != 1:
        raise RuntimeError("expected one selected CPU in HMP info cpus")
    return int(matches[0])


def parse_pc(registers: str) -> str:
    match = re.search(r"\bPC=([0-9a-fA-F]+)\b", registers)
    if not match:
        raise RuntimeError("HMP register output did not contain PC")
    return match.group(1).lower()


def sptm_panic_message(hmp: "HMP", registers: str) -> str:
    """Decode the project-specific SPTM branch-to-self panic convention."""
    pc = int(parse_pc(registers), 16)
    x3_match = re.search(r"\bX03=([0-9a-fA-F]+)\b", registers)
    if not x3_match:
        return ""
    instruction = hmp.command(f"x/1i 0x{pc:x}")
    target = re.search(r"\bb\s+#?0x([0-9a-fA-F]+)\b", instruction)
    if not target or int(target.group(1), 16) not in {pc, pc - 4}:
        return ""
    raw = hmp.command(f"x/256xb 0x{int(x3_match.group(1), 16):x}")
    octets = [int(value, 16) for value in re.findall(
        r"(?<![0-9a-fA-Fx])0x([0-9a-fA-F]{2})(?![0-9a-fA-F])", raw
    )]
    text = "".join(chr(value) if 32 <= value < 127 else "\0"
                   for value in octets)
    return text.split("\0\0", 1)[0].strip("\0").strip()


def serial_hex_clock_bounds(text: str) -> tuple[int | None, int | None]:
    """Extract the firmware trace clock printed as a leading 64-bit hex word."""
    values = [int(value, 16) for value in re.findall(
        r"(?m)^0x([0-9a-fA-F]{16})\s", text
    )]
    return (values[0], values[-1]) if values else (None, None)


def parse_migration_status(text: str) -> str:
    """Accept both legacy `Migration status:` and QEMU 11 `Status:` HMP."""
    match = re.search(r"(?im)^(?:Migration\s+)?Status:\s*([a-z-]+)\s*$", text)
    return match.group(1).lower() if match else ""


def process_argv_env(pid: int) -> tuple[list[str], dict[str, str]]:
    """Read exact argv/env on macOS without lossy `ps` tokenization."""
    if sys.platform != "darwin":
        proc = Path(f"/proc/{pid}")
        if proc.exists():
            argv = proc.joinpath("cmdline").read_bytes().split(b"\0")
            env = proc.joinpath("environ").read_bytes().split(b"\0")
            return ([x.decode(errors="surrogateescape") for x in argv if x],
                    dict(x.decode(errors="surrogateescape").split("=", 1)
                         for x in env if b"=" in x))
        raise RuntimeError("exact argv capture is unsupported on this host")

    # kern.procargs2 is a numeric MIB taking the PID as its third component;
    # /usr/sbin/sysctl cannot express that parameterized query reliably.
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, pid
    size = ctypes.c_size_t()
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    raw = buf.raw[:size.value]
    if len(raw) < 4:
        raise RuntimeError("kern.procargs2 returned a short buffer")
    argc = struct.unpack_from("=i", raw)[0]
    pos = 4

    def take_string() -> bytes:
        nonlocal pos
        end = raw.find(b"\0", pos)
        if end < 0:
            raise RuntimeError("unterminated kern.procargs2 string")
        value = raw[pos:end]
        pos = end + 1
        return value

    take_string()  # executable path stored before argv
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    argv = [take_string().decode(errors="surrogateescape") for _ in range(argc)]
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    env: dict[str, str] = {}
    while pos < len(raw):
        item = take_string()
        if not item:
            break
        if b"=" in item:
            key, value = item.split(b"=", 1)
            env[key.decode(errors="surrogateescape")] = value.decode(
                errors="surrogateescape"
            )
    return argv, env


def qemu_input_files(argv: list[str]) -> list[Path]:
    result: list[Path] = []
    value_options = {"-bootkc", "-dtree", "-tc", "-ramdisk", "-sptm", "-txm"}
    i = 1
    while i < len(argv):
        if argv[i] in value_options and i + 1 < len(argv):
            result.append(Path(argv[i + 1]).resolve())
            i += 2
        else:
            i += 1
    return result


def replace_drive_file(spec: str, old: Path, new: Path) -> str:
    parts = spec.split(",")
    if "id=ans" not in parts:
        return spec
    for i, part in enumerate(parts):
        if part.startswith("file="):
            current = Path(part[5:]).resolve()
            if current != old.resolve():
                raise RuntimeError(
                    f"ANS drive in captured argv is {current}, expected {old}"
                )
            parts[i] = f"file={new}"
            return ",".join(parts)
    raise RuntimeError("captured ANS -drive has no file= component")


def restore_argv(
    source: list[str], source_disk: Path, restore_disk: Path,
    monitor: Path, serial: Path, uart: Path, state: Path,
    gdb_port: int | None,
) -> list[str]:
    out = [source[0]]
    # Follow the serial backend reference rather than assuming probe.sh's ID.
    # Native input boots use input_uart; unrelated chardev sockets must retain
    # their own endpoints instead of being redirected into the guest console.
    serial_devices = {source[n + 1].removeprefix("chardev:")
                      for n, option in enumerate(source[:-1])
                      if option == "-serial" and source[n + 1].startswith("chardev:")}
    i = 1
    saw_monitor = saw_drive = saw_serial = False
    while i < len(source):
        arg = source[i]
        if arg in {"-incoming", "-gdb", "-pidfile", "-qmp"} and i + 1 < len(source):
            i += 2
            continue
        if arg == "-monitor" and i + 1 < len(source):
            out += [arg, f"unix:{monitor},server,nowait"]
            saw_monitor = True
            i += 2
            continue
        if arg == "-drive" and i + 1 < len(source):
            rewritten = replace_drive_file(source[i + 1], source_disk, restore_disk)
            saw_drive |= rewritten != source[i + 1]
            out += [arg, rewritten]
            i += 2
            continue
        if arg == "-chardev" and i + 1 < len(source):
            spec = source[i + 1]
            if spec.split(",")[0] == "socket" and any(
                    f"id={device}" in spec.split(",") for device in serial_devices):
                parts = [p for p in spec.split(",")
                         if not p.startswith("path=") and
                         not p.startswith("logfile=")]
                parts += [f"path={uart}", f"logfile={serial}"]
                spec = ",".join(parts)
                saw_serial = True
            out += [arg, spec]
            i += 2
            continue
        if arg == "-serial" and i + 1 < len(source):
            spec = source[i + 1]
            if spec.startswith("file:"):
                spec = f"file:{serial}"
                saw_serial = True
            out += [arg, spec]
            i += 2
            continue
        if arg == "-daemonize":
            i += 1
            continue
        out.append(arg)
        i += 1

    if not saw_drive:
        raise RuntimeError("captured argv did not contain the selected ANS disk")
    if not saw_monitor:
        out += ["-monitor", f"unix:{monitor},server,nowait"]
    if not saw_serial:
        raise RuntimeError("captured argv has no rewritable serial logfile")
    if "-S" not in out:
        out.append("-S")
    if gdb_port is not None:
        out += ["-gdb", f"tcp::{gdb_port}"]
    out += ["-incoming", f"file:{state}"]
    return out
