"""dt_fixup -enable spmi: keep the SPMI controller and PMU bindings, drop the
placeholder RTC, and strip the other SPMI children.

Runs on a synthetic tree shaped like /arm-io/nub-spmi0 on iPhone17,3 so it
needs neither the IPSW nor /tmp/dvm.
"""
import importlib.util
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location('dt_fixup', os.path.join(REPO, 'dt_fixup.py'))
dtf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtf)


def node(name, **props):
    n = dtf.ADTNode()
    n.props['name'] = name
    n.props.update(props)
    return n


def tree():
    pmu = node('pmu-main', compatible=b'pmu,spmi\x00pmu,baku\x00\x00\x00',
               reg=bytes.fromhex('0e0000000300000000000000040000000000000000000000'))
    pmu.props['info-rtc'] = 'u32:0xf802'
    btm = node('btm', compatible='btm,phone', device_type='btm')
    spmi = node('nub-spmi0', compatible='aapl,spmi', gen='u32:0x3')
    spmi.children = [pmu, btm]
    uart = node('uart0', compatible='uart-1,samsung')
    arm_io = node('arm-io')
    arm_io.children = [spmi, uart]
    root = node('device-tree')
    root.children = [arm_io]
    return root


class SPMIFeature(unittest.TestCase):
    def setUp(self):
        dtf.KEEP_COMPAT_PATHS = set()

    def test_feature_lists_controller_only(self):
        self.assertEqual(dtf.EMULATED_FEATURES['spmi'], ['arm-io/nub-spmi0'])

    def test_disabled_strips_everything(self):
        root = tree()
        dtf.del_compat(root)
        dtf.fixup_spmi(root)
        spmi = root['arm-io']['nub-spmi0']
        self.assertNotIn('compatible', spmi.props)
        for c in spmi.children:
            self.assertNotIn('compatible', c.props, c.props['name'])
        # The samsung UART is in SUPPORTED_DRIVERS and stays.
        self.assertEqual(root['arm-io']['uart0'].props['compatible'], 'uart-1,samsung')

    def test_enabled_keeps_controller_and_pmu_only(self):
        dtf.KEEP_COMPAT_PATHS.update(dtf.EMULATED_FEATURES['spmi'])
        root = tree()
        dtf.del_compat(root)
        dtf.fixup_spmi(root)
        spmi = root['arm-io']['nub-spmi0']
        self.assertEqual(spmi.props['compatible'], 'aapl,spmi')
        self.assertEqual(spmi['pmu-main'].props['compatible'], b'pmu,spmi\x00pmu,baku\x00\x00\x00')
        self.assertNotIn('compatible', spmi['btm'].props)
        self.assertEqual(spmi['btm'].props['device_type'], 'btm')

    def test_enabled_without_node_is_harmless(self):
        dtf.KEEP_COMPAT_PATHS.update(dtf.EMULATED_FEATURES['spmi'])
        root = tree()
        root['arm-io'].remove_child('nub-spmi0')
        dtf.fixup_spmi(root)
        self.assertEqual([c.props['name'] for c in root['arm-io'].children], ['uart0'])


if __name__ == '__main__':
    unittest.main()
