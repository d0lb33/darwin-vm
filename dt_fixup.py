#!/usr/bin/env python3
import math
import re
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
  # SEP, the security processor. We do not want its cryptography — we want
  # AppleSEPManager to exist at all. AMFI's trust manager blocks on it during
  # every spawn ("AMFI: trying to get developer mode status from ACM"), and
  # with no SEP node that wait fails the slow way:
  #
  #   ACMTRM: waitForSEPEndpoint: timed out waiting for AppleSEPManager
  #                               (timeoutMs=5000).
  #
  # 177 of those in one 900-second boot is 885 seconds spent blocked, and app
  # binaries (SpringBoard among them) never finish posix_spawn.
  #
  # /arm-io/sep is "iop-sep,ascwrap-v6" — the same ASC wrapper family as the
  # DCP's "iop,ascwrap-v6", so darwin-asc already backs its mailbox, and
  # dart-sep is a t8110 we already model. Its nub is "iop-nub,sep" rather than
  # rtbuddy-v2, so AppleSEPManager drives it instead of RTBuddy; SEP speaks its
  # own protocol above the mailbox, not RTKit, and we do not emulate that.
  'sep': ['arm-io/sep', 'arm-io/dart-sep'],
}
KEEP_COMPAT_PATHS = set()
EPHEMERAL_DATA_BLOCKS = None
SKIP_KEYBAG = False

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

def fixup_skip_keybag(d):
  # The `keybag` boot task hangs forever on a machine with no SEP, and unlike
  # seputil's 60-second wait it has no deadline at all: the guest's only vCPU
  # parks in the SPTM WFI trap with nothing runnable.
  #
  # The chain, from docs/re/keybag.md: keybagd finds no systembag.kb (normal on
  # a first boot — the "-7" it logs is a hardcoded literal that its own caller
  # swallows), and falls into AppleKeyStore.framework's aks_get_system(). That
  # lands in com.apple.driver.AppleSEPKeyStore, which is the *only* keystore
  # kext in this kernelcache — there is no software fallback to fall back to —
  # and which routes the operation through a SEP mailbox with nothing behind it.
  #
  # keybagd --init checks /product for "boot-ios-diagnostics" before any of
  # that, and exits 0 immediately when it is present. Presence is what is
  # checked, not the value.
  #
  # This is a skip, not a fix: nothing gets a real keybag, so anything that
  # actually needs one will fail later. It buys us the rest of the boot.
  d['product'].props['boot-ios-diagnostics'] = "u32:1"

def fixup_ephemeral_data(d, size_blocks):
  # iOS will not finish booting with a read-only root: it needs a writable
  # /private/var for personas, keybags, containers and databases. Twelve daemons
  # die with "mkdirat: [30: Read-only file system]" without one, and remounting
  # by hand does not work — from a root shell inside the guest,
  #
  #   /dev/md0 on / (apfs, local, read-only, journaled, noatime)
  #   mount_apfs: volume could not be mounted: Operation not permitted
  #
  # We have no storage controller, so there is no second partition to mount.
  # Apple's own answer for exactly this situation is already in the device tree:
  # the recovery/diagnostic environments run /private/var as a tmpfs seeded from
  # the template that ships on the system volume. /sbin/mount picks the fstab
  # node by os_env_type, and the apfs kext reads whichever child node is named
  # literally "fstab" (DT_get_fstab_entries), so selecting that environment is a
  # matter of renaming: the disk fstab out of the way, the ephemeral one in.
  #
  # env 2 (recovery) rather than 3 (diagnostic) because /sbin/fsck only runs its
  # OS-container check when (os_env_type & ~2) == 1, and that check needs an
  # APFS boot device we cannot provide; env 2 skips it and exits 0.
  #
  # vol.fs_mntopts size= is in 512-byte units. Apple ships 524288 (256 MiB) for
  # recovery, which is far too small: the /private/var template on this system
  # volume is over 1 GiB.
  #
  # This is a tmpfs. Everything written to /private/var is lost on reboot.
  fs = d['filesystems']
  if 'fstab-ephemeral-recovery-data' not in fs:
    raise ValueError("no fstab-ephemeral-recovery-data node to promote")
  fs['fstab'].props['name'] = 'fstab-disk'
  eph = fs['fstab-ephemeral-recovery-data']
  eph.props['name'] = 'fstab'

  for vol in eph.children:
    opts = vol.props.get('vol.fs_mntopts')
    if isinstance(opts, str) and 'size=' in opts:
      vol.props['vol.fs_mntopts'] = re.sub(r'size=\d+', f'size={size_blocks}', opts)

def fixup_fstab_drop_unavailable(d):
  # Apple's real fstab names six volumes: xART, Preboot, Data, Baseband-Data,
  # Update and Hardware. We can build a disk image that has System, Data,
  # Preboot and Hardware -- "diskutil apfs addVolume -role" covers those -- but
  # NOT xART: macOS refuses to set that role at all, both at addVolume and via
  # chrole afterwards ("Unable to set the APFS Volume Role (-69599)"). It is
  # secure-enclave storage and the host will not hand it out.
  #
  # DT_get_fstab_entries resolves every fstab child to a volume by ROLE, and on
  # the first boot of a real two-volume image it printed
  #
  #   DT_get_fstab_entries:12873: failed to get volume for role: 256
  #
  # (256 = 0x100 = xART) and then never mounted the Data volume at all -- no
  # disk1s2 line appears in any apfs mount, /private/var stayed the sealed
  # read-only system copy, and fixup-mobile-tmp died with "Read-only file
  # system". So an entry we can never satisfy has to come out of the table.
  #
  # This is the filesystem-side twin of the xART node removal in fixup_sep():
  # there we drop /arm-io/sep/iop-sep-nub/xART so seputil takes its
  # "not supported on platform" path; here we drop the fstab entry so the
  # mounter does not look for a volume that cannot exist.
  fs = d['filesystems']
  if 'fstab' not in fs:
    return
  tab = fs['fstab']
  before = len(tab.children)
  # Roles we cannot supply, and why. macOS's diskutil will create a volume for
  # Preboot (B), Data (D) and Hardware (H), so those stay. It refuses xART
  # outright, and offers no role letter for Baseband-Data (0x80) or Update
  # (0xc0) -- the role field is a bitmask (Update = Data|Baseband,
  # Hardware = xART|Data), and only some combinations are grantable.
  #
  # Measured one boot at a time, which is the point of leaving the failures
  # legible: with only xART removed the mounter moved on to
  # "failed to get volume for role: 128" (Baseband), so the same treatment is
  # owed to every entry whose volume cannot exist.
  DROP = ('xART', 'Baseband-Data', 'Update')
  tab.children = [v for v in tab.children
                  if str(v.props.get('vol.fs_name', '')) not in DROP]
  removed = before - len(tab.children)
  if removed and 'max_fs_entries' in tab.props:
    tab.props['max_fs_entries'] = f"u32:{len(tab.children)}"
  return removed

IOP_REGION_BASE = 0x10010000000
IOP_REGION_SIZE = 0x100000
IOP_REGION_STRIDE = 0x1000000

def fixup_iops(d):
  # Getting an IOP's drivers to *match* is not the same as getting XNU to start
  # it. Without these, RTBuddy binds the DCP and AppleDCPExpert matches, but the
  # coprocessor is never brought up and no endpoint ever opens. Derived in
  # docs/re/dcp-iop-start.md; the addresses for each check are there.
  #
  # ignore-gating, on the AppleA7IOP provider, makes powerOn/powerOff no-ops so
  # the driver does not poll a PMGR power gate we do not model.
  #
  # On the nub, "pre-loaded" (presence is what is checked, not the value) tells
  # RTBuddy the firmware is already resident, which is the path that suits us:
  # we have no iBoot to load an IOP image. region-base/region-size describe
  # where it was supposedly placed. /arm-io/smc/iop-smc-nub carries exactly this
  # shape on real hardware, which is what confirms the pattern.
  #
  # no-firmware-service keeps the nub from waiting on AFKFirmwareService, which
  # this kernelcache has no provider for.
  #
  # Only RTBuddy-style nubs get the nub properties. SEP's nub is "iop-nub,sep"
  # and is driven by AppleSEPManager, which boots the coprocessor its own way;
  # telling it the firmware is pre-loaded would be a claim we have not tested.
  #
  # And only where iBoot did not already describe a firmware region. We are
  # substituting for iBoot, not overriding it: the DCP's nub ships with no
  # region-base at all, which is why we invent one, but ANS and SMC ship real
  # ones (ANS also ships "power-managed", so RTBuddy starts it without being
  # asked). Clobbering those with the DCP's invented address made RTBuddy map
  # memory SPTM had typed XNU_KERNEL_RESTRICTED, and SPTM killed the boot:
  #
  #   panic: [SPTM] VIOLATION_FRAME_TYPE: refcounts_update_page_op
  #          (sptm_types.c:3347) ... fte->type(XNU_KERNEL_RESTRICTED)
  #
  # Leaving those nubs alone, ANS boots clean and Apple's own storage drivers
  # probe: AppleANS3CGv2Controller returns score 500000, AppleANS2NVMeController
  # 100000, and RTBuddy(ANS2) starts.
  # Each IOP that needs an invented region gets its OWN slot. Handing two of
  # them the same base is what made "-enable ans" plus "-enable dcp" panic SPTM
  # at RTBuddy(DCP): start with VIOLATION_FRAME_TYPE / XNU_KERNEL_RESTRICTED
  # while either alone was fine: before ANS's placeholder region-base = 0x0 was
  # recognised, only the DCP was eligible and there was exactly one claimant.
  # Stride is 16x the region so a future size increase cannot overlap.
  slot = 0
  for c in d['arm-io'].children:
    if 'compatible' not in c.props:
      continue
    c.props['ignore-gating'] = "<NULL>"
    for nub in c.children:
      compat = nub.props.get('compatible', '')
      if isinstance(compat, bytes):
        compat = compat.decode('utf8', 'replace')
      # ANS ships region-base = 0x0, iBoot's placeholder, so the "already has
      # a real region" test has to look at the value, not just the key. SMC's
      # is a genuine address (0x30de00000) and must still be left alone -- that
      # overwrite is what caused VIOLATION_FRAME_TYPE / XNU_KERNEL_RESTRICTED.
      # Without this, /arm-io/ans/iop-ans-nub never starts: the ASC logs zero
      # messages and AppleANS2NVMeController parks forever in
      # waitForMatchingService("ANS2Endpoint1", -1). Measured, tags A9DBG/A9FW.
      if 'rtbuddy' not in compat or nub.props.get('region-base') not in (None, 'u64:0x0'):
        continue
      nub.props['pre-loaded'] = "u32:1"
      nub.props['region-base'] = f"u64:{IOP_REGION_BASE + slot * IOP_REGION_STRIDE:#x}"
      nub.props['region-size'] = f"u64:{IOP_REGION_SIZE:#x}"
      nub.props['no-firmware-service'] = "<NULL>"
      slot += 1

def fixup_sep(d):
  # AppleSEPBooter::initForSEP asserts on a property iBoot normally adds and
  # that is in no shipped device tree:
  #
  #   REQUIRE fail: panicBytesData != nullptr
  #                 @ bool AppleSEPBooter::initForSEP(AppleSEPManager *):58
  #
  # The kernelcache names it, and states its size, in the two adjacent asserts
  # from SEPROMPanicBuffer.cpp:
  #
  #   rom-panic-bytes
  #   panicBytesData != nullptr
  #   panicBytesData->getLength() == sizeof(uint32_t)
  #
  # so it is a 4-byte property, and its *value* is a length: setting it to zero
  # clears the first assert and lands on the next one,
  #
  #   REQUIRE fail: length > 0 @ SEPROMPanicBuffer::SEPROMPanicBuffer(size_t):19
  #
  # so it sizes the buffer the SEP ROM would write its panic text into. The
  # asserts bound it (4-byte property, non-zero value) but do not pin the size
  # Apple uses; 0x100 is ours and nothing has yet read it back.
  #
  # It goes on the nub, not on the wrapper. These IOP nodes are a pair: the
  # "ascwrap-v6" node is claimed by AppleASCWrapV6 and the nub below it by the
  # coprocessor's own driver — RTBuddy on the DCP's iop-dcp-nub, AppleSEPManager
  # on iop-sep-nub. Setting it on /arm-io/sep alone leaves the assert firing.
  if 'arm-io/sep' not in KEEP_COMPAT_PATHS:
    # Stripping "compatible" is enough to keep a driver from binding, but not
    # enough for /usr/libexec/seputil: the data-protection boot task keys off
    # the mere presence of IODeviceTree:/arm-io/sep. On the normal boot path,
    # with the node present and no AppleSEPManager answering, it waits 60s and
    # takes launchd down with it, since the task is RequireSuccess:
    #
    #   init_data_protection: Timeout trying to connect to the SEP
    #   panic(cpu 0 ...): seputil[4] exited ... (signal 0, exit status 60)
    #
    # With the node gone it prints "No SEP present on this device" and exits 0.
    if 'sep' in d['arm-io']:
      d['arm-io'].remove_child('sep')
    return
  d['arm-io']['sep']['iop-sep-nub'].props['rom-panic-bytes'] = "u32:0x100"

  # AppleSEPManager::start never returns with the nub's power-gate function
  # in place. After "control endpoints created" it calls
  #
  #   callPlatformFunction("function-wait_for_power_gate", waitForFunction=true)
  #
  # on the nub (AppleSEPManager kext 0xfffffff0095a3328 -> kernel
  # 0xfffffff0085c1a04, x2 = -1). The property points at phandle 0x22, /arm-io/
  # pmgr, whose driver we strip along with every other unmodelled node, so the
  # wait-for-function never completes: no "PM init done", no registerService(),
  # and every waitForMatchingService(AppleSEPManager) in the system times out.
  # Established with gdbstub breakpoints on the calls either side (docs/re/
  # sep-bringup.md). The sibling lookup, "function-sep_sleep_prep", is absent
  # from the tree and returns at once, which is the behaviour we want here.
  d['arm-io']['sep']['iop-sep-nub'].props.pop('function-wait_for_power_gate', None)

  # With the power gate out of the way, _setPowerState(0 -> 2) still does not
  # talk to the ROM: AppleSEPManager only boots the SEP itself when it holds a
  # firmware object, and the one normal boots use comes from
  # AppleSEPFirmware::fromPreload, gated on the nub property "sepfw-loaded"
  # (kext 0xfffffff0095a33a0: copyProperty("sepfw-loaded") -> 0x5a33b0
  # fromPreload). fromPreload wraps the /chosen/memory-map "SEPFW" range in a
  # memory descriptor without parsing it (0xfffffff009591df4..0x591eb4), so
  # the loader's zero-filled SEPFW region is enough. qemu-t8030 sets the same
  # property for the same reason (hw/arm/apple_sep.c, "sepfw-loaded").
  d['arm-io']['sep']['iop-sep-nub'].props['sepfw-loaded'] = "u32:0x1"
  # The loader (xnuboot_sptm.c) fills this in with a reserved, zero-filled
  # range when the entry exists; iBoot's convention for an unset entry is
  # (-1, -1), same as fixup_sptm() uses for the others.
  d['chosen']['memory-map'].props['SEPFW'] = struct.pack("<QQ", 0xffffffffffffffff, 0xffffffffffffffff)

  # Having said the firmware is preloaded, also say the AP does not have to go
  # and fetch it from the filesystem at boot, because on our image it is not
  # there. /usr/libexec/seputil (run by launchd's "data-protection" boot task,
  # RequireSuccess) reads /chosen/sepfw-load-at-boot at 0x100002fec, and when
  # it is set it ends up at 0x100003314 doing
  #
  #   lookupPathForPersonalizedData(5, buf, 0x400)   -- 0x1000043bc
  #
  # which resolves to /private/preboot/<boot-manifest-hash>/usr/standalone/
  # firmware/sep-firmware.img4. That file lives on the Preboot volume, which
  # the IPSW system-volume payload does not contain and our image therefore
  # does not have (checked: /usr/standalone/firmware holds only Rose, SLAM,
  # SmartIOFirmwareT7000.bin and nfrestore). seputil then exits 5:
  #
  #   init_data_protection: can't open '/private/preboot/000...000/usr/
  #     standalone/firmware/sep-firmware.img4', errno: No such file or
  #     directory(2)
  #   panic(cpu 0 ...): seputil[4] exited ... (signal 0, exit status 5 )
  #
  # With the property cleared it takes the branch at 0x100002ff0 instead,
  # prints "Skipping SEP firmware load" (cstring 0x10001bc82) and exits 0.
  # That is the honest description of this machine: darwin-sep's ROM accepts
  # the zero-filled preload region as its IMG4 and reports sepOS alive, so
  # there is nothing for a second, filesystem-sourced firmware load to do.
  #
  # Measured, not assumed: with this cleared the SEP still completes its whole
  # boot conversation (ROM status 1 -> TZ0 -> status 2 -> IMG4 accepted), the
  # TXM secure channel page is still published, and all five endpoints are
  # still advertised -- AppleSEPManager's own use of the property
  # (0xfffffff0095a0500, alongside protected-data-access) is in its xART fetch
  # path, not in the firmware fetch.
  d['chosen'].props['sepfw-load-at-boot'] = "u32:0x0"

  # The same constructor then wants a non-zero chip id:
  #
  #   REQUIRE fail: _chip_id = *(uint32_t *)entry->getBytesNoCopy()
  #                 @ SEPROMPanicBuffer::SEPROMPanicBuffer(size_t):32
  #
  # /chosen/chip-id ships as 0 in the IPSW device tree — it is one of the fields
  # iBoot fills in at boot and we do not. Derive it from the SoC name rather
  # than hardcoding, so this keeps working on other targets: 't8140' -> 0x8140.
  # Only done when SEP is enabled, to keep boots that worked before unchanged.
  platform = get_platform_name(d)
  if platform.startswith('t') and all(c in '0123456789abcdefABCDEF' for c in platform[1:]):
    d['chosen'].props['chip-id'] = f"u32:{hex(int(platform[1:], 16))}"

  # Drop the marker node that claims this SEP has xART storage. We emulate the
  # SEP's mailbox, not its anti-replay store, and the userspace that trusts the
  # marker takes launchd down with it:
  #
  #   init_data_protection: Gigalocker file (/private/xarts/<uuid>.gl)
  #                         doesn't exist: No such file or directory
  #   init_data_protection: Failed to initialize gigalocker: 2
  #   panic(cpu 0 ...): seputil[4] exited ... (signal 0, exit status 2 )
  #
  # /usr/libexec/init_data_protection is a symlink to /usr/libexec/seputil, run
  # by launchd's "data-protection" boot task with RequireSuccess=true (the task
  # table is embedded in /sbin/launchd as a plist; Program is
  # /usr/libexec/init_data_protection, CSIdentityOverride com.apple.seputil).
  # In seputil (iOS 27.0 24A5430a, /usr/libexec/seputil, __TEXT at 0x100000000):
  #
  #   0x1000051d8  gigalocker_init() first calls a helper (0x100014820) whose
  #                whole body is
  #                  *out = IORegistryEntryFromPath(kIOMainPortDefault,
  #                           "IODeviceTree:/arm-io/sep/iop-sep-nub/xART") != 0
  #                (the literal path is the cstring at 0x10001bd63), and
  #   0x1000051e4  when that byte is clear it prints
  #                  "xART is not supported on platform, skipping
  #                   initialization"   (cstring 0x10001b326, printed by
  #                   0x100014598) and returns 0 -- success.
  #   0x10000527c  when the byte is set, it stats /private/xarts/<uuid>.gl and,
  #                if the file is missing, branches on its own argument: the
  #                caller at 0x1000032d8 passes 0, so it returns errno (2)
  #                rather than creating anything. The only caller that passes 1
  #                is 0x100002ecc, reached solely from the --gigalocker-init
  #                long option (getopt_long table at 0x100024180). So a
  #                writable /private/xarts does *not* help: this invocation
  #                never creates the file, it only looks for one a restore
  #                would have left there.
  #   The gated call site itself is guarded by /chosen/protected-data-access
  #                being a nonzero 4-byte value (0x100008f80 searches
  #                IODeviceTree:/chosen for it), which our tree ships as 1.
  #
  # This is why the panic only appears with -enable sep: with SEP disabled the
  # whole /arm-io/sep subtree is removed above, the path lookup fails, and
  # seputil takes the same "not supported" exit.
  #
  # Two kernel consumers read the same node, and both have a no-xART path:
  # AMFI's LocalSigning.cpp ("AMFI: calling %s without xART storage support")
  # and AppleLockdownMode's LDMShouldEnforceParity -- both string pairs sit
  # immediately after the path literal in firmware/bootkc. AppleSEPManager
  # does not look the node up at all; its ART decision is the nub's
  # "self-power-gate" property (docs/re/sep-protocol.md).
  if 'xART' in d['arm-io']['sep']['iop-sep-nub']:
    d['arm-io']['sep']['iop-sep-nub'].remove_child('xART')

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
    # iBoot publishes the reserved display-memory range under this name.
    # IOMobileFramebufferAP's current create_default_fb_surface path panics
    # if it is absent (IOMobileFramebufferAP.cpp:3290).  Keep the placeholder
    # uninitialized here; xnuboot_sptm fills it from the already-reserved -fb
    # carveout so the DT cannot claim ordinary guest RAM as display memory.
    'PurpleGfxMem',
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
  fixup_iops(d)
  if EPHEMERAL_DATA_BLOCKS is not None:
    fixup_ephemeral_data(d, EPHEMERAL_DATA_BLOCKS)
  if SKIP_KEYBAG:
    fixup_skip_keybag(d)
  fixup_sep(d)
  fixup_fstab_drop_unavailable(d)
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
  p.add_argument('-skip-keybag', dest='skip_keybag', action='store_true',
                 help='set /product boot-ios-diagnostics so keybagd --init exits instead of '
                      'blocking forever on a SEP that is not there. A skip, not a fix.')
  p.add_argument('-ephemeral-data', dest='ephemeral_data', nargs='?', const='8388608', default=None,
                 metavar='BLOCKS',
                 help='promote the ephemeral-recovery fstab so /private/var is a writable tmpfs '
                      'seeded from the on-volume template. Needs boot-args rootdev=md0 '
                      '(not rd=md0, which selects the restore path). Size in 512-byte blocks, '
                      'default 8388608 (4 GiB). Contents are lost on reboot.')
  p.add_argument('-dram', default=None,
                 help='DRAM size for the guest, eg. 24G. Must be matched by qemu -m. Default 8G.')
  args = p.parse_args()
  if args.dram:
    global DRAM_SIZE
    v = args.dram.strip().upper()
    mult = {'G': 1 << 30, 'M': 1 << 20, 'K': 1 << 10}.get(v[-1])
    DRAM_SIZE = int(v[:-1], 0) * mult if mult else int(v, 0)
  if args.skip_keybag:
    global SKIP_KEYBAG
    SKIP_KEYBAG = True
  if args.ephemeral_data:
    global EPHEMERAL_DATA_BLOCKS
    EPHEMERAL_DATA_BLOCKS = int(args.ephemeral_data, 0)
  for f in args.enable:
    KEEP_COMPAT_PATHS.update(EMULATED_FEATURES[f])

  dt_root = ADTNode()
  decode_node(args.dtree.read(),dt_root)
  fixup(dt_root, nvram_file=args.nvram)
  args.out.write(encode_node(dt_root))

if __name__=="__main__":
  main()
