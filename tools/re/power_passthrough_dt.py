#!/usr/bin/env python3
"""Add a bounded native external-power provider to an already-fixed Apple DT.

This generator does exactly one DT mutation: it appends an `arm-io` child with
these two string properties and no children:

    name        = "charger,passthrough"
    device_type = "charger,passthrough"

It deliberately adds no `compatible`, `reg`, `interrupts`, capacity, charge,
or battery-health property.  The target personality is the 24A5430a
`AppleARMPassthroughPowerSource` personality in `firmware/bootkc`:
`IOProviderClass = IOService`, `IONameMatch = charger,passthrough`,
`IOProbeScore = 2000` (prelink file offset
`0x436dd8d..0x436de86`).  Its constructor is at static
`0xfffffff0085bb9e0`; its inherited `AppleARMPMUPowerSource` start path is
at static `0xfffffff0085b8e50`.

The native start path looks up provider property `ExternalConnected` at
`0xfffffff0085b8e8c..0xfffffff0085b8ec0` and compares it with the kernel
boolean true object at `0xfffffff0085b8ec4..0xfffffff0085b8edc`.  No
established conversion from an ADT property to that OSBoolean exists in this
experiment, so this tool does not guess one.  A successful attachment therefore
tests matching only; it does not claim an externally-connected state.

Required follow-up evidence from a disposable restore-shell probe:
  * IORegistry shows the provider and a child/class named
    AppleARMPassthroughPowerSource;
  * the native power-source query reports the source it publishes and its
    ExternalConnected state; and
  * a missing/false state is recorded as such, rather than patched with an
    invented DT encoding.

The parser is intentionally lossless. Existing property byte records, child
order, and every existing node's bytes are retained. Only arm-io's child count
is incremented and the provider's encoded leaf is appended. The command
refuses both an existing output and an input which already contains the leaf.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

PROVIDER_NAME = "charger,passthrough"
ARM_IO_NAME = "arm-io"


class DTError(ValueError):
    """A malformed DT or a target-tree invariant failure."""


class ProviderConflict(DTError):
    """The requested provider is already present."""


@dataclass
class Property:
    name: str
    raw: bytes


@dataclass
class Node:
    properties: list[Property] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def get_property(self, name: str) -> Property | None:
        return next((item for item in self.properties if item.name == name), None)

    @property
    def name(self) -> str:
        prop = self.get_property("name")
        if prop is None:
            raise DTError("node has no name property")
        return decode_string_property(prop.raw, "name")


def aligned(value: int) -> int:
    return (value + 3) & ~3


def decode_name(field: bytes) -> str:
    terminator = field.find(b"\0")
    if terminator < 0:
        raise DTError("property name is not NUL-terminated")
    try:
        return field[:terminator].decode("ascii")
    except UnicodeDecodeError as exc:
        raise DTError("property name is not ASCII") from exc


def decode_string_property(raw: bytes, property_name: str) -> str:
    if len(raw) < 36:
        raise DTError(f"property {property_name!r} is truncated")
    length = struct.unpack_from("<I", raw, 32)[0] & ~0x80000000
    padded = aligned(length)
    if len(raw) != 36 + padded:
        raise DTError(f"property {property_name!r} has invalid encoded length")
    value = raw[36 : 36 + length]
    if not value.endswith(b"\0"):
        raise DTError(f"property {property_name!r} is not a C string")
    try:
        return value[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DTError(f"property {property_name!r} is not UTF-8") from exc


def parse_node(data: bytes, offset: int = 0) -> tuple[Node, int]:
    if len(data) - offset < 8:
        raise DTError("truncated node header")
    property_count, child_count = struct.unpack_from("<II", data, offset)
    offset += 8
    node = Node()

    for _ in range(property_count):
        if len(data) - offset < 36:
            raise DTError("truncated property header")
        name = decode_name(data[offset : offset + 32])
        length = struct.unpack_from("<I", data, offset + 32)[0] & ~0x80000000
        end = offset + 36 + aligned(length)
        if end > len(data):
            raise DTError(f"truncated property {name!r}")
        node.properties.append(Property(name, data[offset:end]))
        offset = end

    for _ in range(child_count):
        child, offset = parse_node(data, offset)
        node.children.append(child)

    return node, offset


def parse_tree(data: bytes) -> Node:
    root, offset = parse_node(data)
    if offset != len(data):
        raise DTError(f"{len(data) - offset} trailing bytes after root node")
    return root


def encode_node(node: Node) -> bytes:
    out = bytearray(struct.pack("<II", len(node.properties), len(node.children)))
    for prop in node.properties:
        out.extend(prop.raw)
    for child in node.children:
        out.extend(encode_node(child))
    return bytes(out)


def string_property(name: str, value: str) -> Property:
    try:
        encoded_name = name.encode("ascii")
    except UnicodeEncodeError as exc:
        raise DTError(f"non-ASCII property name {name!r}") from exc
    if len(encoded_name) >= 32:
        raise DTError(f"property name too long: {name!r}")
    payload = value.encode("utf-8") + b"\0"
    raw = (
        encoded_name.ljust(32, b"\0")
        + struct.pack("<I", len(payload))
        + payload.ljust(aligned(len(payload)), b"\0")
    )
    return Property(name, raw)


def provider_leaf() -> Node:
    return Node(
        properties=[
            string_property("name", PROVIDER_NAME),
            string_property("device_type", PROVIDER_NAME),
        ]
    )


def find_direct_child(parent: Node, name: str) -> list[Node]:
    return [child for child in parent.children if child.name == name]


def arm_io_node(root: Node) -> Node:
    matches = find_direct_child(root, ARM_IO_NAME)
    if len(matches) != 1:
        raise DTError(f"expected exactly one root child named {ARM_IO_NAME!r}, found {len(matches)}")
    return matches[0]


def reject_existing_provider(arm_io: Node) -> None:
    matches = find_direct_child(arm_io, PROVIDER_NAME)
    if matches:
        raise ProviderConflict(
            f"{ARM_IO_NAME}/{PROVIDER_NAME} already exists; refusing non-idempotent mutation"
        )


def add_provider(root: Node) -> None:
    arm_io = arm_io_node(root)
    reject_existing_provider(arm_io)
    arm_io.children.append(provider_leaf())


def assert_output_contract(original: Node, output: Node) -> None:
    """Check semantic preservation, the one allowed mutation, and idempotence."""

    original_arm_io = arm_io_node(original)
    output_arm_io = arm_io_node(output)

    def compare(node_before: Node, node_after: Node, path: str) -> None:
        if node_before.properties != node_after.properties:
            raise DTError(f"existing properties changed at {path}")
        if node_before is original_arm_io:
            if node_after.children[:-1] != node_before.children:
                raise DTError(f"existing children changed at {path}")
            leaf = node_after.children[-1] if node_after.children else None
            if leaf is None or leaf.name != PROVIDER_NAME:
                raise DTError(f"provider leaf missing at {path}")
            if len(leaf.children) != 0:
                raise DTError("provider leaf has children")
            if [prop.name for prop in leaf.properties] != ["name", "device_type"]:
                raise DTError("provider leaf has properties beyond name and device_type")
            if any(leaf.get_property(name) is not None for name in ("compatible", "reg", "interrupts")):
                raise DTError("provider leaf has a hardware property")
            if decode_string_property(leaf.get_property("device_type").raw, "device_type") != PROVIDER_NAME:
                raise DTError("provider device_type changed")
            return
        if len(node_before.children) != len(node_after.children):
            raise DTError(f"existing child count changed at {path}")
        for before_child, after_child in zip(node_before.children, node_after.children):
            if before_child.name != after_child.name:
                raise DTError(f"child order changed at {path}")
            compare(before_child, after_child, f"{path}/{before_child.name}")

    compare(original, output, "")
    try:
        reject_existing_provider(output_arm_io)
    except ProviderConflict:
        return
    raise DTError("generated tree did not trigger idempotence conflict")


def generate(source: bytes) -> bytes:
    original = parse_tree(source)
    # Parse a second copy: the validation must compare before/after trees rather
    # than relying on shared in-memory identity.
    output = parse_tree(source)
    add_provider(output)
    encoded = encode_node(output)

    reparsed = parse_tree(encoded)
    if encode_node(reparsed) != encoded:
        raise DTError("encoded output did not round-trip byte-for-byte")
    assert_output_contract(original, reparsed)
    return encoded


def write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise DTError(f"refusing to overwrite existing output: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append the bounded charger,passthrough provider leaf to an already-fixed DT."
    )
    parser.add_argument("input", type=Path, help="already-fixed input DT")
    parser.add_argument("output", type=Path, help="new DT path; must not exist")
    args = parser.parse_args(argv)

    if args.input == args.output:
        parser.error("input and output must be different paths")
    if not args.input.is_file():
        parser.error(f"input is not a regular file: {args.input}")
    if args.output.exists():
        parser.error(f"refusing to overwrite existing output: {args.output}")

    try:
        result = generate(args.input.read_bytes())
        write_exclusive(args.output, result)
    except DTError as exc:
        print(f"power_passthrough_dt: {exc}", file=sys.stderr)
        return 2

    print(
        f"added {ARM_IO_NAME}/{PROVIDER_NAME} to {args.output} "
        f"({len(args.input.read_bytes())} -> {len(result)} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
