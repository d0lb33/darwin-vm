import unittest
from report_compositor_pacing import analyze


class PacingTests(unittest.TestCase):
    def test_reused_ids_preserve_idle_gaps_and_completion_times(self):
        log='\n'.join(f'iomfb: presented 1x1 swap=0 scanout_us=1000 monotonic_ns={t}\n'
                      f'iomfb: swap id 0 D594 nested completed, status 0x0; releasing A408 tag 0 monotonic_ns={t+2000000}'
                      for t in (1000000000,1016000000,3016000000))
        result=analyze(log)
        self.assertEqual(result['presentations'],3)
        self.assertEqual(result['inter_presentation_ms']['maximum'],2000)
        self.assertEqual(result['presentation_to_completion_ms']['maximum'],2)
        self.assertEqual(result['gap_counts']['1000ms'],1)
        self.assertFalse(result['native_60fps_verified'])

    def test_missing_completion_and_wrong_identity_fail(self):
        start='iomfb: presented 1x1 swap=4 scanout_us=1000 monotonic_ns=100\n'
        for tail in ('','iomfb: swap id 5 D594 completed, status 0x0 monotonic_ns=200',
                     'iomfb: swap id 4 D594 completed, status 0x1 monotonic_ns=200',
                     'iomfb: swap id 4 D594 completed, status 0x0 monotonic_ns=90'):
            with self.assertRaises(ValueError):analyze(start+tail)
