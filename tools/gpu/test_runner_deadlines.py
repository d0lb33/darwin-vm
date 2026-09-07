"""Advance the host clock without spending a guest boot on timeout policy."""
import unittest

from runner_deadlines import RunnerDeadlines


class DeadlineTests(unittest.TestCase):
    def test_automated_default_stops_at_600(self):
        policy = RunnerDeadlines()
        self.assertFalse(policy.session_expired(599.999))
        self.assertTrue(policy.session_expired(600))
        self.assertTrue(policy.session_expired(601))

    def test_interactive_idle_and_later_revision_have_no_session_cap(self):
        policy = RunnerDeadlines(session_seconds=None)
        for elapsed in (599, 600, 601, 3600, 86400):
            self.assertFalse(policy.session_expired(elapsed))
            policy.check_runner(100 + elapsed, 100, True, None)
            policy.check_runner(100 + elapsed, 100, True, {'started': 99 + elapsed})

    def test_stalled_boot_expires_even_without_session_cap(self):
        for cap in (600, None):
            policy = RunnerDeadlines(session_seconds=cap)
            policy.check_runner(399.999, 100, False, None)
            with self.assertRaisesRegex(TimeoutError, 'boot/readiness'):
                policy.check_runner(400, 100, False, None)

    def test_test_deadline_is_not_reset_by_ongoing_rpc_progress(self):
        for cap in (600, None):
            policy = RunnerDeadlines(session_seconds=cap)
            job = {'started': 90000}
            policy.check_runner(90099.999, 100, True, job)
            with self.assertRaisesRegex(TimeoutError, 'staging/execution/completion'):
                policy.check_runner(90100, 100, True, job)
            policy.check_runner(90100, 100, True, None)


if __name__ == '__main__':
    unittest.main()
