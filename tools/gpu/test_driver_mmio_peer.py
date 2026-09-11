"""Exercise real worker framing with a fake notification device, not a VM claim."""
import base64
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
import zlib
from driver_mmio_peer import MMIOPeer,MAGIC,STAGING_MAGIC,STAGING_OFFSET,STAGING_BYTES,STAGING_DESCRIPTOR

BUILD=Path(os.environ.get('DVM_DRIVER_BUILD','/tmp/dvm/MMIO_METAL_BUILD1'))
AIR=Path('/tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib')

class MMIOPeerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='dvm-mmio-',dir='/tmp')
        self.out=Path(self.tmp.name)
        self.server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.server.bind(str(self.out/'gpu-notify.sock'));self.server.listen(1)
        self.peer=MMIOPeer(self.out,BUILD/'driver_host',AIR,boot=True)
        struct.pack_into('<II',self.peer.ram,0,MAGIC,1)
        self.peer.ram[16:32]=b'owned-session-01'
        self.peer.pump();self.wire,_=self.server.accept();self.wire.settimeout(1)
        self.assertEqual(self.wire.recv(16),bytes(16))
    def tearDown(self):
        self.peer.close();self.wire.close();self.server.close();self.tmp.cleanup()
    def request(self,seq=1,session=b'owned-session-01',crc_bad=False):
        raw=json.dumps(dict(seq=seq,op='stats')).encode();crc=zlib.crc32(raw) ^ int(crc_bad)
        self.peer.ram[0x10000:0x10000+len(raw)]=raw
        self.peer.ram[0x40:0x60]=session+struct.pack('<QII',seq,len(raw),crc)
        return struct.pack('<QII',seq,len(raw),crc)
    def test_fragmented_notification_real_worker_reply(self):
        packet=self.request();self.wire.sendall(packet[:5]);self.peer.pump()
        self.assertEqual(self.peer.seen,set())
        self.wire.sendall(packet[5:]);self.peer.pump()
        self.assertEqual(self.wire.recv(16),struct.pack('<QII',1,0,0))
        session,seq,n,crc=struct.unpack('<16sQII',self.peer.ram[0x80:0xa0])
        present=(BUILD/"transport-mode.txt").read_text().strip() in ("--mmio-present","--mmio-present-pool")
        offset=0x200000 if present else 0x800000
        raw=self.peer.ram[offset:offset+n]
        self.assertEqual(self.peer.ram[0x300000:0x300040],bytes(64))
        self.assertEqual(zlib.crc32(raw),crc);self.assertEqual(seq,1)
        self.assertEqual(session,b'owned-session-01');self.assertTrue(json.loads(raw)['ok'])
        self.assertEqual(len(self.peer.records),1)
    def test_payload_bound_rejected_before_worker(self):
        present=(BUILD/"transport-mode.txt").read_text().strip() in ("--mmio-present","--mmio-present-pool")
        self.wire.sendall(struct.pack("<QII",1,(0x10000 if present else 0x200000)+1,0))
        with self.assertRaisesRegex(ValueError,"notification"):self.peer.pump()
        self.assertEqual(self.peer.seen,set())
    def test_bad_crc_never_reaches_worker(self):
        self.wire.sendall(self.request(crc_bad=True))
        with self.assertRaisesRegex(ValueError,'CRC'):self.peer.pump()
        self.assertEqual(self.peer.seen,set())
    def test_foreign_session_never_reaches_worker(self):
        self.wire.sendall(self.request(session=b'foreign-session!'))
        with self.assertRaisesRegex(ValueError,'session'):self.peer.pump()
        self.assertEqual(self.peer.seen,set())
    def framed(self,request,seq,payload=None,crc_bad=False):
        """Frame one JSON request the way the guest transport does; a payload
        goes through the staging region with its descriptor in the request."""
        if payload is not None:
            self.peer.ram[STAGING_OFFSET:STAGING_OFFSET+len(payload)]=payload
            request=dict(request,staged=dict(offset=0,length=len(payload),crc=zlib.crc32(payload)^int(crc_bad)))
        raw=json.dumps(dict(request,seq=seq)).encode();crc=zlib.crc32(raw)
        self.peer.ram[0x10000:0x10000+len(raw)]=raw
        self.peer.ram[0x40:0x60]=b'owned-session-01'+struct.pack('<QII',seq,len(raw),crc)
        return struct.pack('<QII',seq,len(raw),crc)
    def reply(self,seq):
        self.assertEqual(self.wire.recv(16),struct.pack('<QII',seq,0,0))
        session,returned,n,crc=struct.unpack('<16sQII',self.peer.ram[0x80:0xa0])
        self.assertEqual((session,returned),(b'owned-session-01',seq))
        raw=self.peer.ram[self.peer.reply_offset:self.peer.reply_offset+n]
        self.assertEqual(zlib.crc32(raw),crc);return json.loads(raw)
    def test_staging_constants_mirror_the_shared_header(self):
        header=(Path(__file__).with_name('driver_capabilities.h')).read_text()
        defines={m[1]:int(m[2],0) for m in __import__('re').finditer(r'#define (DVM_STAGING_\w+) (0x[0-9a-fA-F]+|\d+)',header)}
        self.assertEqual(defines['DVM_STAGING_OFFSET'],STAGING_OFFSET);self.assertEqual(defines['DVM_STAGING_BYTES'],STAGING_BYTES)
        self.assertEqual(defines['DVM_STAGING_DESCRIPTOR'],STAGING_DESCRIPTOR);self.assertEqual(defines['DVM_STAGING_MAGIC'],STAGING_MAGIC)
        self.assertEqual(STAGING_OFFSET+STAGING_BYTES,0x200000)  # ends where mode-3 replies begin
    def test_staged_payload_reaches_worker_as_base64_and_bad_crc_never_does(self):
        if not self.peer.present:self.skipTest('staging is a mode-3 (present/managed) contract')
        self.assertEqual(struct.unpack_from('<3Q',self.peer.ram,STAGING_DESCRIPTOR),(STAGING_MAGIC,STAGING_OFFSET,STAGING_BYTES))
        self.wire.sendall(self.framed(dict(op='buffer',length=65536),1));self.peer.pump()
        handle=self.reply(1)['handle']
        payload=bytes(range(256))*256  # twice the old 32 KiB chunk bound, one request
        self.wire.sendall(self.framed(dict(op='writeRenderBuffer',buffer=handle,offset=0),2,payload));self.peer.pump()
        self.assertTrue(self.reply(2)['ok'])
        record=self.peer.records[-1]
        self.assertEqual(record['staged_bytes'],65536);self.assertLess(record['request_bytes'],256)
        self.assertNotIn('data',record['request']);self.assertNotIn('staged',record['request'])
        for seq,offset in ((3,0),(4,32768)):
            self.wire.sendall(self.framed(dict(op='readRenderBuffer',buffer=handle,offset=offset,length=32768),seq));self.peer.pump()
            self.assertEqual(base64.b64decode(self.reply(seq)['data']),payload[offset:offset+32768])
        self.wire.sendall(self.framed(dict(op='writeRenderBuffer',buffer=handle,offset=0),5,payload,crc_bad=True))
        with self.assertRaisesRegex(ValueError,'staged payload CRC'):self.peer.pump()
        self.assertEqual(max(self.peer.seen),4)
    def test_audit_publication_and_corruption(self):
        raw=b'GPU_LOAD_DRIVER_RUN bounded=1\n'
        self.peer.ram[0x1010:0x1010+len(raw)]=raw
        struct.pack_into('<QII',self.peer.ram,0x1000,1,len(raw),zlib.crc32(raw))
        self.assertEqual(self.peer.audit(),[])
        struct.pack_into('<Q',self.peer.ram,0x180,1)
        self.assertEqual(self.peer.audit(),[raw.decode().strip()])
        self.assertEqual(self.peer.audit(),[])
        struct.pack_into('<QII',self.peer.ram,0x1200,2,len(raw),zlib.crc32(raw)^1)
        self.peer.ram[0x1210:0x1210+len(raw)]=raw
        struct.pack_into('<Q',self.peer.ram,0x180,2)
        with self.assertRaisesRegex(ValueError,'audit CRC'):self.peer.audit()
    def test_audit_head_bounds(self):
        for limit in (64,120):
            self.peer.audit_limit=limit
            struct.pack_into('<Q',self.peer.ram,0x180,limit+1)
            with self.assertRaisesRegex(ValueError,'head bounds'):self.peer.audit()
    def test_duplicate_never_reexecutes(self):
        packet=self.request();self.wire.sendall(packet);self.peer.pump();self.wire.recv(16)
        self.wire.sendall(packet)
        with self.assertRaisesRegex(ValueError,'notification'):self.peer.pump()
        self.assertEqual(len(self.peer.records),1)

if __name__=='__main__':unittest.main()
