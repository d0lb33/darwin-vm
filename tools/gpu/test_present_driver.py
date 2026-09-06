"""Real resident GPU writes, sequence rejection and resource retirement."""
import json,mmap,os,struct,subprocess,tempfile,unittest
from pathlib import Path
from test_driver_host import BUILD,AIR,SHA
class PresentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.ram=Path(self.tmp.name)/'owned.bin'
        with self.ram.open('wb') as f:f.truncate(0x1000000)
        self.fd=self.ram.open('r+b');self.map=mmap.mmap(self.fd.fileno(),0x1000000)
        struct.pack_into('<II',self.map,0,0x44564d31,1)
        env={**os.environ,'DVM_DRIVER_LIBRARY':str(AIR),'DVM_DRIVER_PRESENT_RAM':str(self.ram)};env.pop('DVM_DRIVER_BOOTSTRAP',None)
        self.p=subprocess.Popen([str(BUILD/'driver_host')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env);self.seq=0
    def tearDown(self):
        self.p.stdin.close()
        try:self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        self.p.stdout.close();self.p.stderr.close();self.map.close();self.fd.close();self.tmp.cleanup()
    def rpc(self,op,**fields):
        self.seq+=1;b=json.dumps(dict(seq=self.seq,op=op,**fields)).encode();self.p.stdin.write(struct.pack('<I',len(b))+b);self.p.stdin.flush();n,=struct.unpack('<I',self.p.stdout.read(4));return json.loads(self.p.stdout.read(n))
    def library(self):return self.rpc('library',length=AIR.stat().st_size,sha256=SHA)['handle']
    def test_full_resident_batch_final_oracle_and_retirement(self):
        lib=self.library();r=self.rpc('residentCreate',library=lib,nonce=0x12345678);self.assertTrue(r['ok']);handle=r['handle']
        self.assertGreater(r['resourceBytes'],80*1024*1024)
        self.assertFalse(self.rpc('residentVerify',handle=handle)['ok'])
        for frame in range(1,34):
            r=self.rpc('residentDraw',handle=handle,frame=frame);self.assertTrue(r['ok']);self.assertEqual(r['status'],4);self.assertEqual(r['dispatches'],3)
        # Only now read pixels and execute the independent CPU oracle.
        self.assertEqual(struct.unpack_from('<4I',self.map,0x300000),(0xff44564d,0xff505253,0xff424c52,0xff000021))
        self.assertTrue(self.rpc('residentVerify',handle=handle)['verified'])
        self.assertFalse(self.rpc('residentDraw',handle=handle,frame=34)['ok'])
        for h in (handle,lib):self.assertTrue(self.rpc('release',handle=h)['ok'])
        stats=self.rpc('stats');self.assertEqual(stats['submissions'],33);self.assertEqual(stats['live']['objects'],0);self.assertEqual(stats['live']['resourceBytes'],0)
        self.assertFalse(self.rpc('residentDraw',handle=handle,frame=1)['ok'])
    def test_invalid_setup_and_sequence_do_not_write_output(self):
        lib=self.library();self.map[0x300000:0x300040]=b'\xa5'*64
        self.assertFalse(self.rpc('residentCreate',library=lib,nonce=-1)['ok'])
        handle=self.rpc('residentCreate',library=lib,nonce=1)['handle']
        self.assertFalse(self.rpc('residentCreate',library=lib,nonce=1)['ok'])
        for frame in (0,2,34,1.5,True):self.assertFalse(self.rpc('residentDraw',handle=handle,frame=frame)['ok'])
        self.assertEqual(self.map[0x300000:0x300040],b'\xa5'*64);self.assertEqual(self.rpc('stats')['submissions'],0)
        self.assertTrue(self.rpc('residentDraw',handle=handle,frame=1)['ok'])
        self.assertFalse(self.rpc('residentDraw',handle=handle,frame=1)['ok'])
        self.assertEqual(self.rpc('stats')['submissions'],1)
