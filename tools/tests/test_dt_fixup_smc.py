"""dt_fixup -enable smc: the SMC's RTBuddy nub keeps iBoot's real firmware
region and gains the pre-loaded / no-firmware-service pair only when the SMC
model is enabled.

Synthetic tree shaped like /arm-io/smc/iop-smc-nub and /arm-io/ans/iop-ans-nub
on iPhone17,3 (region-base 0x30de00000 and iBoot's 0x0 placeholder); needs
neither the IPSW nor /tmp/dvm.
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
    smc_nub = node('iop-smc-nub', compatible='iop-nub,rtbuddy-v2',
                   **{'region-base': 'u64:0x30de00000', 'region-size': 'u64:0x100000',
                      'pre-loaded': 'u32:0x0'})
    smc = node('smc', compatible='iop,ascwrap-v6')
    smc.children = [smc_nub]
    ans_nub = node('iop-ans-nub', compatible='iop-nub,rtbuddy-v2',
                   **{'region-base': 'u64:0x0', 'region-size': 'u64:0x0'})
    ans = node('ans', compatible='iop,ascwrap-v6')
    ans.children = [ans_nub]
    arm_io = node('arm-io')
    arm_io.children = [smc, ans]
    root = node('device-tree')
    root.children = [arm_io]
    return root


class SMCFeature(unittest.TestCase):
    def setUp(self):
        dtf.KEEP_COMPAT_PATHS = set()

    def test_feature_lists_the_smc_node(self):
        self.assertEqual(dtf.EMULATED_FEATURES['smc'], ['arm-io/smc'])

    def test_enabled_smc_nub_keeps_region_and_is_pre_loaded(self):
        dtf.KEEP_COMPAT_PATHS.update(dtf.EMULATED_FEATURES['smc'])
        root = tree()
        dtf.fixup_iops(root)
        nub = root['arm-io']['smc']['iop-smc-nub']
        self.assertEqual(nub.props['region-base'], 'u64:0x30de00000')
        self.assertEqual(nub.props['region-size'], 'u64:0x100000')
        self.assertEqual(nub.props['pre-loaded'], 'u32:1')
        self.assertEqual(nub.props['no-firmware-service'], '<NULL>')
        self.assertEqual(root['arm-io']['smc'].props['ignore-gating'], '<NULL>')

    def test_disabled_smc_nub_is_left_alone(self):
        root = tree()
        dtf.fixup_iops(root)
        nub = root['arm-io']['smc']['iop-smc-nub']
        self.assertEqual(nub.props['region-base'], 'u64:0x30de00000')
        self.assertEqual(nub.props['pre-loaded'], 'u32:0x0')
        self.assertNotIn('no-firmware-service', nub.props)

    def test_placeholder_region_still_gets_an_invented_slot(self):
        dtf.KEEP_COMPAT_PATHS.update(dtf.EMULATED_FEATURES['smc'])
        root = tree()
        dtf.fixup_iops(root)
        nub = root['arm-io']['ans']['iop-ans-nub']
        self.assertEqual(nub.props['pre-loaded'], 'u32:1')
        self.assertNotEqual(nub.props['region-base'], 'u64:0x0')
        self.assertNotEqual(nub.props['region-base'], 'u64:0x30de00000')


if __name__ == '__main__':
    unittest.main()
