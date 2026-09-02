# vmapple vs SoC emulation — is Apple's virtual device model a shorter path to display?

**Recommendation up front: no, do not pivot. Keep going with DCP emulation on the
`darwin` (t8140) SoC model.** The vmapple/vphone-cli path is closed for reasons
that are independent of how much effort we put in — it needs Apple proprietary
host code that QEMU/TCG does not have and cannot legally or practically
reimplement to the depth required (Secure Enclave virtualization), and the one
piece that QEMU *does* implement (the outer vmapple device model) has never
booted anything past macOS 12 and has no path to iOS. Details and the evidence
below.

Local checkout referenced throughout: `/Users/jdolbe1/Downloads/darwin-vm`.
Clones made for this investigation:
- `/tmp/dvm/ref/vphone-cli` — `github.com/Lakr233/vphone-cli` @ `87f796c6`, 2026-09-01
- `/tmp/dvm/ref/xnu` — `github.com/apple-oss-distributions/xnu` (already present locally)

---

## 1. Does iOS boot on Apple's virtual device model, or only macOS? — this is the crux

**Neither, directly.** Two separate things both get called "Apple's virtual
device model" and they must not be conflated:

1. **QEMU's `vmapple` machine** (`qemu-sptm/hw/vmapple/vmapple.c`) — an
   independent, from-scratch reimplementation of the *device model that
   Virtualization.framework exposes*: GIC, PL011, virtio-blk, a config-region
   MMIO device, an AES engine, and (only when built against the real host
   framework) a display device. Header comment, `qemu-sptm/hw/vmapple/vmapple.c:1-16`:
   > "VMApple is the device model that the macOS built-in hypervisor called
   > Virtualization.framework exposes to Apple Silicon macOS guests... does
   > not use any code from Virtualization.Framework."
   QEMU's own docs are blunt about its ceiling: **"Currently only macOS 12.x
   guests are supported... newer versions than 12.x are currently NOT
   supported on the guest side."** (https://www.qemu.org/docs/master/system/arm/vmapple.html,
   fetched 2026-09-02). It has never booted iOS, has no SEP device at all
   (confirmed by grep — nothing under `hw/vmapple` references SEP, `grep -rn SEP
   hw/vmapple` is empty), and the patch series that added it to upstream QEMU
   landed in January 2025 with no subsequent progress reported past macOS 12
   (patch v17, https://lists.gnu.org/archive/html/qemu-arm/2025-01/msg00328.html).
   **This is not our target.**

2. **Apple's actual Virtualization.framework**, driven by `vphone-cli`, boots a
   real iOS UI — but not by booting a stock iPhone IPSW unmodified. It boots a
   **hybrid** of three distinct firmware sources, per
   `/tmp/dvm/ref/vphone-cli/research/firmware_manifest_and_origins.md:1-13`:
   - **PCC `vresearch101ap`** — boot chain (LLB/iBSS/iBEC/iBoot) and security
     monitors (SPTM/TXM), `ApBoardID 0x90`
   - **PCC `vphone600ap`** — runtime components (DeviceTree, SEP firmware,
     KernelCache, RecoveryMode), `ApBoardID 0x91`
   - **A real iPhone IPSW** (`iPhone17,3`) — the actual OS image, apps, trust
     caches
   "PCC" here is Apple's **Private Cloud Compute** security-research
   infrastructure. Both `vresearch101ap` and `vphone600ap` are Apple's own
   internal "virtual iPhone" hardware device classes, shipped as part of the
   **CloudOS IPSW**, which Apple publishes on its own CDN with no login gate:
   `scripts/fw_prepare.sh:19`:
   ```
   DEFAULT_CLOUDOS_SOURCE="https://updates.cdn-apple.com/private-cloud-compute/399b664dd623358c3de118ffc114e42dcd51c9309e751d43bc949b98f4e31349"
   ```
   So: **yes, Apple ships a VM-targeted iOS variant** (`vphone600ap`/`vresearch101ap`),
   and it is publicly obtainable — but it is not the IPSW a real iPhone runs,
   and mixing it with a real iPhone's OS image is exactly why `vphone-cli` needs
   between 4 and 141 binary patches depending on variant (README.md "Firmware
   Variants" table) to reconcile mismatched identities and bypass signature/AMFI
   checks that would otherwise reject the Frankenstein image.

**The deeper reason a real IPSW's kernelcache can never boot on either vmapple
or vphone600 unmodified**: XNU compiles a structurally different kernel for
this board. `pexpert/pexpert/arm64/VMAPPLE.h:29-37` (in the public
`apple-oss-distributions/xnu` tree):
```c
#define NO_MONITOR                1
#define HAS_PARAVIRTUALIZED_CTRR  1
#define VMAPPLE                   1
#define APPLEVIRTUALPLATFORM      1
...
#define HAS_PARAVIRTUALIZED_PAC   1
```
`NO_MONITOR=1` means this kernel target has **no SPTM/TXM at all** — the exact
security monitor stack this project spent significant effort emulating (see
`docs/re/dcp-iop-start.md`, `docs/re/txm-selectors.md`). Instead, CTRR (the
read-only-text enforcement our SPTM currently gates on) and PAC key management
are done via `hvc` calls trapped by the **host's** EL2/Hypervisor.framework —
`osfmk/arm64/arm64_hypercall.c:38-56` shows the raw `hvc #0` trap, and the
actual ABI is in `osfmk/arm64/hv_hvc.h:47-53`:
```c
#define VMAPPLE_PAC_SET_INITIAL_STATE          (HVC_CPU_SERVICE | 0x0)
#define VMAPPLE_PAC_GET_DEFAULT_KEYS           (HVC_CPU_SERVICE | 0x1)
#define VMAPPLE_PAC_SET_A_KEYS                 (HVC_CPU_SERVICE | 0x2)
#define VMAPPLE_PAC_SET_B_KEYS                 (HVC_CPU_SERVICE | 0x3)
#define VMAPPLE_PAC_SET_EL0_DIVERSIFIER        (HVC_CPU_SERVICE | 0x4)
#define VMAPPLE_PAC_SET_G_KEY                  (HVC_CPU_SERVICE | 0x6)
```
A real d47ap/t8140 iPhone kernelcache is compiled *without*
`APPLEVIRTUALPLATFORM` and *with* SPTM/TXM — it will never run on a VMAPPLE-
config kernel's boot chain, and a `vphone600ap` kernelcache will never run on
real (or realistically-emulated) t8140 hardware. This is a compile-time board
config, not a device-tree flag, so there is no shortcut through it.

**Verdict on Q1**: iOS boots on Apple's real Virtualization.framework only
because Apple ships a *separate, publicly-downloadable* kernelcache/DT/SEP set
purpose-built for a paravirtualized board — not because a stock iPhone IPSW
"just boots" there. QEMU's own reimplementation of that virtual hardware
(`vmapple`) has no SEP, no PAC hypercall responder, and has never gotten past
macOS 12.

---

## 2. What does it take to make iOS boot there, and what does the guest think it is?

Per `research/firmware_manifest_and_origins.md:56-64` and
`research/0_binary_patch_comparison.md:46-119`:

| Component | Source | Patch |
|---|---|---|
| AVPBooter | `vresearch101ap` — `/System/Library/Frameworks/Virtualization.framework/Versions/A/Resources/AVPBooter.vresearch1.bin` (host's **built-in** framework resource, `sources/VPhoneCore/VPhoneBundleOps.swift:27-33`) | 1 patch: DGST signature-validation bypass, `mov x0, #0` (`sources/FirmwarePatcher/AVPBooter/AVPBooterPatcher.swift:1-14`) |
| AVPSEPBooter | Same framework Resources dir, `AVPSEPBooter.vresearch1.bin` — plus a 512 KB `SEPStorage` backing file created per-VM (`VPhoneBundleOps.swift:57`) | not found to be patched in the surveyed source |
| iBSS/iBEC/LLB | `vresearch101ap` release | serial labels, image4-verify bypass, boot-args redirect, ramdisk/rootfs/panic bypasses (`0_binary_patch_comparison.md:54-80`) |
| iBoot | `vresearch101ap` **research** identity | unpatched — only the research identity carries an iBoot at all |
| TXM | `vresearch101ap` | trustcache bypass, selector24/41/42 bypasses, debugger + dev-mode entitlement grants (12 patches for `dev`/`jb`) |
| DeviceTree, SEP, RecoveryMode | `vphone600ap` **release**, byte-identical, unpatched | `dt=1` (boots without a system keybag) — this is *why* vphone600 is used for runtime instead of vresearch101 (`dt=0` there is fatal, `firmware_manifest_and_origins.md:127-139`) |
| KernelCache | `vphone600ap` **research** | 25+ dynamic patches: APFS seal bypass, sandbox MACF stubs, launch-constraint bypass, `PE_i_can_has_debugger`, etc. (`0_binary_patch_comparison.md:99-119`) |
| OS image / apps / trustcache | real `iPhone17,3` IPSW | dozens more patches to reconcile ABI/identity mismatches with the substituted kernel (e.g. DiskImages2 ABI v9-vs-v11 gate, `0_binary_patch_comparison.md:163`) |

**Device tree**: comes from `vphone600ap`'s own `DeviceTree.vphone600ap.im4p`
— a genuinely different tree, not our `t8140` tree with nodes deleted. Its
`arm-io` node reports `device_type = "vresearch1-io"` and
`soc-generation = "VResearch1"` by default; `sources/FirmwarePatcher/DeviceTree/DeviceTreePatcher.swift:246-272`
patches these to `"t8140-io"`/`"H17"` purely so **userland** identity checks
(that read these strings) pass — it does not add any t8140 IP blocks. The only
"hardware" nodes added under `/arm-io` are five minimal camera-flag stubs
(`isp`, `ispRtb`, `smc/iop-smc-nub/smc-ext-charger`) carrying nothing but
`camera-front`/`camera-rear`/`camera-driver` properties
(`0_binary_patch_comparison.md:600-615`) — there is no DCP, AIC, ASC, or DART
node in this tree at all.

**Guest identity**: `ApBoardID` reported in DFU is `0x90` (`vresearch101ap`);
runtime `hardware target` reports `vphone600ap`
(`firmware_manifest_and_origins.md:9-13, 62`). It does not present as any real
iPhone board id to the boot chain; only userland-facing strings are patched to
resemble D47AP/iPhone17,3.

**Host constraint**: SIP relaxation and an AMFI boot-arg (or the narrower
`amfidont` allowlist route) are required on the host to run the unsigned/
custom-entitled tooling (`README.md` "SIP/AMFI Relaxation" section). Per this
task's rules, none of that was touched — this is reported, not performed.

---

## 3. How does display work there, and is it our target instead of DCP?

**No — it is a different backend entirely, and confirms our current target
(IOMobileFramebuffer) is the right API layer, but tells us nothing new about
implementing DCP.**

The vphone600 kernel drives its virtual display through a kernel driver named
**`AppleParavirtGPU`**, fed by the host's Virtualization.framework paravirtual
GPU (Apple's `ParavirtualizedGraphics.framework`), *not* by `AppleCLCD2`/DCP.
Documented directly in `scripts/patchers/cfw_patch_iomfb_force_kern.py:1-13`:
> "on the vphone600 26.x kernel the host VZ display is fed by the guest
> `AppleParavirtGPU` scanout, which is driven ONLY by the IOMFB userclient
> swap methods — the `_kern_Swap*` family (`_kern_SwapEnd` == external method
> 5)."

The userland API surface is still the public `IOMobileFramebuffer` — the same
one `AppleCLCD2` sits behind on real hardware — so the *presence* of that API
(`SwapBegin`/`SwapEnd`/`SwapSetLayer`, `IOMobileFramebufferUserClient` external
method 5) is validated as the correct target for a display driver regardless
of SoC. But the **backend implementation is completely different**: no AFK
ring transport, no EPIC framing, no DCP coprocessor firmware at all —
`AppleParavirtGPU` is presumably a much smaller in-kernel driver that talks
directly to a paravirtual GPU MMIO/doorbell interface designed for a
hypervisor, analogous to `qemu-sptm/hw/display/apple-gfx-mmio.m`. This is
corroborated by the `checkStructureInputSize` details being kernel-version-
specific rather than firmware-protocol-specific (`cfw_patch_iomfb_swapend.py:1-27`),
and by iOS 27 introducing a *second* swap path (`_virt_Swap*`, an in-process
callback with no userclient call at all) that vphone-cli has to force back
onto `_kern_Swap*` to get anything on screen (`cfw_patch_iomfb_force_kern.py`).

**None of this reduces the amount of DCP protocol RE this project needs.**
It is orthogonal work against an entirely different driver stack that only
exists because the vphone600 kernel target has no DCP/AFK/EPIC code paths to
begin with — it was compiled for a machine that doesn't have that hardware.

For what it's worth: the display device the vphone600 kernel's
`AppleParavirtGPU` talks to *is* architecturally the same thing QEMU's
`apple-gfx-mmio.m` implements — see §4 for why that still doesn't help us.

---

## 4. Would QEMU's `vmapple` machine be usable?

**No — it fails on this exact host/toolchain before the SEP question even
comes up, and the SEP question is unsolved by anyone publicly.**

**a. It requires HVF, not TCG.** `qemu-sptm/hw/vmapple/Kconfig:15-16`:
```
config VMAPPLE
    bool
    depends on ARM
    depends on HVF
```
This is a *build-time* Kconfig gate: the whole machine only compiles into
builds that target the HVF accelerator. Our build is `aarch64-softmmu` with no
HVF requirement — `CONFIG_VMAPPLE` correctly does not appear in
`qemu-sptm/build/config-devices.mak` (verified: `grep` returns nothing), and
`-M help` on our built binary lists `darwin`, `virt-*`, `sbsa-ref` — no
`vmapple` (verified directly against the built binary).

**b. Its one genuinely new device (the display) is macOS-host-only Objective-C
against a proprietary framework, gated even harder than the machine itself.**
`qemu-sptm/hw/display/meson.build:65-66`:
```
system_ss.add(when: [pvg, 'CONFIG_MAC_PVG_PCI'], if_true: [files('apple-gfx.m', 'apple-gfx-pci.m')])
system_ss.add(when: [pvg, 'CONFIG_MAC_PVG_MMIO'], if_true: [files('apple-gfx.m', 'apple-gfx-mmio.m')])
```
`apple-gfx.m:1-13` header:
> "ParavirtualizedGraphics.framework is a set of libraries that macOS provides
> which implements 3d graphics passthrough to the host... This device model
> implements support to drive that library from within QEMU."
It `#import <ParavirtualizedGraphics/ParavirtualizedGraphics.h>` and links
Metal/Mach VM APIs directly (`apple-gfx.m:16-30`) — none of which exist outside
a macOS host. `meson.build:3389-3393` additionally restricts `CONFIG_MAC_PVG`
to targets that support the HVF accelerator specifically:
```meson
# PVG is not cross-architecture. Use accelerator_targets as a proxy...
if pvg.found() and target in accelerator_targets.get('CONFIG_HVF', [])
  target_kconfig += 'CONFIG_MAC_PVG=y'
```
And `vmapple.c:206-217`'s `create_gfx()` instantiates `"apple-gfx-mmio"`
*unconditionally* with `qdev_new()` — if that type isn't registered, this is a
fatal abort, not a graceful fallback to headless. **This project's own build is
already configured `--disable-pvg`** on this exact host
(`qemu-sptm/build/config.log:2`: `'../configure' '--target-list=aarch64-softmmu' '--disable-pvg'`),
because — per `CLAUDE.md` — the ParavirtualizedGraphics API was obsoleted in
the macOS 27 SDK this machine runs (`sw_vers`: macOS 27.0, build 26A5421a).
So even setting aside the HVF gate, `vmapple`'s display device could not link
on this machine today without first solving the same PVG-obsolescence problem
that made `--disable-pvg` necessary in the first place.

**c. Even if (a) and (b) were solved, there is no SEP.** `grep -rn SEP
qemu-sptm/hw/vmapple` returns nothing. QEMU's own docs concede newer-than-12.x
guests are unsupported (§1). vphone-cli's approach depends on
`AVPSEPBooter.vresearch1.bin` — a real SEP boot ROM run through Apple's actual,
proprietary, closed SEP-virtualization layer inside Hypervisor.framework/
Virtualization.framework. There is no public documentation or open-source
prior art for reimplementing that layer (a targeted search turned up only
confirmation that SEP images are unencrypted for VM boot, not that anyone has
built a working software SEP emulator: https://gist.github.com/steven-michaud/fda019a4ae2df3a9295409053a53a65c).
This is a strictly harder, less-charted RE problem than DCP.

**d. The one small, tractable-looking piece is the PAC/CTRR hypercall ABI**
(§1, `hv_hvc.h`) — about a dozen `hvc #0` calls (PAC key set/get, CTRR,
`mach_absolute_time` offset, boot session UUID, vcpu wait/kick). That is
genuinely small compared to DCP/AFK/EPIC, and *is* the kind of thing TCG could
plausibly answer without host hardware. But it only matters if SEP is also
solved — VMAPPLE-board XNU still needs a working SEP for keybags, AMFI/TXM
trust evaluation, and FileVault-adjacent unlock paths, and nothing here
supplies that. Chasing the hypercall ABI without also solving SEP would not
produce a boot.

**Conclusion on Q4**: `vmapple` is not usable on this host as configured, is
architecturally tied to HVF + a macOS-only proprietary display framework even
if it were, and even a fully working `vmapple` build would still lack SEP,
which is the harder of the two problems this project would be trading DCP for.

---

## 5. Anything worth stealing?

**`vphoned`'s touch/HID injection is not directly reusable** — it is a signed,
specially-entitled **guest-side daemon** (`scripts/vphoned/vphoned_hid.m:1-8`)
that fabricates events via a private `IOHIDEventSystemClient` API ("Matches
TrollVNC's `STHIDEventGenerator` approach"), reachable over vsock from the
host. Adopting it would mean adopting the entire jailbreak-adjacent,
SIP/AMFI-relaxed guest-cooperation model this project has correctly avoided —
our host-side HMP `sendkey`/`screendump` approach (`tools/hmp.py`) works
against an unmodified guest, which is strictly the better fit here.

**The protocol shape is worth noting, not the mechanism**: `vphoned` uses
length-prefixed JSON over a socket (`scripts/vphoned/vphoned_protocol.h:1-6`):
`[uint32 big-endian length][UTF-8 JSON payload]`. And `vphone-cli` itself
exposes a **host-side** control socket (`<bundle>/vphone.sock`) for
screenshots/touch/swipes/hardware-keys/clipboard, each action returning an
inline screenshot, explicitly designed for AI-driven E2E testing (`README.md`
"Automation" section; a third-party MCP server wraps it,
https://github.com/pluginslab/vphone-mcp). If `tools/hmp.py` ever grows a
similar "action + inline screenshot" contract for scripted/agent-driven UI
testing, that response shape (not the transport) is a reasonable one to copy.

**A genuinely useful, unrelated find** (flagged here because it was surfaced
during this research, not because it's in scope of vmapple-vs-SoC): item #10
in `research/0_binary_patch_comparison.md:191` and
`scripts/patchers/cfw_patch_dsc_maxslide.py:1-30` describe launchd panicking
with **"Library not loaded: /usr/lib/libSystem.B.dylib"** because the dyld
shared cache's mapped span *plus* its header `maxSlide` (ASLR range, offset
`0xF0`, typically `0x20000000`) overflows the kernel's fixed
`SHARED_REGION_SIZE_ARM64 = 0x180000000` (6 GiB), even when the raw cache span
alone fits comfortably. The fix is zeroing `maxSlide` in the cache header so
it maps at slide 0. This is the same symptom text and failure class as this
project's current rootfs blocker (`CLAUDE.md`, "Where the rootfs work
stands": `dyld cache '(null)' not loaded: syscall to map cache into shared
region failed` / `Library not loaded: /usr/lib/libSystem.B.dylib`), and
`CLAUDE.md` records that raw span-vs-6GiB was already ruled out — but
`maxSlide` on top of span was not checked. Worth trying independent of any
vmapple decision: `cfw_patch_dsc_maxslide.py` shows the exact offsets
(`sharedRegionStart @0xE0`, `sharedRegionSize @0xE8`, `maxSlide @0xF0`, all
little-endian u64 in `dyld_shared_cache_arm64e`'s header) and the check
(`span + maxSlide <= kernel_region_size`).

---

## Recommendation

**Keep going with DCP emulation on the `darwin` SoC model. Do not pivot to
vmapple. Do not spend time trying to make QEMU's `vmapple` boot anything.**

Reasoning, weighted by how load-bearing each point is:

1. **The premise doesn't hold.** iOS does not "boot on Apple's virtual device
   model" in any sense we could reuse — it boots on a *different, purpose-
   compiled kernel target* (`APPLEVIRTUALPLATFORM`/`NO_MONITOR`) that trades
   our entire SPTM/TXM problem for a SEP-virtualization problem that is
   strictly harder and has zero public prior art. There is no version of "port
   the DCP work to vmapple instead" — the kernel binary itself is different.

2. **QEMU's `vmapple` is not a real alternative.** It requires HVF (this
   project targets TCG for portability), its only interesting device is
   macOS-host-only Objective-C against a framework already disabled in this
   exact build for SDK-obsolescence reasons, it has no SEP, and it has never
   booted past macOS 12 in over a year of upstream availability. It would not
   even build cleanly today without re-litigating the `--disable-pvg` decision
   already made for unrelated reasons.

3. **Portability is actively harmed, not helped, by this direction.** The
   user's stated endgame is Windows ARM portability. `vphone-cli`'s working
   path requires Apple Silicon + macOS 15+ + Hypervisor.framework +
   Virtualization.framework + SIP/AMFI relaxation + private entitlements —
   none of which exist on Windows, and none of which QEMU/TCG can substitute
   for (the SEP-virtualization layer specifically is proprietary and closed).
   Continuing DCP emulation under QEMU/TCG is the only one of the paths
   examined that is compatible with the portability goal at all.

4. **The "real iPhone apps" goal is also better served by staying put.** Even
   `vphone-cli`'s successful boot is not a real iPhone: it's a hybrid image
   requiring 4–141 binary patches depending on variant, missing camera/audio/
   cellular, needing region selection during setup to dodge regulatory checks,
   and needing new patches re-derived for each iOS point release (the research
   docs show iOS 26→27 alone required three new patches for the SwapEnd size,
   the DSC maxSlide overflow, and the force-kern swap-path change). That is a
   comparable — arguably worse — version-to-version maintenance burden to what
   this project already has with DCP, for a guest that is admittedly *not*
   the real hardware target.

5. **What we already have (real DCP/AFK/EPIC framing, real IOMFB endpoint
   announces) is validated as the correct approach**, not undercut, by this
   research: `vphone-cli`'s own display path still terminates in the same
   public `IOMobileFramebuffer` userclient API this project targets — Apple
   didn't invent a different display API for the VM, it swapped what's behind
   it. Our job is still "make `AppleCLCD2`/DCP happy," which is exactly what's
   already in progress.

**What would have changed this recommendation**: if QEMU's `vmapple` display
device worked without HVF, or if there were any public SEP-virtualization
reimplementation to build on, the small paravirtualized-PAC hypercall ABI
(§4d) would have been worth prototyping as a genuinely lighter-weight
alternative to SPTM/TXM. Neither condition holds today. Revisit only if QEMU
upstream lands SEP support in `vmapple`, or if `vphone-cli`/Apple's PCC program
ever publishes a build usable outside Virtualization.framework.
