#!/usr/bin/env python3
import json
import unittest

from perf_window import render_to_display_stats


class RenderToDisplayTests(unittest.TestCase):
    @staticmethod
    def submit(received, ready, notified):
        return json.dumps({
            'op': 'renderSubmit',
            'qemu_clock_received_ns': received,
            'qemu_clock_reply_ready_ns': ready,
            'qemu_clock_notification_sent_ns': notified,
            'reply': {'ok': True},
        })

    def test_pairs_each_presentation_with_latest_completed_submit(self):
        journal = '\n'.join([
            self.submit(500_000, 1_000_000, 1_100_000),
            self.submit(10_500_000, 11_000_000, 11_100_000),
        ])
        stderr = '\n'.join([
            'iomfb: presented scanout_us=2000 dma_us=500 convert_us=1000 console_us=500 monotonic_ns=7000000',
            'iomfb: presented scanout_us=2000 dma_us=500 convert_us=1000 console_us=500 monotonic_ns=17000000',
        ])
        result = render_to_display_stats(stderr, journal)
        self.assertTrue(result['available'])
        self.assertEqual(result['paired'], 2)
        self.assertEqual(result['reply_ready_to_scanout_start_ms']['p50'], 4.0)
        self.assertEqual(result['reply_ready_to_present_ms']['p50'], 6.0)
        self.assertEqual(result['scanout_ms']['p50'], 2.0)
        self.assertEqual(result['presentation_to_next_render_received_ms']['p50'], 3.5)

    def test_discards_superseded_submit_before_presentation(self):
        journal = '\n'.join([
            self.submit(100_000, 200_000, 210_000),
            self.submit(500_000, 600_000, 610_000),
        ])
        stderr = ('iomfb: presented scanout_us=100 dma_us=20 convert_us=60 '
                  'console_us=20 monotonic_ns=1000000')
        result = render_to_display_stats(stderr, journal)
        self.assertEqual(result['paired'], 1)
        self.assertEqual(result['reply_ready_to_scanout_start_ms']['p50'], 0.3)

    def test_reports_missing_clock_domain(self):
        result = render_to_display_stats(
            'iomfb: presented scanout_us=1 dma_us=1 convert_us=0 console_us=0 monotonic_ns=2',
            json.dumps({'op': 'renderSubmit', 'reply': {'ok': True}}))
        self.assertFalse(result['available'])


if __name__ == '__main__':
    unittest.main()
