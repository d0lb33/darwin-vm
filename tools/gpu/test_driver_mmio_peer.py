"""Exercise real worker framing with a fake notification device, not a VM claim."""
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest
import zlib
from driver_mmio_peer import MMIOPeer,MAGIC

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
        raw=self.peer.ram[0x800000:0x800000+n]
        self.assertEqual(zlib.crc32(raw),crc);self.assertEqual(seq,1)
        self.assertEqual(session,b'owned-session-01');self.assertTrue(json.loads(raw)['ok'])
        self.assertEqual(len(self.peer.records),1)
    def test_bad_crc_never_reaches_worker(self):
        self.wire.sendall(self.request(crc_bad=True))
        with self.assertRaisesRegex(ValueError,'CRC'):self.peer.pump()
        self.assertEqual(self.peer.seen,set())
    def test_foreign_session_never_reaches_worker(self):
        self.wire.sendall(self.request(session=b'foreign-session!'))
        with self.assertRaisesRegex(ValueError,'session'):self.peer.pump()
        self.assertEqual(self.peer.seen,set())
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
