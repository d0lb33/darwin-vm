"""Synthetic-memory regression tests for the combined stall scanner."""
import importlib
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock
import re


RE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "re")
sys.path.insert(0, RE)
SCAN = importlib.import_module("ramscan_stall")


class RAMScanTests(unittest.TestCase):
    def test_cross_boundary_kext_tail_and_thread_are_both_found(self):
        # After the fixed +0x20000000 slide, this range crosses 0x28ff/0x2900.
        ranges = [(0xFFFFFFF008FFF000, 0xFFFFFFF009001000, "AppleBoundary")]
        _wanted, patterns = SCAN.frame_patterns(ranges, ["AppleBoundary"])
        self.assertEqual(len(patterns), 2)
        memory = bytearray(0x4000)
        tail_lr = 0xFFFFFFF029000100
        struct.pack_into("<Q", memory, 0x100, tail_lr)
        base = 0x800
        memory[base + SCAN.THREAD_SIG_OFFSET:base + SCAN.THREAD_SIG_OFFSET + 8] = SCAN.THREAD_SIG
        for offset, value in SCAN.THREAD_CHECKS:
            struct.pack_into("<Q", memory, base + offset, value)
        struct.pack_into("<Q", memory, base + 0x18, 0xAA)
        struct.pack_into("<Q", memory, base + 0xE0, 0xBB)
        struct.pack_into("<Q", memory, base + 0xF0, 0xFFFFFFE000004000)
        struct.pack_into("<Q", memory, base + 0x10, 0xCC)
        rows, pages = SCAN.scan_buffer(memory, 0x10000000000, 0x10000000000, patterns)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], (0xAA, 0xBB, 0xFFFFFFE000004000, 0xCC))
        self.assertIn((0x100, tail_lr), pages[0x10000000000])

    def test_dynamic_dram_chooses_largest_non_tag_region(self):
        tree = """
  0000010000000000-000001007fffffff (prio 0, ram): dram-tags
  0000010000000000-00000102ffffffff (prio 0, ram): dram
  0000000000000000-00000000000fffff (prio 0, ram): tiny
"""
        with mock.patch.object(SCAN.kmem, "hmp", return_value=tree):
            self.assertEqual(SCAN.discover_dram("sock"),
                             (0x10000000000, 0x300000000, "dram"))

    def test_missing_pmemsave_cannot_reuse_stale_chunk(self):
        ranges = [(0xFFFFFFF008000000, 0xFFFFFFF008001000, "AppleTest")]
        with tempfile.TemporaryDirectory() as root:
            stale = os.path.join(root, "chunk-10000000000-%d.bin" % os.getpid())
            with open(stale, "wb") as stream:
                stream.write(b"x" * 0x1000)
            with mock.patch.object(SCAN.kmem, "ensure_kernel"), \
                    mock.patch.object(SCAN.kmem, "hmp", return_value=""):
                with self.assertRaisesRegex(RuntimeError, "pmemsave failed"):
                    SCAN.scan_range("sock", 0x10000000000, 0x1000, 0x1000,
                                    root, ranges, ["AppleTest"])

    def test_chunk_overlap_finds_thread_whose_signature_is_in_next_chunk(self):
        ram_base = 0x10000000000
        memory = bytearray(0x2000)
        base = 0xF00
        memory[base + SCAN.THREAD_SIG_OFFSET:base + SCAN.THREAD_SIG_OFFSET + 8] = SCAN.THREAD_SIG
        for offset, value in SCAN.THREAD_CHECKS:
            struct.pack_into("<Q", memory, base + offset, value)
        struct.pack_into("<Q", memory, base + 0x18, 1)
        struct.pack_into("<Q", memory, base + 0xE0, 2)
        struct.pack_into("<Q", memory, base + 0xF0, 3)
        struct.pack_into("<Q", memory, base + 0x10, 4)

        def fake_hmp(_sock, command, timeout=20):
            match = re.match(r'pmemsave 0x([0-9a-f]+) 0x([0-9a-f]+) "([^"]+)"', command)
            self.assertIsNotNone(match)
            address, size, path = int(match.group(1), 16), int(match.group(2), 16), match.group(3)
            offset = address - ram_base
            with open(path, "wb") as stream:
                stream.write(memory[offset:offset + size])
            return ""

        ranges = [(0xFFFFFFF008000000, 0xFFFFFFF008001000, "AppleTest")]
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(SCAN.kmem, "ensure_kernel"), \
                mock.patch.object(SCAN.kmem, "hmp", side_effect=fake_hmp):
            rows, _pages = SCAN.scan_range(
                "sock", ram_base, len(memory), 0x1000, root, ranges, ["AppleTest"]
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], ram_base + base)


if __name__ == "__main__":
    unittest.main()
