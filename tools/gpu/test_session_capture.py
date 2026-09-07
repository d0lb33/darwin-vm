"""Fresh snapshot identity and paused copying, independent of pixel hash changes."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from session_cli import Session


class CaptureTests(unittest.TestCase):
    def test_identical_pixels_are_fresh_and_copied_before_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session.__new__(Session)
            session.out = Path(directory)/'run'; session.out.mkdir()
            session.captures = Path(directory)/'captures'; session.captures.mkdir()
            session.witnesses = set()
            session.peer = SimpleNamespace(status=lambda:{}, guest_status=lambda:{},
                                           current=lambda:None, ram=b'ram')
            session.display = SimpleNamespace(presentations=1, completions=1)
            snapshot = 0
            fresh = True

            def command(command):
                nonlocal snapshot
                if command == 'stop':
                    if fresh:
                        snapshot += 1
                        (session.out/'last-scanout.json').write_text(json.dumps(dict(version=1,snapshot=snapshot,ok=True)))
                    for name in ('a408','rgha','bgra'):
                        (session.out/('last-scanout.'+name)).write_bytes(b'identical pixels')
                elif command.startswith('screendump'):
                    Path(command.split('"')[1]).write_bytes(b'console')
                elif command == 'cont':
                    (session.out/'last-scanout.rgha').write_bytes(b'changed after resume')
                return ''

            with patch('session_cli.verifier_interpreter',return_value='python3'), \
                 patch('session_cli.HMP',return_value=SimpleNamespace(command=command)), \
                 patch('session_cli.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')) as verify:
                first = session.capture('first')
                second = session.capture('second')
                self.assertEqual(first['retained_source_sha256'],second['retained_source_sha256'])
                self.assertEqual(second['snapshot']['snapshot'],2)
                self.assertEqual(verify.call_count,2)
                self.assertEqual((session.captures/'second/last-scanout.rgha').read_bytes(),b'identical pixels')
                fresh = False
                third = session.capture('third')
                self.assertTrue(third['scanout']['stale_retained_source'])
                self.assertFalse(third['ok'])
                self.assertEqual(verify.call_count,2)


if __name__ == '__main__':
    unittest.main()
