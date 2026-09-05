#!/usr/bin/env python3
"""Regression tests for display-policy CodeDirectory parsing and rehashing."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "prepare_display_policy", ROOT / "tools/input/prepare_display_policy.py"
)
policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(policy)


def make_signature(page_hashes: list[bytes], *, declared_length=None,
                   hash_offset=None, cd_length=None, code_limit=None,
                   duplicate_slot=False) -> bytes:
    """Build a minimal, valid primary CodeDirectory SuperBlob by default."""
    count = 2 if duplicate_slot else 1
    cd_offset = policy.SUPERBLOB_HEADER.size + count * policy.SUPERBLOB_INDEX.size
    hash_offset = policy.CODEDIRECTORY_HEADER.size if hash_offset is None else hash_offset
    real_cd_length = max(policy.CODEDIRECTORY_HEADER.size,
                         hash_offset + len(page_hashes) * 32)
    written_cd_length = real_cd_length if cd_length is None else cd_length
    length = cd_offset + real_cd_length if declared_length is None else declared_length
    blob = bytearray(max(length, cd_offset + real_cd_length))
    policy.SUPERBLOB_HEADER.pack_into(
        blob, 0, policy.SUPERBLOB_MAGIC, length, count
    )
    policy.SUPERBLOB_INDEX.pack_into(blob, policy.SUPERBLOB_HEADER.size,
                                     policy.CSSLOT_CODEDIRECTORY, cd_offset)
    if duplicate_slot:
        policy.SUPERBLOB_INDEX.pack_into(blob, policy.SUPERBLOB_HEADER.size + 8,
                                         policy.CSSLOT_CODEDIRECTORY, cd_offset)
    policy.CODEDIRECTORY_HEADER.pack_into(
        blob, cd_offset, policy.CODEDIRECTORY_MAGIC, written_cd_length,
        0x20400, 2, hash_offset, 0, 0, len(page_hashes),
        (len(page_hashes) * policy.PAGE_SIZE if code_limit is None else code_limit),
        32, 2, 0, 14, 0,
    )
    for index, value in enumerate(page_hashes):
        start = cd_offset + hash_offset + index * 32
        if start + 32 <= len(blob):
            blob[start:start + 32] = value
    return bytes(blob)


class DisplayPolicySignatureTests(unittest.TestCase):
    def test_policy_patch_is_exactly_four_bytes_inside_a_full_code_page(self):
        source = bytearray(policy.PAGE_SIZE)
        page_offset = policy.OFFSET % policy.PAGE_SIZE
        source[page_offset:page_offset + len(policy.EXPECTED)] = policy.EXPECTED

        original, patched = policy.patch_code_page(bytes(source))

        self.assertEqual(original, policy.EXPECTED)
        self.assertEqual(patched[:page_offset + 8], source[:page_offset + 8])
        self.assertEqual(patched[page_offset + 8:page_offset + 12], policy.REPLACEMENT)
        self.assertEqual(patched[page_offset + 12:], source[page_offset + 12:])

    def test_rehash_updates_only_the_selected_code_hash(self):
        source = bytes(policy.PAGE_SIZE)
        patched = bytearray(source)
        patched[123:127] = policy.REPLACEMENT
        hashes = [hashlib.sha256(b"first page").digest(), hashlib.sha256(source).digest()]
        signature = make_signature(hashes)

        cd, cd_offset, slot_offset, old_hash, new_hash = policy.rehash_code_page(
            signature, 1, source, bytes(patched)
        )

        self.assertEqual(old_hash, hashes[1])
        self.assertEqual(new_hash, hashlib.sha256(patched).digest())
        self.assertEqual(cd[slot_offset:slot_offset + 32], new_hash)
        self.assertEqual(cd[policy.CODEDIRECTORY_HEADER.size:
                            policy.CODEDIRECTORY_HEADER.size + 32], hashes[0])
        self.assertEqual(cd_offset, policy.SUPERBLOB_HEADER.size + policy.SUPERBLOB_INDEX.size)

    def test_rehash_rejects_a_source_page_that_does_not_match_signature(self):
        source = bytes(policy.PAGE_SIZE)
        signature = make_signature([b"x" * 32])
        with self.assertRaisesRegex(ValueError, "does not match"):
            policy.rehash_code_page(signature, 0, source, source)

    def test_rejects_truncated_and_weak_superblob_layouts(self):
        with self.assertRaisesRegex(ValueError, "too short"):
            policy.parse_code_directory(b"", 0)

        # A declared index entry with no bytes must not reach struct.unpack.
        truncated_index = struct.pack(">III", policy.SUPERBLOB_MAGIC, 12, 1)
        with self.assertRaisesRegex(ValueError, "truncated SuperBlob index"):
            policy.parse_code_directory(truncated_index, 0)

        with self.assertRaisesRegex(ValueError, "duplicate SuperBlob slot"):
            policy.parse_code_directory(make_signature([b"a" * 32], duplicate_slot=True), 0)
        overlapping_index = bytearray(make_signature([b"a" * 32]))
        policy.SUPERBLOB_INDEX.pack_into(
            overlapping_index, policy.SUPERBLOB_HEADER.size,
            policy.CSSLOT_CODEDIRECTORY, policy.SUPERBLOB_HEADER.size,
        )
        with self.assertRaisesRegex(ValueError, "overlaps"):
            policy.parse_code_directory(overlapping_index, 0)
        with self.assertRaisesRegex(ValueError, "truncated CodeDirectory"):
            policy.parse_code_directory(
                make_signature([b"a" * 32], cd_length=policy.CODEDIRECTORY_HEADER.size + 33), 0
            )
        with self.assertRaisesRegex(ValueError, "code-hash table"):
            policy.parse_code_directory(
                make_signature([b"a" * 32], hash_offset=0x400,
                               cd_length=policy.CODEDIRECTORY_HEADER.size), 0
            )
        with self.assertRaisesRegex(ValueError, "code limit"):
            policy.parse_code_directory(
                make_signature([b"a" * 32],
                               code_limit=policy.OFFSET + len(policy.EXPECTED) - 1), 0,
                required_code_end=policy.OFFSET + len(policy.EXPECTED),
            )

    def test_rejects_short_or_wrong_policy_code_page(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            policy.patch_code_page(b"\0" * (policy.PAGE_SIZE - 1))
        with self.assertRaisesRegex(ValueError, "expected 24A5430a"):
            policy.patch_code_page(bytes(policy.PAGE_SIZE))


if __name__ == "__main__":
    unittest.main()
