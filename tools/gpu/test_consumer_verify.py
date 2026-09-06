"""Acceptance must require matching guest audit, GPU draws and final pixels."""
import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from consumer_verify import verify
from surface_peer import AIR_SHA


class ConsumerAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.lines = [
            'GPU_LOAD_CA_VERIFIED width=64 height=64 passes=1 draws=1 bad_pixels=0',
            'GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0',
        ]
        self.records = [
            dict(seq=1, op='library', request=dict(sha256=AIR_SHA), reply=dict(ok=True)),
            dict(seq=2, op='renderSubmit', request=dict(commands=[dict(target=1)]),
                 reply=dict(ok=True, status=4, passes=1, draws=1)),
            dict(seq=3, op='read', request=dict(texture=1),
                 reply=dict(ok=True, data=base64.b64encode(bytes([0, 0, 255, 255])*4096).decode())),
            dict(seq=4, op='stats', request={}, reply=dict(ok=True, live=dict(objects=0))),
        ]

    def check(self):
        raw = bytearray(16*1024*1024)
        struct.pack_into('<Q', raw, 0x180, len(self.lines))
        for i, line in enumerate(self.lines):
            data = line.encode()
            struct.pack_into('<QII', raw, 0x1000+i*512, i+1, len(data), zlib.crc32(data))
            raw[0x1010+i*512:0x1010+i*512+len(data)] = data
        (self.out/'shared-ram.bin').write_bytes(raw)
        return verify(self.out, [dict(source='shared-ram-audit', line=x) for x in self.lines], self.records)

    def test_matching_evidence_passes(self):
        self.assertTrue(self.check()['verified'])

    def test_clear_only_cannot_pass_as_draw(self):
        self.records[1]['reply']['draws'] = 0
        with self.assertRaisesRegex(ValueError, 'counts differ'):
            self.check()

    def test_zero_draw_guest_witness_rejected(self):
        self.lines[0] = self.lines[0].replace('draws=1', 'draws=0')
        self.records[1]['reply']['draws'] = 0
        with self.assertRaisesRegex(ValueError, 'pixel oracle'):
            self.check()

    def test_incorrect_pixel_rejected(self):
        pixels = bytearray(bytes([0, 0, 255, 255])*4096)
        pixels[-1] = 0
        self.records[2]['reply']['data'] = base64.b64encode(pixels).decode()
        with self.assertRaisesRegex(ValueError, 'red layer oracle'):
            self.check()

    def test_read_before_render_rejected(self):
        self.records[2]['seq'] = 1
        with self.assertRaisesRegex(ValueError, 'final-only'):
            self.check()


if __name__ == '__main__':
    unittest.main()
