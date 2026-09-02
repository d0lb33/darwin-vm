# ANS / NVMe reference notes (iPhone17,3 / t8140)

Scope: the ANS (Apple NAND Storage) RTKit coprocessor and the NVMe controller
it fronts, plus the SART address filter its DMA goes through. This sits on
top of the already-working generic RTKit/mailbox model in
`qemu-sptm/hw/arm/darwin_asc.c` — nothing here needs to touch that file.
Goal: get `IONVMeFamily` far enough to read blocks from a root filesystem.

Sources cloned to `/tmp/dvm/ref/` (see file:line citations below):

| Path | What |
|---|---|
| `apple-nvme.c` | Linux `drivers/nvme/host/apple.c` (mainline, current) |
| `linux-apple-repo/drivers/soc/apple/sart.c`, `include/linux/soc/apple/sart.h` | Linux SART driver |
| `linux-apple-repo/include/linux/nvme.h` | standard NVMe register/bit definitions Linux uses |
| `m1n1/src/nvme.c`, `src/sart.c`, `src/rtkit.c` | Asahi m1n1's own bring-up code — **actively maintained for M4+**, the single most load-bearing source here |
| `qas/hw/block/apple-silicon/ans.c`, `hw/arm/apple-silicon/sart.c` | ChefKissInc QEMUAppleSilicon's prior models (t8030/A13 target) |
| `xnu/iokit/Kernel/IOMapper.cpp`, `xnu/config/Private.arm64.exports` | apple-oss-distributions/xnu, generic `iommu-parent` plumbing + confirms `pmap_iommu_ioctl` is real |

Plus two sources **not** in the standard four reference bodies, pulled because
they resolved things the reference bodies couldn't:

- **Our actual device tree**: `ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p`,
  extracted with `ipsw img4 im4p extract` and parsed with `dt_fixup.py`'s own
  `ADTNode` decoder. This is the ground truth for reg windows and properties;
  where it disagrees with Linux/m1n1's assumptions, it wins per the task brief.
- **Our actual kernelcache**: `ipsw_db/24A5430a__iPhone17,3/kernelcache.release.iphone17`,
  extracted with `ipsw kernel dec` / `ipsw kernel extract` into
  `com.apple.iokit.IONVMeFamily` and `com.apple.driver.AppleSART`, disassembled
  with `llvm-objdump -d` (no symbols — kexts are stripped in this fileset KC,
  so all attributions below are "found in the kext binary", not "found in
  function X of class Y" unless stated otherwise).
- **Empirical boot evidence**: `tools/probe.sh` runs already sitting in
  `/tmp/dvm/probe/*.serial.log` from parallel work on this same task (not
  produced by me, not committed anywhere — re-run to reproduce). These are
  cited explicitly as "observed in probe logs," separate from static RE.

## 0. Current state (empirical, as of this session)

`-enable ans` (via `dt_fixup.py`, currently **broken** — see below) already
boots to a shell with **no panic** using only the generic RTKit wrapper.
`darwin_ans.c` does not exist yet; `darwin.c` never calls anything analogous
to its `darwin_dcp_create(dt_root, iobase, aic)` for ans (`qemu-sptm/hw/arm/darwin.c:302`).
`arm-io/ans` is instead picked up by `darwin_asc.c`'s generic
ascwrap-scan fallback with `ops=NULL` (`qemu-sptm/hw/arm/darwin_asc.c:480-495`),
confirmed by the single stderr line in every probe log:

```
darwin-asc: ANS2 (ans) at 0x389600000 irqs 0x3f0 0x3ef 0x3f2 0x3f1 0x40f
```

(`/tmp/dvm/probe/s_smc.stderr.log`; base = `0x179600000` reg[0] + `iobase`.)

From `/tmp/dvm/probe/iodeep.serial.log` (io=0x1f driver-match trace), IOKit's
personality scoring on our real kernelcache, for `AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)`:

```
AppleANS2CGNVMeController[...]::probe fails
virtual IOService *AppleANS2CGv2Controller::probe(...)::103:Found (ANS2) provider and coastguard, returning score 400000
AppleANS2DARTNVMeController[...]::probe fails
virtual IOService *AppleANS2NVMeController::probe(...)::251:Found (ANS2) provider, returning score 100000
virtual IOService *AppleANS3CGv2Controller::probe(...)::97:Found (ANS2) provider and coastguard, returning score 500000
virtual IOService *AppleANS3NVMeController::probe(...)::82:Found (ANS2) provider and linear-sq, returning score 300000
```

**`AppleANS3CGv2Controller` wins** (score 500000), not `AppleANS2CGv2Controller`
(400000) — correct this if you assumed otherwise from class-name pattern
matching alone. `AppleANS2CGNVMeController` and `AppleANS2DARTNVMeController`
fail probe outright (hard fail, not a low score — likely a required property
we don't have, e.g. `nvme-ans-dart` for the DART variant). `IOCoastGuardSARTMapper`
also registers successfully against `sart-ans` in the same log
(`iodeep.serial.log:331`), which is worth noting because our SART node has
*no* device model yet either — it registers purely against `darwin-unimp`'s
zero-filled fallback, so whatever it reads at `start()` time tolerates all-zero
readback. No probe run so far shows any MMIO touch on `ans[N]`/`sart-ans[N]`
(`grep`ing all `/tmp/dvm/probe/*.stderr.log` for those or for `0x1300`/`0x1210`
turns up nothing) — every log is the same ~14-line device-model-setup boilerplate.
So neither controller has been observed to call `start()` yet in any run so
far; that's the next thing to instrument (`DARWIN_UNIMP_DEBUG=1` against a
longer `--secs`, or `DARWIN_ASC_DEBUG=1` to watch for endpoint traffic beyond
the generic handshake).

**Caveat on `dt_fixup.py`**: it currently has an uncommitted local diff
(`git diff dt_fixup.py`, adding `-enable smc`) that makes the *unmodified*
`fixup()` path throw `ValueError: child dockchannel-uart not found in arm-io`
on our real `firmware/dtree` — I could not regenerate a device tree myself
this session and relied on the pre-existing logs above. This is squarely
`dt_fixup.py`, which is orchestrator-owned; flagging it rather than touching it.

Because `AppleANS2CGv2Controller` and `AppleANS3CGv2Controller` share the same
`IONVMeFamily` kext binary and neither is separable by symbol (stripped), the
disassembly findings below (§3) are attributed to "the CoastGuard code path in
`IONVMeFamily`" rather than to one specific class.

## 1. Device tree ground truth

Dumped directly from `ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p`
(script: ad-hoc, `dt_fixup.py`'s own `ADTNode`/`decode_prop` reused to also
keep raw bytes — not committed, this is throwaway tooling).

`/arm-io/ans` (`compatible = iop,ascwrap-v6`, `device_type = ans`, `role = ANS2`):

| idx | base | len | best-guess owner | confidence |
|---|---|---|---|---|
| 0 | `0x179600000` | `0x88000` | ASC wrapper + mailbox (existing `darwin_asc.c` convention) | high — already boots |
| 1 | `0x179050000` | `0x4000` | unidentified (clock/power-gate adjacent?) | unresolved |
| 2 | `0x0` | `0x0` | reserved/unused | n/a |
| 3 | `0x17dcc0000` | `0x60000` | **NVMMU registers** | medium-high (m1n1, by generation analogy) |
| 4 | `0x17b000000` | `0x1000000` | unidentified, 16MB — scratch/SRAM? | unresolved |
| 5 | `0x17db90000` | `0xc000` | unidentified — possibly "MSP" (NAND PHY?) | unresolved |
| 6 | `0x17dd47c00` | `0x4000` | falls *inside* sart-ans reg[1]'s range | unresolved |
| 7 | `0x0` | `0x0` | reserved/unused | n/a |
| 8 | `0x0` | `0x0` | reserved/unused | n/a |
| 9 | `0x1bdcc0000` | `0x10000` | **NVMe standard + vendor registers** | medium-high (m1n1, by generation analogy) |
| 10 | `0x17dc00000` | `0x4000` | unidentified, near sart-ans base | unresolved |
| 11 | `0x17dc20000` | `0x4000` | unidentified, near sart-ans base | unresolved |
| 12 | `0x17db00000` | `0x20000` | unidentified — possibly "MSP" | unresolved |

Other properties on `arm-io/ans` worth knowing about: `nvme-interrupt-idx = 4`,
`nvme-queue-entries = 0x40` (64), `nvme-tl-wa` (present, empty/boolean),
`nvme-linear-sq` (present, empty/boolean), `nvme-num-sl = 2`,
`nvme-secure-bar` (present, empty/boolean), `iommu-parent = 0x74` (phandle to
`sart-ans`), `namespaces` (7 triples `(index, nsid, ?)`, first is `(1, 1, 0)` —
namespace 1 is almost certainly the root filesystem), `msp-bfh-params` /
`msp-phy-fw-path = t303.aus5` / `tunable-table-bundle = kbflnskc` (all look
IOP-firmware-consumed, not AP-driver-consumed — see §6).

`interrupts = [0x3f0, 0x3ef, 0x3f2, 0x3f1, 0x40f]` — first 4 are the standard
mailbox IRQs `darwin_asc.c` already wires (a2i empty/not-empty, i2a
empty/not-empty). `nvme-interrupt-idx = 4` points at the 5th entry
(`0x40f`): **NVMe completion delivers on its own dedicated AIC line**,
separate from the mailbox IRQs. Confirmed independently by Linux
`apple_nvme_alloc()` requesting exactly one `platform_get_irq(pdev, 0)`
(`apple-nvme.c:1483`, `:1572`) shared between admin+IO CQ polling
(`apple-nvme.c:707-723`).

`/arm-io/sart-ans` (`compatible = sart,coastguard`, `sart-version = 3`):

| idx | base | len | owner |
|---|---|---|---|
| 0 | `0x17dc50000` | `0xc000` | SART v3 region table (CONFIG/PADDR/SIZE) + power/canary regs |
| 1 | `0x17dd44000` | `0x4000` | unidentified — possibly `AppleSARTimer` (kext symbol, see §5) |
| 2 | `0x17dcc0000` | `0x4000` | **same base address as `ans` reg[3]** — see §5 |

`sart-power-managed` (present, boolean), `sart-power-reg-offset = 0x13e8`,
`power-canary-offset = 0x0` — both offsets fall inside reg[0]'s `0xc000`
window, not a separate BAR.

## 2. Register map

### 2.1 Standard NVMe subset (spec registers Apple keeps)

Source: `linux-apple-repo/include/linux/nvme.h:132-162`, cross-checked
against m1n1 `src/nvme.c:18-49` which independently redefines the same four
it actually pokes (`CC`, `CSTS`, `AQA`, `ASQ`, `ACQ`) with identical values.

| Offset | Name | Source |
|---|---|---|
| `0x00` | CAP | `nvme.h:132` |
| `0x08` | VS | `nvme.h:133` |
| `0x0c` | INTMS | `nvme.h:134` |
| `0x10` | INTMC | `nvme.h:135` |
| `0x14` | CC | `nvme.h:136`; m1n1 `nvme.c:18` |
| `0x1c` | CSTS | `nvme.h:137`; m1n1 `nvme.c:25` |
| `0x24` | AQA | `nvme.h:139`; m1n1 `nvme.c:32` |
| `0x28` | ASQ | `nvme.h:140`; m1n1 `nvme.c:33` |
| `0x30` | ACQ | `nvme.h:141`; m1n1 `nvme.c:34` |
| `0x1000` | DBS (SQ0TDBL) | `nvme.h:162` — **only used on the non-linear-sq (t8015) path**, see §4 |

`CC.EN` (bit 0), `CC.SHN` (bits 15:14, shutdown notify), `CSTS.RDY` (bit 0),
`CSTS.CFS` (bit 1), `CSTS.SHST` (bits 3:2) are the bits both Linux and m1n1
actually poll/branch on (`nvme.h:207-256`; m1n1 `nvme.c:185-217`) — a model
must get these four bits right. `CAP` is read back and its `MQES`/`TIMEOUT`/
`MPSMIN`/`MPSMAX`/`CSS` fields feed the generic `nvme_enable_ctrl()` core path
(`apple-nvme.c:1162`) — **no reference here states Apple's real CAP value**;
a model needs a plausible synthesized constant (MQES ≥ 63, CSS bit 0 set,
MPSMIN=0) rather than zero, since generic NVMe core code sanity-checks these
fields before enabling.

### 2.2 Apple vendor extension registers (relative to the "NVMe" window — see §2.4 for which reg index that is)

| Offset | Name | Source | Read back / branched on? |
|---|---|---|---|
| `0x1004` | ACQ_DB (admin CQ head doorbell) | Linux `apple-nvme.c:42`; m1n1 `nvme.c:36` | write-only, doorbell |
| `0x100c` | IOCQ_DB (IO CQ head doorbell) | Linux `apple-nvme.c:43`; m1n1 `nvme.c:37` | write-only, doorbell |
| `0x1200` | IOQ_CMDS (IO SQ base, **M4+/nvme-secure-bar only**) | m1n1 `nvme.c:45`, `:432-433` — **absent from Linux driver entirely** | write-only |
| `0x1208` | IOQ_CQES (IO CQ base, **M4+/nvme-secure-bar only**) | m1n1 `nvme.c:46`, `:432-433` — **absent from Linux driver entirely** | write-only |
| `0x1210` | MAX_PEND_CMDS_CTRL | Linux `apple-nvme.c:45`; m1n1 `nvme.c:47`; QAS `ans.c:51`; **confirmed present in our T8140 kernelcache disassembly** (`mov w9, #0x1210` at `IONVMeFamily+0x110878`-ish, see §3) | write-only, tuning value |
| `0x1300` | BOOT_STATUS | Linux `:47`; m1n1 `:39`; QAS `:53`; **confirmed in T8140 disassembly**, magic `0xde71ce55` literally built via `mov`/`movk` right before the offset load | **yes — polled until it equals the magic**, this is load-bearing |
| `0x1304` | MODESEL (QAS-only, never written/read in QAS's own emulation logic, unverified) | QAS `ans.c:59` | **treat as unverified**, no other source mentions it |
| `0x1308` | BASE_CMD_ID (QAS-only, hardcoded readback `0x6000`, no citation in QAS itself) | QAS `ans.c:55-56`, `:148-150` | **treat as unverified / possibly a QAS author's guess** |
| `0x24908` | LINEAR_SQ_CTRL, bit 0 = EN | Linux `:50-51`; m1n1 `:42-43`; QAS `:57-58` | write-only enable bit |
| `0x2490c` | LINEAR_ASQ_DB (write tag to trigger an admin command) | Linux `:53`; m1n1 `:48` | write-only, this **is** the submission mechanism, see §4 |
| `0x24910` | LINEAR_IOSQ_DB (write tag to trigger an IO command) | Linux `:54`; m1n1 `:49` | write-only, ditto |

`0x1210` under the secure-bar flag: our T8140 kernelcache disassembles to a
conditional offset select, `mov w9, #0x1210` / `mov w8, #0x5210` /
`csel w20, w9, w8, eq` gated on a flag byte at struct offset `+0x92a` (bit 0),
immediately followed by an `orr w2, w0, w21, lsl #16` and a virtual call with
`x1 = w20` — i.e. exactly `write32(offset, depth | (depth<<16))`, matching
`MAX_PEND_CMDS_CTRL`'s known semantics from Linux/m1n1, but at **either
`0x1210` or `0x1210+0x4000=0x5210`** depending on a runtime flag. The kext
also carries ivars named `fSecureBaseRegisterMap`/`fSecureBaseAddress`
alongside `fANS2ParityWidgetRegisterMap`/`fANSSHABlockRegMap` (strings-only
evidence, `com.apple.iokit.IONVMeFamily` binary). Best-supported reading:
**within the "NVMe" BAR, offsets `[0, 0x4000)` are a normal alias and
`[0x4000, 0x8000)` are a "secure" shadow**, and which one XNU's driver uses
depends on whether iBoot/SPTM has already locked the low alias (see
`nvme-iboot-sptm-security`, `nvme-secure-reg-layout` DT property names, §3).
This is **not corroborated by m1n1** — m1n1 uses plain `0x1210` unconditionally
even on M4 (`nvme.c:383`), which only bounds this to being an SPTM/XNU
kernel-side behaviour, not a hardware requirement m1n1 itself needs to satisfy.
**A model can almost certainly just implement both `0x1210` and `0x5210`
(and, by extension, the same +0x4000 shift for any other vendor register) as
the same backing storage** and not worry about which alias gets used.

### 2.3 NVMMU registers

| Offset | Name | Source |
|---|---|---|
| `0x28100` | NUM_TCBS (queue depth - 1, shared by both queues) | Linux `apple-nvme.c:56`; m1n1 `nvme.c:51` |
| `0x28108` | ASQ_TCB_BASE (64-bit, admin queue TCB array physaddr) | Linux `:57`; m1n1 `:52` |
| `0x28110` | IOSQ_TCB_BASE (64-bit, IO queue TCB array physaddr) | Linux `:58`; m1n1 `:53` |
| `0x28118` | TCB_INVAL (write tag after consuming a completion) | Linux `:59`; m1n1 `:54` |
| `0x28120` | TCB_STAT (Linux) / `0x29120` (m1n1) — **discrepancy, not resolved by either source, pick one and log a warning if the guest reads the other** | Linux `:60`; m1n1 `:55` |

`apple_nvmmu_tcb` (Linux `apple-nvme.c:104-120`, m1n1 `nvme.c:96-108`, both
`static_assert`-checked at 128 bytes, both agree on the layout up to the AES
region):

```
+0x00 u8  opcode          (always written 0)
+0x01 u8  dma_flags       bit0 FROM_DEVICE, bit1 TO_DEVICE, 0 if no PRP
+0x02 u8  command_id      == tag
+0x03 u8  reserved
+0x04 u16 length           (m1n1 treats this as a u32, functionally compatible
                            since the upper 16 bits are always 0)
+0x06..0x17 reserved
+0x18 u64 prp1
+0x20 u64 prp2
+0x28..0x37 reserved
+0x38 u8[8]  aes_iv
+0x40 u8[64] _aes_unk      unexplored inline-crypto context; our DT's
                           `nvme-ans-sha-present`/`nvme-sha3-*` properties
                           suggest this region differs from M1/A11-era
                           hardware, not modeled by any reference here
```

Read back / branched on: `TCB_INVAL` write must be followed by a `TCB_STAT`
read that's compared against 0 — Linux logs a rate-limited warning if nonzero
(`apple-nvme.c:302-304`), m1n1 does the same (`nvme.c:272-274`). A model can
just always report success (0) here; nothing depends on failure semantics.

### 2.4 Which reg index is which (resolving priority #1)

**This is the single most load-bearing finding in this document, and it
comes from `m1n1/src/nvme.c:311-338`, quoted directly:**

```c
if (adt_get_property(adt, node, "nvme-secure-bar"))
    // M4+ generations have the nvme-secure-bar property and 10+ regs.
    // They use reg[3] for NVMMU registers and reg[9] for NVMe registers,
    // and require extra writes to set up the IO queues.
    nvme_type = NVME_T8132;
else
    // M1-M3 generations use reg[3] for both NVMMU and NVMe registers.
    nvme_type = NVME_T8103;
```

Our real `arm-io/ans` node **has** `nvme-secure-bar` and **has** 13 reg
entries (well over "10+"), so by m1n1's own classification t8140 belongs to
the `NVME_T8132` ("M4+") family: **reg[3] = NVMMU base, reg[9] = NVMe base**,
matching the table in §1. Self-consistency check: `reg[9].base (0x1bdcc0000)
== reg[3].base (0x17dcc0000) + 0x40000000` exactly — the "secure BAR" is a
fixed `+0x40000000` alias of the NVMMU block's own base address, which is a
sanity check this document's DT reading is right, not something stated by any
reference (synthesized from our own DT).

**Open inconsistency, not resolved by any source**: `reg[9]`'s *declared*
length is `0x10000` (64KB), but `LINEAR_IOSQ_DB` alone is at offset `0x24910`
(≈146KB) — past the end of the declared window by ~82KB. Either (a) the DT
length is conservative and doesn't reflect the full backing aperture (a model
should just back a region generously, e.g. `0x30000`+, regardless of what the
DT claims), or (b) `LINEAR_SQ_CTRL`/the linear doorbells actually live inside
`reg[3]` (which is `0x60000`, comfortably large enough) rather than `reg[9]`
on this generation, diverging from m1n1's flat single-`nvme_base` model. **Not
resolvable from any of the four reference bodies — needs either an empirical
MMIO trace (`DARWIN_UNIMP_DEBUG=1` once a device model claims reg[3]/reg[9]
and something else backs the rest) or SPTM/firmware disassembly.**

## 3. What T8140's own kernelcache disassembly adds

Extracted via `ipsw kernel dec` → `ipsw kernel extract ... com.apple.iokit.IONVMeFamily
com.apple.driver.AppleSART` → `llvm-objdump -d` (system `otool -tV` finds
nothing because these kexts put code in `__TEXT_EXEC,__text`, not the
`__TEXT,__text` section `otool -t` looks for by convention). No symbol table
(`nm` reports "no symbols" — fileset kernelcache kexts are locally stripped),
so everything below is "found in the binary," not attributed to a specific
C++ method.

- `0xde71ce55` (BOOT_STATUS magic) is built via `mov w22, #0xce55` /
  `movk w22, #0xde71, lsl #16` immediately before a virtual call passed
  `w1 = 0x1300`, inside a retry loop counting down from `w21 = 0x186a0`
  (100000) — i.e. **BOOT_STATUS is still polled at offset `0x1300` for the
  literal Linux/m1n1 magic on t8140**, high confidence.
- `MAX_PEND_CMDS_CTRL` still resolves to `0x1210` (or `0x5210`, see §2.2) and
  is written with `depth | (depth << 16)`, matching Linux
  `apple-nvme.c:1136-1138` exactly.
- Register offsets for the doorbells (`0x1004`/`0x100c`/`0x2490c`/`0x24910`)
  and the NVMMU TCB-base registers (`0x28108`/`0x28110`) **do not appear
  anywhere in this kext as immediate literals**. Combined with kernelcache
  strings `"pmap_iommu_ioctl - Setting Admin Queue Regs - failed"`,
  `"...Setting IOSQ base addr - failed"`, `"...Setting IOCQ base addr -
  failed"`, `"...NVME_PPL_IOCTL_SET_TCB_ADDR - failed"`,
  `"...NVME_MAP_PAGES_IN_TCB - failed"`, `"...NVME_PPL_IOCTL_RESET_SQ_ENTRY -
  failed"`, `"...NVME_PPL_IOCTL_SET_SQ_ENTRY - failed"`, `"...NVME_PPL_IOCTL_ENABLE_CG
  - failed"`, `"...Setting SHA Regs - failed"` (all found via `strings` on
  the extracted kext, all inside `IONVMeFamily`) — the strong inference is:
  **on t8140, the queue-base-pointer and per-command TCB registers are
  written by SPTM on the kernel's behalf via `_pmap_iommu_ioctl` (a real,
  exported kernel symbol — confirmed in `xnu/config/Private.arm64.exports:95`),
  not by direct MMIO store from `IONVMeFamily` itself.** Plain
  tuning/status registers (`BOOT_STATUS`, `MAX_PEND_CMDS_CTRL`,
  `LINEAR_SQ_CTRL` enable) are still poked directly.
  This is exactly the scenario the Linux driver's own comment anticipates
  without naming SPTM specifically (`apple-nvme.c:93-102`: *"this hardware is
  designed for a kernel that runs NVMMU code in a higher exception level...
  the driver programs the SQE, then executes a hypercall to the code allowed
  to program the NVMMU"*) — SPTM is that hypercall target on t8140.
- Practically, for a device model this probably does not matter: SPTM's own
  binary (`firmware/sptm` / `ipsw_db/.../sptm.t8140.release.im4p`, not
  disassembled in this pass) presumably performs the equivalent raw MMIO
  store to the same physical address once it validates the request — the
  *sequence of register writes a model must satisfy* is unlikely to change,
  only *which guest code path issues them*. This is inference, not proof;
  flagged in §6 as needing SPTM RE if the model doesn't see the writes it
  expects.
- Same `pmap_iommu_ioctl` pattern shows up for SART: kernelcache string
  `KERN_SUCCESS == pmap_iommu_ioctl(&_ppl->super, SART_IOCTL_SET_ACTIVE,
  &activeState, sizeof(activeState), NULL, 0)` inside `com.apple.driver.AppleSART`,
  plus the class hierarchy `IOSARTMapper` (base, has a virtual
  `_setActive(bool)` and macros `rSART_REGION`/`rSART_REGION_BASE` implying
  direct MMIO CONFIG/PADDR-style access — this is presumably what backs the
  plain, non-CoastGuard `sart,t8015`/`sart,t8103`-style SART Linux/m1n1
  model) vs. `IOCoastGuardSARTMapper` (subclass, matches our
  `sart,coastguard` compatible, overrides `iovmMapMemory`/`iovmUnmapMemory`
  and very plausibly `_setActive` to route through `pmap_iommu_ioctl` instead).

## 4. Bring-up sequence

Primary source: **m1n1 `src/nvme.c:304-438`**, m1n1's own `nvme_init()` — this
is a complete, currently-working implementation for real hardware including
the M4+/`nvme-secure-bar` generation our t8140 matches. Cross-checked against
Linux `apple-nvme.c:1038-1220` (`apple_nvme_reset_work()`), which implements
the same sequence for the older t8103/t8015 generations and additionally
covers what generic `nvme_enable_ctrl()`/Identify Controller/Identify
Namespace do on top (standard NVMe, out of scope for the Apple-specific parts
of this doc but required regardless — see §7).

1. **RTKit handshake** — already generic, already working per §0. `iop-ans-nub`
   has `power-managed = 1` (our DT dump, §1), so per `dt_fixup.py`'s own
   comment (`dt_fixup.py:109`, unchanged) RTBuddy self-initiates
   `MGMT_SET_IOP_PWR` without the AP asking — `darwin_asc.c`'s existing
   `rtk_handle_mgmt()` already answers this (`darwin_asc.c:273-287`)
   unconditionally, nothing ANS-specific needed here.
2. **No ANS-specific RTKit endpoint traffic is required.** m1n1's own generic
   `rtkit.c` only knows about the *shared* endpoints (`RTKIT_EP_MGMT=0`,
   `CRASHLOG=1`, `SYSLOG=2`, `DEBUG=3`, `IOREPORT=4`, `OSLOG=8` —
   `rtkit.c:22-27`, numerically identical to `darwin_asc.c`'s own `EP_*`
   enum) and m1n1's `nvme_init()` goes **straight from `rtkit_boot()` to
   polling `BOOT_STATUS` over plain MMIO** — no endpoint besides mgmt is ever
   started for `nvme`/ans (`nvme.c:369-379`). The `"ANS2Endpoint1"` matching
   dictionary seen in kernelcache strings (§0) is presumably XNU/RTBuddy's
   own IOKit-nub-per-started-endpoint naming convention applied to whichever
   of the shared endpoints comes first — not a distinct wire message.
3. **Wait for `BOOT_STATUS == 0xde71ce55`** at NVMe-base + `0x1300`
   (`nvme.c:376`; Linux equivalent `apple-nvme.c:1106-1109`, 1-second
   timeout, polled every 1ms). This is the first point at which the
   coprocessor firmware itself (not just RTKit) is confirmed alive.
4. **Enable linear submission queues**: write `LINEAR_SQ_CTRL_EN` (bit 0) to
   NVMe-base + `0x24908` (`nvme.c:382`; Linux `:1132-1133`, gated behind
   `has_lsq_nvmmu` which is `true` for every generation newer than t8015).
5. **Set max pending commands**: write `(depth-1)|((depth-1)<<16)` to
   NVMe-base + `0x1210` (`nvme.c:383-384`; Linux `:1136-1138`) — depth is
   `nvme-queue-entries` from our DT = `0x40` (64).
6. **Configure NVMMU**: write `depth-1` to NVMMU-base + `0x28100`, then the
   admin and IO TCB array physical base addresses (64-bit) to NVMMU-base +
   `0x28108`/`+0x28110` (`nvme.c:385-387`; Linux `:1141-1157`). On t8140 this
   step is plausibly SPTM-mediated per §3 — a model should accept the write
   regardless of which guest PC issues it.
7. **Standard NVMe admin-queue bring-up**: disable controller (clear
   `CC.EN`, wait `CSTS.RDY==0`), write `ASQ`/`ACQ`/`AQA`, set `CC.EN`, wait
   `CSTS.RDY==1` (`nvme.c:390-400`; Linux `:1145-1165`, going through the
   generic `nvme_enable_ctrl()`).
8. **Identify Controller / Identify Namespace** via the generic NVMe admin
   queue (`nvme_init_ctrl_finish`, Linux `:1180`) — standard NVMe protocol,
   not Apple-specific; see §7 for what this requires of the model.
9. **Create IO CQ then IO SQ** via standard `nvme_admin_create_cq`/`create_sq`
   admin commands, `NVME_QUEUE_CONTIGUOUS` flag, executed through the
   Apple submission mechanism itself (§4a below) since even admin commands go
   through the TCB/tag scheme (`nvme.c:403-426`; Linux `:1184-1201`).
10. **M4+/`nvme-secure-bar`-only extra step**: after CreateIOCQ/CreateIOSQ
    succeed, write the IO queue's CQE and SQE array base addresses *again*,
    directly, to NVMe-base + `0x1208`/`+0x1200` (`nvme.c:428-434`). m1n1's own
    comment: *"Extra write required to set up the IO queues on M4+. Otherwise
    the ANS crashes and the crashlog says `I/O SQ: 0x0` / `I/O CQ: 0x0`."*
    **This step does not exist in the Linux driver at all** (Linux never
    writes `0x1200`/`0x1208`) — it is new precisely for the generation our
    `nvme-secure-bar` property places t8140 in. If a model's guest hangs or
    the (virtual) coprocessor firmware "crashes" right after IO queue
    creation, this is the first thing to check.
11. Controller live; standard NVMe Read/Write/Flush commands follow, submitted
    via §4a.

**4a. Submission mechanism (the actual protocol divergence)** — described
fully in §5 below, but the short version used at every step above: instead of
writing a full 64-byte SQE into a ring and bumping a tail-index doorbell, the
driver writes the SQE into `queue->cmds[tag]` (an array indexed by tag, not a
ring), writes/validates a matching `apple_nvmmu_tcb` at `tcbs[tag]`, then
triggers execution by writing the **tag** (not an index) to
`LINEAR_ASQ_DB`/`LINEAR_IOSQ_DB` (`nvme.c:219-302`; Linux
`apple_nvme_submit_cmd_t8103()`, `apple-nvme.c:328-363`).

## 5. NVMe protocol divergences from spec

All of these are Apple-specific and none are covered by generic
`hw/nvme/*` — enumerated with citations, ordered roughly by how load-bearing
they are for a model:

1. **Linear submission "queue" is really a tag-indexed array, not a ring.**
   The SQE for tag N always lives at `cmds[N]`; there is no head/tail wrap
   logic on the submission side at all. Confirmed by our DT's
   `nvme-linear-sq` property and by both Linux (`apple-nvme.c:122-149`
   comment) and m1n1 (`nvme.c:110-119`, `nvme.h` struct).
2. **Submission is triggered by writing the tag value** to a dedicated
   doorbell register (`LINEAR_ASQ_DB`/`LINEAR_IOSQ_DB`), not by writing a new
   tail index to a standard NVMe SQyDBL register. `NVME_REG_DBS` (`0x1000`)
   is used only on the *non*-linear-sq (t8015/A11) fallback path
   (`apple-nvme.c:1509-1513`) — irrelevant to t8140.
3. **Every command, including admin commands, must have a matching
   `apple_nvmmu_tcb` entry** at `tcbs[tag]` before the doorbell write, with
   PRP1/PRP2 duplicated from the SQE and a DMA direction flag
   (`apple-nvme.c:328-347`; `nvme.c:219-241`). On t8140 this programming step
   is plausibly SPTM-mediated (§3) but the *data* a model needs to trust is
   unchanged.
4. **Combined admin+IO tag space, capped at 64.** Linux's own comment:
   *"Both the admin and IO queue share the same tag space. Additionally,
   tags cannot be higher than 0x40 which effectively limits the combined
   queue depth to 0x40"* (`apple-nvme.c:62-71`). Linux's own driver chooses
   `APPLE_NVME_AQ_DEPTH = 2` to leave more of that shared space to IO
   (`:71-72`) — **this is Linux's own optimization, not a hardware
   requirement**: m1n1 allocates a full 64-entry admin queue with no problem
   (`nvme.c:16`, used for both `adminq`/`ioq`). What XNU's own AQA value is
   has not been captured (needs an empirical trace) — a model should honor
   whatever the guest writes rather than hardcoding either reference's choice.
5. **No AEN (Asynchronous Event Notification) support** — Linux explicitly
   notes it doesn't need to reserve tag space for `NVME_NR_AEN_COMMANDS`
   because the controller doesn't support it (`apple-nvme.c:68-69`). A model
   doesn't need to implement AEN.
6. **Single shared IRQ line for both admin and IO CQ**, on a dedicated AIC
   vector separate from the RTKit mailbox IRQs (§1, `nvme-interrupt-idx`),
   handled by one ISR that polls both queues (`apple-nvme.c:707-723`).
7. **Completion requires an explicit NVMMU TCB invalidation** (`TCB_INVAL`
   write of the completed tag) before that tag can be reused — not present
   in the NVMe spec at all (`apple-nvme.c:297-305`; `nvme.c:272-274`).
8. **M4+/secure-bar generation needs the extra IO-queue-base writes** at
   `0x1200`/`0x1208` after CreateSQ/CreateCQ (§4 step 10) — the sharpest
   version-specific divergence found, and the one most likely to bite if a
   model is built purely off the Linux driver without checking m1n1.
9. **`nvme-tl-wa` and `nvme-num-sl` are present in our DT but referenced by
   *neither* Linux nor m1n1 nor found anywhere in our own `IONVMeFamily`
   kext's strings** — see §6, likely IOP-firmware-side, not AP-driver-side.

## 6. SART spec

Register layout — **three independent sources agree** (Linux
`linux-apple-repo/drivers/soc/apple/sart.c:52-62,152-169`; m1n1
`m1n1/src/sart.c:45-56,137-164`; QAS `qas/hw/arm/apple-silicon/sart.c:69-115`),
v3 (our `sart-version = 3`):

| Offset | Field | Notes |
|---|---|---|
| `0x00 + 4*i` | CONFIG[i] | whole u32 is the "flags" byte per Linux/m1n1 comment: *"probably a bitfield but the exact meaning of each bit is unknown"* — allow value used by the driver is `0xff` |
| `0x40 + 4*i` | PADDR[i] | physical address `>> 12` |
| `0x80 + 4*i` | SIZE[i] | size `>> 12`, 30-bit max field |

16 entries max (`APPLE_SART_MAX_ENTRIES = 16`, all three sources). m1n1 also
knows a **v4** layout (`CONFIG@0x00`, `PADDR@0x60`, `SIZE@0xc0`,
`m1n1/src/sart.c:57-67`) for something even newer than what our
`sart-version=3` claims — **not applicable to t8140 per our own DT, but worth
knowing the number space isn't exhausted at 3.**

Programming model (same across all three sources,
`sart.c:271-303`/Linux, `nvme.c:272`/m1n1 usage,
`sart.c:117-148`/QAS's IOMMU-shaped reimplementation): find a free entry,
write PADDR then CONFIG (order matters in Linux/m1n1: PADDR first, then the
CONFIG write that actually enables the entry — QAS's `base_reg_write`
re-derives entries on *every* register write instead, order-independent).
`apple_sart_add_allowed_region()`/`sart_add_allowed_region()` do exactly this
for whatever physical range needs DMA access; no remapping, pure allow/deny
(Linux comment `sart.c:6-11`: *"no remapping can be done"*).

**What SART is used for, on the NVMe path specifically**: not just the
NVMMU's own PRP validation — Linux's `apple-nvme.c` also uses SART to approve
the **RTKit shared-memory buffers** (crashlog/syslog/ioreport DMA regions)
via `apple_sart_add_allowed_region()`/`_remove_allowed_region()` wired as the
`shmem_setup`/`shmem_destroy` RTKit ops (`apple-nvme.c:256-295`). This is
generic RTKit shmem plumbing, potentially relevant to every coprocessor
(DCP included), not ANS-specific — out of my lane to say whether
`darwin_asc.c` already handles it; worth a note to whoever writes the model,
since DCP bring-up reaching as far as it has (per `docs/re/dcp-bringup.md`)
suggests it might not be blocking, but ANS's IOP firmware may be more eager
to request a crashlog/syslog buffer early.

**CoastGuard-specific additions — not covered by Linux, m1n1, or QAS at
all**, only visible via our own DT + kernelcache strings:

- `sart-power-managed` (boolean), `sart-power-reg-offset = 0x13e8`,
  `power-canary-offset = 0x0` — both offsets land inside reg[0] (§1), not a
  separate window.
- Kernelcache string evidence (`com.apple.driver.AppleSART`): base class
  `IOSARTMapper` has a virtual `_setActive(bool)`; subclass
  `IOCoastGuardSARTMapper` (matches our `sart,coastguard` compatible) very
  plausibly overrides it to call `pmap_iommu_ioctl(..., SART_IOCTL_SET_ACTIVE,
  ...)` instead of a raw register poke (string:
  `"KERN_SUCCESS == pmap_iommu_ioctl(&_ppl->super, SART_IOCTL_SET_ACTIVE,
  &activeState, sizeof(activeState), NULL, 0)"`).
- **Empirically**: `IOCoastGuardSARTMapper` already registers as a live
  IOService in our sandbox today against `darwin-unimp`'s all-zero MMIO
  fallback (`/tmp/dvm/probe/iodeep.serial.log:331`), with no device model at
  all. That's a strong hint the power/canary reads tolerate zero and don't
  need precise modeling just to get past `start()` — though this hasn't been
  confirmed to survive an actual `apple_sart_add_allowed_region()`-equivalent
  call yet, since NVMe hasn't gotten that far in any run so far (§0).
- `sart-ans` reg[2] (`0x17dcc0000`, `0x4000`) shares its **exact base
  address** with `ans` reg[3] (`0x17dcc0000`, `0x60000`, identified as
  NVMMU in §2.4) — synthesized purely from cross-referencing our own DT, not
  stated by any external source, but a strong structural hint that NVMMU and
  SART are the same or adjacent hardware on this generation.

## 7. QAS's prior model — useful for wiring, not for protocol

`qas/hw/block/apple-silicon/ans.c` targets t8030 (A13/iPhone 11), a generation
Linux's driver doesn't even name (Linux's oldest entry is t8015/A11,
`apple-nvme.c:1711`) and m1n1 doesn't cover either. Two things worth pulling
from it, and one large caveat:

- **Useful**: the RTKit wiring shape (`apple_ans_from_node()`,
  `qas/hw/block/apple-silicon/ans.c:215-286`) — `reg[1]` (not `reg[0]`) is
  passed as a *size* to `apple_rtkit_new`, because QAS's flat `reg[]` array is
  addr/size pairs (`reg[0]`=addr, `reg[1]`=size of window 0, `reg[2]`=addr,
  `reg[3]`=size of window 1, ...) — i.e. QAS's "reg[1]" is our "reg[0].len".
  Our own `darwin_asc.c` already made the equivalent choice (`reg[0].len` as
  `mmio-size`, `darwin_asc.c:514`), so nothing to change here, just confirms
  the pattern is the standard one.
- **Useful**: `NVME_APPLE_BOOT_STATUS = 0x1300` / `_BOOT_STATUS_OK =
  0xde71ce55` / `NVME_APPLE_MAX_PEND_CMDS = 0x1210` (`ans.c:51-58`) agree with
  every other source, good independent confirmation those two offsets are
  stable across at least four SoC generations (t8030 through t8140).
- **Caveat, load-bearing**: QAS's `ans.c` **does not implement the Apple
  linear-SQ/NVMMU submission mechanism at all**. It instantiates a full
  generic QEMU `TYPE_NVME` PCIe controller (`hw/nvme/nvme.h`) behind an alias
  window, adds a `is-apple-ans` boolean property that only affects namespace
  `nstype` (`qas/hw/nvme/ns.c:773`, confirmed via `grep is_apple_ans` — no
  other use anywhere in `hw/nvme/`), and overlays exactly three readback
  registers (`MAX_PEND_CMDS`, `BOOT_STATUS`, and a **guessed, uncited**
  `BASE_CMD_ID = 0x6000`). Standard PCI-style SQ-tail doorbells and a real
  `hw/nvme` command executor do all the actual work underneath. This
  reproduces enough for whichever older/DART-routed XNU controller class
  t8030 uses (plausibly `AppleANS2NVMeController`/`AppleANS2DARTNVMeController`
  — both of which **fail probe on our t8140 DT**, §0) but is **not a
  behavioral reference for the linear-SQ/NVMMU protocol** our t8140 target
  actually needs (`AppleANS2CGv2Controller`/`AppleANS3CGv2Controller`, which
  won the match, §0). Use QAS only for the wiring shape and the two
  cross-confirmed constants; do not extrapolate its submission mechanics.
- Same caveat for `qas/hw/arm/apple-silicon/sart.c`: implements SART as a
  real `IOMMUMemoryRegion` with full translate callbacks — a reasonable
  simplification (translated_addr == addr always, i.e. pure allow-list, no
  remap — matches the "no remapping" comment in Linux `sart.c:6-11`), and its
  v1/v2/v3 register math matches Linux/m1n1's independently. No CoastGuard
  awareness at all, same as Linux/m1n1.

## 8. Version sensitivity summary

| Fact | t8015 (A11) | t8103 (M1, Linux's default) | t8132/"M4+" (m1n1's `NVME_T8132`) | t8140 (ours) |
|---|---|---|---|---|
| Submission mechanism | plain SQ-tail doorbell, no NVMMU | linear-SQ + NVMMU (tag doorbell) | linear-SQ + NVMMU | **linear-SQ + NVMMU** (`nvme-linear-sq` present in our DT) |
| Queue depth | 16 | 64 | 64 (m1n1 hardcodes `NVME_QUEUE_SIZE=64`) | **64** (`nvme-queue-entries=0x40` in our DT — directly confirmed, not inferred) |
| NVMe register window | `reg[3]` (shared with NVMMU) | `reg[3]` (shared with NVMMU) | separate: NVMMU=`reg[3]`, NVMe=`reg[9]` | **inferred same as t8132** by the `nvme-secure-bar` criterion m1n1 itself uses — not independently confirmed for t8140/A18 specifically |
| Extra IO-queue-base writes (`0x1200`/`0x1208`) | no | no | **yes** (m1n1) | inferred yes, same basis as above |
| `0x1210`/`0x5210` secure-bar register aliasing | n/a | n/a | not observed by m1n1 (uses `0x1210` unconditionally even on M4) | **observed in our own kernelcache disassembly** — appears to be an XNU/SPTM-side behaviour, not a hardware requirement m1n1 needs to satisfy |
| Controller class in XNU | — | plausibly `AppleANS2NVMeController`/`AppleANS2DARTNVMeController` (score lower, or fail, on our DT) | — | **`AppleANS3CGv2Controller`, confirmed by empirical probe score (500000, highest)** |
| SART variant | `sart,t8015`, v0 register layout | `sart,t8103` or similar, v2/v3 | unknown | `sart,coastguard`, v3 register layout **plus** an unmodeled power/canary extension |

Everything in the "t8140 (ours)" column that says "inferred" rather than
"confirmed" is inferred by the *same criterion* (`nvme-secure-bar` presence)
that m1n1 itself uses to switch behavior — i.e. this document is not
extrapolating blindly across generations, it's applying m1n1's own
generation test to our own DT. But t8140/A18 was not literally what m1n1 was
validated against (M4 Macs), so treat "inferred" rows as the first thing to
verify empirically once a model exists.

## 9. What's not covered here — needs firmware RE or empirical testing

- **The ANS coprocessor's own RTKit firmware** (not part of the AP
  kernelcache — loaded by iBoot as a separate image before XNU boots, same as
  every other IOP). None of the four reference bodies cover its internal
  behavior beyond the wire protocol; properties that look IOP-firmware-consumed
  rather than AP-driver-consumed and are **not referenced anywhere in our own
  `IONVMeFamily`/`AppleSART` kext strings** (checked via `strings` on the
  extracted kexts): `nvme-num-sl`, `msp-bfh-params`, `msp-phy-fw-path`,
  `tunable-table-bundle`, `nand-debug`. `nvme-tl-wa` is also absent from
  those two kexts' strings specifically, though present kernelcache-wide —
  worth double-checking with a wider kext search if it matters.
- **SPTM's own implementation of `pmap_iommu_ioctl` for the NVMe/SART
  ioctls** (`sptm.t8140.release.im4p`, not disassembled this pass) — needed
  to confirm the §3 hypothesis that SPTM performs equivalent raw MMIO stores
  rather than something a device model needs to special-case.
- **Exact `CAP` register value** Apple's real hardware reports — no source
  states it; needs either real-hardware capture or acceptance of a
  synthesized plausible value (§2.1).
- **`reg[1]`, `reg[4]`, `reg[5]`, `reg[6]`, `reg[10]`, `reg[11]`, `reg[12]`**
  on `ans`, and `sart-ans` `reg[1]` — unidentified, best-guesses only in §1,
  none corroborated by any of the four reference bodies.
- **The `0x24910` vs `reg[9]`-length inconsistency** (§2.4) — first thing to
  resolve empirically once any part of this is wired up.
- **Whether `IONVMeFamily`'s `start()` for `AppleANS3CGv2Controller` ever
  gets called** in our sandbox — every probe run so far stops at the probe
  score line (§0); nothing here explains why, since `-enable ans` currently
  wires only the generic RTKit wrapper with no NVMe MMIO window backing at
  all (so there's nothing yet to observe a `start()` failure against).
- **AES/inline-crypto TCB region** (`_aes_unk[64]`, TCB offset `0x40`) and
  the SHA3/CoastGuard crypto registers hinted at by `fANSSHABlockRegMap`/
  `nvme-sha3-*` properties — unexplored by every source here.

## 10. Recommended minimum viable subset for read-only block access

Goal is booting a root filesystem, not full driver conformance. Based on the
above:

1. **Don't touch `darwin_asc.c`.** ANS's RTKit handshake needs nothing
   beyond what it already does generically (§4 step 1-2) — confirmed both by
   m1n1's own minimal `nvme_init()` and by our empirical boot logs already
   completing the handshake today with `ops=NULL`.
2. **Write `darwin_ans.c`** following the `darwin_dcp.c` shape
   (`darwin_asc_create(node, iobase, aic, eps, n_eps, &ans_ops, d)` with
   `eps` empty — no ANS-specific endpoints exist per §4) plus additional
   `sysbus_init_mmio` regions for:
   - reg[3] (NVMMU, `0x60000` at `0x17dcc0000`) — implement `0x28100`
     (NUM_TCBS), `0x28108`/`0x28110` (TCB bases, accept the write regardless
     of whether it arrives via a "normal" guest PC or an SPTM one), `0x28118`/
     `0x28120` (TCB_INVAL/STAT, always report success).
   - reg[9] (NVMe, back at least `0x30000` regardless of the DT's `0x10000`
     — see the §2.4 open inconsistency): standard `CAP`/`VS`/`CC`/`CSTS`/
     `AQA`/`ASQ`/`ACQ`, `BOOT_STATUS` (return the magic once "booted"),
     `MAX_PEND_CMDS_CTRL` (log-only), `LINEAR_SQ_CTRL` (log-only, or gate
     boot-status on it if being strict), the three doorbells (these *are*
     the command triggers, §5), and `0x1200`/`0x1208` (log-only, but present
     — omitting them risks nothing since nothing reads them back, but the
     m1n1 comment about the ANS "crashing" without them describes real
     firmware behavior we're not emulating, not a readback check XNU makes).
   - the `+0x4000` alias of every offset in reg[9] (§2.2), backed by the same
     storage as the unshifted offset.
3. **Write a `darwin_sart.c`** implementing v3 CONFIG/PADDR/SIZE
   (`0x00`/`0x40`/`0x80` + `4*i`, 16 entries) as an actual allow-list (or,
   more simply for a first cut, unconditionally allow everything — SART is
   an address filter for *this emulated device's own* DMA, and QEMU's DMA
   already goes through host RAM directly; the filter only matters for
   guest-visible failure modes XNU checks, which for read-only boot is just
   "does the driver see the region get approved," not real isolation).
   Model `sart-power-reg-offset`/`power-canary-offset` as logged no-ops
   first — the empirical evidence in §6 suggests zero-readback already gets
   `IOCoastGuardSARTMapper` through `start()`.
4. **Implement the tag-indexed submission mechanism** (§4a, §5.1-5.3): a
   command array indexed by tag (not a ring), a `LINEAR_ASQ_DB`/
   `LINEAR_IOSQ_DB` write-of-tag as the trigger, and standard NVMe command
   execution (Identify Controller/Namespace, Read) against a QEMU block
   backend underneath — i.e. reuse `hw/nvme`'s command semantics (as QAS
   does) but replace its ring/doorbell submission path with this one, rather
   than adopting QAS's PCI-doorbell approach wholesale (§7 caveat).
5. Skip: AEN, multiple IO queues (`nr_io_queues` is always 1, Linux
   `apple-nvme.c:1194`), CMB/PMR, namespace management beyond NSID 1 (our DT's
   first `namespaces` entry, §1), any of the SHA3/CoastGuard crypto registers,
   and the SPTM-mediated ioctl path itself — treat those registers as logged
   no-ops per CLAUDE.md's rule (log what's ignorable, only model what's read
   back and branched on).
6. First empirical checkpoint once this exists: rerun with
   `DARWIN_UNIMP_DEBUG=1` and confirm *nothing* under `/arm-io/ans` or
   `/arm-io/sart-ans` still falls through to `darwin-unimp` except the
   explicitly-unresolved reg windows from §1/§9 — any surprise hit there is
   this document's next correction.
