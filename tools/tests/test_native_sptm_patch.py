"""Check instruction adaptation without modifying matching data words."""
import importlib.util
from pathlib import Path
import struct
import unittest


spec = importlib.util.spec_from_file_location(
    'native_sptm_patch', Path(__file__).resolve().parents[1] / 'perf/native_sptm_patch.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)


class NativeSptmPatchTests(unittest.TestCase):
    def test_guarded_immediates_and_data_separation(self):
        # One segment containing identical instruction and non-instruction
        # sections. Unsupported bit-4 forms must remain visible to the native
        # executable-page validator, never silently acquire supported semantics.
        words = [0x00201420, 0x00201421, 0x0020142f, 0x00201430,
                 0x0020143f, 0x00201400, 0x00201421, 0xd503201f]
        payload = struct.pack('<8I', *words)
        original = bytearray(0x300)
        struct.pack_into('<8I', original, 0, 0xfeedfacf, 0x100000c, 0, 2,
                         1, 72 + 160, 0, 0)
        struct.pack_into('<II', original, 32, 0x19, 72 + 160)
        struct.pack_into('<I', original, 32 + 64, 2)
        for index, offset in enumerate((0x200, 0x240)):
            section = 32 + 72 + index * 80
            struct.pack_into('<QQI', original, section + 32,
                             0x100000000 + offset, len(payload), offset)
            struct.pack_into('<I', original, section + 64,
                             0x80000400 if index == 0 else 0)
            original[offset:offset + len(payload)] = payload
        before = bytes(original)
        adapted, records = patch.rewrite(original, virtual_el2=True)
        self.assertEqual(bytes(original), before)
        self.assertEqual([r['original'] for r in records],
                         [hex(words[i]) for i in (0, 1, 2, 5, 6)])
        self.assertEqual(records[1]['index'], records[4]['index'])
        expected = bytearray(before)
        for record in records:
            word = int(record['replacement'], 16)
            self.assertEqual(word & 0xffe0001f, 0xd4000003)
            self.assertEqual((word >> 5) & 0xffff, 0xe000 + record['index'])
            struct.pack_into('<I', expected, int(record['offset'], 16), word)
        self.assertEqual(adapted, expected)


if __name__ == '__main__':
    unittest.main()
