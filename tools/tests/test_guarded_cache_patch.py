import importlib.util
import io
from pathlib import Path
import sys
import unittest


directory = Path(__file__).resolve().parents[1] / 'input'
sys.path.insert(0, str(directory))
try:
    spec = importlib.util.spec_from_file_location('guarded_cache_patch', directory / 'prepare_guarded_cache_patch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
finally:
    sys.path.pop(0)


class GuardedCachePatchTests(unittest.TestCase):
    def spec(self, edits):
        return dict(cache_name='dyld_shared_cache_arm64e.05',
                    purpose='Reviewed diagnostic instruction substitution', edits=edits)

    def test_validates_multiple_edits_without_changing_source(self):
        source = io.BytesIO(bytes(32768))
        edits = [dict(offset='0x5000', before='00000000', after='1f2003d5'),
                 dict(offset='0x4000', before='00000000', after='1f2003d5')]
        result = module.reviewed_edits(self.spec(edits), source)
        self.assertEqual([edit[0] for edit in result], [0x4000, 0x5000])
        self.assertEqual(source.getvalue(), bytes(32768))

    def test_rejects_wrong_preimage_before_staging(self):
        with self.assertRaisesRegex(ValueError, 'preimage mismatch'):
            module.reviewed_edits(self.spec([
                dict(offset='0x4000', before='01000000', after='1f2003d5')]),
                io.BytesIO(bytes(32768)))

    def test_rejects_overlapping_or_cross_page_edits(self):
        for edits, error in [
            ([dict(offset='0x4000', before='00000000', after='1f2003d5'),
              dict(offset='0x4002', before='00000000', after='1f2003d5')], 'overlapping'),
            ([dict(offset='0x3ffe', before='00000000', after='1f2003d5')], 'one code page'),
        ]:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                module.reviewed_edits(self.spec(edits), io.BytesIO(bytes(32768)))


if __name__ == '__main__':
    unittest.main()
