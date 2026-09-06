"""Final display evidence must not accept a hash of the wrong or missing frame."""
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import present_peer as p

class FinalDisplayEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.out=Path(self.tmp.name)
        pixels=bytearray(p.BYTES)
        struct.pack_into('<4I',pixels,0,0xff44564d,0xff505253,0xff424c52,0xff000021)
        self.pixels=pixels
        self.events=[dict(line=f'GPU_LOAD_PRESENT_FINAL verified=1 frame=33 sha={hashlib.sha256(pixels).hexdigest()} bad_pixels=0')]
        (self.out/'last-presented.bgra').write_bytes(pixels)
        with (self.out/'shared-ram.bin').open('wb') as f:
            f.seek(0x300000);f.write(pixels)
        (self.out/'stderr.log').write_text(f'iomfb: gpu-present-export frame=33 count=33 bytes={p.BYTES} ok=1\n')

    def test_requires_actual_display_export_not_only_gpu_bytes(self):
        self.assertTrue(p.verify_final(self.out,self.events)['verified'])
        (self.out/'last-presented.bgra').unlink()
        with self.assertRaises(OSError):p.verify_final(self.out,self.events)

    def test_changed_display_pixel_fails_even_when_gpu_oracle_passed(self):
        self.pixels[-4]^=1
        (self.out/'last-presented.bgra').write_bytes(self.pixels)
        with self.assertRaisesRegex(ValueError,'display pixels differ'):p.verify_final(self.out,self.events)

    def test_incomplete_batch_or_wrong_final_identity_fails(self):
        (self.out/'stderr.log').write_text(f'iomfb: gpu-present-export frame=32 count=32 bytes={p.BYTES} ok=1\n')
        with self.assertRaisesRegex(ValueError,'scanout export'):p.verify_final(self.out,self.events)
        self.events[0]['line']=self.events[0]['line'].replace('frame=33','frame=32')
        with self.assertRaisesRegex(ValueError,'final pixel oracle'):p.verify_final(self.out,self.events)

if __name__=='__main__':unittest.main()
