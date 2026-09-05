"""Exercise wire parsing and motion coalescing without submitting host HID input."""
from collections import deque
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('relay', HERE/'relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class InputTests(unittest.TestCase):
    def test_motion_coalescing_keeps_release_and_next_press(self):
        queue = deque()
        for down, x in [(True, 1), (True, 9), (True, 2), (False, 3), (True, 4)]:
            relay.enqueue(queue, dict(down=down, x=x, y=100))
        self.assertEqual([(r['down'], r['x']) for r in queue],
                         [(True, 1), (True, 2), (False, 3), (True, 4)])

    def test_native_parser_rejects_invalid_and_truncated_records(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory)/'input-validator'
            subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', str(HERE/'dvm_input.c'),
                            '-o', str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary), '--validate'], input=(
                'DVMINPUT1 1 T 1 100 200\n'
                'DVMINPUT1 1 T 0 100 200\n'
                'DVMINPUT1 2 T 2 100 200\n'
                'DVMINPUT1 3 T 0 32768 200\n'
                'DVMINPUT1 4 H 1 0 0\n'
                'DVMINPUT1 5 H 0 0 0 junk\n'
                'DVMINPUT1 6 S 0 0 0\n'
                'DVMINPUT1 7 S 1 0 0\n'
                'DVMINPUT1 8 R 1 0 32767\n'
                'DVMINPUT1 9 R 0 32768 0\n'
                'DVMINPUT1 6 T 0 100 200'),
                text=True, capture_output=True, check=True)
            self.assertIn('DVM_INPUT_ACK 1 1', result.stderr)
            self.assertIn('DVM_INPUT_ACK 4 1', result.stderr)
            self.assertIn('DVM_INPUT_ACK 6 1', result.stderr)
            self.assertIn('DVM_INPUT_ACK 8 1', result.stderr)
            self.assertEqual(result.stderr.count('DVM_INPUT_ACK'), 4)
            self.assertEqual(result.stderr.count('DVM_INPUT_REJECT'), 7)


if __name__ == '__main__':
    unittest.main()
