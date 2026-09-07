import unittest
from audit_compositor_budget import analyze


class BudgetAuditTests(unittest.TestCase):
    def test_mips_views_and_failed_release_keep_distinct_accounting(self):
        def record(seq, op, request, reply):
            return dict(seq=seq, op=op, request=request, reply=reply)
        rows = [
            record(1, 'texture', dict(width=4, height=2, format=115, levels=3),
                   dict(ok=True, handle=1, allocatedSize=4096)),
            record(2, 'textureView', dict(texture=1), dict(ok=True, handle=2, allocatedSize=4096)),
            record(3, 'release', dict(handle=1), dict(ok=False)),
            record(4, 'texture', dict(width=2, height=2, format=115), dict(ok=False)),
            record(5, 'release', dict(handle=2), dict(ok=True)),
            record(6, 'release', dict(handle=1), dict(ok=True)),
        ]
        result = analyze(rows)
        self.assertEqual(result['peak_ordinary_logical_bytes'], 88)
        self.assertEqual(result['failed_allocations'][0]['required_logical_bytes'], 120)
        self.assertEqual(result['released_ordinary_logical_bytes'], 88)
        self.assertEqual(result['final_ordinary_logical_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
