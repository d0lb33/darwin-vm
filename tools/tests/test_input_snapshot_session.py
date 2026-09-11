"""Host-only gates for interaction checkpoint selection."""
import importlib.util
from pathlib import Path
import tempfile
import json
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "snapshot_session",
    Path(__file__).resolve().parents[1] / "input" / "snapshot_session.py",
)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class SnapshotGateTests(unittest.TestCase):
    def test_idle_requires_ready_released_transport(self):
        ready = dict(guest_state="R", inflight=0, queue_len=0, wire_pending=0,
                     wheel_pending=0, contact_sent=False, btn_down=False)
        self.assertTrue(snapshot.idle_input(ready))
        for key, value in (("inflight", 1), ("queue_len", 1),
                           ("wire_pending", 1), ("wheel_pending", 1),
                           ("contact_sent", True), ("btn_down", True)):
            self.assertFalse(snapshot.idle_input(dict(ready, **{key: value})))
        self.assertFalse(snapshot.idle_input(dict(ready, guest_state="I")))

    def test_last_complete_power_transition_wins(self):
        text = ("iomfb: A484 display power 1 -> 0 (flags 00 00 00 00)\n"
                "iomfb: A484 display power 0 -> 1 (flags 00 00 00 00)\n")
        self.assertTrue(snapshot.last_display_power(text))
        self.assertFalse(snapshot.last_display_power(text.splitlines()[0]))
        self.assertIsNone(snapshot.last_display_power("iomfb: presented frame\n"))

    def test_stable_input_retries_periodic_ping(self):
        ready = dict(guest_state="R", inflight=0, queue_len=0, wire_pending=0,
                     wheel_pending=0, contact_sent=False, btn_down=False,
                     epoch=2, guest_pid=9)
        busy = dict(ready, inflight=1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps(busy))
            calls = iter([busy, ready, ready])
            with patch.object(snapshot, "read_json", side_effect=lambda _p: next(calls)), \
                 patch.object(snapshot.time, "monotonic",
                              side_effect=[0, 0, 0, .001, .001, .02, .02]), \
                 patch.object(snapshot.time, "sleep"):
                first, second = snapshot.wait_stable_input(path, 1, .01)
            self.assertEqual((first["epoch"], second["epoch"]), (2, 2))

    def test_launch_resources_are_selected_by_identity(self):
        argv = ["qemu", "-monitor", "unix:/tmp/run/monitor.sock,server,nowait",
                "-drive", "if=none,id=ans,file=/tmp/run/disk.qcow2,format=qcow2"]
        self.assertEqual(snapshot.endpoint(argv, "-monitor"),
                         Path("/tmp/run/monitor.sock").resolve())
        self.assertEqual(snapshot.ans_disk(argv),
                         Path("/tmp/run/disk.qcow2").resolve())


if __name__ == "__main__":
    unittest.main()
