import importlib.util
from pathlib import Path
import plistlib
import json
import tempfile
from unittest.mock import patch
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from warm_boot_probe import boot_command
import warm_boot_probe
import boot_native_smc

spec = importlib.util.spec_from_file_location('cache_service', TOOLS/'input/cache_service.py')
cache_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_service)


class WarmBootTests(unittest.TestCase):
    def test_mismatched_disk_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'disk': {
                'path': str(Path(directory) / 'wrong.qcow2'),
                'backing_chain': [{'path': str(Path(directory) / 'verified.qcow2')}],
            }}))
            with patch.object(sys, 'argv', ['warm_boot_probe', str(manifest),
                                           '--tag', 'REJECT_WRONG_DISK']), \
                    patch.object(warm_boot_probe.subprocess, 'run') as run, \
                    patch.object(warm_boot_probe.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(ValueError, 'selected disk'):
                    warm_boot_probe.main()
                run.assert_not_called()
                popen.assert_not_called()

    def test_interactive_boot_rejects_mismatched_disk_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'manifest.json'
            manifest.write_text(json.dumps({'battery_source': 'emulated-smc', 'disk': {
                'path': str(Path(directory) / 'wrong.qcow2'),
                'backing_chain': [{'path': str(Path(directory) / 'verified.qcow2')}],
            }}))
            with patch.object(sys, 'argv', ['boot_native_smc', '--manifest', str(manifest)]), \
                    patch.object(boot_native_smc.subprocess, 'run') as run, \
                    patch.object(boot_native_smc.subprocess, 'Popen') as popen, \
                    patch.object(sys, 'stderr'):
                with self.assertRaises(SystemExit) as error:
                    boot_native_smc.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()
                popen.assert_not_called()

    def test_disk_boot_cannot_inherit_ram_or_debugger_endpoints(self):
        argv = ['/a path/qemu', '-M', 'darwin', '-smp', '6', '-S', '-s',
                '-drive', 'if=none,id=ans,file=/immutable/disk.qcow2',
                '-incoming', 'file:/old/ram', '-gdb', 'tcp::1234',
                '-chardev', 'socket,path=/old/uart', '-serial', 'chardev:old']
        result = boot_command(argv, Path('/tmp/new'))
        self.assertEqual(result[0], '/a path/qemu')
        self.assertEqual(result[result.index('-smp')+1], '6')
        for removed in ('-incoming', '-gdb', '-S', '-s', 'file:/old/ram', 'chardev:old'):
            self.assertNotIn(removed, result)
        self.assertIn('if=none,id=ans,file=/tmp/new/disk.qcow2,format=qcow2', result)

    def test_registration_preserves_other_services_and_rejects_conflicts(self):
        service = plistlib.loads((TOOLS/'input/com.apple.dvm-input.plist').read_bytes())
        cache = {'LaunchDaemons': {'/existing': {'Label': 'existing'}},
                 'VersionNumber': 4, 'AppExtensions': {'unchanged': b'payload'}}
        updated = cache_service.register(cache, service)
        self.assertEqual(cache['LaunchDaemons'], {'/existing': {'Label': 'existing'}})
        self.assertEqual(updated['AppExtensions'], cache['AppExtensions'])
        self.assertEqual(updated['VersionNumber'], 4)
        self.assertEqual(cache_service.register(updated, service), updated)
        conflict = dict(cache, LaunchDaemons={'/wrong-path': service})
        with self.assertRaisesRegex(ValueError, 'duplicate label'):
            cache_service.register(conflict, service)


if __name__ == '__main__':
    unittest.main()
