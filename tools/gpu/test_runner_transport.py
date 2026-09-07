"""Audit ring wrap and immutable revision staging without a guest boot."""
import json
import base64
import copy
import mmap
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from driver_mmio_peer import MMIOPeer
from driver_runner_peer import RunnerPeer
from verify_audit_capture import verify_audits


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

    def test_drained_capture_verifies_overwritten_slots(self):
        for seq in range(120,381):
            self.put(seq,f'GPU_LOAD_FRAME frame={seq}');self.peer.audit()
        records=[json.loads(x) for x in (self.peer.out/'driver-audit.jsonl').read_text().splitlines()]
        raw=self.peer.ram[:]
        self.assertEqual(verify_audits(records,raw),'drained-slots-v1')
        bad=copy.deepcopy(records);slot=bytearray(base64.b64decode(bad[0]['slot_v1']['bytes']));slot[-1]^=1
        bad[0]['slot_v1']['bytes']=base64.b64encode(slot).decode()
        with self.assertRaisesRegex(ValueError,'CRC'):verify_audits(bad,raw)
        with self.assertRaisesRegex(ValueError,'sequence'):verify_audits(records[:10]+records[11:],raw)
        bad=copy.deepcopy(records);bad[0]['slot_v1']['session']='00'*16
        with self.assertRaisesRegex(ValueError,'session'):verify_audits(bad,raw)
        bad=copy.deepcopy(records);del bad[0]['slot_v1']
        with self.assertRaisesRegex(ValueError,'mixed'):verify_audits(bad,raw)


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

    def test_acceptance_exception_preserves_guest_result_and_raw_evidence(self):
        for shared in (False, True):
            with self.subTest(shared=shared), tempfile.TemporaryDirectory() as temp:
                p=self.peer([]);p.out=Path(temp);p.records=[];p.ram=b'captured-transport'
                (p.out/'driver-audit.jsonl').write_text('')
                directory=p.out/'job';directory.mkdir()
                p.pending=dict(job=dict(job=42,expected='pass',shared_surface=shared),
                    result=dict(job=42,pid=123,spawn=0,exit=0,signal=0),out=directory,
                    first_record=0,first_audit=0,first_serial=0,first_display=0,started=10,ended=11)
                # A successful process exit with no completion witness must
                # fail acceptance without losing the exit or killing collection.
                with patch.object(MMIOPeer,'audit',return_value=[]), patch(
                        'driver_runner_peer.capture_uart_interval',return_value=(b'raw-log',{})):
                    self.assertEqual(p.audit(),[])
                result=json.loads((directory/'result.json').read_text())
                self.assertEqual(result['exit'],0)
                self.assertFalse(result['verified'])
                self.assertIn('ValueError',result['verification_error'])
                self.assertEqual((directory/'shared-ram.bin').read_bytes(),p.ram)
                self.assertEqual((directory/'guest-loading.log').read_bytes(),b'raw-log')
                self.assertIsNone(p.pending)
                self.assertEqual(p.results,[result])
                if shared:
                    with self.assertRaisesRegex(ValueError,'VM recovery'):
                        p.control_reply(dict(op='runnerNext'))


if __name__=='__main__':unittest.main()
