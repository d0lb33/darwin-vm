"""Real AIR execution, malformed protocol, and public-selector driver checks."""
import base64
import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import unittest
import tempfile
from driver_peer import DriverPeer

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path(os.environ.get('DVM_DRIVER_BUILD', '/tmp/dvm/METAL_DRIVER_BUILD1'))
AIR = Path('/tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib')
SHA = '8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364'

class HostTests(unittest.TestCase):
    def setUp(self):
        self.p = subprocess.Popen([str(BUILD/'driver_host')], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, 'DVM_DRIVER_LIBRARY':str(AIR)})
        self.seq = 0
    def tearDown(self):
        self.p.stdin.close()
        try:self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        self.p.stdout.close();self.p.stderr.close()
    def rpc(self, op, **fields):
        self.seq += 1
        raw = json.dumps(dict(seq=self.seq,op=op,**fields)).encode()
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush()
        n,=struct.unpack('<I',self.p.stdout.read(4))
        return json.loads(self.p.stdout.read(n))
    def test_identity_and_stale_handles(self):
        b=self.rpc('buffer',length=96);self.assertTrue(b['ok'])
        self.assertFalse(self.rpc('read',texture=b['handle'])['ok'])
        self.assertTrue(self.rpc('release',handle=b['handle'])['ok'])
        self.assertFalse(self.rpc('read',buffer=b['handle'])['ok'])
        c=self.rpc('buffer',length=16);self.assertGreater(c['handle'],b['handle'])
    def test_library_and_bounds(self):
        self.assertFalse(self.rpc('library',length=AIR.stat().st_size,sha256='0'*64)['ok'])
        lib=self.rpc('library',length=AIR.stat().st_size,sha256=SHA);self.assertTrue(lib['ok'])
        self.assertFalse(self.rpc('pipeline',library=lib['handle'],function='read_write_surf_compute')['ok'])
        p=self.rpc('pipeline',library=lib['handle'],function='compute_sum_luma');self.assertTrue(p['ok'])
        self.assertFalse(self.rpc('submit',commands=[dict(pipeline=p['handle'],textures=[],bytes=[],groups=[1,1,1],threads=[8,1,1])])['ok'])
        self.assertEqual(self.rpc('stats')['submissions'],0)
    def test_full_buffer_transfer(self):
        b=self.rpc('buffer',length=96)['handle'];data=bytes(range(96))
        self.assertFalse(self.rpc('upload',buffer=b,data=base64.b64encode(data[:-1]).decode())['ok'])
        self.assertTrue(self.rpc('upload',buffer=b,data=base64.b64encode(data).decode())['ok'])
        self.assertEqual(base64.b64decode(self.rpc('read',buffer=b)['data']),data)
    def test_bad_second_encoder_cannot_execute_first(self):
        lib=self.rpc('library',length=AIR.stat().st_size,sha256=SHA)['handle']
        avg=self.rpc('pipeline',library=lib,function='compute_average_luma')['handle']
        total=self.rpc('pipeline',library=lib,function='compute_sum_luma')['handle']
        texture=self.rpc('texture',width=64,height=48,format=115,usage=1)['handle']
        partial=self.rpc('buffer',length=96)['handle']
        result=self.rpc('buffer',length=16)['handle']
        uniform=base64.b64encode(bytes.fromhex('0000000040003000010000000600000001000000')).decode()
        first=dict(pipeline=avg,textures=[texture],buffers=[dict(index=2,buffer=partial,offset=0)],
            bytes=[dict(index=0,data=uniform)],threadgroupMemory=[dict(index=0,length=1024)],groups=[1,6,1],threads=[8,8,1])
        second=dict(pipeline=total,textures=[],buffers=[dict(index=1,buffer=partial,offset=0),dict(index=2,buffer=result,offset=0)],
            bytes=[dict(index=0,data=uniform)],threadgroupMemory=[dict(index=0,length=128)],groups=[1,1,1],threads=[8,1,1])
        variants=[]
        bad=copy.deepcopy(second);bad['buffers'][0]['buffer']=result;variants.append(bad)
        bad=copy.deepcopy(second);bad['buffers'][1]['offset']=16;variants.append(bad)
        bad=copy.deepcopy(second);bad['threadgroupMemory'][0]['length']=16;variants.append(bad)
        bad=copy.deepcopy(second);bad['groups']=[2,1,1];variants.append(bad)
        for bad in variants:
            self.assertFalse(self.rpc('submit',commands=[first,bad])['ok'])
            self.assertEqual(self.rpc('stats')['submissions'],0)
        poison=base64.b64encode(bytes([0xa5])*96).decode()
        upload=dict(buffer=partial,data=poison)
        for commands,uploads,readbacks in (
            ([first,variants[0]],[upload],[result]),
            ([first,second],[upload,dict(buffer=result,data=poison)],[result]),
            ([first,second],[upload],[999999]),
            ([first,second],[upload,upload],[result]),
            ([first,second],[upload],[result,result]),
        ):
            self.assertFalse(self.rpc('submit',commands=commands,uploads=uploads,readbacks=readbacks)['ok'])
            self.assertEqual(self.rpc('stats')['submissions'],0)
            self.assertEqual(base64.b64decode(self.rpc('read',buffer=partial)['data']),bytes(96))
    def test_oversized_frame(self):
        self.p.stdin.write(struct.pack('<I',2*1024*1024+1));self.p.stdin.flush()
        self.assertEqual(self.p.wait(timeout=5),3)
    def test_depth_state_descriptor_validation_and_retirement(self):
        good=dict(compare=7,write=False,front=[],back=[7,0,0,0,0xffffffff,0xffffffff])
        for fields in (dict(good,compare=8),dict(good,write=0),dict(good,front=[0]),dict(good,back=[7,8,0,0,0,0])):
            self.assertFalse(self.rpc('depthState',**fields)['ok'])
        state=self.rpc('depthState',**good);self.assertTrue(state['ok'])
        self.assertTrue(self.rpc('release',handle=state['handle'])['ok'])
        self.assertEqual(self.rpc('stats')['live']['objects'],0)
    def test_resource_limits_and_typed_counts(self):
        self.assertFalse(self.rpc('buffer',length=True)['ok'])
        self.assertFalse(self.rpc('texture',width=512,height=512,format=115,usage=1)['ok'])
        self.rpc('buffer',length=16)
        live=self.rpc('stats')['live']
        self.assertEqual(live['buffers'],1)
        self.assertEqual(live['textures'],0)
        self.assertEqual(live['resourceBytes'],16)
    def test_linear_alias_bounds_coherence_and_parent_lifetime(self):
        alignment=self.rpc('linearLayout',format=30)['alignment']
        row=max(256,alignment);parent=self.rpc('buffer',length=row*128)['handle']
        fields=dict(buffer=parent,width=128,height=128,format=30,usage=1,offset=0,row=row)
        for bad in (dict(fields,offset=1),dict(fields,row=1),dict(fields,height=129)):
            self.assertFalse(self.rpc('linearTexture',**bad)['ok'])
        texture=self.rpc('linearTexture',**fields)['handle']
        pattern=bytes((i%251 for i in range(row*128)))
        self.assertTrue(self.rpc('upload',buffer=parent,data=base64.b64encode(pattern).decode())['ok'])
        pixels=base64.b64decode(self.rpc('read',texture=texture)['data'])
        self.assertEqual(pixels,b''.join(pattern[y*row:y*row+256] for y in range(128)))
        self.assertFalse(self.rpc('release',handle=parent)['ok'])
        self.assertTrue(self.rpc('release',handle=texture)['ok'])
        self.assertTrue(self.rpc('release',handle=parent)['ok'])
        self.assertEqual(self.rpc('stats')['live']['resourceBytes'],0)
    def test_render_chunk_bounds_and_failed_batch_does_not_clear(self):
        b=self.rpc('buffer',length=65536)['handle']
        raw=base64.b64encode(bytes([91])*32768).decode()
        self.assertFalse(self.rpc('writeRenderBuffer',buffer=b,offset=32769,data=raw)['ok'])
        self.assertTrue(self.rpc('writeRenderBuffer',buffer=b,offset=32768,data=raw)['ok'])
        self.assertEqual(base64.b64decode(self.rpc('read',buffer=b)['data']),bytes(32768)+bytes([91])*32768)
        target=self.rpc('texture',width=64,height=64,format=80,usage=5)['handle']
        original=bytes([71])*16384
        self.rpc('upload',texture=target,row=256,data=base64.b64encode(original).decode())
        good=dict(kind='render',target=target,load=2,store=1,clear=[1,0,0,1],operations=[])
        for tasks in ([True],[-1],[2**32],list(range(17))):
            self.assertFalse(self.rpc('renderSubmit',commands=[good],uploads=[],readbacks=[],guestTaskIDs=tasks)['ok'])
        bad=dict(good,operations=[['pipeline',999999]])
        self.assertFalse(self.rpc('renderSubmit',commands=[good,bad],uploads=[],readbacks=[])['ok'])
        self.assertEqual(base64.b64decode(self.rpc('read',texture=target)['data']),original)
        result=self.rpc('renderSubmit',commands=[good],uploads=[],readbacks=[])
        self.assertTrue(result['ok']);self.assertEqual(result['passes'],1)
        self.assertGreaterEqual(result['gpu_end'],result['gpu_start'])
        self.assertEqual(base64.b64decode(self.rpc('read',texture=target)['data']),bytes([0,0,255,255])*4096)

class DriverTests(unittest.TestCase):
    def test_render_writeback_completion_and_ordering(self):
        result=subprocess.run([str(BUILD/'test_render_writeback')],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASS render writes:',result.stderr)

    def test_capability_batch_is_negotiated_and_bound_to_validation(self):
        result=subprocess.run([str(BUILD/'driver_capability_test')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('bounded_render_stages=1',result.stderr)
    def test_host_bootstrap_precedes_guest_and_preserves_first_sequence(self):
        with tempfile.TemporaryDirectory() as root:
            peer=DriverPeer(Path(root),BUILD/'driver_host',AIR,boot=True)
            try:
                self.assertEqual(os.pread(peer.fd,4,96),struct.pack('<I',1))
                self.assertFalse((Path(root)/'input-status.json').exists())
                self.assertFalse((Path(root)/'stderr.log').exists())
                bootstrap=json.loads((Path(root)/'driver-readiness.json').read_text())
                self.assertTrue(bootstrap['bootstrap']['queue'])
                raw=json.dumps(dict(seq=1,op='stats')).encode()
                peer.proc.stdin.write(struct.pack('<I',len(raw))+raw);peer.proc.stdin.flush()
                length,=struct.unpack('<I',peer.read(4));reply=json.loads(peer.read(length))
                self.assertTrue(reply['ok']);self.assertEqual(reply['seq'],1)
                self.assertEqual(reply['submissions'],0)
            finally:peer.close()
    def test_failed_host_cannot_publish_boot_ready(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(RuntimeError):DriverPeer(Path(root),Path('/usr/bin/false'),AIR,boot=True)
            self.assertEqual((Path(root)/'aux.raw').read_bytes()[96:100],bytes(4))
            self.assertFalse((Path(root)/'driver-readiness.json').exists())
    def test_failure_and_ownership_contract(self):
        result=subprocess.run([str(BUILD/'driver_contract_test')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('no_false_success=1 retirement=1',result.stderr)
    def test_public_api_two_pass_execution(self):
        forward_r,forward_w=os.pipe();back_r,back_w=os.pipe()
        out=BUILD/'driver-host-test';out.mkdir(exist_ok=True)
        with (out/'worker.log').open('wb') as wlog,(out/'client.log').open('wb') as clog:
            worker=subprocess.Popen([str(BUILD/'driver_host')],stdin=forward_r,stdout=back_w,stderr=wlog,
                env={**os.environ,'DVM_DRIVER_LIBRARY':str(AIR)})
            client=subprocess.Popen([str(BUILD/'driver_client'),'proxy',str(AIR),'8'],stdin=back_r,stdout=forward_w,stderr=clog)
            for fd in (forward_r,forward_w,back_r,back_w):os.close(fd)
            try:
                self.assertEqual(client.wait(timeout=45),0)
                self.assertEqual(worker.wait(timeout=5),0)
            finally:
                for p in (client,worker):
                    if p.poll() is None:p.kill();p.wait()
        text=(out/'client.log').read_text();gpu=(out/'worker.log').read_text()
        self.assertEqual(text.count('verified=1 dispatches=2'),8)
        self.assertEqual(gpu.count('dispatches=2 status=4'),8)
        self.assertIn('result=pass resources=0',text)
        self.assertGreater(len(set(s.split('result=')[-1] for s in text.splitlines() if 'GPU_LOAD_DRIVER_RUN' in s)),1)

if __name__=='__main__':unittest.main()
