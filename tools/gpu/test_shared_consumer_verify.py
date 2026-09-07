"""Reject false GPU, ownership, pixel and native-presentation acceptance."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
import struct
from shared_consumer_verify import verify_records,verify_scanout_export,verify_handoff,verify_pacing,verify_pixels,BYTES,WIDTH,HEIGHT,ROW


class SharedConsumerVerification(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
        self.data=bytearray(759*16384)
        for y in range(HEIGHT):self.data[y*ROW:y*ROW+WIDTH*4]=bytes([255,0,0,255])*WIDTH
        self.data[:16]=b''.join(x.to_bytes(4,'little') for x in (0xff44564d,0xff505253,0xff424c52,0xff000003))
        (self.out/'managed-final.bgra').write_bytes(self.data)
        self.lines=['GPU_LOAD_CA_SHARED_SETUP us=1500 width=1179 height=2556 row=4864 surface=42 frames=3']
        self.records=[]
        def add(op,request,reply):self.records.append(dict(seq=len(self.records)+1,op=op,request=request,reply=dict(reply,ok=True)))
        add('library',dict(sha256='8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364'),dict(handle=1))
        add('sharedRenderCreate',{},dict(handle=2))
        display=[]
        for i in range(1,4):
            add('sharedRenderAcquire',dict(handle=2,epoch=i),{})
            add('renderSubmit',dict(commands=[dict(target=2)]),dict(status=4,draws=5))
            add('sharedRenderSeal',dict(handle=2,epoch=i),dict(completedWrites=1))
            add('sharedRenderRetire',dict(handle=2,epoch=i,swap=i+10,waitMode=1,waitResult=0),{})
            self.lines.append(f'GPU_LOAD_CA_SHARED_FRAME frame={i} swap={i+10} completed_writes=1 render_us=2000 display_us=1000 total_us=3000')
            display.append(f'iomfb: gpu-present frame={i} swap={i+10} dva=0x1234 monotonic_ns={100000*i}\nD594 nested completed, status 0x0\n')
        add('stats',{},dict(live=dict(objects=0,resourceBytes=0)))
        self.lines.extend([f'GPU_LOAD_CA_SHARED_FINAL frames=3 bad_pixels=0 sha={hashlib.sha256(self.data[:BYTES]).hexdigest()} verification_reads_in_batch=0','GPU_LOAD_CA_SHARED_POWER_RESET rc=0','GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0'])
        self.display=''.join(display);(self.out/'display.log').write_text(self.display)

    def tearDown(self):self.tmp.cleanup()

    def test_complete_fixture_and_independent_scanout(self):
        result=verify_records(self.out,self.lines,self.records,3)
        self.assertTrue(result['verified']);self.assertFalse(result['final_scanout_export_checked'])
        (self.out/'last-presented.bgra').write_bytes(self.data[:BYTES])
        self.assertTrue(verify_scanout_export(self.out,self.out)['verified'])
        wrong=bytearray(self.data[:BYTES]);wrong[-4]^=1
        (self.out/'last-presented.bgra').write_bytes(wrong)
        with self.assertRaisesRegex(ValueError,'actual final DCP'):verify_scanout_export(self.out,self.out)

    def test_scene_identity_is_not_taken_from_guest_claim(self):
        lines=list(self.lines);lines[0]+=' scene=1'
        with self.assertRaisesRegex(ValueError,'requested scene'):verify_records(self.out,lines,self.records,3,scene=0)

    def test_alpha_tolerance_does_not_cover_markers_or_other_colors(self):
        # Frame 8: moving stripe [136,296), front opaque stripe [280,360).
        row=bytes([255,0,0,255])*136+bytes([127,0,129,255])*144+bytes([0,255,0,255])*80+bytes([255,0,0,255])*(WIDTH-360)
        data=bytearray((row+bytes(ROW-len(row)))*HEIGHT)
        data[:16]=struct.pack('<4I',0xff44564d,0xff505253,0xff424c52,0xff000008)
        verify_pixels(data,8,1)
        for offset,value in ((0,0),(4*136,126),(4*136+1,1),(4*136+3,254),(4*280+1,254)):
            bad=bytearray(data);bad[offset]=value
            with self.assertRaisesRegex(ValueError,'pixel oracle'):verify_pixels(bad,8,1)

    def test_false_completion_wrong_target_and_early_retirement(self):
        for mutate in (lambda r:r[3]['reply'].update(status=3),lambda r:r[3]['request']['commands'][0].update(target=9),
                       lambda r:r[4]['reply'].update(completedWrites=0),lambda r:r[5]['request'].update(waitMode=0),
                       lambda r:r[5]['request'].update(epoch=2),lambda r:r[-1]['reply']['live'].update(objects=1)):
            records=copy.deepcopy(self.records);mutate(records)
            with self.assertRaises(ValueError):verify_records(self.out,self.lines,records,3)

    def test_matching_forged_hash_cannot_replace_pixel_oracle(self):
        self.data[100]^=1;(self.out/'managed-final.bgra').write_bytes(self.data)
        lines=list(self.lines);lines[-3]=f'GPU_LOAD_CA_SHARED_FINAL frames=3 bad_pixels=0 sha={hashlib.sha256(self.data[:BYTES]).hexdigest()} verification_reads_in_batch=0'
        with self.assertRaisesRegex(ValueError,'pixel oracle'):verify_records(self.out,lines,self.records,3)

    def test_missing_native_frame_and_completion(self):
        for bad in (self.display.replace('gpu-present frame=2','other frame=2'),self.display.replace('D594 nested completed, status 0x0','D594 failed')):
            (self.out/'display.log').write_text(bad)
            with self.assertRaises(ValueError):verify_records(self.out,self.lines,self.records,3)

    def test_handoff_requires_parent_receipt_and_shared_bytes(self):
        lines=['GPU_LOAD_SURFACE_SEND job=7 result=0 surface=42',
               'GPU_LOAD_SURFACE_RECEIVED job=7 surface=42 bytes=12435456',*self.lines,
               'GPU_LOAD_SURFACE_RETURN job=7 pid=100 surface=42 alias_verified=1']
        struct.pack_into('<QQ',self.data,BYTES,7,7^0x44564d48414e4446)
        (self.out/'managed-final.bgra').write_bytes(self.data)
        (self.out/'managed-pages.bin').write_bytes(bytes(16)+struct.pack('<QQ',759*16384,759)+struct.pack('<759Q',*(i*16384 for i in range(759))))
        (self.out/'shared-ram.bin').write_bytes(bytes(32))
        self.assertTrue(verify_handoff(self.out,lines,7,100)['verified'])
        for old,new in [('pid=100','pid=101'),('alias_verified=1','alias_verified=0'),('result=0','result=5')]:
            with self.assertRaises(ValueError):verify_handoff(self.out,[x.replace(old,new) for x in lines],7,100)
        with self.assertRaisesRegex(ValueError,'order'):verify_handoff(self.out,lines[::-1],7,100)
        self.data[BYTES+8]^=1;(self.out/'managed-final.bgra').write_bytes(self.data)
        with self.assertRaisesRegex(ValueError,'independent shared-memory'):verify_handoff(self.out,lines,7,100)

    def test_pacing_reports_slow_frames_without_hiding_them(self):
        frames=[]
        for i,(start,finish,target) in enumerate(((0,100000,-1),(100000,140000,-1),(150000,160000,150000),(166667,200000,166666.667))):
            total=finish-start
            frames.append(dict(start_us=str(start),finish_us=str(finish),target_us=str(target),total_us=str(total),render_us=str(total-1000),display_us='1000'))
        result=verify_pacing(frames,60,[10,20,30,40000])
        self.assertEqual(result['deadline_misses'],1)
        self.assertEqual(result['over_60hz_work_budget'],1)
        self.assertEqual(result['work_us']['max'],33333)
        with self.assertRaisesRegex(ValueError,'targets'):verify_pacing(frames,30,[10,20,30,40000])
        with self.assertRaisesRegex(ValueError,'scanout'):verify_pacing(frames,60,[10,20,30,29])

    def test_shared_memory_and_pacing_complete_fixture(self):
        lines=self.lines[:];lines[0]+=' hz=60 warmup=2'
        frame=0
        for i,line in enumerate(lines):
            if line.startswith('GPU_LOAD_CA_SHARED_FRAME '):
                frame+=1;start=frame*20000
                lines[i]+=f' start_us={start} finish_us={start+3000} target_us={start if frame==3 else -1}'
        lines[-1:-1]=[f'GPU_LOAD_CA_SHARED_MEMORY frame={f} resident=16000000 footprint=15000000 objects=2 resource_bytes=12435456' for f in (2,3)]
        records=[]
        for r in copy.deepcopy(self.records):
            records.append(r)
            if r['op']=='sharedRenderRetire' and r['request']['epoch'] in (2,3):
                records.append(dict(op='stats',request={},reply=dict(ok=True,live=dict(objects=2,resourceBytes=12435456))))
        for i,r in enumerate(records):r['seq']=i+1
        result=verify_records(self.out,lines,records,3,60)
        self.assertEqual(result['memory_change']['resource_bytes'],0)
        profiled=[x+' acquire_us=100 update_us=100 encode_us=100 seal_us=1700 enqueue_us=400 wait_us=200 retire_us=400' if x.startswith('GPU_LOAD_CA_SHARED_FRAME ') else x for x in lines]
        profiled[0]+=' profile=1'
        self.assertEqual(verify_records(self.out,profiled,records,3,60)['pacing']['stage_us']['seal_us']['mean'],1700)
        with self.assertRaisesRegex(ValueError,'profile accounting'):
            verify_records(self.out,[x.replace('seal_us=1700','seal_us=1699') for x in profiled],records,3,60)
        bad=[x.replace('resource_bytes=12435456','resource_bytes=100') for x in lines]
        with self.assertRaisesRegex(ValueError,'differs from backend'):verify_records(self.out,bad,records,3,60)


if __name__=='__main__':unittest.main()
