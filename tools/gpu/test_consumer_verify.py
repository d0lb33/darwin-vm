"""Acceptance must require matching guest audit, GPU draws and final pixels."""
import base64
import copy
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from consumer_verify import verify, verify_records
from surface_peer import AIR_SHA


class ConsumerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.lines = [
            'GPU_LOAD_CA_VERIFIED width=64 height=64 passes=1 draws=1 bad_pixels=0',
            'GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0',
        ]
        self.records = [
            dict(seq=1, op='library', request=dict(sha256=AIR_SHA), reply=dict(ok=True)),
            dict(seq=2, op='renderSubmit', request=dict(commands=[dict(target=1)]),
                 reply=dict(ok=True, status=4, passes=1, draws=1)),
            dict(seq=3, op='read', request=dict(texture=1),
                 reply=dict(ok=True, data=base64.b64encode(bytes([0, 0, 255, 255])*4096).decode())),
            dict(seq=4, op='stats', request={}, reply=dict(ok=True, live=dict(objects=0))),
        ]

    def check(self):
        raw = bytearray(16*1024*1024)
        struct.pack_into('<Q', raw, 0x180, len(self.lines))
        for i, line in enumerate(self.lines):
            data = line.encode()
            struct.pack_into('<QII', raw, 0x1000+i*512, i+1, len(data), zlib.crc32(data))
            raw[0x1010+i*512:0x1010+i*512+len(data)] = data
        (self.out/'shared-ram.bin').write_bytes(raw)
        return verify(self.out, [dict(source='shared-ram-audit', line=x) for x in self.lines], self.records)

    def test_matching_evidence_passes(self):
        self.assertTrue(self.check()['verified'])

    def test_clear_only_cannot_pass_as_draw(self):
        self.records[1]['reply']['draws'] = 0
        with self.assertRaisesRegex(ValueError, 'counts differ'):
            self.check()

    def test_zero_draw_guest_witness_rejected(self):
        self.lines[0] = self.lines[0].replace('draws=1', 'draws=0')
        self.records[1]['reply']['draws'] = 0
        with self.assertRaisesRegex(ValueError, 'pixel oracle'):
            self.check()

    def test_incorrect_pixel_rejected(self):
        pixels = bytearray(bytes([0, 0, 255, 255])*4096)
        pixels[-1] = 0
        self.records[2]['reply']['data'] = base64.b64encode(pixels).decode()
        with self.assertRaisesRegex(ValueError, 'red layer oracle'):
            self.check()

    def test_read_before_render_rejected(self):
        self.records[2]['seq'] = 1
        with self.assertRaisesRegex(ValueError, 'final-only'):
            self.check()


class SequenceAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name);self.lines=[]
        self.records=[dict(seq=1,op='library',request=dict(sha256=AIR_SHA),reply=dict(ok=True))]
        for frame in range(4):
            self.records.append(dict(seq=len(self.records)+1,op='renderSubmit',request=dict(commands=[dict(target=1)]),reply=dict(ok=True,status=4,passes=1,draws=3)))
            if frame in (0,1,3):
                # Explicit scene geometry oracle separate from verifier code.
                data=bytearray(bytes([255,0,0,255])*4096)
                for y in range(64):
                    for x in range(frame*7,frame*7+16):data[(y*64+x)*4:(y*64+x)*4+4]=bytes([0,0,255,255])
                    for x in range(28,36):data[(y*64+x)*4:(y*64+x)*4+4]=bytes([0,255,0,255])
                self.records.append(dict(seq=len(self.records)+1,op='read',request=dict(texture=1),reply=dict(ok=True,data=base64.b64encode(data).decode())))
                self.lines.append(f'GPU_LOAD_CA_SEQUENCE_PIXELS frame={frame} bad_pixels=0')
        self.lines.append('GPU_LOAD_CA_SEQUENCE_VERIFIED width=64 height=64 frames=4 passes=4 draws=12 bad_pixels=0')
        self.records.append(dict(seq=len(self.records)+1,op='stats',request={},reply=dict(ok=True,live=dict(objects=0))))

    def check(self,frames=4):return verify_records(self.out,self.lines,self.records,frames)
    def test_sequence_passes(self):self.assertEqual(self.check()['frames'],4)
    def test_wrong_requested_frame_count(self):
        with self.assertRaisesRegex(ValueError,'frame count'):self.check(5)
    def test_stale_frame_pixels_rejected(self):
        reads=[x for x in self.records if x['op']=='read'];reads[-1]['reply']['data']=reads[0]['reply']['data']
        with self.assertRaisesRegex(ValueError,'moving layer oracle'):self.check()
    def test_read_before_frame_completes_rejected(self):
        reads=[x for x in self.records if x['op']=='read'];reads[-1]['seq']-=1
        with self.assertRaisesRegex(ValueError,'completion order'):self.check()
    def test_missing_guest_pixel_check_rejected(self):
        self.lines.pop(1)
        with self.assertRaisesRegex(ValueError,'pixel witnesses'):self.check()
    def test_leaked_resources_rejected(self):
        self.records[-1]['reply']['live']['objects']=1
        with self.assertRaisesRegex(ValueError,'not retired'):self.check()
    def test_wrong_scene_identity_rejected(self):
        self.lines.insert(0,'GPU_LOAD_CA_SCENE id=1')
        with self.assertRaisesRegex(ValueError,'scene identity'):self.check()
    def test_alpha_oracle_and_alpha_channel_rejection(self):
        self.lines.insert(0,'GPU_LOAD_CA_SCENE id=1')
        for record in self.records:
            if record['op']=='read':
                data=bytearray(base64.b64decode(record['reply']['data']))
                for i in range(0,len(data),4):
                    if data[i:i+4]==bytes([0,0,255,255]):data[i:i+4]=bytes([127,0,128,255])
                record['reply']['data']=base64.b64encode(data).decode()
        self.assertTrue(verify_records(self.out,self.lines,self.records,4,1)['verified'])
        record=next(x for x in self.records if x['op']=='read');data=bytearray(base64.b64decode(record['reply']['data']));data[3]=254
        record['reply']['data']=base64.b64encode(data).decode()
        with self.assertRaisesRegex(ValueError,'oracle'):verify_records(self.out,self.lines,self.records,4,1)
    def test_opaque_scene_cannot_pass_as_image(self):
        self.lines.insert(0,'GPU_LOAD_CA_SCENE id=3')
        with self.assertRaisesRegex(ValueError,'oracle'):verify_records(self.out,self.lines,self.records,4,3)

    def test_intermediate_targets_need_private_allocation_and_frame_completion(self):
        self.lines.insert(0,'GPU_LOAD_CA_SCENE id=4')
        self.lines[-1]=self.lines[-1].replace('passes=4 draws=12','passes=8 draws=14')
        records=[self.records[0],dict(op='texture',request=dict(storage=2),reply=dict(ok=True,handle=2,nativeStorageMode=2))]
        frame=0;passes=draws=0
        for r in self.records[1:]:
            if r['op']=='renderSubmit':
                if frame>=2:r['request']['commands']=[dict(target=1),dict(target=2),dict(target=1)];r['reply'].update(passes=3,draws=4)
                records.append(r);passes+=r['reply']['passes'];draws+=r['reply']['draws'];frame+=1
                records.append(dict(op='stats',request={},reply=dict(ok=True,live=dict(objects=3),renderPasses=passes,renderDraws=draws)))
            else:
                if r['op']=='read':
                    data=bytearray(base64.b64decode(r['reply']['data']))
                    for i in range(0,len(data),4):
                        if data[i:i+4]==bytes([0,0,255,255]):data[i:i+4]=bytes([128,0,128,255])
                        elif data[i:i+4]==bytes([0,255,0,255]):data[i:i+4]=bytes([128,128,0,255])
                    r['reply']['data']=base64.b64encode(data).decode()
                records.append(r)
        for i,r in enumerate(records):r['seq']=i+1
        self.assertTrue(verify_records(self.out,self.lines,records,4,4)['verified'])
        for mutate in (lambda r:r[1]['reply'].update(nativeStorageMode=0),lambda r:r[3]['reply'].update(renderPasses=999)):
            bad=copy.deepcopy(records);mutate(bad)
            with self.assertRaises(ValueError):verify_records(self.out,self.lines,bad,4,4)


if __name__ == '__main__':
    unittest.main()
