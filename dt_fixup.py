#!/usr/bin/env python3
import math
import struct
import argparse

SUPPORTED_DRIVERS=[b'AppleARM', b'aic', b'arm-io', b'uart-1,samsung']
FREQUENCY = "u32:0x100000"
IBOOT_NAME="qemu-sptm"
DRAM_SIZE=0x200000000   # 8G; override with -dram

AMCC_BANK_STRIDE = 0x100
AMCC_LOWER_LIMIT_REG = 0x10
AMCC_UPPER_LIMIT_REG = 0x20

class ADTNode:
  def __init__(self):
    self.props = {}
    self.children = []

  def __getitem__(self, key):
    for c in self.children:
      if c.props['name'] == key:
        return c
    raise ValueError(f"key {key} not present in node {self.props['name']}")

  def __contains__(self, key):
    return any(c.props['name'] == key for c in self.children)

  def remove_child(self, child_name):
    for c in self.children:
      if c.props['name'] == child_name:
        self.children.remove(c)
        return
    raise ValueError(f"child {child_name} not found in {self.props['name']}")

def round_up_to_multiple_of_4(i):
  return 4 * math.ceil(i/4)

def decode_null_terminated_string(s):
  return s.decode("utf8").split('\x00')[0]

def is_probably_a_string(s):
  if len(s) == 0:
    return False
  try:
    x=s.split(b'\x00')
    for e in x[1:]:
      if e != b'':
        return False
  except:
    return False

  try:
    d=s.decode("ascii").split('\x00')[0]
  except:
    return False

  if len(d) < 3:
    return False

  return all(i.isprintable() for i in d)

def decode_prop(name,prop):
  if name == "name" or is_probably_a_string(prop):
    return decode_null_terminated_string(prop)
  if len(prop) == 0:
    return "<NULL>"
  if len(prop) == 4:
    return f'u32:{hex(struct.unpack("<I",prop)[0])}'
  if len(prop) == 8:
    return f'u64:{hex(struct.unpack("<Q",prop)[0])}'
  return prop

def decode_node(dt,node):
  if len(dt) < 8:
    raise ValueError('dtree is too small')

  our_size = 0
  n_props, n_children = struct.unpack("<II", dt[0:8])
  dt = dt[8:]
  our_size += 8

  for _ in range(n_props):
    prop_name = decode_null_terminated_string(dt[0:32])
    prop_len = struct.unpack("<I", dt[32:36])[0]
    prop_len &= ~0x80000000
    prop_len = round_up_to_multiple_of_4(prop_len)
    prop = dt[36:36+prop_len]
    node.props[prop_name] = decode_prop(prop_name,prop)
    dt = dt[36+prop_len:]
    our_size += 36 + prop_len

  for _ in range(n_children):
    new_child = ADTNode()
    child_sz = decode_node(dt, new_child)
    node.children.append(new_child)
    our_size += child_sz
    dt = dt[child_sz:]

  return our_size

# Node paths (relative to the root) whose "compatible" property we keep so the
# matching XNU drivers bind to hardware that qemu-sptm emulates. Selected with
# -enable <feature>. Children of a listed node are kept too.
EMULATED_FEATURES = {
  # DCP display coprocessor: ASC mailbox + RTKit, its DART, and the display pipe nodes
  'dcp': ['arm-io/dcp', 'arm-io/dart-dcp', 'arm-io/disp0', 'arm-io/dcp0-expert', 'arm-io/dart-disp0', 'arm-io/dart-dispgrt'],
  # ANS storage coprocessor. Unlike the DCP its nub is "power-managed", so
  # RTBuddy brings it up itself rather than waiting for a client to request
  # power. Its DMA goes through the SART address filter, not a DART.
  'ans': ['arm-io/ans', 'arm-io/sart-ans'],
  # SMC. Worth enabling even though we do not need its sensors: both
  # IONVMeFamily and AppleFirmwareKit declare com.apple.driver.AppleSMC as a
  # dependency, and a kext whose dependency never starts does not register its
  # own OSMetaClasses (which is what produces 'Couldn't alloc class
  # "AFKFirmwareService"'). Its firmware-name is t8140smc.
  'smc': ['arm-io/smc'],
}
KEEP_COMPAT_PATHS = set()

def del_compat(d, path=''):
  for c in d.children:
    del_compat(c, path + '/' + c.props['name'] if path or d.props.get('name') != 'device-tree' else c.props['name'])

  rel = path.lstrip('/')
  if any(rel == k or rel.startswith(k + '/') for k in KEEP_COMPAT_PATHS):
    return

  if 'compatible' in d.props:
    # Device tree compatible fields will be str or bytes (if the underlying
    # field is a stringlist, which we parse as bytes). To support either
    # single-str compatible fields, or stringlist fields, encode whatever we
    # got into a bytes, and then check whether any of our supported drivers are
    # a substring of the string or stringlist.
    compat=d.props['compatible']

    if type(compat) == str:
      compat = compat.encode('utf8')

    if not any(x in compat for x in SUPPORTED_DRIVERS):
      del d.props['compatible']

def drop_exclave_routes(d):
  # An IOP nub's "routes" property points at a secure-rtbuddy-proxy node, i.e.
  # the exclave (secure world) side of that coprocessor's mailbox. We don't
  # emulate exclaves, and while the route is present RTBuddy brings the IOP up
  # but the AP-side drivers above it never bind. Dropping it lets the normal
  # driver stack attach (eg. AppleDCPExpert on the DCP).
  for c in d['arm-io'].children:
    if 'compatible' not in c.props:
      continue
    for nub in c.children:
      if 'routes' in nub.props:
        del nub.props['routes']

def fixup_darts(d):
  # SPTM bootstraps every DART whose node still has a compatible, and expects
  # iBoot to have added a unique "dart-id" to each of them (it panics with
  # "error -1 getting dart-id" otherwise). Number the kept DARTs.
  dart_id = 0
  for c in d['arm-io'].children:
    if c.props.get('device_type') == 'dart' and 'compatible' in c.props:
      c.props['dart-id'] = f"u32:{dart_id}"
      dart_id += 1

def fixup_aic(aic):
  if 'compatible' not in aic.props:
    raise ValueError("aic doesn't have a 'compatible' field")

  compat=aic.props['compatible']
  if type(compat) == str:
    compat = compat.encode('utf8')

  if any(i in compat for i in [b'aic,2', b'aic,3']):
    aic.props['aic-iack-offset'] = "u64:0x1000"

def fixup_sptm(d):
  m = d['chosen']['memory-map']

  for i in [
    'TXM-ro', 'TXM-rx', 'TXM-bx', 'TXM-rw', 'TXM-le', 'TXM-entry', 'TXM-virt',
    'TrustCache',
    'BootKC-rx', 'BootKC-bx', 'BootKC-ro', 'BootKC-rs', 'BootKC-rw', 'BootKC-le', 'BootKC-virt', 'BootKC-entry',
    'DeviceTree',
    'SPTM-ro', 'SPTM-rm', 'SPTM-rx', 'SPTM-rw', 'SPTM-le', 'SPTM-entry', 'SPTM-virt',
    'BootArgs', 'slide',
    'CL4-rx', 'CL4-ro', 'CL4-rw', 'CL4-le', 'CL4-dummypage', 'CL4-entry', 'CL4-virt', 'CL4-dummypage',
    'RAMDisk',
  ]:
    # iBoot sets uninitialized regions to (-1, -1)
    m.props[i] = struct.pack("<QQ", 0xffffffffffffffff, 0xffffffffffffffff)

  # slide is always zero
  m.props['slide'] = struct.pack("<QQ", 0,0)

  # Skip the iommu init stuff (gfx-shared-region-base & friends)
  d['arm-io'].remove_child('sgx')

def get_platform_name(d):
  compat = d['arm-io'].props['compatible']
  if type(compat) == bytes:
    compat = compat.decode("utf8")

  return compat.split(",")[1]

def get_soc_gen(d):
  soc_gen_name = d['arm-io'].props['soc-generation']
  if type(soc_gen_name) != str:
    return 0

  if soc_gen_name[0] != 'H':
    return 0

  return int(soc_gen_name[1:])

def fixup(d, nvram_file):
  # This is a one-shot transform on a device tree straight out of an IPSW. Run
  # twice, it fails deep inside with a confusing error (the second pass looks
  # for nodes the first pass removed), so say so up front.
  if d['chosen'].props.get('firmware-version') == IBOOT_NAME:
    raise SystemExit(
      "error: this device tree has already been patched by dt_fixup "
      f"(chosen/firmware-version is already '{IBOOT_NAME}').\n"
      "       Pass the original tree from the IPSW, eg:\n"
      "         ipsw img4 im4p extract $(find ipsw_db -iname 'DeviceTree*' | head -1) -o dtree_raw")

  d.props['platform-name'] = get_platform_name(d)
  soc_gen = get_soc_gen(d)

  if soc_gen <= 14:
    d['chosen'].props['dram-base'] = "u64:0x800000000"
  else:
    d['chosen'].props['dram-base'] = "u64:0x10000000000"
  # XNU takes the DRAM size from the device tree, not from qemu's -m, so the
  # two have to be raised together. Booting a full OS filesystem from a ramdisk
  # needs far more than the 8G default.
  d['chosen'].props['dram-size'] = f"u64:{DRAM_SIZE}"

  d['chosen'].props['firmware-version'] = IBOOT_NAME
  d['chosen'].props['system-firmware-version'] = IBOOT_NAME
  d['cpus']['cpu0'].props['state'] = "running"
  d['chosen'].props['random-seed'] = b'A' * len(d['chosen'].props['random-seed'])
  d['chosen'].props['kernel-ctrr-to-be-enabled'] = "u32:0"
  d['defaults'].props['serial-device'] = d['arm-io']['uart0'].props['AAPL,phandle']
  d['cpus']['cpu0'].props['memory-frequency'] = FREQUENCY
  d['cpus']['cpu0'].props['peripheral-frequency'] = FREQUENCY
  d['cpus']['cpu0'].props['fixed-frequency'] = FREQUENCY
  d['cpus']['cpu0'].props['clock-frequency'] = FREQUENCY
  d['cpus']['cpu0'].props['timebase-frequency'] = FREQUENCY
  d['chosen'].props['nvram-bank-count'] = "u32:1"
  d['chosen'].props['nvram-current-bank'] = "u32:1"
  d['chosen'].props['nvram-proxy-data'] = nvram_file.read()
  d['chosen'].props['nvram-total-size'] = f"u32:{len(d['chosen'].props['nvram-proxy-data'])}"
  d['chosen'].props['nvram-bank-size']  = f"u32:{len(d['chosen'].props['nvram-proxy-data'])}"

  if 'InvalidateHmac' in d['arm-io']['sep']['iop-sep-nub']:
    d['arm-io']['sep']['iop-sep-nub']['InvalidateHmac'].props['config'] = "u32:1"
    d['arm-io']['sep']['iop-sep-nub']['InvalidateHmac'].props['sio-hmac1-offset'] = "u64:0"
    d['arm-io']['sep']['iop-sep-nub']['InvalidateHmac'].props['sio-hmac1-disable-mask'] = "u64:0xffffffffffffffff"

  d['arm-io'].remove_child('dockchannel-uart')

  # disable RTC timeout in IOKitInitializeTime
  # IOKitInitializeTime waits for the IORTC resource which never appears since
  # we don't load a driver for it. AppleARMPE::start checks for a "no-rtc" key
  # in the device tree root, and if so, sets a flag. Later,
  # AppleARMPE::platformAdjustService checks if that flag is set, and if so,
  # checks for an "rtc" node in the dtree root by calling IODTMatchNubWithKeys.
  # If it finds the rtc nub, it calls IOService::publishResource to publish a
  # fake RTC, allowing us to skip the 30 second timeout in IOKitInitializeTime.
  d.props['no-rtc'] = "<NULL>"
  rtc_node = ADTNode()
  rtc_node.props['name'] = 'rtc'
  rtc_node.props['__placeholder_val'] = "<NULL>"
  d.children.append(rtc_node)

  # This fixes panic(cpu 0 caller 0xfffffff008b8e7b8): "AMFI: No PMGR?\n" @ConfigurationSettings.cpp:388
  d['defaults'].props['vmm-present'] = "u32:1"

  amcc=d['chosen']['lock-regs']['amcc']
  amcc.props['aperture-count'] = "u32:1"
  amcc.props['aperture-size'] = "u32:0x4000"
  amcc.props['plane-count'] = "u32:1"
  amcc.props['plane-stride'] = "u32:0"
  amcc.props['plane-size'] = amcc.props['aperture-size']
  amcc.props['aperture-phys-addr'] = "u64:0x220000000"
  amcc.props['cache-status-reg-offset'] = "u32:0"
  amcc.props['cache-status-reg-mask'] = "u32:0"
  amcc.props['cache-status-reg-value'] = "u32:0"

  for (i,ctrr_v) in enumerate(['a', 'b', 'c', 'd']):
    ctrr=amcc[f'amcc-ctrr-{ctrr_v}']

    # this shifts lower/upper regs right by this many bytes
    # we assume it's zero in apple_amcc.c, so keep this zero
    ctrr.props['page-size-shift'] = "u32:0"

    ctrr.props['lower-limit-reg-offset'] = f"u32:{(AMCC_BANK_STRIDE*i)+AMCC_LOWER_LIMIT_REG}"
    ctrr.props['upper-limit-reg-offset'] = f"u32:{(AMCC_BANK_STRIDE*i)+AMCC_UPPER_LIMIT_REG}"
    ctrr.props['upper-limit-reg-mask'] = "u32:0xFFFFFFFF"
    ctrr.props['lower-limit-reg-mask'] = "u32:0xFFFFFFFF"
    ctrr.props['lock-reg-offset'] = "u32:0"
    ctrr.props['lock-reg-mask'] = "u32:0"
    ctrr.props['lock-reg-value'] = "u32:0"
    ctrr.props['enable-reg-offset'] = "u32:0"
    ctrr.props['enable-reg-mask'] = "u32:1"
    ctrr.props['enable-reg-value'] = "u32:1"
    ctrr.props['write-disable-reg-offset'] = "u32:0"
    ctrr.props['write-disable-reg-mask'] = "u32:1"
    ctrr.props['write-disable-reg-value'] = "u32:1"

  del_compat(d)
  drop_exclave_routes(d)
  fixup_darts(d)
  fixup_aic(d['arm-io']['aic'])
  fixup_sptm(d)
  del d.props['secure-root-prefix']

# returns (length, value)
def parse_prop_entry(v) -> tuple[int, bytes]:
  if type(v) == bytes:
    return len(v), v.ljust(round_up_to_multiple_of_4(len(v)),b'\x00')

  if type(v) == int:
    raise TypeError("int type not specified")
  if type(v) == dict:
    raise TypeError("dicts aren't allowed as properties")
  if type(v) != str:
    raise ValueError(f"not a str ({type(v)})")

  if v.startswith("u32:"):
    return 4, struct.pack("<I",int(v[4:],0))
  elif v.startswith("u64:"):
    return 8, struct.pack("<Q",int(v[4:],0))
  elif v == "<NULL>":
    return 0, b""
  else:
    # add 1 for null terminator, return strlen (incl. null byte) but pad to multiple of 4
    strlen = len(v) + 1
    prop_len = round_up_to_multiple_of_4(strlen)
    rv1, rv2 = strlen, bytes(v,'utf8').ljust(prop_len,b'\x00')
    if rv2[-1] != 0:
      raise ValueError(f"Property {rv2} isn't NULL terminated ({rv2[-1]})")
    return rv1, rv2

def encode_node(d):
  outv = b""
  outv+=struct.pack("<II", len(d.props), len(d.children))

  for k,v in d.props.items():
    if len(k) >= 32:
      raise ValueError(f"property name {k} is too long")
    prop_name = bytes(k,'utf8').ljust(32,b'\x00')
    if prop_name[-1] != 0:
      raise ValueError(f"Property name {prop_name} isn't NULL terminated ({prop_name[-1]})")
    prop_len, prop_val = parse_prop_entry(v)

    if len(prop_val) % 4 != 0:
      raise ValueError(f"Property {k}'s value isn't a multiple of 4 bytes long ({prop_val})")

    outv += prop_name
    outv += struct.pack("<I", prop_len)
    outv += prop_val
    if len(outv) % 4 != 0:
      raise ValueError(f"Binary stream misaligned at property {k}")

  for c in d.children:
    outv += encode_node(c)
  return outv

def main():
  p = argparse.ArgumentParser(prog='dt_fixup')
  p.add_argument('dtree', type=argparse.FileType('rb', 0))
  p.add_argument('out', type=argparse.FileType('wb', 0))
  p.add_argument('-nvram', required=True, type=argparse.FileType('rb', 0))
  p.add_argument('-enable', action='append', default=[], choices=sorted(EMULATED_FEATURES.keys()),
                 help='keep the device tree nodes for an emulated feature so its XNU drivers bind (eg. -enable dcp)')
  p.add_argument('-dram', default=None,
                 help='DRAM size for the guest, eg. 24G. Must be matched by qemu -m. Default 8G.')
  args = p.parse_args()
  if args.dram:
    global DRAM_SIZE
    v = args.dram.strip().upper()
    mult = {'G': 1 << 30, 'M': 1 << 20, 'K': 1 << 10}.get(v[-1])
    DRAM_SIZE = int(v[:-1], 0) * mult if mult else int(v, 0)
  for f in args.enable:
    KEEP_COMPAT_PATHS.update(EMULATED_FEATURES[f])

  dt_root = ADTNode()
  decode_node(args.dtree.read(),dt_root)
  fixup(dt_root, nvram_file=args.nvram)
  args.out.write(encode_node(dt_root))

if __name__=="__main__":
  main()

