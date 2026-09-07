"""Acceptance negatives using supplied historical glyph and native fixtures.

This exercises the verifier, not new guest display execution.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from verify_uikit_display import verify_reference


@unittest.skipUnless(os.getenv('DVM_UIKIT_JOB') and os.getenv('DVM_UIKIT_NATIVE'),'requires captured inputs')
class DisplayReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Path(self.temp.name);source=Path(os.environ['DVM_UIKIT_NATIVE']);job=Path(os.environ['DVM_UIKIT_JOB'])
        refs={}
        for name,key in [('gpu.bgra','gpu_sha256'),('manifest.json','manifest_sha256'),('result.log','log_sha256')]:
            data=(source/name).read_bytes();(self.p/('reference-'+name)).write_bytes(data);refs[key]=hashlib.sha256(data).hexdigest()
        (self.p/'job.json').write_text(json.dumps(dict(uikit_reference=refs)))
        self.records=[json.loads(x) for x in (job/'driver-host.jsonl').read_text().splitlines()]
        for r in self.records:
            if 'upload_file' in r:shutil.copyfile(job/r['upload_file'],self.p/r['upload_file'])
        pixels=(source/'gpu.bgra').read_bytes()
        self.backing=b''.join(pixels[y*4716:(y+1)*4716]+bytes(148) for y in range(2556))
    def test_padded_reference_roundtrip(self):self.assertTrue(verify_reference(self.p,[],self.records,3,self.backing)['verified'])
    def test_wrong_pixel(self):
        data=bytearray(self.backing);data[8000]^=255
        with self.assertRaisesRegex(ValueError,'pixel comparison'):verify_reference(self.p,[],self.records,3,data)
    def test_wrong_frame(self):
        with self.assertRaisesRegex(ValueError,'reference mode'):verify_reference(self.p,[],self.records,4,self.backing)
    def test_reference_replacement(self):
        (self.p/'reference-gpu.bgra').write_bytes(bytes(len(self.backing)))
        with self.assertRaisesRegex(ValueError,'reference hash'):verify_reference(self.p,[],self.records,3,self.backing)


if __name__=='__main__':unittest.main()
