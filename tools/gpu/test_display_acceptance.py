import unittest
from display_acceptance import DisplayAcceptance


class DisplayAcceptanceTests(unittest.TestCase):
    def test_reused_swap_id_needs_new_completion(self):
        a = DisplayAcceptance()
        for frame in range(8):
            a.feed('iomfb: presented 1179x2556 BGRA swap=0 scanout_us=6000')
            self.assertFalse(a.reached(frame + 1))
            a.feed('iomfb: swap id 0 D594 nested completed, status 0x0; releasing A408 tag 0')
            self.assertTrue(a.reached(frame + 1))
        self.assertEqual((a.presentations, a.completions), (8, 8))

    def test_empty_duplicate_failed_and_out_of_order(self):
        for line in ('iomfb: swap id 8 D594 completed, status 0x1',
                     'iomfb: swap id 9 D594 completed, status 0x0',
                     'iomfb: presented 1179x2556 BGRA swap=8 scanout_us=10'):
            a = DisplayAcceptance()
            a.feed('iomfb: swap id 8 D594 completed, status 0x0')
            self.assertFalse(a.reached(1))
            a.feed('iomfb: presented 1179x2556 BGRA swap=8 scanout_us=10')
            a.feed(line)
            self.assertIsNotNone(a.failure)
            self.assertFalse(a.reached(1))
