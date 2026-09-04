#!/usr/bin/env python3
"""Reproduce the static A18 iBoot PMGR-to-CPM boundary scan.

This is deliberately a signature/constant audit, not a disassembler.  It
reports aligned ARM64 MOVZ/MOVK materializations of the two physical addresses
proved by the d47 device tree and the command-store sequences observed in the
two supported iBoot builds.  It never assigns contents to the PMGR registers.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path


PMGR_PAIR = 0x3082_B8074
CPM_TAIL = 0x210E_4C000

# mov w8, #0x5a01; movk w8, #5, lsl #16; str x8, [x9, #8]
INIT_COMMAND_STORE = bytes.fromhex("28408b52a800a072280500f9")
# mov w9, #0x5a5a; str x9, [x8, #8]
CLEAR_COMMAND_STORE = bytes.fromhex("494b8b52090500f9")


def movz_x(rd: int, imm16: int, shift: int) -> int:
    return 0xD2800000 | ((shift // 16) << 21) | (imm16 << 5) | rd


def movk_x(rd: int, imm16: int, shift: int) -> int:
    return 0xF2800000 | ((shift // 16) << 21) | (imm16 << 5) | rd


def materialization(value: int, rd: int) -> bytes:
    return struct.pack(
        "<III",
        movz_x(rd, value & 0xFFFF, 0),
        movk_x(rd, (value >> 16) & 0xFFFF, 16),
        movk_x(rd, (value >> 32) & 0xFFFF, 32),
    )


def aligned_hits(data: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        off = data.find(needle, start)
        if off < 0:
            return hits
        if off % 4 == 0:
            hits.append(off)
        start = off + 1


def address_hits(data: bytes, value: int) -> list[tuple[int, int]]:
    hits: list[tuple[int, int]] = []
    for rd in range(31):
        hits.extend((off, rd) for off in aligned_hits(data, materialization(value, rd)))
    return sorted(hits)


def fmt_addr(load_base: int, off: int) -> str:
    return f"raw+0x{off:x}/static=0x{load_base + off:x}"


def infer_load_base(data: bytes) -> int:
    """Find the first page-aligned boot-memory literal in the startup block."""
    for off in range(0, min(len(data), 0x500) - 7, 8):
        value = struct.unpack_from("<Q", data, off)[0]
        if 0x1FC0_00000 <= value < 0x1FD0_00000 and value % 0x4000 == 0:
            return value
    raise ValueError("no page-aligned iBoot load literal in the startup block")


def scan(label: str, path: Path) -> None:
    data = path.read_bytes()
    if len(data) < 0x500:
        raise ValueError(f"{path}: too short for the iBoot startup literals")
    load_base = infer_load_base(data)
    digest = hashlib.sha256(data).hexdigest()

    print(f"{label}: {path}")
    print(f"  sha256={digest} size=0x{len(data):x} load-base=0x{load_base:x}")
    for name, value in (("pmgr-pair", PMGR_PAIR), ("cpm-tail", CPM_TAIL)):
        hits = address_hits(data, value)
        rendered = ", ".join(
            f"{fmt_addr(load_base, off)}(x{rd})" for off, rd in hits
        )
        print(f"  {name}=0x{value:x}: {rendered or 'no exact materialization'}")

    for name, signature in (
        ("init-command-0x55a01", INIT_COMMAND_STORE),
        ("clear-command-0x5a5a", CLEAR_COMMAND_STORE),
    ):
        hits = aligned_hits(data, signature)
        rendered = ", ".join(fmt_addr(load_base, off) for off in hits)
        print(f"  {name}: {rendered or 'not found'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "images",
        nargs="+",
        metavar="LABEL=RAW_IMAGE",
        help="label and unwrapped iBoot payload",
    )
    args = parser.parse_args()

    for item in args.images:
        if "=" not in item:
            parser.error(f"expected LABEL=RAW_IMAGE, got {item!r}")
        label, raw_path = item.split("=", 1)
        scan(label, Path(raw_path))


if __name__ == "__main__":
    main()
