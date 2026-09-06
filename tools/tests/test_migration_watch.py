import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('migration_watch',Path(__file__).resolve().parents[1]/'re/migration_watch.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class WatchConditions(unittest.TestCase):
    def test_captured_sep_missing_reply(self):
        self.assertTrue(m.is_sep_no_reply('sep(SEP): scrd tag 4 cmd 0x29 body 61 is unmodelled; no reply'))
    def test_normal_boot_notifications_do_not_stop(self):
        for line in ('asc(ANS2): tracekit host version 1 (no reply awaited)',
                     'dcp: EPIC frame ep 0x20 (not a command, no reply): iface 1',
                     'sep(SEP): scrd v1 cmd 0x28 seq 0x10 replied with 12-byte OOL envelope'):
            self.assertFalse(m.is_sep_no_reply(line))
