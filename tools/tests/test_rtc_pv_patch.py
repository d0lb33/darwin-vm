#!/usr/bin/env python3
"""Standalone generation tests for tools/re/rtc_pv_patch.py."""
import importlib.util
from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "rtc_pv_patch", ROOT / "tools/re/rtc_pv_patch.py"
)
rtc_pv_patch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(rtc_pv_patch)


def target_image(words=rtc_pv_patch.ORIGINAL_WORDS,
                 uuid=rtc_pv_patch.TARGET_UUID) -> bytes:
    """Build only the Mach-O pieces needed to exercise the patch guard."""
    size = rtc_pv_patch.PATCH_OFFSET + len(rtc_pv_patch.ORIGINAL_BYTES)
    image = bytearray(size)
    # mach_header_64 followed by one LC_UUID command.
    struct.pack_into("<IiiIIIII", image, 0, rtc_pv_patch.MACHO_64_MAGIC,
                     0, 0, 0, 1, 24, 0, 0)
    struct.pack_into("<II", image, 32, rtc_pv_patch.LC_UUID, 24)
    image[40:56] = uuid
    image[rtc_pv_patch.PATCH_OFFSET:rtc_pv_patch.PATCH_OFFSET +
          len(rtc_pv_patch.ORIGINAL_BYTES)] = \
        rtc_pv_patch.pack_words(words)
    return bytes(image)


class RTCPVPatchTests(unittest.TestCase):
    def test_patch_code_keeps_bti_c_and_has_the_verified_ten_instruction_sequence(self):
        self.assertEqual(rtc_pv_patch.PATCH_OFFSET, 0x15C9FD0)
        self.assertEqual(
            rtc_pv_patch.PATCH_BYTES.hex(),
            "5f2403d523ff38d5044099d24473a7f26508c49aa38c049b250000f9430000b900008052c0035fd6",
        )
        self.assertEqual(rtc_pv_patch.PATCH_WORDS[0], rtc_pv_patch.BTI_C)
        # MRS X3, S3_0_C15_C15_1: verify register fields without an assembler.
        mrs = rtc_pv_patch.PATCH_WORDS[1]
        self.assertEqual(mrs & 0x1F, 3)       # Rt
        self.assertEqual((mrs >> 19) & 3, 3)  # op0
        self.assertEqual((mrs >> 16) & 7, 0)  # op1
        self.assertEqual((mrs >> 12) & 0xF, 15)
        self.assertEqual((mrs >> 8) & 0xF, 15)
        self.assertEqual((mrs >> 5) & 7, 1)   # op2

    def test_emulated_leaf_uses_native_nanoseconds_and_success_status(self):
        # Native AppleDialogSPMIPMURTC writes a 1e9/32768 tick remainder to
        # x2, rather than microseconds. Exercise boundaries around that
        # second and a realistic Unix timestamp through the emitted MOVZ/MOVK
        # divisor, not a separately hard-coded test divisor.
        cases = (
            (0, 0, 0),
            (999_999_999, 0, 999_999_999),
            (1_000_000_000, 1, 0),
            (1_788_510_018_987_654_321, 1_788_510_018, 987_654_321),
        )
        for epoch_ns, expected_seconds, expected_nanoseconds in cases:
            seconds, nanoseconds, status = rtc_pv_patch.emulate_patched_get(epoch_ns)
            self.assertEqual(seconds, expected_seconds)
            self.assertEqual(nanoseconds, expected_nanoseconds)
            self.assertLess(nanoseconds, rtc_pv_patch.NANOSECONDS_PER_SECOND)
            self.assertEqual(status, 0)

    def test_rejects_a_patch_without_the_indirect_branch_landing_pad(self):
        with self.assertRaisesRegex(ValueError, "BTI c"):
            rtc_pv_patch.validate_patch_words(
                (rtc_pv_patch.PATCH_WORDS[1],) + rtc_pv_patch.PATCH_WORDS[1:]
            )

    def test_patch_changes_only_the_guarded_entry(self):
        source = target_image()
        patched, source_sha = rtc_pv_patch.patch_image(source)
        self.assertEqual(source_sha, rtc_pv_patch.hashlib.sha256(source).hexdigest())
        self.assertEqual(patched[:rtc_pv_patch.PATCH_OFFSET],
                         source[:rtc_pv_patch.PATCH_OFFSET])
        self.assertEqual(patched[rtc_pv_patch.PATCH_OFFSET:
                                 rtc_pv_patch.PATCH_OFFSET + len(rtc_pv_patch.PATCH_BYTES)],
                         rtc_pv_patch.PATCH_BYTES)

    def test_rejects_wrong_uuid_and_instruction_guard(self):
        with self.assertRaisesRegex(ValueError, "UUID"):
            rtc_pv_patch.patch_image(target_image(uuid=b"x" * 16))
        changed = list(rtc_pv_patch.ORIGINAL_WORDS)
        changed[0] ^= 1
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            rtc_pv_patch.patch_image(target_image(words=tuple(changed)))

    def test_rejects_a_patched_image(self):
        source = target_image()
        patched, _ = rtc_pv_patch.patch_image(source)
        with self.assertRaisesRegex(ValueError, "guard mismatch"):
            rtc_pv_patch.patch_image(bytes(patched))


if __name__ == "__main__":
    unittest.main()
