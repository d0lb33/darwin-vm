"""Regression: an append to a live UART file must not manufacture an overflow."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gpu'))
from driver_runner_peer import capture_uart_interval


class UARTIntervalTests(unittest.TestCase):
    def test_append_after_first_read_is_outside_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'serial.log';path.write_bytes(b'old\njob\n')
            real_open=Path.open;calls=[]
            class AppendingReader(io.BytesIO):
                def read(self,n=-1):
                    calls.append(n);value=super().read(n)
                    with real_open(path,'ab') as w:w.write(b'next\n')
                    return value
            with patch.object(Path,'open',return_value=AppendingReader(b'old\njob\n')):
                data,interval=capture_uart_interval(path,4)
            self.assertEqual(data,b'job\n')
            self.assertEqual(interval,dict(start=4,end=8,bytes=4))
            self.assertEqual(calls,[4])
            self.assertEqual(path.read_bytes(),b'old\njob\nnext\n')

    def test_real_overflow_and_truncation_still_fail(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'serial.log';path.write_bytes(b'12345')
            with self.assertRaises(ValueError):capture_uart_interval(path,0,limit=4)
            with self.assertRaises(ValueError):capture_uart_interval(path,6)
            with patch.object(Path,'open',return_value=io.BytesIO(b'1')):
                with self.assertRaises(ValueError):capture_uart_interval(path,0)


if __name__=='__main__':unittest.main()
