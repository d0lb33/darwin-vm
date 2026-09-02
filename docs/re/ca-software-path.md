# Does anything in this iOS build render UIKit content without AGX?

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/H17P), kernelcache
`firmware/bootkc` (MH_FILESET, unslid VAs, `file_offset = VA - 0xfffffff007004000`),
device tree `/tmp/dvm/dtree_raw` (unpatched) and `firmware/dtree` (patched),
`firmware/sptm`; extracted 2026-09-02.

Userspace evidence comes from two builds of the *same* QuartzCore source that
are readable on this host without mounting anything:

| build | file | why it is a proxy, and how far the proxy goes |
|---|---|---|
| iOS 27.0 **Simulator** runtime 24A5355p (`ProductBuildVersion` from its `SystemVersion.plist`) | `/Library/Developer/CoreSimulator/Volumes/iOS_24A5355p/.../RuntimeRoot/System/Library/Frameworks/QuartzCore.framework/QuartzCore` (5,902,128 bytes, arm64, 19,416 symbols, unstripped) and `.../RuntimeRoot/usr/libexec/backboardd` | same major version as the device build, iOS-flavoured, but compiled for the simulator platform; it has no IOMobileFramebuffer server class |
| **macOS 27** host, from `/System/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_arm64e` | `ipsw dyld extract ... QuartzCore` (6,883,560 bytes, 23,957 symbols) | Apple-silicon macOS drives its panel through the same IOMobileFramebuffer/DCP stack, so it *does* have the IOMFB-backed server class the phone must use |

**The iPhone build of QuartzCore and backboardd was not examined** -- they live
inside the system-volume DMG, which this task forbade mounting. Every
userspace claim below is therefore "true of the codebase in two sibling builds"
and is labelled with which build it was read from. The closing section lists
exactly what to extract and what to check in it.

## Summary

CoreAnimation's window server has a CPU compositor, `CA::OGL::SWContext`, and
its concrete IOMFB-backed server class uses it automatically whenever
`MTLCreateSystemDefaultDevice()` returns nil: `AccelServer::render_update()`
calls the virtual `renderer()`, and if that is NULL tail-calls
`Server::render_update()`, which composites the whole tree with the software
renderer. Nothing has to be enabled; the trigger is the absence of any
`IOAcceleratorES` service, which is our current state because
`dt_fixup.py:558` deletes `/arm-io/sgx`, the only node `AGXAcceleratorG17P`
matches. What a QEMU model must provide is therefore **not a GPU** but the
IOMobileFramebuffer pixel path (surface map, swap, scanout through the DCP
model) so that the software-composited IOSurface reaches the panel.

## 1. Is there a software compositing path? (Q1)

### 1.1 The backend classes exist

Simulator QuartzCore, from `nm | c++filt`:

| backend | factory (local symbol) | vtable | evidence |
|---|---|---|---|
| software | `sw_new_context(void*, void*, unsigned)` at `0x120958` | `vtable for CA::OGL::SWContext` at `0x373fe0` | `nm QuartzCore.sim`; namespace `CA::OGL::SW` has 972 symbols (`SamplerData`, `Format`, `image_sampler`, `Blend`, `Poly`, `scanline`, `scan_convert`, `Texture`, ...): a rasteriser, not a stub |
| Metal | `metal_new_context` at `0x120c20` | `CA::OGL::MetalContext` at `0x375178` | ditto |
| GLES | `gles_new_context` at `0x120ab4` | `CA::OGL::GLESContext` at `0x36f880` | ditto |
| null | `new_null_context` at `0x120c38` | `CA::OGL::NullContext` at `0x3729a0` | ditto |

macOS 27 QuartzCore has `sw_new_context`, `metal_new_context`,
`new_null_context` (no GLES) and the strings
`%d by %d image is too large for software renderer, ignoring`,
`CA_NO_ACCEL`, `CA_FORCE_LOCAL_SERVER`, `CA_ENABLE_TEST_DISPLAY`
(`strings -n 6 macqc/QuartzCore`). So the software backend is a
codebase-wide feature, not a simulator-only one.

`SWContext` can target the display's surfaces: it has
`create_surface_from_iosurface`, `set_destination`, `set_surface`,
`copy_destination`, `finalize_surface` (sim `nm`, `CA::OGL::SWContext::*`).
`SWContext::function_supported()` (sim `0x141e18`) declines shader function
types outside a fixed bitmask (`0x141e2c`-`0x141e3c`), so some filters are
skipped rather than rendered; the composite itself is not gated.

### 1.2 The window server falls back to it when there is no Metal device

Base class, identical in both builds:

| function | sim | macOS | what it does |
|---|---|---|---|
| `CA::WindowServer::Server::renderer()` | `0x26c800`: `mov x0, 0; ret` | vtable `0x1e893a280` slot 59 | base returns **no** accelerated renderer |
| `CA::WindowServer::Server::sw_renderer()` | `0x266558` | `0x18b2eafcc` | lazily allocates a `0x52c0`-byte `CA::OGL::Context`, installs vtable `0x373ff0` = SWContext vtable + 0x10 (`str x8, [x20]` at `0x2665c4`), wraps it in a `CA::OGL::Renderer` |
| `CA::WindowServer::Server::render_update()` | `0x266740`: `bl sw_renderer` at `0x266754`, then `b Display::render_display(Renderer&, Update*)` at `0x266780` | `0x18b2eb200`: `bl sw_renderer` at `0x18b2eb218`, `b Display::render_display` at `0x18b2eb254` | **composites the display with the software renderer** |
| `CA::WindowServer::Server::render_surface()` | `0x2666a4`: `bl sw_renderer` at `0x2666d4` | -- | same for offscreen surfaces |

Concrete server classes override `render_update` and fall back to the base:

| build | class | `renderer()` | `render_update()` |
|---|---|---|---|
| sim | `SimServer` | `0x196450`: `bl _CAMetalContextCreate` at `0x196474`; `cbz x0 -> 0x196574` at `0x19647c`; returns `[x19+0x410]`, still 0 -> **NULL** | `0x196678`: `blraa` vtable slot `0x1d0` (= `renderer()`) at `0x19669c`; `cbz x0 -> 0x1966f4` at `0x1966a0`; `b Server::render_update` at `0x196708` |
| macOS | `AccelServer` (the IOMFB display server, see 1.3) | `0x18b0c3e38`: `bl _CAMetalContextCreate` at `0x18b0c3e60`; `cbz x0 -> 0x18b0c3f5c` at `0x18b0c3e68`; `mov x0, 0` at `0x18b0c3f64` -> **NULL** | `0x18b0c40a8`: `blraa` slot `0x1d8` at `0x18b0c40e8`; `cbz x0 -> 0x18b0c4150` at `0x18b0c40ec`; `b Server::render_update` at `0x18b0c4174` |
| sim | `VirtualServer` | `0x473e0`, same shape (`_CAMetalContextCreate` at `0x47418`) | `0x47610` -> `Server::render_update` (xref at `0x476c0`) |

`_CAMetalContextCreate` (sim `0x2bee28`) is an autorelease-pool wrapper around
exactly one call, `MTLCreateSystemDefaultDevice` (`0x2bee3c`); it returns
whatever that returns. The only other caller of `MTLCreateSystemDefaultDevice`
in QuartzCore is `-[CAMetalLayer init]` (`0x1c5c20`), i.e. client-side
`CAMetalLayer`, which UIKit's stock views do not use.

So the decision is: **no Metal device -> `renderer()` is NULL -> the
IOMFB/Sim server's `render_update` tail-calls the base -> `SWContext`
composites.** There is no flag, and nothing to enable.

### 1.3 The IOMFB-backed server inherits exactly this fallback (macOS)

macOS vtables decoded from the extracted dylib (chained-fixup targets resolved
against `nm`):

| vtable | slot 59 (`renderer`) | slot 64 (`render_update`) | slot 0 (`shutdown`) |
|---|---|---|---|
| `CA::WindowServer::Server` `0x1e893a280` | `Server::renderer` | `Server::render_update` | `Server::shutdown` |
| `CA::WindowServer::IOMFBServer` `0x1e893a718` | `Server::renderer` | `Server::render_update` | `IOMFBServer::shutdown` |
| `CA::WindowServer::AccelServer` `0x1e892ed08` | `AccelServer::renderer` | `AccelServer::render_update` | `IOMFBServer::shutdown` |

`AccelServer` inherits `IOMFBServer::shutdown` and `immediate_render`, and
`AccelServer::AccelServer(IOMFBDisplay*, CFString const*)` (`0x18b0c390c`)
references the `IOMFBServer(IOMFBDisplay*, ...)` constructor block, so the
hierarchy is `AccelServer : IOMFBServer : Server`. `IOMFBServer` is the
display plumbing (`vsync_callback`, `need_swap_callback`,
`try_swap_begin_async`, `req_dcp_reset_callback`, `hotplug_callback`,
`frame_info_callback`, `relbuf_info_callback`, ...; 99 symbols) and
`AccelServer` adds the Metal renderer on top. On a Metal-less machine the
object is still an `AccelServer`; its `render_update` just lands in the
software path shown in 1.2.

Inference, labelled: the iPhone build uses the same `AccelServer :
IOMFBServer` pair (the simulator lacks it only because the simulator has no
IOMobileFramebuffer; macOS has it because Apple silicon Macs do). This is the
one link that only the device binary can confirm.

### 1.4 Environment variables are not the switch

| variable | reader (sim) | what it actually controls |
|---|---|---|
| `CA_NO_ACCEL` (`0x30c88f`) | `CA::CG::AccelQueue::AccelQueue(AccelDrawable)` at `0x2ee00` | the Metal-accelerated **CoreGraphics** drawing path (`CA::CG::MetalContext`, `CA::CG::IOSurfaceContext`), i.e. client backing-store rasterisation -- not the compositor |
| `CA_ACCEL_BACKING` (`0x30c97a`) | `accel_init()` at `0x323f4`, string `forcing %saccelerated backing` | same subsystem |
| `CA_ENABLE_LOCAL_DISPLAY` (`0x30c108`) | `local_display_enabled()` block at `0x21260` | in-process ("local") display |
| `CA_FORCE_LOCAL_SERVER` (`0x316d5e`) | `force_local_server()` block at `0x167380` | in-process render server |
| `CA_ENABLE_TEST_DISPLAY` (`0x32526e`) | referenced only from data at `0x37f618` | virtual/test display (`CA::WindowServer::VirtualDisplay`, `VirtualServer`) |

The full `CA_*` list (326 names, `strings -n 4 QuartzCore.sim | grep '^CA_'`)
contains no "use software renderer" switch; `CA_DISABLE_RENDER` exists but is
the opposite direction. The software compositor is reached by *absence of
hardware*, matching the pattern of every other degraded path this project has
used.

## 2. Is there a headless / no-GPU mode? (Q2)

backboardd (sim, `/usr/libexec/backboardd`, links QuartzCore, Metal,
IOSurface; hosts `com.apple.CARenderServer` per its LaunchDaemon plist
`MachServices`):

| step | address | evidence |
|---|---|---|
| `StartWindowServer` | `sym.func.100013894` | references `StartWindowServer: headless (display:%{BOOL}u/server:%{BOOL}u)` (`0x100068522`) at `0x100013fe0` |
| creates the server | `0x100013940` | `[CAWindowServer serverWithOptions:]` (selref `0x10008ee60` -> `0x1000606a2`), then `setRendererFlags:` (`0x100013958`, selref `0x10008f318`) |
| finds the display | `0x100013964` -> x21 | `[CADisplay mainDisplay]` (selref `0x10008e9a8` -> `0x10005f5ca`) |
| finds the server display | `0x10001397c` -> x22 | `[server displayWithDisplayId:]` (selref `0x10008e330` -> `0x10005de0c`) |
| headless test | `0x100013a24` `cbz x21`, `0x100013a28` `cbz x22` -> `0x100013e18` | either object nil -> "headless" |
| headless continuation | `0x100013ebc` | `[CAWindowServer serverIfRunning]` (selref `0x10008ee58` -> `0x100057573`); the function continues and returns normally |
| `BKDisplayIsHeadless` | block `0x1000137dc` | asserts `please invoke BKDisplayStartWindowServer before BKDisplayIsHeadless` (`0x100063fe4`) -- headlessness is a first-class state |
| display enumeration | `func.100028ee4` | logs `We seeem to be headless` (`0x10006a3ad`) after walking `CADisplay`s, checking `Wireless` / `TVOut` names |

So backboardd tolerates having no display at all. We do not need that mode:
`AppleCLCD2` already binds to `/arm-io/disp0` (CLAUDE.md), so `[CADisplay
mainDisplay]` should exist; what we need is 1.2 (no GPU), which is the other
axis and is automatic.

## 3. What binds to `sgx`, and what happens when it is absent (Q3)

### 3.1 The node

Raw tree `/arm-io/sgx`: `compatible = "gpu,t8140"`, `device_type = "sgx"`,
26 properties (`reg`, `interrupts` x8, `clock-gates`, `power-gates`,
`gpu-num-perf-states`, `gpu-device-max-power`, `metal-standard = 0x100`,
`opengl-standard = 0x300`, `agx-address-space-mgmt-mode`, `has-kf`, ...), no
children. `dt_fixup.py:558` (`d['arm-io'].remove_child('sgx')`) deletes it;
the patched `firmware/dtree` still has `gfx-asc`, `gfx1-asc`
(`iop,ascwrap-v6`) and `mapper-gfx-asc` (`iommu-mapper,gfx`).

### 3.2 What matches it

`__PRELINK_INFO` (`fileoff 70615040`, `IOKitPersonalities`):

| bundle | personality | `IOClass` | provider / match |
|---|---|---|---|
| `com.apple.AGXG17P` 360.34.5a1 | `AGXG17P` | `AGXAcceleratorG17P` | `AppleARMIODevice`, `IONameMatch [gpu,t8140, gpu,t8015, gpu,t8027, gpu,t8030, gpu,t8103, gpu,t8122]`, `IOMatchCategory IOAcceleratorES`, `MetalPluginName AGXMetalG17P`, `IOGLESBundleName AppleMetalGLRenderer` |
| `com.apple.AGXG17P` | `AGXArmFirmwareMapper` | `AGXArmFirmwareMapper` | `IONameMatch iommu-mapper,gfx` (that is `/arm-io/mapper-gfx-asc`) |
| `com.apple.AGXFirmwareKextG17PRTBuddy` | `Firmware-Sched` / `Firmware-Power` | `AGXFirmwareKextG16RTBuddy` | `RTBuddyService` with `role = GFX` / `GFX1` |
| `com.apple.iokit.IOSurface` 402.8 | `FirstPersonality` | `IOSurfaceRoot` | `IOResources`, `IOResourceMatch IOBSD` -- **no GPU dependency** |

`AGXG17P`'s `OSBundleLibraries` depend on `IOGPUFamily`, `IOSurface`,
`RTBuddy`; nothing in the display or IOSurface stack depends on AGX.

### 3.3 Absent: nothing binds, nothing waits, nothing panics

- `AGXAcceleratorG17P` can only match `gpu,t8140`; with the node gone it never
  probes. Every SpringBoard-era boot log confirms it: `SEPDCP.clean.log`
  (42,092 lines) contains one `RTBuddy(...)` instance, `RTBuddy(DCP)`, and
  zero lines matching `AGX|gfx|IOGPU|IOAccel|sgx` outside `staged_system_apps`
  file names; `SEPDCP.stderr.log` has zero `gfx` mentions, so the model never
  even sees an MMIO touch on `gfx-asc`.
- The `IOWorkloadConfig` strings `CA_RENDER_SERVER` / `CA_CLIENT` at
  kernel `0xfffffff0070caf5e` / `0xfffffff0070caf80` are scheduler
  work-interval names (`/tmp/dvm/apple-xnu/iokit/Kernel/IOWorkloadConfig.cpp:207-216`),
  not rendering -- recorded so nobody chases them.

### 3.4 Present but bare: SPTM dies before serial

Measured, 2026-09-02, `tools/probe.sh --dtree <tree> --secs 100`:

| tag | tree | serial lines | PC at freeze |
|---|---|---|---|
| `SKS_FINAL_DEFAULT` (baseline, same day) | `firmware/dtree` | 499 | boots |
| `SGXKEEP2` | `firmware/dtree` with the raw `sgx` node bytes (1,264 B) appended under `arm-io` and `nchildren` bumped 136 -> 137, every other byte identical (`/private/tmp/claude-501/.../scratchpad/dt_sgx2.bin`, 343,220 B) | **0** | `0xfffffff0070f75a8`: `b #0xfffffff0070f75a4` (SPTM's parking loop), `X3 = 0xfffffff007122f73` |
| `SGXKEEP` | same tree produced by decode/encode round-trip | 0 | SPTM panic `random-seed ... size mismatch (257)` -- an **artifact** of the decoder's string heuristic re-encoding a byte property with a trailing NUL; do not use round-tripped trees for experiments |

Why SPTM: `strings -n 5 firmware/sptm` contains `/arm-io/sgx`,
`%s: error %d looking up /arm-io/sgx`,
``%s: The `gpu-iouat` property should be %lu bytes not %d``,
`%s: UAT Handoff region should not be null.`,
`%s: uat-enforce-gpu-carveout is not %zu bytes wide (%d)`,
`%s: uat-vaddr-size is %zu bytes wide (%d)`, `%s: Invalid UAT mode found %llu`,
`IOMMU_ID_UAT`, `SPTM_DISPATCH_TABLE_UAT`, `uat_register_gfx_data_segment`.
SPTM brings up the GPU's UAT IOMMU from iBoot-supplied properties on the `sgx`
node; the raw node has none of them (`gpu-iouat` is not among its 26
properties), so a bare node is worse than no node. The exact message is
**unverified**: `X3`'s page offset `0xf73` coincides with the NUL terminator of
`VIOLATION_UAT_INVALID_ROOT_TABLE` (`firmware/sptm` offset `0xf53` + 32), and
the buffer read back through the monitor was empty.

The kernel would object next. `IOUnifiedAddressTranslator` (IOUAT) lives in
`com.apple.kernel` `__TEXT_EXEC.__text` (`0xfffffff00aa61000`-`0xfffffff00b34a4f0`),
init at `0xfffffff00b2d0c8c` (logs `IOUAT %s:%d: *** IOUnifiedAddressTranslator entry`),
reached through the vtable slot at `0xfffffff007e57c80`; it is exported to kexts
(`config/Private.arm64.exports:118-122`) and AGX subclasses it (`AGXUAT`,
`AGXUATMux`, `AGXUATSingleStage` strings in `com.apple.AGXG17P`). Its init maps
five regions, each from a device-tree property fetched by the helper at
`0xfffffff00b2d1108` (`[self+0x18]->vtbl+0x370`, then `->vtbl+0x118(name)`,
type-checked against the `OSData` metaclass at `0xfffffff00b6bda28`):

| call site | property | log name |
|---|---|---|
| `0xfffffff00b2d0de0` | `gfx-shared-region-size` | -- |
| `0xfffffff00b2d0e1c` | `gfx-shared-region-base` | `ASC carveout region` |
| `0xfffffff00b2d0e38` | `gfx-shared-l2-region-base` | `TTBR1 shared L2 table` |
| `0xfffffff00b2d0e58` | `gpu-region-base` | `TTBAT` |
| `0xfffffff00b2d0e78` | `gfx-handoff-base` | `GPU handoff region` |

A missing property goes to `IOUAT ERROR: Failed to retrieve '%s' DT prop
@%s:%d` (`0xfffffff00b2d11d4`) followed by `bl 0xfffffff00b331ec4`, which is
the kernel's `panic` trampoline (4,585 `bl` callers in kernel text; it calls
`0xfffffff00aab1bd8`, the routine that prints `panic: %s` and `Kernel panicked
very early before serial init, spinning forever...`). None of the five
properties is in the raw tree; iBoot adds them on hardware (qemu-t8030 modelled
the same carveouts as RAM: `hw/arm/t8030.c:469-471`, "GFX handoff", "GFX
shared region", "GPU region"). This is the "iommu init stuff" the
`dt_fixup.py:557` comment refers to.

## 4. Boot-args and device-tree switches (Q4)

What the binaries actually check, by kext (`strings` over the extracted
fileset entries in `/private/tmp/claude-501/.../scratchpad/kexts/`):

| kext | switch | effect | evidence |
|---|---|---|---|
| `IOMobileGraphicsFamily-DCP`, `AppleMobileDispH17P-DCP` | boot-arg `iomfb_disable_display` | `AP DRIVER: Display disabled using boot-arg %s` -- turns the display **off**, the wrong direction | readers at `0xfffffff00a0ef5e0` and `0xfffffff0091884f4` (`kaddr.py` on strings `0xfffffff00799e507`, `0xfffffff007620a52`) |
| `IOMobileGraphicsFamily(-DCP)` | `iomfb_disable_async_swap`, `iomfb_swap_wait_timeout_s`, `iomfb_dis_fwhash_check`, `iomfb_rack_debug`, `iomfb_panic_on_boot_failure`, `iomfb_abort_swap_disable`, `iomfb_drop_noop_swap`, ... | swap/timeout debugging, none about rendering | token list |
| `IOGPUFamily` | `iogpu_*` (`_debug`, `_event_timeout_secs`, `_disable_restart_limit`, `_force_mapped_memory`, `_sysmem_mb`, ...), `panic_on_gpu_hang`, `spin_wait_for_gpu`, `gpu_no_zero_fill` | GPU-family debugging; only meaningful once a GPU exists | token list, `"GPU hang (boot-args contains \"panic_on_gpu_hang=1\")"` |
| `AGXG17P` | no boot-arg strings at all (`grep -iE 'boot-?arg'` empty); ~150 `gpu-*` device-tree tunables (`gpu-perf-*`, `gpu-pwr-*`, `gpu-idleoff-*`, ...) | power/perf tuning | token list |
| whole kernelcache | every boot-arg-shaped string matching `gpu|gfx|agx|render|swrender|headless|nogpu|metal|CA_` | only device-tree property names and the two IOWorkloadConfig names | `awk`/`grep` over `/tmp/dvm/bootkc.strings` (312,713 lines; positive control `AppleCLCD2` at `0x61c6d5`) |

There is **no** `gpu=`, `-nogpu`, or "software rendering" boot-arg or
device-tree property in this kernelcache, and none is needed: the userspace
fallback keys off the absence of the accelerator service.

## 5. What a non-GPU path costs us

The composited frame has to reach the panel through the path we are already
building:

- `IOSurface` allocation needs only `IOSurfaceRoot` (3.2).
- The render server's display object drives `IOMobileFramebuffer` swaps:
  `IOMobileFramebufferAP::surface_map_dcp(IOSurface*, IODMACommand**, dva_t*, bool)`,
  `swap_submit`, `swap_wait`, `surface_complete`, `need_swap_notify`
  (`IOMobileGraphicsFamily-DCP` strings) -- i.e. the DCP pixel path in
  `darwin_iomfb.c`, which today stops after `A353` with no framebuffer ever
  requested (CLAUDE.md). That work is unchanged by this finding; it is simply
  the *only* remaining display work rather than one of two.
- Framebuffer compression: `AccelServer::renderer()` consults
  `_CADeviceUseFramebufferCompression` (`0x18b0c3ec4`) only on the Metal
  path, so the software path produces uncompressed surfaces; the DCP side
  logs `IOMFB: Surface %d has cacheMode 0x%x, needs to be 0x%x for display RT
  fetch` and `GP Decompression Error` for the compressed case, which we then
  never enter. Risk, not blocker.
- Rotation/scaling copies: `CA::WindowServer::Display` has
  `iosurface_accelerator_supports_{scale,size,color_remap}` and the env
  `CA_FORCE_COPY_SURFACE_{GPU,MSR}` (sim strings). The MSR is
  `AppleM2ScalerCSCDriver`, present in this kernelcache. Whether the software
  path needs it for a portrait phone panel is **unverified**.
- Speed: CPU compositing under TCG. Not measured.

## 6. If the device build has compiled the software path out

Then AGX is unavoidable and the minimum surface is large, because three
layers gate on it before any pixel: SPTM's UAT bring-up from iBoot-supplied
properties (`gpu-iouat`, UAT handoff, `gfx-shared-region-*`, `gpu-region-base`,
`gfx-handoff-base`, `uat-enforce-gpu-carveout`, `uat-vaddr-size`; section
3.4), the GFX RTKit firmware on `gfx-asc`/`gfx1-asc`
(`AGXFirmwareKextG16RTBuddy`, `AGXArmFirmware::kickFirmware`,
`AGXFirmware::isGFXBooted`), and `AGXAccelerator::start` /
`configureDevice` / `IOGPU` command queues feeding a user-space Metal plugin
(`MetalPluginName AGXMetalG17P`) that emits real shader ISA. That is a GPU
emulator, not a device model. Nothing found here suggests that is required.

## Open questions

1. **The iPhone QuartzCore.** Needs the device `dyld_shared_cache_arm64e`
   (+ subcaches and the `.symbols` file) from the system volume, then
   `ipsw dyld extract <cache> QuartzCore`. Check, in this order:
   `sw_new_context` / `CA::OGL::SWContext` (symbols, or the strings
   `software renderer` and `software-temp-surface`); an `AccelServer :
   IOMFBServer` pair; `AccelServer::render_update` with the
   `cbz` on `renderer()` followed by `b Server::render_update`;
   `AccelServer::renderer()` returning NULL after `_CAMetalContextCreate`.
2. **The iPhone Metal.framework.** `MTLCreateSystemDefaultDevice()` must
   return nil, not abort, when no `IOAcceleratorES` service exists. Same
   extraction.
3. **The iPhone backboardd** (`/usr/libexec/backboardd`, a loose file): the
   `StartWindowServer` headless logic and whether anything else in it
   hard-requires Metal (`BKSecureRendering`, `IOSurfaceAcceleratorParavirtClient`
   are the strings to start from).
4. The exact SPTM panic for a bare `sgx` node (3.4); a `pmemsave` of the SPTM
   data pages at the parked PC would settle it. Only worth doing if someone
   wants to keep the node.
5. Whether the software path needs the M2 scaler for the internal panel
   (section 5).
