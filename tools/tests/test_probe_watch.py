"""Fast tests for condition-triggered probe stopping and LLDB event lifetime."""
import importlib.util
import json
import os
import sys
import tempfile
import time
import types
import unittest


TOOLS = os.path.dirname(os.path.dirname(__file__))
RE = os.path.join(TOOLS, "re")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeWatchTests(unittest.TestCase):
    def setUp(self):
        self.watch = load("probe_watch_test", os.path.join(RE, "probe_watch.py"))

    def test_overdue_pending_selector_requests_stop(self):
        with tempfile.TemporaryDirectory() as root:
            events = os.path.join(root, "events")
            os.mkdir(events)
            with open(os.path.join(events, "ready"), "w") as stream:
                stream.write("{}\n")
            with open(os.path.join(events, "pending.7.json"), "w") as stream:
                json.dump({"call_id": 7, "selector": 0x4F, "time": time.time() - 5}, stream)
            stop = os.path.join(root, "stop")
            rc = self.watch.main([
                "--stop-file", stop, "--event-dir", events, "--selector", "0x4f",
                "--pending-secs", "1", "--poll-secs", "0.01", "--max-secs", "1",
            ])
            self.assertEqual(rc, 0)
            with open(stop) as stream:
                self.assertIn("selector-deadline selector=0x4f call_id=7", stream.read())

    def test_returned_call_does_not_stop(self):
        with tempfile.TemporaryDirectory() as root:
            events = os.path.join(root, "events")
            os.mkdir(events)
            with open(os.path.join(events, "ready"), "w") as stream:
                stream.write("{}\n")
            stop = os.path.join(root, "stop")
            rc = self.watch.main([
                "--stop-file", stop, "--event-dir", events, "--selector", "79",
                "--pending-secs", "0", "--poll-secs", "0.01", "--max-secs", "0.03",
            ])
            self.assertEqual(rc, 3)
            self.assertFalse(os.path.exists(stop))

    def test_durable_return_revokes_overdue_claim(self):
        with tempfile.TemporaryDirectory() as root:
            events = os.path.join(root, "events")
            os.mkdir(events)
            with open(os.path.join(events, "ready"), "w") as stream:
                stream.write("{}\n")
            event = {"call_id": 8, "selector": 0x4F, "time": time.time() - 5}
            with open(os.path.join(events, "pending.8.json"), "w") as stream:
                json.dump(event, stream)
            with open(os.path.join(events, "returned.8.json"), "w") as stream:
                json.dump(event, stream)
            stop = os.path.join(root, "stop")
            rc = self.watch.main([
                "--stop-file", stop, "--event-dir", events, "--selector", "0x4f",
                "--pending-secs", "1", "--poll-secs", "0.01", "--max-secs", "0.03",
            ])
            self.assertEqual(rc, 3)
            self.assertFalse(os.path.exists(stop))
            self.assertFalse(os.path.exists(os.path.join(events, "claimed.8.json")))

    def test_log_regex_requests_stop(self):
        with tempfile.TemporaryDirectory() as root:
            log = os.path.join(root, "serial.log")
            with open(log, "w") as stream:
                stream.write("set_power_state done powerState=0\n")
            stop = os.path.join(root, "stop")
            rc = self.watch.main([
                "--stop-file", stop, "--stop-on", log, "powerState=0",
                "--poll-secs", "0.01", "--max-secs", "1",
            ])
            self.assertEqual(rc, 0)
            with open(stop) as stream:
                self.assertIn("matched-log", stream.read())


class ReturnEventTests(unittest.TestCase):
    def test_wrong_thread_never_clears_pending_return(self):
        old_lldb = sys.modules.get("lldb")
        sys.modules["lldb"] = types.ModuleType("lldb")
        try:
            callbacks = load("display_iokit_callbacks_test",
                             os.path.join(RE, "display_iokit_callbacks.py"))
        finally:
            if old_lldb is None:
                del sys.modules["lldb"]
            else:
                sys.modules["lldb"] = old_lldb

        class Breakpoint:
            def GetID(self):
                return 9

        class Location:
            def GetBreakpoint(self):
                return Breakpoint()

        class Target:
            def __init__(self):
                self.deleted = []

            def BreakpointDelete(self, bp_id):
                self.deleted.append(bp_id)

        class Process:
            def __init__(self, target):
                self.target = target

            def GetTarget(self):
                return self.target

        class Thread:
            def __init__(self, process):
                self.process = process

            def GetProcess(self):
                return self.process

            def GetThreadID(self):
                return 3

        class Frame:
            def __init__(self, tp, thread):
                self.tp = tp
                self.thread = thread

            def GetThread(self):
                return self.thread

        target = Target()
        thread = Thread(Process(target))
        callbacks.RET_CONFIG[9] = {
            "label": "IOCONNECT_CALL_METHOD_RET", "tp": 0x111, "t": time.time(),
            "extra": "selector=0x4f", "hits": 0, "call_id": 12, "selector": 0x4F,
        }
        callbacks._reg = lambda frame, name: frame.tp if name == "tpidr_el1" else 0
        callbacks.progname = lambda _process: "backboardd"
        callbacks.STALL_SELECTOR = 0x4F
        with tempfile.TemporaryDirectory() as root:
            callbacks.EVENT_DIR = root
            pending = os.path.join(root, "pending.12.json")
            with open(pending, "w") as stream:
                stream.write("{}\n")
            for _ in range(20):
                callbacks.on_return(Frame(0x222, thread), Location(), {})
            self.assertIn(9, callbacks.RET_CONFIG)
            self.assertTrue(os.path.exists(pending))
            callbacks.on_return(Frame(0x111, thread), Location(), {})
            self.assertNotIn(9, callbacks.RET_CONFIG)
            self.assertFalse(os.path.exists(pending))
            self.assertTrue(os.path.exists(os.path.join(root, "returned.12.json")))
            self.assertEqual(target.deleted, [9])


if __name__ == "__main__":
    unittest.main()
