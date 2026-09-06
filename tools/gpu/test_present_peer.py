"""Final display evidence must not accept a hash of the wrong or missing frame."""
import hashlib
import json
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

    def test_recovery_requires_new_display_and_ack_after_batch(self):
        before=dict(presents=100,acked=25)
        after=dict(presents=101,fresh_ack=True,stable_seconds=10,input_status=dict(guest_state='R',acked=26))
        (self.out/'present-recovery-baseline.json').write_text(json.dumps(before))
        (self.out/'stderr.log').write_text('iomfb: presented 1179x2556\nswap id 0 D594 nested completed, status 0x0\n')
        (self.out/'post-batch-native.json').write_text(json.dumps(dict(verified=True,after_batch=True,log_offset=0,host_started_ns=1)))
        target=self.out/'driver-display.json'
        target.write_text(json.dumps(after));p.verify_recovery(self.out)
        after['presents']=100;target.write_text(json.dumps(after))
        with self.assertRaisesRegex(ValueError,'after batch'):p.verify_recovery(self.out)
        after['presents']=101;after['input_status']['acked']=25;target.write_text(json.dumps(after))
        with self.assertRaisesRegex(ValueError,'after batch'):p.verify_recovery(self.out)

    def test_recovery_does_not_accept_old_frames_or_uncompleted_scanout(self):
        log=self.out/'stderr.log';log.write_text('iomfb: presented old\nD594 nested completed, status 0x0\n')
        offset=log.stat().st_size
        self.assertIsNone(p.observe_native_recovery(self.out,offset,1))
        with log.open('a') as f:f.write('iomfb: presented new\n')
        self.assertIsNone(p.observe_native_recovery(self.out,offset,1))
        with log.open('a') as f:f.write('D594 nested completed, status 0x0\n')
        self.assertTrue(p.observe_native_recovery(self.out,offset,1)['verified'])

if __name__=='__main__':unittest.main()
