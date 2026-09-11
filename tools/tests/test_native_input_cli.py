"""The UI probe must not confuse queue ACKs with completed HID input."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import tempfile
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

    def test_periodic_pings_are_not_reported_as_user_input(self):
        before = self.baseline()
        after = dict(before, sent=5, acked=5, pings=3, dispatched=2)
        result = native.outcome(before, after)
        self.assertEqual(result['pings'], 3)
        self.assertEqual(result['records_sent'], 2)
        self.assertEqual(result['records_acked'], 2)
        self.assertTrue(native.successful(result))

    def test_bad_coordinates_rejected_before_sending(self):
        for value in ('-1', '32768'):
            with self.assertRaises(ValueError):
                native.norm(value, 1179, False)


class WakeTests(unittest.TestCase):
    def test_power_uses_restore_stderr_name(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'qemu.stderr.log'
            log.write_text('iomfb: A484 display power 0 -> 1 (flags 00)\n')
            self.assertTrue(native.DisplayPower(directory).poll())

    def test_power_requires_complete_A484_not_a_frame_or_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory)/'stderr.log'
            log.write_text('iomfb: A484 display power 1 -> 0 (flags 00)\n')
            power = native.DisplayPower(directory)
            self.assertFalse(power.poll())
            with log.open('a') as f:
                f.write('iomfb: presented frame\niomfb: A484 display power 0 -> 1 ')
            self.assertFalse(power.poll())
            with log.open('a') as f:
                f.write('(flags 00)\n')
            self.assertTrue(power.poll())

    def test_wake_is_idempotent_and_unknown_does_not_toggle(self):
        args = SimpleNamespace(hold_ms=80, wake_timeout=.1)
        with patch.object(native, 'DisplayPower') as power, patch.object(native, 'key_press') as press:
            power.return_value.poll.return_value = True
            self.assertTrue(native.wake_display(args, Path('/unused'))['already_on'])
            press.assert_not_called()
            power.return_value.poll.return_value = None
            with self.assertRaisesRegex(RuntimeError, 'power unknown'):
                native.wake_display(args, Path('/unused'))
            press.assert_not_called()

    def test_ack_without_power_on_times_out_without_retry(self):
        args = SimpleNamespace(hold_ms=80, wake_timeout=0)
        result = dict(records_sent=2, records_acked=2, ack_failed=0, ack_not_ready=0,
                      ack_rejected=0, timeouts=0, dispatch_failed=0, overflow_or_not_ready_drops=0)
        with patch.object(native, 'DisplayPower') as power, \
             patch.object(native, 'key_press', return_value=result) as press, \
             patch.object(native, 'ready', return_value={'epoch':1}):
            power.return_value.poll.return_value = False
            with self.assertRaisesRegex(TimeoutError, 'A484 display ON'):
                native.wake_display(args, Path('/unused'))
            self.assertEqual(press.call_count, 1)


if __name__ == '__main__':
    unittest.main()
