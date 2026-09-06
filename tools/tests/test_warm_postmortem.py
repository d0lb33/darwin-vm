import importlib.util
from pathlib import Path
import tempfile
import struct
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('warm_postmortem',
    Path(__file__).resolve().parents[1] / 're/warm_boot_postmortem.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FrozenMonitor:
    def __init__(self, privileged=True):
        self.cpu = 0
        self.privileged = privileged
        self.commands = []

    def command(self, command):
        self.commands.append(command)
        if command == 'info status':
            return 'VM status: paused'
        if command == 'info cpus':
            return '* CPU #0: thread_id=1\n  CPU #1: thread_id=1'
        if command.startswith('cpu '):
            self.cpu = int(command.split()[1])
            return ''
        if command == 'info registers':
            return 'PSTATE= EL2t' if self.cpu and self.privileged else 'PSTATE= EL0t'
        raise AssertionError(command)


class WarmPostmortemTests(unittest.TestCase):
    def test_scan_includes_final_process_with_null_next_link(self):
        raw = bytearray(0x800)
        struct.pack_into('<Q', raw, 8, 0xffffffe000001000)
        struct.pack_into('<Q', raw, 0x790, 0xffffffe000002000)
        raw[0x55c:0x566] = b'dvm-input\0'
        memory = module.Memory.__new__(module.Memory)
        memory.maps = [(0x10000000000, raw)]
        self.assertEqual([pa for pa, _ in memory.candidates('dvm-input')],
                         [0x10000000000])

    def test_selects_privileged_cpu_without_running_guest(self):
        monitor = FrozenMonitor()
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'HMP', return_value=monitor):
            module.Memory(Path('monitor.sock'), Path(directory))
        self.assertEqual(monitor.cpu, 1)
        self.assertNotIn('cont', monitor.commands)

    def test_refuses_kernel_translation_when_every_cpu_is_in_userspace(self):
        monitor = FrozenMonitor(privileged=False)
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'HMP', return_value=monitor):
            with self.assertRaisesRegex(RuntimeError, 'no frozen privileged CPU'):
                module.Memory(Path('monitor.sock'), Path(directory))


if __name__ == '__main__':
    unittest.main()
