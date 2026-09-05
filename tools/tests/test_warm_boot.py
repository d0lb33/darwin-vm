import importlib.util
from pathlib import Path
import plistlib
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from warm_boot_probe import boot_command

spec = importlib.util.spec_from_file_location('cache_service', TOOLS/'input/cache_service.py')
cache_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache_service)


class WarmBootTests(unittest.TestCase):
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
