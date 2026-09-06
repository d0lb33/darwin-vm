import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from aux_probe import AuxProbe
from aux_ready import AuxReady


class Wire:
    def __init__(self):self.sent=[]
    def send(self,data):self.sent.append(data);return len(data)


class ReadyTests(unittest.TestCase):
    def test_extended_budget_still_has_hard_deadline(self):
        with tempfile.TemporaryDirectory(dir="/tmp/dvm") as temp:
            peer=AuxProbe(temp,latency=True,post_boot=True,readiness_seconds=450)
            try:
                r=AuxReady(Path(temp),peer,{"readiness_deadline_seconds":450})
                r.tick(Wire(),300);r.tick(Wire(),449.9)
                with self.assertRaisesRegex(TimeoutError,"450 seconds"):r.tick(Wire(),450)
                self.assertFalse(peer.released)
            finally:peer.close()

    def test_reviewed_home_action_finishes_before_new_settling(self):
        with tempfile.TemporaryDirectory(dir='/tmp/dvm') as temp:
            out=Path(temp);peer=AuxProbe(out,latency=True,post_boot=True)
            try:
                r=AuxReady(out,peer,{});wire=Wire()
                r.feed('GPU_LOAD_AUX_WAIT version=1',wire,1)
                r.feed('DVM_INPUT_READY protocol=1',wire,2)
                r.feed('DVM_INPUT_ACK 910001 1',wire,3)
                with patch('aux_ready.HMP') as hmp:
                    hmp.return_value.command.side_effect=lambda command:(out/'ready-1.png').write_bytes(b'mocked lockscreen')
                    r.tick(wire,18)
                (out/'review-1.json').write_text(json.dumps(dict(r.candidate,home_visible=False,request_home=True,screen="LOCKSCREEN")))
                r.tick(wire,19)
                self.assertIn(b'910002 H 1 0 0',wire.sent[-1])
                r.feed('DVM_INPUT_ACK 910002 1',wire,20)
                self.assertIn(b'910003 H 0 0 0',wire.sent[-1])
                r.feed('DVM_INPUT_ACK 910003 1',wire,21)
                self.assertIn(b'910004 S 0 0 0',wire.sent[-1])
                self.assertIsNone(r.ack)
                r.feed('DVM_INPUT_ACK 910004 1',wire,22)
                r.tick(wire,36)
                self.assertIsNone(r.candidate);self.assertFalse(peer.released)
            finally:peer.close()

    def test_release_requires_exact_ack_image_and_fresh_ack(self):
        with tempfile.TemporaryDirectory(dir='/tmp/dvm') as temp:
            out=Path(temp);peer=AuxProbe(out,latency=True,post_boot=True)
            try:
                r=AuxReady(out,peer,{});wire=Wire()
                r.feed('GPU_LOAD_AUX_WAIT version=1',wire,10)
                r.feed('DVM_INPUT_ACK 910001 1',wire,11)
                self.assertIsNone(r.ack)
                r.feed('DVM_INPUT_READY protocol=1',wire,20)
                r.feed('DVM_INPUT_ACK 910001 10',wire,21)
                self.assertIsNone(r.ack)
                r.feed('DVM_INPUT_ACK 910001 1',wire,22)
                r.tick(wire,36);self.assertIsNone(r.candidate)
                with patch('aux_ready.HMP') as hmp:
                    hmp.return_value.command.side_effect=lambda command:(out/'ready-1.png').write_bytes(b'test-only mock image')
                    r.tick(wire,37)
                review=dict(r.candidate,home_visible=True,reviewer='unit-test')
                (out/'review-1.json').write_text(json.dumps(review))
                r.tick(wire,38);self.assertFalse(peer.released)
                r.feed('DVM_INPUT_ACK 910001 1',wire,39);self.assertFalse(peer.released)
                r.feed('DVM_INPUT_ACK 910002 1',wire,40);self.assertTrue(peer.released)
                with self.assertRaisesRegex(RuntimeError,'input restarted'):
                    r.feed('prefix DVM_INPUT_START pid=2',wire,41)
            finally:peer.close()

    def test_restart_invalidates_ack_and_global_readiness_deadline(self):
        with tempfile.TemporaryDirectory(dir='/tmp/dvm') as temp:
            peer=AuxProbe(temp,latency=True,post_boot=True)
            try:
                r=AuxReady(Path(temp),peer,{});wire=Wire()
                r.feed('DVM_INPUT_READY protocol=1',wire,1)
                r.feed('DVM_INPUT_ACK 910001 1',wire,2)
                r.feed('prefix DVM_INPUT_START pid=2',wire,3)
                self.assertIsNone(r.ack)
                r.feed('DVM_INPUT_ACK 910001 1',wire,4);self.assertIsNone(r.ack)
                with self.assertRaisesRegex(TimeoutError,'300 seconds'):r.tick(wire,300)
                self.assertFalse(peer.released)
            finally:peer.close()


if __name__=='__main__':unittest.main()
