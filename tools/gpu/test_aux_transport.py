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
        self.run_c_peer(False)

    def test_c_peer_latency64(self):
        self.run_c_peer(True)

    def test_c_peer_retries_incomplete_latency_response(self):
        self.run_c_peer(True, torn=True)

    def test_c_peer_waits_for_session_gate(self):
        self.run_c_peer(True, post_boot=True)

    def test_c_peer_legacy_wait_budget(self):
        self.run_c_peer(True,post_boot=True,budget_override=0)

    def test_c_peer_rejects_invalid_wait_budget(self):
        self.run_c_peer(True,post_boot=True,budget_override=481,expect_failure=True)

    def run_c_peer(self, latency, torn=False, post_boot=False, budget_override=None, expect_failure=False):
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
            peer = AuxProbe(out, latency=latency,post_boot=post_boot,readiness_seconds=450 if post_boot else 300)
            if budget_override is not None:
                peer.budget_config=struct.pack("<I",budget_override)
                os.pwrite(peer.fd,peer.budget_config,152)
            proc = None
            try:
                with (out/'guest.log').open('w') as log:
                    proc = subprocess.Popen([str(out/'peer'),str(peer.path)],stderr=log)
                    deadline=time.monotonic()+30
                    torn_deadline=None
                    gate_deadline=None
                    while proc.poll() is None and time.monotonic()<deadline:
                        if post_boot and not peer.released:
                            if 'GPU_LOAD_AUX_WAIT ' in (out/'guest.log').read_text() and gate_deadline is None:
                                identity=bytearray(peer.header);identity[21]^=1
                                foreign=bytes(identity)+b'DVMGO001'
                                os.pwrite(peer.fd,(foreign+struct.pack('<I',zlib.crc32(foreign))).ljust(4096,b'\0'),0x30000)
                                gate_deadline=time.monotonic()+.25
                            self.assertEqual(os.pread(peer.fd,4096,0x10000),bytes(4096))
                            self.assertEqual(os.pread(peer.fd,4096,0x400000),bytes(4096))
                            if gate_deadline and time.monotonic()>=gate_deadline:peer.release()
                        if torn and not peer.seen:
                            request=os.pread(peer.fd,4096,0x10000)
                            if request[:64]==peer.header and torn_deadline is None:
                                payload=bytes(4024)
                                os.pwrite(peer.fd,request[:68]+struct.pack('<I',zlib.crc32(payload)^1)+payload,0x20000)
                                torn_deadline=time.monotonic()+.02
                            if torn_deadline and time.monotonic()<torn_deadline:
                                time.sleep(.001);continue
                        peer.pump();time.sleep(.001)
                self.assertIsNotNone(proc.poll(),'C peer exceeded its bounded test')
                if expect_failure:
                    self.assertNotEqual(proc.returncode,0)
                    self.assertFalse(peer.seen)
                    self.assertEqual(os.pread(peer.fd,4096,0x400000),bytes(4096))
                    return
                if post_boot:
                    self.assertIn(f"budget_seconds={budget_override or 330 if budget_override is not None else 480}",(out/"guest.log").read_text())
                self.assertEqual(proc.returncode,0,(out/'guest.log').read_text())
                self.assertEqual(peer.verify()['live_requests_verified'],64 if latency else 10)
                if latency:
                    records=[s for s in (out/'guest.log').read_text().splitlines()
                        if s.startswith('GPU_LOAD_AUX_LAT seq=')]
                    self.assertEqual(len(records),64)
                    self.assertTrue(all(' valid=1 ' in s for s in records))
                    if torn:
                        self.assertGreater(int(records[0].split('crc_retries=')[1].split()[0]),0)
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
