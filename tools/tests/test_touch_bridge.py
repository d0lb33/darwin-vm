"""Host regressions for fragmented input and stale LLDB frame metadata."""
import importlib.util
import json
import struct
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


def load_bridge():
    spec = importlib.util.spec_from_file_location('touch_test',
        Path(__file__).parents[1] / 're/touch_bridge.py')
    module = importlib.util.module_from_spec(spec)
    fake_lldb = types.ModuleType('lldb')
    fake_lldb.SBError = lambda: Mock(Fail=lambda: False)
    with patch.dict(sys.modules, {'lldb':fake_lldb,
                                 'welcome_abort_callbacks':types.ModuleType('names')}):
        spec.loader.exec_module(module)
    return module


class TouchBridgeTests(unittest.TestCase):
    def test_tap_uses_native_stream_geometry_without_assuming_display_scale(self):
        bridge = load_bridge()
        points = [{'t':0,'x':3835,'y':3269,'down':True},
                  {'t':150,'x':3835,'y':3269,'down':False}]
        frame = Mock()
        with patch.object(bridge,'read',side_effect=[(0x40).to_bytes(4,'little'),
                                                    struct.pack('<dd',1179,2556)]):
            address,args = bridge.gesture_steps(points)[3](frame,{'results':[0x10000]})
        self.assertEqual(address,0x29b20041c)
        self.assertAlmostEqual(args['d0'],138,delta=.02)
        self.assertAlmostEqual(args['d1'],255,delta=.02)

    def test_call_return_uses_raw_registers_when_frame_metadata_is_stale(self):
        bridge = load_bridge()
        values = {'pc':0x1955e84e8, 'sp':0x16fa41f20, 'tpidr_el1':0xffffffeb0223f060}
        def register(name):
            value = values.get(name, 0)
            r = Mock()
            r.IsValid.return_value = True
            r.GetByteSize.return_value = 8
            r.GetValueAsUnsigned.return_value = value
            r.GetData.return_value.ReadRawData.return_value = value.to_bytes(8,'little')
            return r
        frame = Mock()
        frame.FindRegister.side_effect = register
        frame.GetPC.return_value = 0x195614724
        frame.GetSP.return_value = 0xdeadbeef
        with patch.object(bridge, 'advance'):
            bridge.begin(frame, [], 'test')
        self.assertEqual(bridge.STATE['pc'], values['pc'])
        self.assertEqual(bridge.STATE['sp'], values['sp'])

    def test_partial_up_record_does_not_complete_gesture_early(self):
        bridge = load_bridge()
        down = json.dumps({'t':0,'x':100,'y':200,'down':True})+'\n'
        up = json.dumps({'t':100,'x':300,'y':400,'down':False})+'\n'
        bridge.FILE = Mock()
        bridge.FILE.read.side_effect = [down+up[:15], up[15:]]
        bridge.poll()
        self.assertEqual(bridge.QUEUE, [])
        bridge.poll()
        self.assertEqual(len(bridge.QUEUE), 1)
        self.assertEqual([p['down'] for p in bridge.QUEUE[0]], [True,False])
        self.assertEqual(bridge.PARTIAL, '')


if __name__ == '__main__':
    unittest.main()
