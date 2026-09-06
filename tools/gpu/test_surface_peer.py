"""Mailbox rejection tests and opt-in real host Metal integration.

DVM_SURFACE_WORKER=/absolute/metal_proxy_server DVM_SURFACE_AIR=/absolute/slice0.metallib
python3 -m unittest discover -s tools/gpu -p test_surface_peer.py -v
The optional Metal test simulates the guest; only a VM run proves guest integration.
"""
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from surface_peer import SurfacePeer, checked_packet


def request(identity, seq, nonce):
    data=bytes((i*17+(i>>8)*31+seq*43+(nonce>>((i%4)*8)))&255 for i in range(12288))
    packet=bytearray(4096);packet[:64]=identity
    struct.pack_into('<IIII',packet,64,seq,nonce,len(data),zlib.crc32(data))
    struct.pack_into('<I',packet,4092,zlib.crc32(packet[:-4]))
    return packet,data


class SurfaceTests(unittest.TestCase):
    def test_rejects_cross_session_and_torn_publication(self):
        identity=os.urandom(64)
        packet,_=request(identity,1,123)
        self.assertIsNotNone(checked_packet(packet,identity))
        self.assertIsNone(checked_packet(packet,os.urandom(64)))
        packet[70]^=1
        self.assertIsNone(checked_packet(packet,identity))

    def test_rejects_valid_crc_but_invalid_size(self):
        identity=os.urandom(64);packet,_=request(identity,1,123)
        struct.pack_into('<I',packet,72,0xffffffff)
        struct.pack_into('<I',packet,4092,zlib.crc32(packet[:-4]))
        with self.assertRaisesRegex(ValueError,'contract'):
            checked_packet(packet,identity)

    @unittest.skipUnless(os.getenv('DVM_SURFACE_WORKER') and os.getenv('DVM_SURFACE_AIR'),'requires explicit real Metal worker and exact guest AIR')
    def test_real_metal_and_no_duplicate_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            peer=SurfacePeer(Path(directory),os.environ['DVM_SURFACE_WORKER'],os.environ['DVM_SURFACE_AIR'])
            try:
                for seq in (1,2,3):
                    packet,data=request(peer.header,seq,seq*193+7)
                    os.pwrite(peer.fd,data,0x100000);os.pwrite(peer.fd,packet,0x10000)
                    peer.pump();peer.pump()
                    reply=os.pread(peer.fd,4096,0x20000)
                    self.assertEqual(reply[:80],packet[:80])
                    self.assertEqual(zlib.crc32(reply[:-4]),struct.unpack_from('<I',reply,4092)[0])
                    self.assertEqual(os.pread(peer.fd,12288,0x200000),data)
                self.assertEqual(peer.verify()['submissions'],3)
                peer.finish()
            finally:
                peer.close()

if __name__=='__main__':
    unittest.main()
