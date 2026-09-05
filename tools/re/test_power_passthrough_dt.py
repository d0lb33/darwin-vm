#!/usr/bin/env python3
"""Unit tests for tools/re/power_passthrough_dt.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("power_passthrough_dt.py")
SPEC = importlib.util.spec_from_file_location("power_passthrough_dt", MODULE_PATH)
assert SPEC and SPEC.loader
power = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = power
SPEC.loader.exec_module(power)


class PowerPassthroughDTTests(unittest.TestCase):
    def source_tree(self) -> bytes:
        """Create a tree with opaque, non-string binary data to test preservation."""
        root = power.Node(
            properties=[power.string_property("name", "device-tree")],
            children=[
                power.Node(
                    properties=[
                        power.string_property("name", "arm-io"),
                        power.Property("opaque", b"opaque".ljust(32, b"\0") + b"\x04\x00\x00\x00\xde\xad\xbe\xef"),
                    ],
                    children=[
                        power.Node(properties=[power.string_property("name", "kept")]),
                    ],
                ),
                power.Node(properties=[power.string_property("name", "unrelated")]),
            ],
        )
        return power.encode_node(root)

    def test_generate_preserves_existing_tree_and_adds_only_leaf(self) -> None:
        source = self.source_tree()
        result = power.generate(source)
        before = power.parse_tree(source)
        after = power.parse_tree(result)

        self.assertEqual(before.children[0].properties, after.children[0].properties)
        self.assertEqual(before.children[0].children, after.children[0].children[:-1])
        leaf = after.children[0].children[-1]
        self.assertEqual(leaf.name, power.PROVIDER_NAME)
        self.assertEqual([item.name for item in leaf.properties], ["name", "device_type"])
        self.assertEqual(leaf.children, [])
        self.assertEqual(power.encode_node(power.parse_tree(result)), result)

    def test_generated_tree_conflicts_on_second_attempt(self) -> None:
        generated = power.generate(self.source_tree())
        with self.assertRaises(power.ProviderConflict):
            power.generate(generated)

    def test_write_exclusive_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.dt"
            power.write_exclusive(output, b"first")
            with self.assertRaises(power.DTError):
                power.write_exclusive(output, b"second")
            self.assertEqual(output.read_bytes(), b"first")


if __name__ == "__main__":
    unittest.main()
