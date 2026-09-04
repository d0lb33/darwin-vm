import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('merge_exclave', Path(__file__).parents[1] / 'rootfs/merge_exclave.py')
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


class ExclaveMergeTests(unittest.TestCase):
    def test_copy_verify_reuse_and_refuse_differing_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source', root / 'preboot/Cryptexes/ExclaveOS'
            assets = source / merge.ASSETS
            assets.mkdir(parents=True)
            for name in ('cam_mic.plist', 'cam_mic_constraints.plist', 'cam_mic.bin'):
                (assets / name).write_bytes(name.encode())
            (source / 'System/asset-link').symlink_to(merge.ASSETS.relative_to('System'))
            expected = merge.merge_tree(source, target)
            self.assertEqual(merge.inventory(target), expected)
            self.assertTrue((target / 'System/asset-link').is_symlink())
            self.assertEqual(merge.merge_tree(source, target), expected)
            (target / merge.ASSETS / 'cam_mic.bin').write_bytes(b'old-image-content')
            with self.assertRaisesRegex(RuntimeError, 'refusing to overwrite'):
                merge.merge_tree(source, target)
            self.assertEqual((target / merge.ASSETS / 'cam_mic.bin').read_bytes(), b'old-image-content')

    def test_missing_payload_fails_before_creating_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source', root / 'target'
            source.mkdir()
            with self.assertRaisesRegex(RuntimeError, 'missing required'):
                merge.merge_tree(source, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
