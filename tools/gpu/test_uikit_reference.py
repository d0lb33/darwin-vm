"""Negative acceptance checks against explicitly supplied completed captures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from analyze_uikit_native_reference import compare


@unittest.skipUnless(os.environ.get('DVM_UIKIT_JOB') and os.environ.get('DVM_UIKIT_NATIVE'), 'requires captured UIKit fixture')
class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.job=Path(os.environ['DVM_UIKIT_JOB']);self.source=Path(os.environ['DVM_UIKIT_NATIVE'])
        self.temp=tempfile.TemporaryDirectory();self.native=Path(self.temp.name)
        for name in ('manifest.json','gpu.bgra','result.log'):shutil.copyfile(self.source/name,self.native/name)
    def tearDown(self):self.temp.cleanup()
    def metadata(self,change):
        path=self.native/'manifest.json';data=json.loads(path.read_text());change(data);path.write_text(json.dumps(data))
    def test_valid_capture(self):self.assertTrue(compare(self.job,self.native)['verified'])
    def test_wrong_pixels_with_valid_file_hash(self):
        path=self.native/'gpu.bgra';data=bytearray(path.read_bytes());data[0]^=255;path.write_bytes(data)
        self.metadata(lambda m:m.update(gpu_sha256=hashlib.sha256(data).hexdigest()))
        result=compare(self.job,self.native);self.assertFalse(result['verified']);self.assertGreater(result['channels_over_2'],0)
    def test_wrong_source(self):
        self.metadata(lambda m:m['guest_rasters']['labels'][0].update(source_sha256='0'*64))
        with self.assertRaisesRegex(ValueError,'native glyph inputs'):compare(self.job,self.native)
    def test_wrong_corner_curve(self):
        self.metadata(lambda m:m['guest_rasters']['layer_corner_curves'].__setitem__(2,'circular'))
        with self.assertRaisesRegex(ValueError,'layer metadata'):compare(self.job,self.native)
    def test_forwarded_oracle_disallowed(self):
        self.metadata(lambda m:m.update(forwarded=True))
        with self.assertRaisesRegex(ValueError,'default native Metal'):compare(self.job,self.native)
    def test_execution_witness_required(self):
        (self.native/'result.log').write_text('')
        with self.assertRaisesRegex(ValueError,'execution witnesses'):compare(self.job,self.native)


if __name__=='__main__':unittest.main()
