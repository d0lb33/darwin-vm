import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import promote_base


class Promotion(unittest.TestCase):
    def test_preserves_old_manifest_and_rejects_unrelated_boot_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default, candidate, backup = (root / n for n in ('default', 'candidate', 'backup'))
            old = dict(disk=dict(backing_chain=[dict(path='original')]),
                       qemu_env={}, battery_source='emulated-smc', qemu_inputs={},
                       qemu_argv=['qemu', '-tc', 'old.tc', '-drive', 'original', '-sptm', 'sptm'])
            new = json.loads(json.dumps(old))
            new['disk']['backing_chain'].insert(0, dict(path='owned-child'))
            new['cellular_service_installation'] = {'binary_sha256': 'fixture'}
            new['qemu_argv'][2] = 'new.tc'
            new['qemu_argv'][4] = 'owned-child'
            original = json.dumps(old).encode()
            default.write_bytes(original)
            new['qemu_env']['unexpected'] = '1'
            candidate.write_text(json.dumps(new))
            argv = ['promote', str(candidate), str(default), str(backup)]
            with patch.object(sys, 'argv', argv), patch.object(promote_base, 'verify_backing_chain'):
                with self.assertRaises(SystemExit):
                    promote_base.main()
                self.assertEqual(default.read_bytes(), original)
                self.assertFalse(backup.exists())
                new['qemu_env'] = {}
                candidate.write_text(json.dumps(new))
                promote_base.main()
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(json.loads(default.read_text())['source_manifest'], str(backup.resolve()))


if __name__ == '__main__':
    unittest.main()
