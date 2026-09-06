"""Actual Metal execution and rejection before upload for bounded blur frames."""
import base64,struct,json
import test_driver_host as existing
from blur_peer import reply as decode_reply,request as decode_request
class BlurTests(existing.HostTests):
    def setup_blur(self):
        lib=self.rpc('library',length=existing.AIR.stat().st_size,sha256=existing.SHA)['handle']
        p=self.rpc('pipeline',library=lib,function='compute_simd_blur_5')['handle']
        ts=[self.rpc('texture',width=w,height=h,format=115,usage=3)['handle'] for w,h in ((96,96),(64,96),(64,64))]
        raw=bytearray(192+96*96*8)
        struct.pack_into('<IIQ4I4Q',raw,0,0x31514c42,1,self.seq+1,len(raw),64,64,96*96*8,p,*ts)
        for i in range(2):
            off=64+i*64
            struct.pack_into('<10H',raw,off,0,0,0,0,0 if i else 32,32 if i else 0,32,32,0 if i else 1,0x3c00)
            struct.pack_into('<5e',raw,off+20,.0625,.25,.375,.25,.0625)
            struct.pack_into('<8I',raw,off+32,2,2 if i else 3,1,32,32,1,32,32)
        raw[192:]=struct.pack('<e',1)*96*96*4
        return raw,ts
    def wire(self,raw):
        self.seq+=1;struct.pack_into('<Q',raw,8,self.seq)
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush()
        n,=struct.unpack('<I',self.p.stdout.read(4));out=self.p.stdout.read(n)
        return (decode_reply(out),out[64:]) if out[:4]==b'BLP1' else (json.loads(out),None)
    def test_exact_blur_and_reuse(self):
        raw,ts=self.setup_blur();decode_request(raw)
        for _ in range(2):
            r,pixels=self.wire(raw);self.assertTrue(r['ok']);self.assertEqual(pixels,struct.pack('<e',1)*64*64*4)
        self.assertEqual(self.rpc('stats')['submissions'],2)
        for handle in range(1,6):self.rpc('release',handle=handle)
        self.assertEqual(self.rpc('stats')['live']['objects'],0)
    def test_bad_state_does_not_upload_or_submit(self):
        raw,ts=self.setup_blur();poison=struct.pack('<e',.25)*96*96*4
        self.assertTrue(self.rpc('upload',texture=ts[0],row=768,data=base64.b64encode(poison).decode())['ok'])
        for offset in (64,64+20,64+32,64+56,128+32):
            bad=bytearray(raw);bad[offset]^=1;r,_=self.wire(bad);self.assertFalse(r['ok'])
            self.assertEqual(self.rpc('stats')['submissions'],0)
            self.assertEqual(base64.b64decode(self.rpc('read',texture=ts[0])['data']),poison)
    def test_unknown_and_alias_handles_rejected(self):
        raw,ts=self.setup_blur()
        for handle in (99999,ts[0]):
            bad=bytearray(raw);struct.pack_into('<Q',bad,56,handle);self.assertFalse(self.wire(bad)[0]['ok'])
        self.assertEqual(self.rpc('stats')['submissions'],0)
    def test_malformed_extent_rejected(self):
        raw,_=self.setup_blur();struct.pack_into('<I',raw,24,257)
        with self.assertRaises(ValueError):decode_request(raw)
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush();self.assertEqual(self.p.wait(timeout=5),3)
