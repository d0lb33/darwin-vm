"""Audit ring wrap and immutable revision staging without a guest boot."""
import json
import mmap
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from driver_mmio_peer import MMIOPeer
from driver_runner_peer import RunnerPeer


class AuditRingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=MMIOPeer.__new__(MMIOPeer);p.out=Path(self.temp.name)
        p.ram=mmap.mmap(-1,16*1024*1024);self.addCleanup(p.ram.close)
        p.sock=object();p.header=bytes(range(16));p.ram[16:32]=p.header
        p.runner=True;p.audit_limit=120;p.audit_seen=119
        self.peer=p

    def put(self,seq,line):
        p=self.peer;data=line.encode();offset=0x1000+((seq-1)%120)*512
        p.ram[offset+16:offset+16+len(data)]=data
        struct.pack_into('<QII',p.ram,offset,seq,len(data),zlib.crc32(data))
        struct.pack_into('<Q',p.ram,0x180,seq)

    def test_wrap_preserves_order_and_ack(self):
        self.put(120,'GPU_LOAD_A');self.put(121,'GPU_LOAD_B')
        self.assertEqual(self.peer.audit(),['GPU_LOAD_A','GPU_LOAD_B'])
        self.assertEqual(struct.unpack_from('<Q',self.peer.ram,0x188)[0],121)
        records=[json.loads(x) for x in (self.peer.out/'driver-audit.jsonl').read_text().splitlines()]
        self.assertEqual([r['seq'] for r in records],[120,121])

    def test_overwrite_before_consumption_rejected(self):
        self.put(240,'GPU_LOAD_LAPPED')
        with self.assertRaisesRegex(ValueError,'head bounds'):self.peer.audit()

    def test_corrupt_wrapped_payload_not_acknowledged(self):
        self.put(120,'GPU_LOAD_A');self.put(121,'GPU_LOAD_B')
        self.peer.ram[0x1010]=0
        with self.assertRaisesRegex(ValueError,'CRC'):self.peer.audit()
        self.assertEqual(struct.unpack_from('<Q',self.peer.ram,0x188)[0],120)


class RunnerVerdictTests(unittest.TestCase):
    def peer(self,results):
        p=RunnerPeer.__new__(RunnerPeer);p.current=None;p.pending=None;p.results=results
        return p

    def test_observed_loading_failures_are_not_gpu_success(self):
        p=self.peer([dict(pid=1,expected='observe',verified=False)])
        with self.assertRaisesRegex(ValueError,'no required verified GPU'):p.verify([])

    def test_required_failure_cannot_hide_behind_another_pass(self):
        p=self.peer([dict(pid=1,expected='pass',verified=True),dict(pid=2,expected='pass',verified=False)])
        with self.assertRaisesRegex(ValueError,'required runner workload failed'):p.verify([])

    def test_reused_pid_fails_even_with_pixel_verification(self):
        p=self.peer([dict(pid=1,expected='pass',verified=True),dict(pid=1,expected='pass',verified=True)])
        with self.assertRaisesRegex(ValueError,'fresh-process identity'):p.verify([])

    def test_shared_failure_prevents_backend_reset_and_pool_reuse(self):
        p=self.peer([dict(shared_surface=True,verified=False)])
        with self.assertRaisesRegex(ValueError,'VM recovery'):p.control_reply(dict(op='runnerNext'))
        p=self.peer([]);p.pending=dict(result='awaiting verification')
        self.assertEqual(p.control_reply(dict(op='runnerNext')),dict(action='idle'))


if __name__=='__main__':unittest.main()
