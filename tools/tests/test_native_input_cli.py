"""The UI probe must not confuse queue ACKs with completed HID input."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'native_input', Path(__file__).resolve().parents[1] / 'input/native_input.py')
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)


class CompletionTests(unittest.TestCase):
    def baseline(self):
        return dict(epoch=1, guest_state='R', inflight=0, queue_len=0,
                    wire_pending=0, wheel_pending=0, contact_sent=False,
                    sent=0, acked=0, pings=0, dispatched=0, dispatch_failed=0,
                    ack_failed=0, ack_not_ready=0, ack_rejected=0, timeouts=0,
                    overflow_or_not_ready_drops=0)

    def test_home_waits_for_dispatch_and_rejects_failed_hid(self):
        before = self.baseline()
        queued = dict(before, sent=2, acked=2, inflight=2)
        failed = dict(queued, inflight=0, dispatch_failed=2)
        observed = []

        def wait(path, predicate, timeout, label):
            observed.append(predicate(queued))
            self.assertTrue(predicate(failed))
            return failed

        with patch.object(native, 'wait_status', side_effect=wait):
            after = native.completed(SimpleNamespace(ack_timeout=1), None, before, 2)
        self.assertEqual(observed, [False])
        self.assertFalse(native.successful(native.outcome(before, after)))

    def test_wheel_batch_and_epoch_change_cannot_pass(self):
        before = self.baseline()
        def wait(path, predicate, timeout, label):
            self.assertFalse(predicate(dict(before, sent=1, acked=1,
                                            dispatched=1, wheel_pending=1)))
            return predicate(dict(before, epoch=2))
        with patch.object(native, 'wait_status', side_effect=wait):
            with self.assertRaisesRegex(RuntimeError, 'epoch changed'):
                native.completed(SimpleNamespace(ack_timeout=1), None, before, 1)

    def test_not_ready_and_dropped_input_are_failures(self):
        before = self.baseline()
        for key in ('ack_not_ready', 'ack_rejected', 'overflow_or_not_ready_drops'):
            after = dict(before, sent=2, acked=2, dispatched=2)
            after[key] = 1
            self.assertFalse(native.successful(native.outcome(before, after)))

    def test_bad_coordinates_rejected_before_sending(self):
        for value in ('-1', '32768'):
            with self.assertRaises(ValueError):
                native.norm(value, 1179, False)


if __name__ == '__main__':
    unittest.main()
