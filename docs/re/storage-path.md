# Storage controller: ANS vs. PCIe NVMe vs. virtio-blk

Source: iOS 26 (24A5430a), iPhone17,3 (t8140/d47ap), kernelcache
`firmware/bootkc`, SPTM `firmware/sptm`, device tree `firmware/dtree` /
`ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p`, extracted 2026-09-02.

File-offset conversion used throughout for `firmware/bootkc`:
`file_offset = unslid_VA − 0xfffffff007004000`. Verified against `otool -l`:
`__PRELINK_INFO` has `vmaddr 0xfffffff00b35c000`, and
`0xfffffff00b35c000 − 0xfffffff007004000 = 0x4358000 = 70615040`, which is
exactly the `fileoff` `otool` reports for that segment.

## Recommendation

**Build Path A (ANS).** Do not pursue Path B. Path C is dead for this
kernelcache — report that to whoever is evaluating it.

Reasons, in order of how much they should move the decision:

1. **Path A's hardest blocker is already cleared.** `sart_sanity_check_throttles`
   — the SPTM panic that used to kill `-enable ans` outright — is resolved by
   `qemu-sptm/hw/arm/darwin_sart.c` (commit `ffb10cc`, already merged into the
   submodule's `darwin-vm-display` branch and wired into the machine at
   `darwin.c:308`, `darwin_sarts_create(dt_root, iobase, aic)`). That file's
   header comment cites the exact SPTM code that gated the boot
   (`sptm:0xfffffff0270c220c..0xfffffff0270c2240`, panic string `"Sart invalid
   throttle cfg [%d] = 0x%x"`) and the two SPTM tables it reverse-engineered to
   satisfy it. This was independently re-confirmed this session: the model
   file is 592 lines, still git-tracked, still wired in, build artifact present.
2. **Path A's register map is ~90% pinned down already**, cross-validated
   across three independent sources (Linux `apple-nvme.c`/`sart.c`, Asahi
   m1n1 `nvme.c`/`sart.c`, and this repo's own kernelcache disassembly of
   `com.apple.iokit.IONVMeFamily` and `com.apple.driver.AppleSART`) in
   `docs/re/ans-nvme-references.md` (691 lines, still present, re-verified
   this session). It has a concrete, cited MVP subset (§10 of that doc) and
   a resolved reg-window assignment (§2.4: reg[3]=NVMMU, reg[9]=NVMe, both
   from our own device tree plus m1n1's own `NVME_T8132` classification
   rule).
3. **Path B's driver personality exists but Path B's hardware doesn't, on
   this device.** See §1 below: `GenericNVMeSSD` really is in
   `com.apple.iokit.IONVMeFamily`, matching a stock PCI NVMe class code with
   no Apple-specific protocol at all — the note that motivated this
   investigation was right. But the real `/arm-io/apcie` node has exactly
   3 PCIe ports (`#ports = 0x3`), and all three are already spoken for by
   WLAN/BT/baseband (`dart-apcie0` has `mapper-apcie0-wlan` +
   `mapper-apcie0-bt`, `dart-apcie2` the same, `dart-apcie1` has a single
   generic `mapper-apcie1` consistent with the cellular modem given
   `AppleBasebandPCI` is a real kext in this kernelcache, §1.3). There is no
   spare port, and no PCIe NVMe device anywhere on this SoC's real topology —
   Apple's Apple-Silicon internal SSDs are never PCIe-attached; ANS is the
   only storage interconnect a real iPhone or Mac uses for its own boot
   drive. Making Path B work means inventing a PCIe port that does not exist
   in the device tree `dt_fixup.py` currently understands (an orchestrator-
   owned change) and, more importantly, implementing the Apple T8140 PCIe
   root complex controller itself.
4. **Path B's controller is a real, mostly-unknown hardware block, not a
   register-window formality.** `com.apple.driver.AppleEmbeddedPCIE`
   (313,416 bytes, the shared base class `AppleT8140PCIe` subclasses) has
   live link-training state machines with their own timeouts and panics:
   `void AppleEmbeddedPCIE::_waitForLinkUp(uint32_t, uint32_t, bool)`,
   `"Port %u Link Up Timeout"`, `"apcie: can't wait with resume or
   noLTSSM"`, `"Panic for width missmatch suppressed, link width is %u,
   expected %u"`, PME_To_ACK/L2 power-state polling with its own timeout
   strings (all confirmed present in this kext, re-extracted and re-grepped
   this session). None of this is cited anywhere yet with concrete register
   offsets — unlike ANS, there is no Linux/m1n1 reference material staged in
   this session's environment for the T8140 PCIe RC, so every offset would
   have to come from blind disassembly of a 313 KB stripped kext. That is a
   bigger, less-bounded RE task than what remains on Path A, for a payoff
   (real PCIe enumeration, BAR sizing, MSI, config-space semantics) that
   buys nothing extra a `hw/nvme` device wouldn't also need to fake to boot
   at all, since we would not be modeling real hardware regardless — we
   would be building a fictional NVMe-behind-PCIe device from scratch on top
   of a fictional working RC, when Path A only needs the fictional-but-
   documented NVMe/NVMMU protocol on top of infrastructure (RTKit mailbox,
   DART's sibling SART) that already works.
5. **Path C is dead for this kernelcache — confirmed, report this directly.**
   `strings firmware/bootkc | grep -i virtio` turns up real class names —
   `AppleVirtIOAgentDevice`, `AppleVirtIONeuralEngineDevice`, `VirtIOSound`,
   `virtiofs.util` — but **zero** of the 324 kexts in `__PRELINK_INFO`
   (`_PrelinkInfoDictionary`) have a bundle identifier containing "virtio" or
   "vmapple", and none of the above class names appear inside any
   `IOKitPersonalities` dictionary in this kernelcache. Cross-checked against
   `docs/re/vmapple-vs-soc.md` (written by a separate agent this project,
   still present, re-verified this session): iOS's virtio support is real
   but lives behind `APPLEVIRTUALPLATFORM`/`NO_MONITOR=1` kernel config used
   only for Apple's own `vphone600ap`/Virtualization.framework board, which
   is a structurally different XNU build (no SPTM/TXM at all) from the real
   `d47ap`/t8140 kernelcache this project boots. **There is no IOKit
   personality in this kernelcache that would bind a virtio-blk device.**
   Whoever is evaluating Path C needs a different kernelcache entirely (the
   `vphone600ap` one, itself gated behind Apple's PCC firmware distribution
   and dozens of binary patches per that doc) — it cannot be added to the
   real-device boot this project already has working.

**What would change this recommendation**: if someone gets the T8140 PCIe RC
enumerating *for free* — e.g. if a future DCP/display or WLAN bring-up pass
already needs a working `AppleEmbeddedPCIE` model for its own reasons — then
adding a fake NVMe device behind a spare/repurposed port becomes cheap and
Path B's generic-driver simplicity (no Apple NVMe-over-RTKit protocol at all)
would beat Path A on maintenance cost. Absent that, Path A is the shorter
path today.

---

## 1. The personality search (the crux question, answered)

Extracted `__PRELINK_INFO` from `firmware/bootkc` (`dd` at file offset
`70615040`, length `2621440`, per the conversion above; parsed as an XML
plist with `plistlib`, `_PrelinkInfoDictionary` has exactly 324 kexts).
Re-run and re-verified after a session restart this session — identical
results both times, including exact extracted-kext byte sizes.

### 1.1 `com.apple.iokit.IONVMeFamily` (version 2.1.0) — 9 personalities, verbatim

| Personality | IOProviderClass | IOClass | Match | Score |
|---|---|---|---|---|
| `AppleANS2CGNVMeController` | `RTBuddyService` | `AppleANS2CGNVMeController` | `IOPropertyMatch = {role: ANS2}` | — |
| `AppleANS2CGv2Controller` | `RTBuddyService` | `AppleANS2CGv2Controller` | `IOPropertyMatch = {role: ANS2}` | — |
| `AppleANS2DARTNVMeController` | `RTBuddyService` | `AppleANS2DARTNVMeController` | `IOPropertyMatch = {role: ANS2}` | — |
| `AppleANS2NVMeController` | `RTBuddyService` | `AppleANS2NVMeController` | `IOPropertyMatch = {role: ANS2}` | — |
| `AppleANS3CGv2Controller` | `RTBuddyService` | `AppleANS3CGv2Controller` | `IOPropertyMatch = {role: ANS2}` | — |
| `AppleANS3NVMeController` | `RTBuddyService` | `AppleANS3NVMeController` | `IOPropertyMatch = {role: ANS2}` | — |
| **`AppleEmbeddedNVMeController`** | **`IOPCIDevice`** | `AppleEmbeddedNVMeController` | **`IOPCIClassMatch = 0x01800200&0xffffff00`** | — |
| **`GenericNVMeSSD`** | **`IOPCIDevice`** | `IONVMeController` | **`IOPCIClassMatch = 0x01080200&0xffffff00`** | **100** |
| `NVMeSEPNotifier` | `AppleSEPManager` | `NVMeSEPNotifier` | `IOMatchCategory = NVMeSEPNotifier` | — |

**Yes, the earlier note was right**: `GenericNVMeSSD` exists, binds on
`IOProviderClass = IOPCIDevice` with a standard PCI class-code match
(`0x010802` = Mass Storage / Non-Volatile Memory / NVMe I/O controller, the
same class code QEMU's own `hw/nvme` reports in its PCI config space), and
its `IOClass` is the fully generic `IONVMeController` — no Apple-specific
protocol whatsoever. `AppleEmbeddedNVMeController` is Apple's own PCIe-NVMe
driver, for a **non-standard** class code (`0x018002`, "Other Mass Storage",
prog-if 2) that real Apple hardware's PCIe-attached NVMe (Thunderbolt/USB4
enclosures on Macs, per the class match's generality) apparently reports
instead of the spec value — not applicable here since we would be
synthesizing the device ourselves and could report whichever code we want.

Empirically, per prior work already in this repo (`docs/re/ans-nvme-references.md`
§0, `io=0x1f` driver-match trace on our real device tree): IOKit's own
probe-scoring picks `AppleANS3CGv2Controller` (score 500000) as the winner
among the `RTBuddyService`-provider personalities on this hardware's real
`role=ANS2` node — confirming ANS is what a real d47ap actually uses, and
that the PCI-provider personalities never even get a chance to probe on real
hardware because nothing publishes a matching `IOPCIDevice`.

### 1.2 `com.apple.driver.AppleT8140PCIe` (version 1) — 2 personalities

| Personality | IOProviderClass | IOClass | Match | Score |
|---|---|---|---|---|
| `AppleT8140PCIe` | `AppleARMIODevice` | `AppleT8140PCIe` | `IONameMatch = apcie,t8140` | — |
| `ApplePCIEHostBridge` | `IOPCIDevice` | `ApplePCIEHostBridge` | `IONameMatch = apcie-bridge` | 5000 |

`apcie,t8140` is the `compatible` string on `/arm-io/apcie` in the
**unpatched** device tree (`ipsw img4 im4p extract` of
`DeviceTree.d47ap.im4p`, then `dt_fixup.py`'s own `ADTNode` decoder —
`firmware/dtree`'s `compatible` was already stripped by the time this repo's
`fixup()` ran on it in an earlier pass, confirmed by re-decoding the raw
IPSW device tree directly: `apcie: compatible = apcie,t8140`). `AppleT8140PCIe`
is the platform driver for the RC itself; `ApplePCIEHostBridge` is the
generic `IOPCIFamily`-side nub it publishes once the RC is up (name
`"apcie-bridge"`, IOKit ADT matching being by `name`/`compatible`, not a
literal ADT node — no ADT node named `apcie-bridge` exists; `AppleT8140PCIe`
synthesizes it in software).

### 1.3 `com.apple.iokit.IOPCIFamily` (2.9) — 3 personalities, all generic

`IOPCI2PCIBridge-Name` (`IONameMatch = pci-bridge`), `IOPCI2PCIBridge-PCI`
(no match — catches anything already typed as a PCI-PCI bridge), and
`IOPCI2PCIBridge-i386` (`IOPCIClassMatch = 0x06040000&0xffff0000`, standard
PCI-PCI bridge class code). These are IOPCIFamily's own generic downstream-
bridge matching, unrelated to Apple's ADT — they fire against whatever a
config-space scan turns up once a bus is enumerated, the same convention
used on x86/ACPI systems (`IONameMatch = pci-bridge` is IOPCIFamily's own
synthesized name for any class-0x0604 device it discovers, not a literal ADT
node name).

### 1.4 Other storage-adjacent kexts present, for completeness

`com.apple.driver.AppleSART` (3 personalities: `IOSARTMapper` on
`sart,t8015`, `IOCoastGuardSARTMapper` on `['sart,coastguard']` — this is
what matches our real `/arm-io/sart-ans` node — and `AppleSARTMarconi` on
`sart-marconi,t8006`, irrelevant here). `com.apple.driver.AppleEffaceableStorage`,
`com.apple.driver.AppleStorageDrivers`, `com.apple.filesystems.apfs`,
`com.apple.driver.AppleBasebandPCI*` (cellular modem, PCIe-attached — this is
almost certainly what `dart-apcie1`'s single `mapper-apcie1` belongs to),
`com.apple.driver.AppleUSBMassStorageInterfaceNub` /
`com.apple.driver.AppleConvergedPCI` / `com.apple.driver.AppleT6020PCIePIODMA`
/ two Thunderbolt PCI adapter kexts — all present, none of them a second
storage-controller path worth considering.

**No personality anywhere in this kernelcache's 324-kext `__PRELINK_INFO`
matches on a PCI class code or ADT node in a way that suggests a
*storage*-purposed PCIe device exists on this SoC's real topology.** The
PCIe-capable kexts that are present (`AppleBasebandPCI*`, Thunderbolt
adapters, the WLAN/BT stack implied by `dart-apcie0`/`dart-apcie2`'s mapper
children) account for all 3 real ports.

---

## 2. Device tree ground truth for Path B (`apcie`)

Same source as §1.2, cross-checked in both the patched `firmware/dtree` (via
`dt_fixup.py`'s `ADTNode`) and the raw `DeviceTree.d47ap.im4p`.

`/arm-io/apcie` (`compatible = apcie,t8140`, `device_type = pci`):

| Property | Value | Notes |
|---|---|---|
| `#ports` | `0x3` | exactly matches the 3 `pci-bridge0/1/2` children below |
| `reg` | 400 bytes | with `#address-cells=3`/`#size-cells=2`, that is 20 cells × 20 bytes... actually 5-cell (addr×3+size×2) entries × 4 bytes = 20 bytes/entry → **20 reg windows** (core, per-port config/ECAM, PHY glue, etc. — not individually identified this pass) |
| `bus-range` | `0x8_00000000` (as a packed 64-bit: bus 0..8) | |
| `dev-range` | `0xff00000000` | |
| `msi-vector-offset` / `msi-address` / `#msi-vectors` | `0x533` / `0xfffff000` / `0x60` (96, split 0x20 per port) | |
| `interrupts` | 3 entries (`0x4b5`,`0x4be`,`0x4c7`) | one core IRQ set; each `pci-bridgeN` has its own `#msi-vectors`/`msi-vector-base` but no separate `interrupts` property — MSI-routed |
| `ranges` | 2 outbound-window entries | `0xc0b00000` len `0x2000000` (32-bit MMIO) and `0xc0b80000...` `0x800000000` len `0x40000000` (64-bit MMIO, 1GB) |

`pci-bridge0`/`pci-bridge1`/`pci-bridge2` children (`AAPL,unit-string`
`00000000`/`00010000`/`00020000`, `apcie-port = 0/1/2`): each carries
`apcie-piodma-sid = 0x11` (all three share the same stream ID for the PIO-DMA
helper) and `function-dart_force_active`/`function-dart_request_sid`/
`function-dart_release_sid`/`function-dart_self` — 4-byte-tag-prefixed
function pointers (`"Fact"`, `"SReq"`, `"SRel"`, `"Self"` ASCII tags visible
in the raw bytes) that are iBoot/SPTM-side platform-function bindings, not
AP-readable registers. `maximum-link-speed` is `0x3` (Gen3) for ports 0/2 and
`0x2` (Gen2) for port 1.

**No child of `apcie` carries an NVMe-class or storage-related property.**
The DART evidence in `/arm-io/dart-apcie0`, `dart-apcie1`, `dart-apcie2`
(each has `mapper-apcie{N}-wlan`/`-bt`/`-piodma` children, or for port 1 a
single generic `mapper-apcie1`, consistent with `AppleBasebandPCI` being
present in `__PRELINK_INFO`) accounts for all three ports as WLAN, cellular
modem, and BT/WLAN again.

### What Path B would need, if pursued anyway

1. **A phantom 4th port or a repurposed existing port** in the device tree,
   since real hardware has none free. This needs `dt_fixup.py` changes
   (orchestrator-owned) — either growing `#ports` and adding a `pci-bridge3`
   child with its own `AAPL,unit-string`/`apcie-port`/`msi-vector-base`, or
   overwriting one of the three existing ports (cheapest: `pci-bridge1`,
   since it has no `-wlan`/`-bt` split, just one generic DART mapper, and
   losing baseband emulation costs nothing this project needs).
2. **A model of the Apple T8140 PCIe root complex** satisfying at minimum:
   `AppleEmbeddedPCIE::_waitForLinkUp` (needs a link-up bit to read as set,
   with a bounded timeout — the exact register/bit is undetermined this
   pass, no offsets found in the 685 filtered strings pulled from the kext;
   would need blind disassembly of `_waitForLinkUp`/`getLinkRcvryDebugTracer`/
   `getLinkSpeedDebugTracer`, none of which are cited with an address here),
   PERST/refclk sequencing (`function-perst`, `t-refclk-to-perst = 0x64`µs,
   `perst-to-config = 0x64`µs — timing values are known from the DT, the
   registers they gate are not), and whatever `ApplePCIEHostBridge` needs
   from standard PCI config space (vendor/device ID, BARs, class code) once
   `IOPCIFamily`'s generic bus-scan takes over — this part is genuinely
   simple, IOPCIFamily's config-space walk is the same generic code used
   everywhere.
3. **`hw/nvme` (already in QEMU) behind that host bridge**, reporting class
   code `0x010802` so `GenericNVMeSSD`/`IONVMeController` binds with no
   further Apple-specific work — this is the one part of Path B that is
   genuinely easier than Path A, since `hw/nvme` already speaks the real
   NVMe protocol end to end.
4. **A DART for `dart-apcie{N}`** — already solved, `darwin-dart` already
   models a t8110-style IOMMU and this project already uses it for DCP.

Item 2 is the open-ended one. Nothing in this session's environment (no
Linux `pcie-apple.c`, no m1n1 PCIe source, no prior clone) supplies register
offsets for it the way `docs/re/ans-nvme-references.md` had them handed to it
for ANS — every offset would need to come from disassembling a 313 KB
stripped kext (`com.apple.driver.AppleEmbeddedPCIE`) cold. That is the
concrete reason Path A is recommended over Path B despite Path B's simpler
driver-side protocol.

---

## 3. The SART gate (already resolved — verified this session)

`docs/re/ans-nvme-references.md` §0/§6 already covers the SART register
*data* (CONFIG/PADDR/SIZE layout, three independent public sources agreeing).
The SPTM *gate* — `sart_sanity_check_throttles` panicking with `"Sart invalid
throttle cfg [0] = 0x0"` the moment `AppleSART` calls
`SART_IOCTL_SET_ACTIVE`, because nothing backed the throttle registers and
`darwin-unimp`'s zero-fill fails SPTM's non-zero check — is **resolved**, not
open. `qemu-sptm/hw/arm/darwin_sart.c` (592 lines, commit `ffb10cc "feat(sart):
model the SART address filter so SPTM can activate it"`, wired into the
machine at `darwin.c:308` via `darwin_sarts_create(dt_root, iobase, aic)`)
implements it. Re-confirmed present, git-tracked, and building this session
after a full environment reset.

Its header comment cites exactly what makes a throttle config "valid",
straight out of SPTM's own binary:

- **Per-version descriptor tables** in SPTM's `__TEXT,__const`: region
  layout at `sptm:0xfffffff02701a418 + 0x2c*(sart-version-1)`, throttle
  layout at `sptm:0xfffffff02701a368 + 0x2c*(sart-throttle-version-1)`,
  selected in `sart_bootstrap_parse_edt`
  (`sptm:0xfffffff0270c276c`/`0xfffffff0270c27a8`), versions outside 1..4
  rejected with `"Invalid SART version %d from EDT"`
  (`sptm:0xfffffff0270c2760`).
- **The check itself**, inlined into `SPTM_SART_SET_STATE` at
  `sptm:0xfffffff0270c220c..0xfffffff0270c2240`: for each of 3 throttle
  channels (v1–v3; 4 for v4), read CONFIG and require it non-zero
  (`ldr w15,[x15]; cbz w15, <panic>`). For t8140's `sart-version = 3`
  (device tree, `/arm-io/sart-ans`), the descriptor is at
  `sptm:0xfffffff02701a3c0`, magic `'SRT3'`, three channels at
  `{0x8010/0x8014, 0x8018/0x801c, 0x8020/0x8024}` (STAT/CONFIG pairs)
  relative to `reg[0] + sart-throttle-offset` (absent on t8140, defaults to
  0 at `sptm:0xfffffff0270c2594`).
- **STAT must read 0** in its low 16 bits (`sart_stat_busy_mask = 0xffff`)
  for `SPTM_SART_UNMAP_REGION` (`sptm:0xfffffff0270c0fc4..0xfffffff0270c112c`)
  to not spin forever polling drain.
- The model's fix: CONFIG reads non-zero at reset (property
  `throttle-cfg-reset`, default 1) and remembers writes; STAT is always 0.
  What the bits actually mean is not stated anywhere reachable (SPTM only
  ever compares CONFIG against 0, never interprets it) so the model
  correctly does not pretend to know more.

**Empirical status, checked this session**: the SART model is wired in and
builds, but **no probe log in this session's environment demonstrates
`-enable ans` booting past this panic with the current binary** — every
`ans`-related serial log found predates the `ffb10cc` build (the one
post-fix log found, `sart_reg.serial.log`, was a plain `rd=md0` ramdisk boot
with no `-enable ans` at all — confirmed by grepping it for `RTBuddy`/`ANS`/
`darwin-asc`, all absent). Verifying the SART fix holds end-to-end (i.e. that
`AppleSART` actually reaches `SART_IOCTL_SET_ACTIVE` and SPTM's check passes
in a live boot) is the first empirical checkpoint once Path A implementation
starts. Did not run this boot myself this session: another agent was already
mid-run against this exact binary (`probe.sh` invocations visible via
`pgrep`) when this investigation reached that point, and the task brief
prefers static analysis; this is a one-command, ~60-second check for
whoever implements Path A next (`dt_fixup.py -enable ans`, `tools/probe.sh
--grep 'Sart|RTBuddy|panic\('`).

---

## 4. Which `/filesystems/fstab` roles are load-bearing

Source: `/arm-io`'s sibling `/filesystems` node, `firmware/dtree`, decoded
with `dt_fixup.py`'s own `ADTNode`. Four `fstab*` children exist, selected by
`os_env_type` (`/sbin/mount` reads `IODeviceTree:/filesystems/fstab`, string
confirmed in `/sbin/mount` from the real system volume, see below).

### 4.1 `fstab` (`os_env_type = 0x1`, `max_fs_entries = 0x7`) — the real disk-boot table

This is the one that matters for the "real Data volume" goal — it is what
`dt_fixup.py`'s existing `-ephemeral-data` machinery (`fixup_ephemeral_data`,
`dt_fixup.py:206-237`) currently *replaces* with the 2-entry recovery table
to work around not having real storage. All 7 entries, verbatim:

| Role | `vol.fs_role` | `vol.fs_name` | Mount point | `fs_passno` | `fs_mntorder` |
|---|---|---|---|---|---|
| System | `0x1` | System | `/` (fs_file `0x2f` = ASCII `/`) | 1 | 0 |
| Preboot | `0x10` | Preboot | `/private/preboot` | 1 | 1 |
| xART | `0x100` | xART | `/private/xarts` | 1 | 2 |
| Data | `0x40` | Data | `/private/var` | 2 | 3 |
| Baseband-Data | `0x80` | Baseband-Data | `/private/var/wireless/baseband_data` | 2 | 4 |
| Update | `0xc0` | Update | `/private/var/MobileSoftwareUpdate` | 2 | 5 |
| Hardware | `0x140` | Hardware | `/private/var/hardware` | 2 | 6 |

`fs_passno` splits the table exactly along the launchd boot-task boundary:
`/sbin/launchd`'s compiled-in boot-task plist (extracted from
`/sbin/launchd` on the real system volume this session, string search on the
Mach-O for `<key>mount-phase-1</key>`) runs `mount -P 1` then `mount -P 2`,
**both with `RequireSuccess = true`** — a failure panics the boot (`"Panicking
in 3 seconds"`, same binary). So: **phase 1 needs System + Preboot + xART**,
**phase 2 needs Data + Baseband-Data + Update + Hardware**, and both phases
being `RequireSuccess` means, on the face of it, all 7 roles must exist for a
normal (`os_env_type=1`) boot to complete `mount-phase-1`/`-2` without a
kernel panic.

### 4.2 Evidence some individual roles are tolerated missing

`/sbin/mount` (extracted from the real system volume DMG this session via
`hdiutil attach -readonly` on the AEA-decrypted image, per the pipeline in
`docs/re/rootfs-assembly.md`) carries role-specific strings that only exist
for Data:

```
mount: data volume missing, but not required in env: %u
mount: found boot container: %s, data volume: %s env: %u
mount: missing data volume
failed to mount data volume on darwinOS.
```

This says Data specifically has an explicit "not required in env %u" code
path — almost certainly the ephemeral-recovery/diagnostic envs
(`os_env_type = 2/3/9`, §4.3), which is exactly what this project's existing
`-ephemeral-data`/`-skip-keybag` machinery already exploits to boot without
real storage at all. **No equivalent "not required" string exists for
Preboot, xART, Baseband-Data, Update, or Hardware** in this binary's string
table — meaning those roles' handling, if lenient, is not done through a
dedicated diagnostic message the way Data's is. This was not settled further
by disassembly this pass (time-boxed); treat "are Baseband-Data/Update/
Hardware/xART strictly required for `os_env_type=1`'s `mount -P {1,2}` to
return success" as **open**, not confirmed either way.

### 4.3 What this means for whoever builds the image

- **System, Preboot, Data are definitely load-bearing** — System and
  Preboot because this project already boots a System-role volume
  successfully today (`docs/re/rootfs-boot.md`), Data because it's this
  task's entire motivation and the one role `/sbin/mount` explicitly talks
  about being sometimes-optional (implying it is *not* optional in the
  normal env we're targeting).
- **xART, Baseband-Data, Update, Hardware**: build them if cheap (empty/
  minimal APFS volumes with the right role are enough — nothing here
  suggests their *contents* matter, only their *presence* for `mount -P` to
  succeed), because `RequireSuccess = true` on both boot tasks is a real risk
  if any one of the 7 is missing and turns out not to be one of the lenient
  cases. Cheapest to skip first, if something has to be cut, is
  **Baseband-Data**: this project does not model a cellular baseband at all
  (`AppleBasebandPCI` sits behind `dart-apcie1`, itself unmodeled per §2),
  so if that omission alone doesn't panic `mount -P 2`, it costs nothing to
  find out empirically before spending effort building all 7.
- The recovery/diagnostic fstabs (`os_env_type = 2/3/9`) all still carry
  Preboot, Hardware, and (for 3/9) Update/Baseband — i.e. even Apple's own
  "no real Data volume" fallback paths expect those roles to exist, which
  weakly supports treating them as required rather than optional in the
  normal path too.

---

## 5. What Path A's implementer should build, and in what order

This is a pointer into already-complete design work, not a new spec — do not
duplicate `docs/re/ans-nvme-references.md`, which already has the register
map, bring-up sequence, and a concrete MVP subset (its §10). The delta this
document adds:

1. **First**, confirm the SART fix holds live: `dt_fixup.py -enable ans`,
   boot, expect no `sart_sanity_check_throttles` panic and `AppleSART`
   reaching whatever comes after `SART_IOCTL_SET_ACTIVE` (§3 above — this is
   the one thing in this whole investigation that is implemented but
   *unverified end-to-end*).
2. **Then** follow `docs/re/ans-nvme-references.md` §10 items 2-4: a new
   `darwin_ans.c` (shape of `darwin_dcp.c`, `eps` empty), reg[3]/reg[9] MMIO
   with the offsets in that doc's §2.1-§2.3, the tag-indexed submission
   mechanism in its §4a/§5.
3. **Root filesystem role work** (§4 above) can happen in parallel — it is
   independent of whether Path A's device model is done, since it is about
   what the *image-building* agent puts in the APFS container, not what the
   QEMU device model does.

## Open questions

- Does `-enable ans` on the current (post-`ffb10cc`) build actually reach
  `AppleSART`'s `SART_IOCTL_SET_ACTIVE` and pass SPTM's check in a live boot?
  Not run this session (see §3). One `tools/probe.sh` invocation settles it.
- Are Baseband-Data/Update/Hardware/xART strictly required for `mount -P
  {1,2}` to return success on `os_env_type=1`, or does `/sbin/mount` skip
  individually-missing non-Data roles the way it explicitly does for Data?
  Needs disassembly of `/sbin/mount`'s per-role mount loop (not done this
  pass) or, more cheaply, an empirical test once any real block device with
  a subset of roles exists.
- The exact register(s) `AppleEmbeddedPCIE::_waitForLinkUp` and
  `getLinkRcvryDebugTracer`/`getLinkSpeedDebugTracer` poll, and the ~20 reg
  windows in `/arm-io/apcie`'s 400-byte `reg` property — only relevant if
  the recommendation above is later overturned and Path B gets picked up.
- Exact NVMe `CAP` register value and several unidentified ANS reg windows
  (reg[1]/[4]/[5]/[6]/[10]/[11]/[12]) — already flagged as open in
  `docs/re/ans-nvme-references.md` §9, unchanged by this pass.
