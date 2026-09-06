import struct
import unittest
import zlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from driver_peer import MAX,PAGE,packet_fields,DriverPeer

class PacketTests(unittest.TestCase):
    def packet(self,seq=1,length=256):
        p=bytearray(PAGE);p[:64]=b'x'*64;struct.pack_into('<QII',p,64,seq,length,0x12345678)
        struct.pack_into('<I',p,PAGE-4,zlib.crc32(p[:-4]));return p
    def test_corruption_and_foreign_session_not_executed(self):
        p=self.packet();self.assertEqual(packet_fields(p,b'x'*64),(1,256,0x12345678))
        self.assertIsNone(packet_fields(p,b'y'*64));p[75]^=1;self.assertIsNone(packet_fields(p,b'x'*64))
    def test_invalid_but_crc_valid_contract_rejected(self):
        for seq,n in ((0,256),(1,0),(1,MAX+1)):
            with self.assertRaises(ValueError):packet_fields(self.packet(seq,n),b'x'*64)
        p=self.packet();p[80]=1;struct.pack_into('<I',p,PAGE-4,zlib.crc32(p[:-4]))
        with self.assertRaises(ValueError):packet_fields(p,b'x'*64)

class ReadinessTests(unittest.TestCase):
    def test_stale_ready_and_restart_cannot_release_work(self):
        with tempfile.TemporaryDirectory() as root:
            p=DriverPeer.__new__(DriverPeer);p.out=Path(root);p.started=0
            p.released=False;p.ready_since=None;p.ready_identity=None;p.ready_acks=0
            p.fd=os.open(p.out/'aux.raw',os.O_CREAT|os.O_RDWR,0o600)
            try:
                (p.out/'stderr.log').write_text('iomfb: presented 1179x2556\n')
                def gate(t,pid,acks):
                    (p.out/'input-status.json').write_text(json.dumps(dict(guest_state='R',guest_pid=pid,guest_epoch=pid,acked=acks)))
                    with patch('driver_peer.time.monotonic',return_value=t):p.gate()
                gate(0,69,10);gate(20,69,10)
                self.assertFalse(p.released) # R with no new ACK is insufficient.
                gate(21,70,11);gate(22,70,12)
                self.assertFalse(p.released) # A restart begins a new stable interval.
                gate(31,70,13)
                self.assertTrue(p.released)
                self.assertEqual(os.pread(p.fd,4,96),struct.pack('<I',1))
            finally:os.close(p.fd)

if __name__=='__main__':unittest.main()
