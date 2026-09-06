"""Binary codec and actual host Metal, including rejection before GPU mutation."""
import base64
import copy
import json
import struct
import test_driver_host as existing
from driver_binary import encode_request,decode_request,decode_reply

class BinaryTests(existing.HostTests):
    def setup_luma(self):
        lib=self.rpc('library',length=existing.AIR.stat().st_size,sha256=existing.SHA)['handle']
        avg=self.rpc('pipeline',library=lib,function='compute_average_luma')['handle']
        total=self.rpc('pipeline',library=lib,function='compute_sum_luma')['handle']
        texture=self.rpc('texture',width=64,height=48,format=115,usage=1)['handle']
        partial=self.rpc('buffer',length=96)['handle'];result=self.rpc('buffer',length=16)['handle']
        uniform=base64.b64encode(bytes.fromhex('0000000040003000010000000600000001000000')).decode()
        commands=[dict(pipeline=avg,textures=[texture],buffers=[dict(index=2,buffer=partial,offset=0)],bytes=[dict(index=0,data=uniform)],threadgroupMemory=[dict(index=0,length=1024)],groups=[1,6,1],threads=[8,8,1]),
            dict(pipeline=total,textures=[],buffers=[dict(index=1,buffer=partial,offset=0),dict(index=2,buffer=result,offset=0)],bytes=[dict(index=0,data=uniform)],threadgroupMemory=[dict(index=0,length=128)],groups=[1,1,1],threads=[8,1,1])]
        return dict(op='submit',commands=commands,uploads=[dict(buffer=partial,data=b'\xa5'*96),dict(buffer=result,data=b'\xa5'*16),dict(texture=texture,row=512,data=struct.pack('<4e',1,2,3,4)*3072)],readbacks=[partial,result])
    def binary(self,r):
        self.seq+=1;r={**r,'seq':self.seq};raw=encode_request(r)
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush()
        n,=struct.unpack('<I',self.p.stdout.read(4));reply=self.p.stdout.read(n)
        return decode_reply(reply) if n==272 else json.loads(reply)
    def test_binary_exact_output_reuse_and_retirement(self):
        r=self.setup_luma()
        for i in range(2):
            reply=self.binary(r);self.assertTrue(reply['ok'])
            a,b=r['readbacks'];self.assertEqual(base64.b64decode(reply['buffers'][str(a)]),struct.pack('<4f',512,1024,1536,2048)*6)
            self.assertEqual(base64.b64decode(reply['buffers'][str(b)]),struct.pack('<4f',3072,6144,9216,12288))
        stats=self.rpc('stats');self.assertEqual(stats['submissions'],2);self.assertEqual(stats['creations'],6)
        for handle in range(1,7):self.assertTrue(self.rpc('release',handle=handle)['ok'])
        self.assertEqual(self.rpc('stats')['live']['objects'],0)
    def test_binary_bad_handle_and_geometry_no_mutation(self):
        r=self.setup_luma()
        for field,value in [('pipeline',99999),('groups',[2,1,1])]:
            bad=copy.deepcopy(r);bad['commands'][1][field]=value
            self.assertFalse(self.binary(bad)['ok']);self.assertEqual(self.rpc('stats')['submissions'],0)
            self.assertEqual(base64.b64decode(self.rpc('read',buffer=r['readbacks'][0])['data']),bytes(96))
    def test_binary_out_of_bounds_slot_rejected_before_decode(self):
        r=self.setup_luma();r['seq']=self.seq+1;raw=bytearray(encode_request(r));struct.pack_into('<I',raw,288+16,0xfffffff0)
        with self.assertRaises(ValueError):decode_request(raw)
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush();self.assertEqual(self.p.wait(timeout=5),3)
    def test_binary_version_rejected(self):
        r=self.setup_luma();r['seq']=self.seq+1;raw=bytearray(encode_request(r));raw[4]=2
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush();self.assertEqual(self.p.wait(timeout=5),3)
    def test_binary_reserved_command_bytes_rejected(self):
        r=self.setup_luma();r['seq']=self.seq+1;raw=bytearray(encode_request(r));raw[32+96]=1
        with self.assertRaises(ValueError):decode_request(raw)
        self.p.stdin.write(struct.pack('<I',len(raw))+raw);self.p.stdin.flush();self.assertEqual(self.p.wait(timeout=5),3)
