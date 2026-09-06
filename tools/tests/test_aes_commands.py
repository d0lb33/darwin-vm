import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'decode_aes_commands', Path(__file__).parents[1] / 're/decode_aes_commands.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class NativeAESCommandTests(unittest.TestCase):
    # APP_AES_COMMANDS1 first native software-key request. Both the key and
    # IV were all zero; decoder intentionally does not emit either payload.
    words = ([0x10910000] + [0] * 8 + [0x20000000] + [0] * 4 +
             [0x50000010, 0x01000100, 0x9c40, 0x11a00,
              0x80000100, 0x60000100, 0x10, 0x88000001])

    def test_native_request_boundaries_and_42_bit_dma(self):
        result = module.decode(self.words)
        self.assertEqual([r['word'] for r in result], [0, 9, 14, 18, 19, 21])
        self.assertEqual([r['command'] for r in result],
                         ['KEY', 'IV', 'DATA', 'FLAG', 'STORE_IV', 'FLAG'])
        self.assertEqual(result[0]['key_bytes'], 32)
        self.assertEqual(result[0]['selector'], 0)
        self.assertTrue(result[0]['encrypt'])
        self.assertEqual(result[0]['mode'], 1)
        self.assertEqual(result[2]['bytes'], 16)
        self.assertEqual(result[2]['source'], '0x10000009c40')
        self.assertEqual(result[2]['destination'], '0x10000011a00')
        self.assertEqual(result[4]['destination'], '0x10000000010')
        self.assertFalse(result[3]['interrupt'])
        self.assertTrue(result[5]['interrupt'])
        self.assertEqual(result[5]['tag'], 1)

    def test_truncation_never_becomes_a_complete_command(self):
        for length in (1, 8, 10, 13, 15, 16, 17, 20):
            with self.subTest(length=length), self.assertRaises(ValueError):
                module.decode(self.words[:length])

    def test_unknown_or_wrapped_commands_are_rejected(self):
        for words in ([0x30000000], [0x10c00000], [0x10200000],
                      [0x100000000], [0x10000000, -1, 0, 0, 0]):
            with self.subTest(words=words), self.assertRaises(ValueError):
                module.decode(words)

    def test_hardware_selector_does_not_consume_next_command(self):
        result = module.decode([0x11910000, 0x88000002])
        self.assertEqual(result[0]['selector'], 1)
        self.assertEqual(result[1]['tag'], 2)
