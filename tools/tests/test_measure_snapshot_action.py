"""Host-only tests for snapshot interaction timeline analysis."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "measure_snapshot_action",
    Path(__file__).resolve().parents[1] / "input" / "measure_snapshot_action.py",
)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def ppm(path, width, height, pixels):
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + bytes(pixels))


class FrameTests(unittest.TestCase):
    def test_jit_counters_are_parsed(self):
        text = ("gen code size       123/456\nTB count            9\n"
                "TB flush count      1\nTB invalidate count 2\n"
                "TLB full flushes    3\nTLB partial flushes 4\n"
                "TLB elided flushes  5\n")
        self.assertEqual(measure.parse_jit(text), {
            "generated_code_bytes": 123, "translation_blocks": 9,
            "tb_flushes": 1, "tb_invalidations": 2,
            "tlb_full_flushes": 3, "tlb_partial_flushes": 4,
            "tlb_elided_flushes": 5,
        })

    def test_ppm_sampling_and_content_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "full.ppm"
            pixels = bytearray(12 * 12 * 3)
            pixels[((6 * 12) + 6) * 3:((6 * 12) + 6) * 3 + 3] = b"\xff\x00\x00"
            ppm(path, 12, 12, pixels)
            width, height, sampled = measure.sample_six(path)
            self.assertEqual((width, height), (2, 2))
            self.assertEqual(sampled[-3:], b"\xff\x00\x00")

    def test_timeline_pairs_completion_and_finds_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = bytes(4 * 10 * 3)
            changed = bytearray(baseline)
            changed[4 * 3:5 * 3] = b"\xff\x00\x00"
            ppm(root / "transition-0000.ppm", 4, 10, changed)
            text = (
                "darwin-input: timing epoch=2 seq=7 kind=D a=1 b=2 c=0 monotonic_ns=1000000\n"
                "iomfb-timing: present swap=9 scanout_us=20 monotonic_ns=3000000\n"
                "iomfb: transition file=transition-0000.ppm present_ns=3000000 capture_us=5 ok=1\n"
                "iomfb-timing: complete swap=9 status=0 monotonic_ns=4000000\n"
            )
            result = measure.parse_timeline(text, root, baseline, 4, 10, .01)
            self.assertEqual(result["first_present_ms"], 2)
            self.assertEqual(result["first_significant_visible_change_ms"], 2)
            self.assertIsNone(result["first_major_visible_change_ms"])
            self.assertTrue(result["all_presentations_have_completion"])


if __name__ == "__main__":
    unittest.main()
