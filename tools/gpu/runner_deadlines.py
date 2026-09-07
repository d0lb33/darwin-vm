"""Host-clock safeguards independent of an interactive VM's lifetime."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerDeadlines:
    session_seconds: int | None = 600
    readiness_seconds: int = 300
    job_seconds: int = 100

    def session_expired(self, elapsed):
        return self.session_seconds is not None and elapsed >= self.session_seconds

    def check_runner(self, now, started, ready, current):
        if not ready and now - started >= self.readiness_seconds:
            raise TimeoutError('runner boot/readiness deadline exceeded')
        # Include staging and completion delivery; the child's own 90-second
        # watchdog and individual GPU completion deadlines remain independent.
        if current is not None and now - current['started'] >= self.job_seconds:
            raise TimeoutError('runner test staging/execution/completion deadline exceeded')
