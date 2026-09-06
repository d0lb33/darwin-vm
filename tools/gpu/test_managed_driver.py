"""Real host Metal over registered scattered pages; no guest/display claim."""
import json,os,struct,subprocess,tempfile,unittest,time,select
from pathlib import Path
from test_driver_host import BUILD,AIR,SHA
from managed_pages import read_resource,LENGTH,COUNT,PAGE

class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
        self.session=b'owned-managed-01';self.seq=0
        self.offsets=[(COUNT-i)*PAGE for i in range(COUNT)]
        for name,size in (('shared-ram.bin',0x1000000),('managed-ram.bin',0x300000000)):
            fd=os.open(self.out/name,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);os.ftruncate(fd,size);os.close(fd)
        with (self.out/'shared-ram.bin').open('r+b') as f:f.write(struct.pack('<II',0x44564d31,1));f.seek(16);f.write(self.session)
        self.record=self.session+struct.pack('<QQ',LENGTH,COUNT)+struct.pack('<759Q',*self.offsets)
        self.manifest(self.record);self.canaries()
        env={k:v for k,v in os.environ.items() if not k.startswith('DVM_DRIVER_')}
        env.update(DVM_DRIVER_LIBRARY=str(AIR),DVM_DRIVER_PRESENT_RAM=str(self.out/'shared-ram.bin'),
                   DVM_DRIVER_MANAGED_RAM=str(self.out/'managed-ram.bin'),DVM_DRIVER_MANAGED_PAGES=str(self.out/'managed-pages.bin'))
        if self._testMethodName=='test_gpu_completion_waits_for_delayed_event':env['DVM_DRIVER_MANAGED_DELAY_US']='150000'
        self.p=subprocess.Popen([str(BUILD/'driver_host')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env)
    def manifest(self,data):
        path=self.out/'managed-pages.bin';path.write_bytes(data);path.chmod(0o600)
    def canaries(self):
        with (self.out/'managed-ram.bin').open('r+b') as f:f.seek(self.offsets[0]);f.write(struct.pack('<3I',0x44564d41,0x44564d42,0))
    def tearDown(self):
        self.p.stdin.close()
        try:self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
        self.p.stdout.close();self.p.stderr.close();self.tmp.cleanup()
    def rpc(self,op,**fields):
        self.seq+=1;raw=json.dumps(dict(seq=self.seq,op=op,**fields)).encode()
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush()
        n,=struct.unpack('<I',self.p.stdout.read(4));return json.loads(self.p.stdout.read(n))
    def library(self):return self.rpc('library',length=AIR.stat().st_size,sha256=SHA)['handle']
    def test_registration_rejects_foreign_duplicate_unaligned_and_out_of_range(self):
        library=self.library()
        variants=[b'foreign-session!'+self.record[16:],self.record[:-8]]
        for value in (self.offsets[1],1,0x300000000):
            r=bytearray(self.record);struct.pack_into('<Q',r,32,value);variants.append(r)
        for record in variants:
            self.manifest(record)
            self.assertFalse(self.rpc('residentCreate',library=library,nonce=1)['ok'])
        self.manifest(self.record)
        self.assertTrue(self.rpc('residentCreate',library=library,nonce=1)['ok'])
    def test_gpu_batch_retirement_guards_and_release_recreate(self):
        library=self.library();handle=self.rpc('residentCreate',library=library,nonce=1)['handle']
        self.assertEqual(struct.unpack_from('<I',read_resource(self.out),8)[0],0x44564d43)
        self.assertFalse(self.rpc('residentRetire',handle=handle,frame=0)['ok'])
        for frame in range(1,34):
            self.assertTrue(self.rpc('residentDraw',handle=handle,frame=frame)['ok'])
            if frame==1:
                self.assertFalse(self.rpc('residentDraw',handle=handle,frame=2)['ok'])
                self.assertFalse(self.rpc('release',handle=handle)['ok'])
                self.assertFalse(self.rpc('residentRetire',handle=handle,frame=2)['ok'])
            self.assertTrue(self.rpc('residentRetire',handle=handle,frame=frame)['ok'])
        self.assertTrue(self.rpc('residentVerify',handle=handle)['ok'])
        self.assertEqual(struct.unpack_from('<4I',read_resource(self.out)),(0xff44564d,0xff505253,0xff424c52,0xff000021))
        self.assertTrue(self.rpc('release',handle=handle)['ok'])
        self.assertFalse(self.rpc('residentDraw',handle=handle,frame=1)['ok'])
        self.canaries();new=self.rpc('residentCreate',library=library,nonce=2)['handle']
        self.assertGreater(new,handle)
        self.assertTrue(self.rpc('residentDraw',handle=new,frame=1)['ok'])
        self.assertTrue(self.rpc('residentRetire',handle=new,frame=1)['ok'])
        self.assertTrue(self.rpc('release',handle=new)['ok'])
        self.assertTrue(self.rpc('release',handle=library)['ok'])
        self.assertEqual(self.rpc('stats')['live']['objects'],0)
    def test_gpu_completion_waits_for_delayed_event(self):
        library=self.library();handle=self.rpc('residentCreate',library=library,nonce=1)['handle']
        self.seq+=1;raw=json.dumps(dict(seq=self.seq,op='residentDraw',handle=handle,frame=1)).encode()
        started=time.monotonic();self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush()
        self.assertEqual(select.select([self.p.stdout],[],[],.03)[0],[])
        self.assertEqual(struct.unpack_from('<I',read_resource(self.out))[0],0x44564d41)
        n,=struct.unpack('<I',self.p.stdout.read(4));reply=json.loads(self.p.stdout.read(n))
        self.assertTrue(reply['ok']);self.assertGreaterEqual(time.monotonic()-started,.14)
        self.assertEqual(struct.unpack_from('<I',read_resource(self.out))[0],0xff44564d)
        self.assertFalse(self.rpc('release',handle=handle)['ok'])
        self.assertTrue(self.rpc('residentRetire',handle=handle,frame=1)['ok'])
        self.assertTrue(self.rpc('release',handle=handle)['ok'])
    def test_extended_frame_identity_and_early_verification(self):
        library=self.library()
        for frames in (True,1,8193):
            self.assertFalse(self.rpc('residentCreate',library=library,nonce=7,frames=frames)['ok'])
        handle=self.rpc('residentCreate',library=library,nonce=7,frames=257)['handle']
        for frame in range(1,258):
            self.assertTrue(self.rpc('residentDraw',handle=handle,frame=frame)['ok'])
            self.assertTrue(self.rpc('residentRetire',handle=handle,frame=frame)['ok'])
            if frame==33:self.assertFalse(self.rpc('residentVerify',handle=handle)['ok'])
        self.assertFalse(self.rpc('residentDraw',handle=handle,frame=258)['ok'])
        self.assertTrue(self.rpc('residentVerify',handle=handle)['ok'])
        self.assertEqual(struct.unpack_from('<I',read_resource(self.out),12)[0],0xff000101)
