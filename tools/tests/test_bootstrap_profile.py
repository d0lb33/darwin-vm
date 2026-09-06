"""Profile boundaries and fail-closed guest installation, without a VM."""
import importlib.util
import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch
from test_prepare_display_policy import make_signature

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bootstrap_profile', ROOT / 'tools/rootfs/bootstrap_profile.py')
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)


class BootstrapProfileTests(unittest.TestCase):
    def test_cache_payload_checks_signature_and_preserves_source(self):
        page = bytearray(bp.PAGE_SIZE)
        page[16:20] = b'old!'
        header = bytearray(bp.PAGE_SIZE)
        header[:4] = b'dyld'
        signature = make_signature([b'\0' * 32, hashlib.sha256(page).digest()])
        struct.pack_into('<QQ', header, 0x28, bp.PAGE_SIZE * 2, len(signature))
        data = bytes(header + page) + signature
        spec = dict(cache_name='dyld_shared_cache_arm64e.13', purpose='fixture',
                    edits=[dict(offset=bp.PAGE_SIZE + 16, before=b'old!'.hex(), after=b'new!'.hex())])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / spec['cache_name']
            source.write_bytes(data)
            regions, cdhash = bp.cache_payload(source, spec)
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(len(cdhash), 40)
            self.assertEqual(regions[1][2][16:20], b'new!')
            corrupt = bytearray(data)
            corrupt[bp.PAGE_SIZE + 100] ^= 1
            source.write_bytes(corrupt)
            with self.assertRaisesRegex(ValueError, 'signed page mismatch'):
                bp.cache_payload(source, spec)

    def test_native_has_no_userspace_or_pv_adapters(self):
        native = bp.profile_config('native')
        self.assertEqual(native['cpus'], 1)
        for key in ('userspace_patches', 'runtime_helpers', 'kernel_adapters'):
            self.assertEqual(native[key], [])
        self.assertEqual(native['env']['DARWIN_RTC_PV'], '0')
        self.assertEqual(native['clock_source'], 'native-spmi-pmu')
        self.assertNotIn('DARWIN_SMP_PV', native['env'])
        self.assertFalse(native['development_activation'])
        self.assertEqual(native['setup_completion'], 'unchanged')

    def test_patched_is_explicit_and_activation_is_independent(self):
        native = bp.profile_config('native', True)
        patched = bp.profile_config('patched')
        self.assertTrue(native['development_activation'])
        self.assertFalse(patched['development_activation'])
        self.assertEqual(patched['runtime_helpers'], ['input', 'power-pv-service'])
        self.assertEqual(patched['env']['DARWIN_RTC_PV'], '0')
        self.assertEqual(patched['kernel_adapters'], ['smp-pv'])
        self.assertEqual(patched['cpus'], 6)
        self.assertNotIn('DARWIN_RTC_PV', bp.DISPLAY_ENV)

    def test_diagnostic_environment_and_resume_cannot_leak(self):
        with patch.dict(os.environ, {'DARWIN_RTC_PV': '1', 'DVM_QEMU_WRAPPER': 'debugger',
                                    'START_AT': 'normal2', 'SEED_ONLY': '1'}):
            clean = bp.clean_env()
        for key in ('DARWIN_RTC_PV', 'DVM_QEMU_WRAPPER', 'START_AT', 'SEED_ONLY'):
            self.assertNotIn(key, clean)

    def test_launchd_export_with_existing_helpers_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'launchd.plist'
            source.write_bytes(plistlib.dumps({'LaunchDaemons': {'custom': {'Label': 'com.apple.dvm-input'}}}))
            with self.assertRaisesRegex(ValueError, 'already contains'):
                bp.original_launchd(source)

    def run_installer(self, patched, stale=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'guest'
            payload = Path(tmp) / 'payload'
            bin_dir = Path(tmp) / 'bin'
            for path in (root / 'usr/lib', payload, bin_dir):
                path.mkdir(parents=True)
            for name in ('libobjc-trampolines.dylib', 'libramrod.dylib'):
                (root / 'usr/lib' / name).write_bytes(b'loose cryptex file')
            (root / 'first').write_bytes(b'original first')
            (root / 'last').write_bytes(b'wrong' if stale else b'original last')
            (payload / 'first-before').write_bytes(b'original first')
            (payload / 'last-before').write_bytes(b'original last')
            (payload / 'first-after').write_bytes(b'changed first')
            for command in ('mount_apfs', 'umount', 'sync', 'chown'):
                file = bin_dir / command
                file.write_text('#!/bin/sh\nexit 0\n')
                file.chmod(0o755)
            script = bp.installer_script(
                [('/first', 'first-before', None, 0), ('/last', 'last-before', None, 0)],
                [('/first', 'first-after', None, 0)], [], patched)
            # Redirect only fixed guest roots to the isolated host fixture.
            script = script.replace('/mnt1', str(root)).replace('/libexec/', str(payload) + '/')
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['sh'], input=script, text=True, capture_output=True, env=env)
            return result, (root / 'first').read_bytes()

    def test_native_verifies_without_applying_available_replacement(self):
        result, data = self.run_installer(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DVM_PROFILE_INSTALLED', result.stdout)
        self.assertEqual(data, b'original first')

    def test_late_preimage_failure_prevents_earlier_write(self):
        result, data = self.run_installer(True, stale=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('DVM_PROFILE_INSTALLED', result.stdout)
        self.assertEqual(data, b'original first')

    def test_patched_install_requires_successful_readback(self):
        result, data = self.run_installer(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DVM_PROFILE_INSTALLED', result.stdout)
        self.assertEqual(data, b'changed first')

    def test_early_boot_alone_is_not_success(self):
        result = bp.verdict('Early boot complete', '', 'patched')
        self.assertFalse(result['storage_pass'])
        self.assertFalse(result['display_observed'])

    def test_patched_requires_new_frame_in_addition_to_storage(self):
        serial = '\n'.join(['BSD root: disk1s1', 'Early boot complete',
            'mount-complete volume Preboot', 'mount-complete volume Hardware',
            'disk1s5 mount-complete volume User', '/dev/disk1s2 on /private/var (protect)',
            'handle_mount:893: disk1s2 is encrypted', 'AppleARMRTC publishing service!',
            'AppleDialogSPMIPMURTC started!'])
        self.assertFalse(bp.verdict(serial, '', 'patched')['validation_pass'])
        self.assertTrue(bp.verdict(serial, 'iomfb: presented 1179x2556 BGRA', 'patched')['validation_pass'])

    def test_shell_entrypoint_exposes_profile_help_without_artifacts(self):
        result = subprocess.run(['bash', str(ROOT / 'tools/rootfs/rebuild_persistent_parent.sh'), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--profile {native,patched}', result.stdout)

    def test_panic_invalidates_otherwise_valid_storage_boot(self):
        serial = '\n'.join(['BSD root: disk1s1', 'Early boot complete',
            'mount-complete volume Preboot', 'mount-complete volume Hardware',
            'disk1s5 mount-complete volume User', '/dev/disk1s2 on /private/var (protect)',
            'handle_mount:893: disk1s2 is encrypted', 'AppleARMRTC publishing service!',
            'AppleDialogSPMIPMURTC started!'])
        self.assertTrue(bp.verdict(serial, '', 'native')['storage_pass'])
        self.assertFalse(bp.verdict(serial, 'panic(cpu', 'native')['storage_pass'])


if __name__ == '__main__':
    unittest.main()
