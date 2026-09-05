#!/usr/bin/env python3
"""Cross-language mailbox test and DT opaque-byte preservation checks."""
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
import zlib
from aux_probe import AuxProbe
from aux_namespace_dt import EXPECTED, extend, properties


class AuxTests(unittest.TestCase):
    def test_c_peer_bulk_ping_timeout_recovery(self):
        with tempfile.TemporaryDirectory(prefix='gpu-aux-test-', dir='/tmp/dvm') as temp:
            out = Path(temp)
            source = out/'peer.c'
            header = Path(__file__).with_name('aux_transport_probe.h').resolve()
            source.write_text('''#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <errno.h>
#include <unistd.h>
#include "'''+str(header)+'''"
int main(int argc,char **argv){
    if(argc!=2)return 2;
    int fd=open(argv[1],O_RDWR);unsigned char h[4096];
    if(fd<0||pread(fd,h,sizeof(h),0)!=sizeof(h))return 3;
    int rc=aux_transfer(fd,h);close(fd);return rc;
}
''')
            subprocess.run(['xcrun','clang','-O2','-Wall','-Wextra','-Werror',
                '-Wno-unused-function','-Wno-unused-variable',str(source),'-o',str(out/'peer')], check=True)
            peer = AuxProbe(out)
            proc = None
            try:
                with (out/'guest.log').open('w') as log:
                    proc = subprocess.Popen([str(out/'peer'),str(peer.path)],stderr=log)
                    deadline=time.monotonic()+30
                    while proc.poll() is None and time.monotonic()<deadline:
                        peer.pump();time.sleep(.001)
                self.assertIsNotNone(proc.poll(),'C peer exceeded its bounded test')
                self.assertEqual(proc.returncode,0,(out/'guest.log').read_text())
                self.assertEqual(peer.verify()['live_requests_verified'],10)
                os.pwrite(peer.fd,b'bad',0x400000)
                with self.assertRaisesRegex(ValueError,'exact guest bulk'):
                    peer.verify()
            finally:
                if proc and proc.poll() is None:
                    proc.kill();proc.wait()
                peer.close()

    def test_stale_session_and_bad_crc_ignored(self):
        with tempfile.TemporaryDirectory(prefix='gpu-aux-test-',dir='/tmp/dvm') as temp:
            peer=AuxProbe(temp)
            try:
                os.pwrite(peer.fd,b'foreign-session'+bytes(4081),0x10000)
                peer.pump();self.assertFalse(peer.seen)
                identity=bytearray(peer.header);identity[21]^=1
                payload=bytes((i*13+17)&255 for i in range(72,4096))
                os.pwrite(peer.fd,identity+struct.pack('<II',1,zlib.crc32(payload))+payload,0x10000)
                peer.pump();self.assertFalse(peer.seen)
                os.pwrite(peer.fd,peer.header+struct.pack('<II',1,1)+bytes(4024),0x10000)
                peer.pump();self.assertFalse(peer.seen)
            finally:peer.close()

    def test_lossless_opaque_dt(self):
        def prop(k,v):
            return k.encode().ljust(32,b'\0')+struct.pack('<I',len(v))+v+bytes((-len(v))%4)
        def node(name,values,children):
            return struct.pack('<II',len(values)+1,len(children))+prop('name',name.encode()+b'\0')+b''.join(prop(k,v) for k,v in values)+b''.join(children)
        ans=node('ans',[('namespaces',b''.join(struct.pack('<III',*v) for v in EXPECTED))],[])
        original=node('device-tree',[('random-seed',b'A'*256)],
            [node('arm-io',[],[ans])])
        result=extend(original)
        self.assertEqual(len(result),len(original)+12)
        self.assertEqual(properties(result)[('/device-tree','random-seed')][2],b'A'*256)
        with self.assertRaisesRegex(ValueError,'unexpected namespace list'):
            extend(result)
        with self.assertRaises((ValueError,struct.error)):
            extend(original[:-1])


if __name__=='__main__':unittest.main()
