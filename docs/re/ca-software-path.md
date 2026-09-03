# Does anything in this iOS build render UIKit content without AGX?

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/H17P), kernelcache
`firmware/bootkc` (MH_FILESET, unslid VAs, `file_offset = VA - 0xfffffff007004000`),
device tree `/tmp/dvm/dtree_raw` (unpatched) and `firmware/dtree` (patched),
`firmware/sptm`; extracted 2026-09-02.

Userspace evidence, in order of authority:

| build | file | role |
|---|---|---|
| **iPhone 24A5430a (the device build)** | `~/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e` (+ subcaches, `.symbols`), extracted with `ipsw dyld extract ... QuartzCore Metal IOMobileFramebuffer --stubs` (QuartzCore 6,796,976 B, 22,379 symbols; Metal 5,419,968 B); `~/dvm-artifacts/extract/bin/backboardd` (801,488 B, arm64e) | **the binary that matters; every claim below is now made against it** |
| iOS 27.0 Simulator 24A5355p | `/Library/Developer/CoreSimulator/Volumes/iOS_24A5355p/.../RuntimeRoot/System/Library/Frameworks/QuartzCore.framework/QuartzCore` (5,902,128 B, 19,416 symbols) | first read; retained as corroboration |
| macOS 27 host | `ipsw dyld extract /System/Cryptexes/OS/System/Library/dyld/dyld_shared_cache_arm64e QuartzCore` (6,883,560 B, 23,957 symbols) | corroboration; shares the IOMFB server classes |

Addresses are unslid cache VAs (`0x18...`/`0x1a...`/`0x1e...`) for the two
caches and `0x1000...` for backboardd. Device-build addresses are marked
**[dev]**, simulator **[sim]**, macOS **[mac]**.

## Summary

**Confirmed on the device build.** CoreAnimation's window server has a CPU
compositor, `CA::OGL::SWContext`, and the class the iPhone's panel actually
instantiates -- `CA::WindowServer::AccelServer`, built by
`AppleDisplay::new_server()` -- uses it automatically whenever
`MTLCreateSystemDefaultDevice()` returns nil: `AccelServer::render_update()`
calls the virtual `renderer()`, and if that is NULL tail-calls
`Server::render_update()`, which composites the whole tree with the software
renderer. `MTLCreateSystemDefaultDevice()` on this iOS build returns nil, with
no failure report, when `IOServiceMatching("IOAcceleratorES")` finds nothing.
Nothing has to be enabled; the trigger is the absence of any
`IOAcceleratorES` service, which is our current state because
`dt_fixup.py:558` deletes `/arm-io/sgx`, the only node `AGXAcceleratorG17P`
matches. What a QEMU model must provide is therefore **not a GPU** but the
IOMobileFramebuffer pixel path (surface map, swap, scanout through the DCP
model) so that the software-composited IOSurface reaches the panel.

## 1. Is there a software compositing path? (Q1)

### 1.1 The backend classes exist -- confirmed on device

Device QuartzCore **[dev]**, from `nm | c++filt` on the `.symbols`-restored
extraction:

| backend | factory (local symbol) | vtable | evidence |
|---|---|---|---|
| **software** | `sw_new_context(void*, void*, unsigned)` at `0x1846a04a4` | `vtable for CA::OGL::SWContext` at `0x1e9050118` | `CA::OGL::SW::` 379 symbols, `CA::OGL::SWContext::` 41 symbols; strings `%d by %d image is too large for software renderer, ignoring`, `software-temp-surface` |
| Metal | `metal_new_context` at `0x1846a084c` | `CA::OGL::MetalContext` at `0x1e9051270` | ditto |
| GLES | `gles_new_context` at `0x1846a061c` | (no vtable symbol) | ditto |
| null | `new_null_context` at `0x1846a0864` | `CA::OGL::NullContext` at `0x1e904ec58` | ditto |

Same four factories in the Simulator **[sim]** (`sw_new_context` `0x120958`,
`CA::OGL::SW` 972 symbols, SWContext vtable `0x373fe0`) and three of them in
macOS **[mac]** (no GLES). The software backend is a codebase-wide feature and
the iPhone build ships it.

`SWContext` can target the display's surfaces: it has
`create_surface_from_iosurface`, `set_destination`, `set_surface`,
`copy_destination`, `finalize_surface` (**[dev]** and **[sim]** `nm`,
`CA::OGL::SWContext::*`). `SWContext::function_supported()` (**[sim]**
`0x141e18`) declines shader function types outside a fixed bitmask
(`0x141e2c`-`0x141e3c`), so some filters are skipped rather than rendered;
the composite itself is not gated.

### 1.2 The window server falls back to it when there is no Metal device

Base class, identical in all three builds:

| function | **device [dev]** | sim | macOS | what it does |
|---|---|---|---|---|
| `CA::WindowServer::Server::renderer()` | `0x1847786f0` (vtable `0x1e9056978` slot 58) | `0x26c800`: `mov x0, 0; ret` | vtable `0x1e893a280` slot 59 | base returns **no** accelerated renderer |
| `CA::WindowServer::Server::sw_renderer()` | `0x184775ef0` | `0x266558` | `0x18b2eafcc` | lazily allocates a `0x52c0`-byte `CA::OGL::Context`, installs the SWContext vtable (sim: `str x8, [x20]` at `0x2665c4` with `x8 = 0x373ff0` = vtable + 0x10), wraps it in a `CA::OGL::Renderer` |
| `CA::WindowServer::Server::render_update()` | `0x184776174`: `bl sw_renderer` at `0x18477618c`, `b Display::render_display(Renderer&, Update*)` at `0x1847761c8` | `0x266740` (`0x266754`, `0x266780`) | `0x18b2eb200` (`0x18b2eb218`, `0x18b2eb254`) | **composites the display with the software renderer** |
| `CA::WindowServer::Server::render_surface()` | `0x1847760d4`: `bl sw_renderer` at `0x184776108` | `0x2666a4` (`0x2666d4`) | -- | same for offscreen surfaces |

Concrete server classes override `render_update` and fall back to the base:

| build | class | `renderer()` | `render_update()` |
|---|---|---|---|
| **device** | **`AccelServer`** (the IOMFB display server, see 1.3) | `0x18440f824`: `bl _CAMetalContextCreate` at `0x18440f84c`; `str x0, [x20, 0x140]`; `cbz x0 -> 0x18440f99c` at `0x18440f854`; there `ldr x0, [x19, 0x630]` (still 0), `cbnz` not taken, `b 0x18440f990` -> returns **NULL** | `0x184451598`: `blraa` vtable slot `0x1d0` (= `renderer()`) at `0x1844515d8`; `cbz x0 -> 0x184451640` at `0x1844515dc`; `b Server::render_update` at `0x184451664`. `render_surface` (`0x1845ac65c`) likewise ends in `b Server::render_surface` at `0x1845ac770` |
| macOS | `AccelServer` | `0x18b0c3e38`: `bl _CAMetalContextCreate` at `0x18b0c3e60`; `cbz x0 -> 0x18b0c3f5c` at `0x18b0c3e68`; `mov x0, 0` at `0x18b0c3f64` | `0x18b0c40a8`: `blraa` slot `0x1d8` at `0x18b0c40e8`; `cbz x0 -> 0x18b0c4150`; `b Server::render_update` at `0x18b0c4174` |
| sim | `SimServer` | `0x196450`: `_CAMetalContextCreate` at `0x196474`; `cbz x0 -> 0x196574` at `0x19647c` -> NULL | `0x196678`: `blraa` slot `0x1d0` at `0x19669c`; `cbz` at `0x1966a0`; `b Server::render_update` at `0x196708` |
| sim / device | `VirtualServer` | sim `0x473e0`; device `0x184623b00` (`_CAMetalContextCreate` at `0x184623b3c`) | device `0x184623d8c` -> `b Server::render_update` at `0x184623e90` |

`_CAMetalContextCreate` (**[dev]** `0x1847bcf94`, **[sim]** `0x2bee28`) is an
autorelease-pool wrapper around exactly one call, `MTLCreateSystemDefaultDevice`;
it returns whatever that returns. A raw `bl` scan of the device text lists
every caller of `_CAMetalContextCreate`: `AccelServer::renderer` (`0x18440f84c`),
`VirtualServer::renderer` (`0x184623b3c`), `CA::OGL::AsynchronousDispatcher::renderer`
(`0x18456117c`), `CA::CG::AccelRenderer::acquire` (`0x184586f34`),
`-[CASDFGenerator init]` (`0x18460b968`), `create_cgimage_from_iosurface`
(`0x184618b54`), `CACreateFloat16TextureFromTexture` (`0x1846d7f18`),
`CA::WindowServer::IOSurface::allocate_iosurface` (`0x18471c11c`, `0x18471c1e8`)
and the `IOMFBDisplay::get_ctx()` block (`0x1847a8c78`). None is on the server
*construction* path; the three on the display path are NULL-safe (1.4).

So the decision is: **no Metal device -> `renderer()` is NULL -> the
IOMFB server's `render_update` tail-calls the base -> `SWContext`
composites.** There is no flag, and nothing to enable.

### 1.3 The IOMFB-backed server inherits exactly this fallback -- confirmed on device

Device vtables **[dev]**, chained-fixup targets resolved against `nm`:

| vtable | slot 0 (`shutdown`) | slot 58 (`renderer`) | slot 62 (`immediate_render`) | slot 63 (`render_update`) | slot 64 (`render_surface`) |
|---|---|---|---|---|---|
| `CA::WindowServer::Server` `0x1e9056978` | `Server::shutdown` | `Server::renderer` | `Server::immediate_render` | `Server::render_update` | `Server::render_surface` |
| `CA::WindowServer::IOMFBServer` `0x1e9056e08` | `IOMFBServer::shutdown` | `Server::renderer` | `IOMFBServer::immediate_render` | `Server::render_update` | `Server::render_surface` |
| `CA::WindowServer::AccelServer` `0x1e904b218` | `IOMFBServer::shutdown` | `AccelServer::renderer` | `IOMFBServer::immediate_render` | `AccelServer::render_update` | `AccelServer::render_surface` |

`AccelServer` inherits `IOMFBServer`'s `shutdown`/`immediate_render` and
overrides the three render slots, so the hierarchy is
`AccelServer : IOMFBServer : Server`. The `IOMFBServer` constructor is only
ever entered from `AccelServer::AccelServer(IOMFBDisplay*, CFString const*)`
(`0x184629180`; the `IOMFBServer` ctor block is referenced from it at
`0x18462982c`...`0x184629ae4`), so there is no standalone `IOMFBServer`
object. macOS **[mac]** has the same three vtables with the same layout
(`0x1e893a280` / `0x1e893a718` / `0x1e892ed08`).

Who builds it, on device:

| step | address | evidence |
|---|---|---|
| backboardd `StartWindowServer` creates the server first | `0x100025918` | `[CAWindowServer serverWithOptions:]` (stub `0x1000631a0`, selref `0x1000a4be0` -> `serverWithOptions:`), *before* any display test (section 2) |
| the panel display object | `0x1845f7fd4` | block in `CA::WindowServer::AppleInternalDisplay::open(unsigned long)` constructs `CA::WindowServer::AppleDisplay` |
| `new_server` is a virtual at vtable `+0x720` | slots `0x1e9046dd8` (AppleDisplay), `0x1e9048e10` (AppleInternalDisplay), `0x1e9047880` (AppleExternalDisplay) | all three resolve to `CA::WindowServer::AppleDisplay::new_server()` `0x1845f77f8` (raw `0x80156870045f77f8`) |
| `AppleDisplay::new_server()` | `0x1845f78d0`-`0x1845f78f4` | `malloc_type_zone_calloc(zone, 1, 0x638, type)`; `cbz x0` at `0x1845f78e4` guards **only the allocation**; then `bl AccelServer::AccelServer` at `0x1845f78f4`. No Metal check. (`AppleWirelessDisplay::new_server` does the same at `0x1845f89bc`.) |
| the `AccelServer` constructor | `0x184629a94` | calls `AccelServer::renderer()` once, then `0x184629a98`-`0x184629ad0` is the stack-canary check and `retab`: the NULL result is **ignored**, the constructor cannot fail on it |

The only `AccelServer` creators in the device text are the two `new_server`
functions (raw `bl` scan). Inference, labelled: `AppleInternalDisplay` is the
class used for the built-in panel (by name and by its `open()` block being one
of the three `AppleDisplay` constructors); which of the three `open()`s runs is
not in the disassembly but the vtable makes it irrelevant -- all of them reach
`AppleDisplay::new_server`.

### 1.4 The other Metal touches on the display path are NULL-safe (device)

| site | what it does with a nil device |
|---|---|
| `CA::WindowServer::IOSurface::allocate_iosurface` `0x18471bf38` | surfaces are created by `CA::SurfaceUtil::CAIOSurfaceCreate` (`0x18471bff0`) before Metal is consulted. At `0x18471c118`-`0x18471c124` the cached device (`[display+0x140]`) is created if absent, then `[device supportsFamily:0x3ef]` (stub `0x18801dfa0`) at `0x18471c1b0`; on nil that message returns 0 and `cbz w0 -> 0x18471c2a4` (`0x18471c1b4`) skips the shared-event block (`newSharedEventWithOptions:`/`newSharedEvent`, stubs `0x18801b650`/`0x18801b610`) |
| `IOMFBDisplay::get_ctx()` once-block `0x1847a8c5c` | `bl _CAMetalContextCreate` `0x1847a8c78`; `cbz x0 -> 0x1847a8ca8` (`0x1847a8c80`) skips `new_metal_context`; the global at `0x1e400fdc8` stays NULL |
| `IOMFBDisplay::finish_update` `0x18446c5a0` | the only reader of that global (ADRP+LDR scan of the whole text): `ldr x24, [.., 0xdc8]`; `cbz x24 -> 0x18446c668` at `0x18446c5a4` skips the Metal section |
| `Display::render_display(CA::OGL::Renderer&, Update*)` `0x1844510f0` | backend-agnostic: `ogl_display()`, `set_colorspace`, `prepare_clip_shape`, `CA::OGL::Renderer::render(...)` at `0x1844514bc`; no Metal symbol referenced |

### 1.5 Environment variables are not the switch

| variable | reader (sim) | what it actually controls |
|---|---|---|
| `CA_NO_ACCEL` (`0x30c88f`) | `CA::CG::AccelQueue::AccelQueue(AccelDrawable)` at `0x2ee00` | the Metal-accelerated **CoreGraphics** drawing path (`CA::CG::MetalContext`, `CA::CG::IOSurfaceContext`), i.e. client backing-store rasterisation -- not the compositor |
| `CA_ACCEL_BACKING` (`0x30c97a`) | `accel_init()` at `0x323f4`, string `forcing %saccelerated backing` | same subsystem |
| `CA_ENABLE_LOCAL_DISPLAY` (`0x30c108`) | `local_display_enabled()` block at `0x21260` | in-process ("local") display |
| `CA_FORCE_LOCAL_SERVER` (`0x316d5e`) | `force_local_server()` block at `0x167380` | in-process render server |
| `CA_ENABLE_TEST_DISPLAY` (`0x32526e`) | referenced only from data at `0x37f618` | virtual/test display (`CA::WindowServer::VirtualDisplay`, `VirtualServer`) |

The full `CA_*` list (326 names, `strings -n 4 QuartzCore.sim | grep '^CA_'`)
contains no "use software renderer" switch; `CA_DISABLE_RENDER` exists but is
the opposite direction. The device build carries the same `CA_NO_ACCEL`,
`CA_FORCE_LOCAL_SERVER`, `CA_ENABLE_TEST_DISPLAY` strings **[dev]**. The software compositor is reached by *absence of
hardware*, matching the pattern of every other degraded path this project has
used.

## 2. Is there a headless / no-GPU mode? (Q2) -- device backboardd

Device backboardd **[dev]** (`~/dvm-artifacts/extract/bin/backboardd`,
arm64e; links `IOMobileFramebuffer`, `QuartzCore`, `Metal`, `IOSurface`,
`BackBoardServices`). Its **only** Metal imports are `MTLSetShaderCachePath`
and `MTLMakeShaderCacheWritableByAllUsers` (`nm -u`); it never asks Metal for
a device itself.

| step | address | evidence |
|---|---|---|
| `StartWindowServer` | `sym.func.1000257cc` | references `StartWindowServer: headless (display:%{BOOL}u/server:%{BOOL}u)` (`0x100083db4`) at `0x100026014` |
| shader cache | `0x1000258a4` `cbz x20` -> `MTLSetShaderCachePath` `0x1000258ac`, `MTLMakeShaderCacheWritableByAllUsers` `0x1000258b0` | only if a cache path was obtained; the only Metal calls in the binary |
| **creates the window server first** | `0x100025918` | `[CAWindowServer serverWithOptions:]` (stub `0x1000631a0`, selref `0x1000a4be0`), options `kCAWindowServerDisableUpdatesOnMainDisplay` / `kCAWindowServerDisableOutOfProcessDisplayObservation` (relocs `0x1000258c9`-`0x1000258dc`) -- this is what runs `AppleDisplay::new_server()` -> `AccelServer` (1.3) |
| finds the display | `0x10002593c` -> x21 | `[CADisplay mainDisplay]` (stub `0x100061b40`, selref `0x1000a4648`) |
| finds the server display | `0x100025948` / `0x100025954` -> x22 | `[x21 displayId]` (stub `0x10005fde0`, selref `0x1000a3ef0`), `[server displayWithDisplayId:]` (stub `0x10005fe80`, selref `0x1000a3f18`) |
| headless test | `0x1000259fc` `cbz x21`, `0x100025a00` `cbz x22` -> `0x100025df0` | either object nil -> "headless" |
| headless continuation | `0x100025df0` logs; `0x100025e84` `sym.func.1000395f8`; `0x100025ef0` `[CAWindowServer serverIfRunning]` (stub `0x100063180`, selref `0x1000a4bd8`); `sharedInstance` (stub `0x100064dc0`, selref `0x1000a52e8`); the function then returns normally | headlessness is tolerated, not fatal |
| `BKDisplayIsHeadless` | block `sym.func.100003da0` (string ref `0x100003e2c`) | asserts `please invoke BKDisplayStartWindowServer before BKDisplayIsHeadless` -- a first-class state |
| display enumeration | `sym.func.100039984` | logs `We seeem to be headless` (`0x100086388`) at `0x100039c24` |

The simulator backboardd has the identical structure (`sym.func.100013894`,
`serverWithOptions:` at `0x100013940`, `mainDisplay` `0x100013964`,
`displayWithDisplayId:` `0x10001397c`, `cbz` at `0x100013a24`/`0x100013a28`,
`serverIfRunning` at `0x100013ebc`) **[sim]**.

Conclusion: the window server is created before backboardd knows whether a
display exists, no accelerator is consulted at any point, and a missing
display is logged and tolerated. With `AppleCLCD2` bound to `/arm-io/disp0`
(CLAUDE.md) we expect the non-headless path; either way the server starts.

## 3. `MTLCreateSystemDefaultDevice()` returns nil, it does not trap (Q3) -- device Metal

Device Metal.framework **[dev]**, extracted with `--stubs`; objc selector
stubs resolved with `ipsw dsc disass --vaddr`.

| function | address | behaviour |
|---|---|---|
| `MTLCreateSystemDefaultDevice` | `0x1a54bfc28` | `bl MTLDeviceArrayInitialize` (`0x1a54bfc3c`); builds a block-byref result whose value slot is **zeroed** (`stp x16, xzr, [sp+0x50]` at `0x1a54bfc78`; value lives at byref `+0x28` = `sp+0x58`); runs the block through libdispatch (stub `0x1a80d9c90` -> `libdispatch.dylib __TEXT.__text`) at `0x1a54bfcd0`; reads the value back (`ldr x8, [sp+0x38]; ldr x19, [x8, 0x28]` at `0x1a54bfcd4`-`0x1a54bfcd8`) and returns it |
| `___MTLCreateSystemDefaultDevice_block_invoke` | `0x1a54bfdb4` | `[array count]` on the global device array at `0x1e71d5570` (stub `0x1a8002f20` = `objc_msgSend$count`); **`cbz x0 -> 0x1a54bfe04` at `0x1a54bfdd4`: zero devices -> return without storing -> caller returns nil**; `cmp x0, 1; b.ne 0x1a54bfe10` (`0x1a54bfde0`-`0x1a54bfde4`): more than one device -> `MTLReportFailure(0, "MTLCreateSystemDefaultDevice_block_invoke", 0x380, ...)` (`0x1a54bfe10`-`0x1a54bfe40`); exactly one -> `objectAtIndex:` (stub `0x1a80057f0`), retain, store |
| `MTLDeviceArrayInitialize` | `0x1a54bf718` | env-gated tooling only (`METAL_CAPTURE_ENABLED` `0x1a56d626b`, `MTL_CAPTURE_ENABLED`, `MTL_CAPTURE_PATH`, `METAL_LOAD_INTERPOSER`, `DYMTL_TOOLS_DYLIB_PATH`, `MTL_HUD_ENABLED`, `ENABLE_METAL_3_ON_4`); its two `MTLReportFailure` calls (`0x1a54bf874`, `0x1a54bfaec`) sit on the GPUToolsCapture / interposer paths; tail-calls `MTLRegisterDevices` at `0x1a54bfc14` |
| `MTLRegisterDevices` | `0x1a54bfd08` | `objc_alloc_init` of `_MTLIOAccelServiceGlobalContext` (class `0x1e71d30b0`) at `0x1a54bfd54`, stored at `0x1e71d5518`; `[ctx processPendingCreateIOAccelServiceRequests]` (stub `0x1a805c640`) at `0x1a54bfd78` |
| `-[_MTLIOAccelServiceGlobalContext init]` | `0x1a54fcd48` | `IOMainPort` (`0x1a54fcd94`); `IOServiceMatching("IOAcceleratorES")` (`0x1a54fcdc4`, string `0x1a56e3214`); `IOServiceGetMatchingServices` (`0x1a54fcdd4`; kern failure -> `NSLog` `0x1a54fcdf0` and return); `IOIteratorNext` loop (`0x1a54fce24`), **`cbz w0 -> 0x1a54fce84` on exhaustion -> `IOObjectRelease`, return** -- zero services is the ordinary loop exit; per service `getMetalPluginClassForService` (`0x1a54fce38`), `initWithAcceleratorPort:deviceClass:` (stub `0x1a8059f90`), `addObject:` (stub `0x1a8001ed0`) to `_pendingCreateAccelServiceRequests` (`[self+8]`) |
| `-[_MTLIOAccelServiceGlobalContext processPendingCreateIOAccelServiceRequests]` | `0x1a54fdde4` | `count` (`0x1a54fde04`), `cbz -> 0x1a54fde5c` (nothing pending); per request create the device object, `cbz x0` skip (`0x1a54fde40`), `MTLAddDevice` (`0x1a54fde48`) |
| `MTLAddDevice` | `0x1a54ff634` | validates the device (`conformsToProtocol:`, `initLimits`, `initFeatureQueries`, `initWorkarounds`) and appends to the **same** global array `0x1e71d5570` (`ldr x8, [.., 0x570]` at `0x1a54ff698`) |
| `MTLReportFailure` | `0x1a566ef20` | error mode from `MTLFailureTypeGetErrorModeType` (`0x1a54cda3c`): 1 -> `objc_exception_throw` (`0x1a566f250`), 2 -> `NSLog` (`0x1a566f108`), 3 -> `fprintf` (`0x1a566f144`), 4-7 -> `os_log` (`0x1a566f0ec`, `0x1a566f1bc`), default -> `abort` (`0x1a566f264`). **Not reached on the zero-device path.** |

Plugin loading is service-driven: `getMetalPluginClassForService` reads the
accelerator personality's `MetalPluginName` (`AGXMetalG17P` in
`__PRELINK_INFO`, section 4.2) and loads `%s/%s.bundle` under
`/System/Library/Extensions` (Metal strings). Without an `IOAcceleratorES`
service none of that runs.

**Result:** with no `IOAcceleratorES` service -- which is exactly what
deleting `sgx` produces (4.2, 4.3) -- the pending list is empty, the device
array stays empty, and `MTLCreateSystemDefaultDevice()` returns nil with no
log, no report and no trap. `AccelServer::renderer()` then returns NULL
(1.2) and the software compositor runs.

## 4. What binds to `sgx`, and what happens when it is absent

### 4.1 The node

Raw tree `/arm-io/sgx`: `compatible = "gpu,t8140"`, `device_type = "sgx"`,
26 properties (`reg`, `interrupts` x8, `clock-gates`, `power-gates`,
`gpu-num-perf-states`, `gpu-device-max-power`, `metal-standard = 0x100`,
`opengl-standard = 0x300`, `agx-address-space-mgmt-mode`, `has-kf`, ...), no
children. `dt_fixup.py:558` (`d['arm-io'].remove_child('sgx')`) deletes it;
the patched `firmware/dtree` still has `gfx-asc`, `gfx1-asc`
(`iop,ascwrap-v6`) and `mapper-gfx-asc` (`iommu-mapper,gfx`).

### 4.2 What matches it

`__PRELINK_INFO` (`fileoff 70615040`, `IOKitPersonalities`):

| bundle | personality | `IOClass` | provider / match |
|---|---|---|---|
| `com.apple.AGXG17P` 360.34.5a1 | `AGXG17P` | `AGXAcceleratorG17P` | `AppleARMIODevice`, `IONameMatch [gpu,t8140, gpu,t8015, gpu,t8027, gpu,t8030, gpu,t8103, gpu,t8122]`, `IOMatchCategory IOAcceleratorES`, `MetalPluginName AGXMetalG17P`, `IOGLESBundleName AppleMetalGLRenderer` |
| `com.apple.AGXG17P` | `AGXArmFirmwareMapper` | `AGXArmFirmwareMapper` | `IONameMatch iommu-mapper,gfx` (that is `/arm-io/mapper-gfx-asc`) |
| `com.apple.AGXFirmwareKextG17PRTBuddy` | `Firmware-Sched` / `Firmware-Power` | `AGXFirmwareKextG16RTBuddy` | `RTBuddyService` with `role = GFX` / `GFX1` |
| `com.apple.iokit.IOSurface` 402.8 | `FirstPersonality` | `IOSurfaceRoot` | `IOResources`, `IOResourceMatch IOBSD` -- **no GPU dependency** |

`AGXG17P`'s `OSBundleLibraries` depend on `IOGPUFamily`, `IOSurface`,
`RTBuddy`; nothing in the display or IOSurface stack depends on AGX.

### 4.3 Absent: nothing binds, nothing waits, nothing panics

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

### 4.4 Present but bare: SPTM dies before serial

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

## 5. Boot-args and device-tree switches

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

## 6. What a non-GPU path costs us

The composited frame has to reach the panel through the path we are already
building:

- `IOSurface` allocation needs only `IOSurfaceRoot` (4.2).
- The render server's display object drives `IOMobileFramebuffer` swaps:
  `IOMobileFramebufferAP::surface_map_dcp(IOSurface*, IODMACommand**, dva_t*, bool)`,
  `swap_submit`, `swap_wait`, `surface_complete`, `need_swap_notify`
  (`IOMobileGraphicsFamily-DCP` strings) -- i.e. the DCP pixel path in
  `darwin_iomfb.c`, which today stops after `A353` with no framebuffer ever
  requested (CLAUDE.md). That work is unchanged by this finding; it is simply
  the *only* remaining display work rather than one of two.
- Framebuffer compression: `AccelServer::renderer()` consults
  `_CADeviceUseFramebufferCompression` (**[dev]** `0x18440f8dc`, **[mac]**
  `0x18b0c3ec4`) only on the Metal path, so the software path produces uncompressed surfaces; the DCP side
  logs `IOMFB: Surface %d has cacheMode 0x%x, needs to be 0x%x for display RT
  fetch` and `GP Decompression Error` for the compressed case, which we then
  never enter. Risk, not blocker.
- Rotation/scaling copies: `CA::WindowServer::Display` has
  `iosurface_accelerator_supports_{scale,size,color_remap}` and the env
  `CA_FORCE_COPY_SURFACE_{GPU,MSR}` (**[dev]**/**[sim]** strings). The MSR is
  `AppleM2ScalerCSCDriver`, present in this kernelcache. Whether the software
  path needs it for a portrait phone panel is **unverified**.
- Speed: CPU compositing under TCG. Not measured.

## 7. For the record: what AGX would have required

The device build has not compiled the software path out (section 1), so this
is not needed. It is kept because it bounds the alternative: AGX would have
required three layers before any pixel: SPTM's UAT bring-up from iBoot-supplied
properties (`gpu-iouat`, UAT handoff, `gfx-shared-region-*`, `gpu-region-base`,
`gfx-handoff-base`, `uat-enforce-gpu-carveout`, `uat-vaddr-size`; section
4.4), the GFX RTKit firmware on `gfx-asc`/`gfx1-asc`
(`AGXFirmwareKextG16RTBuddy`, `AGXArmFirmware::kickFirmware`,
`AGXFirmware::isGFXBooted`), and `AGXAccelerator::start` /
`configureDevice` / `IOGPU` command queues feeding a user-space Metal plugin
(`MetalPluginName AGXMetalG17P`) that emits real shader ISA. That is a GPU
emulator, not a device model. Nothing found here suggests that is required.

## Open questions

Closed on the device build (2026-09-02, second pass):

1. ~~The iPhone QuartzCore~~ -- carries `sw_new_context` (`0x1846a04a4`),
   `CA::OGL::SWContext` (vtable `0x1e9050118`, 41 methods, `CA::OGL::SW` 379
   symbols); `AccelServer : IOMFBServer : Server` (vtables `0x1e904b218` /
   `0x1e9056e08` / `0x1e9056978`); `AccelServer::render_update`
   `cbz x0` at `0x1844515dc` -> `b Server::render_update` at `0x184451664`;
   `AccelServer::renderer` returns NULL after `_CAMetalContextCreate`
   (`0x18440f854`). Section 1.
2. ~~The iPhone Metal.framework~~ -- `MTLCreateSystemDefaultDevice` returns a
   zero-initialised result when the `IOAcceleratorES` enumeration adds
   nothing; no `MTLReportFailure`, no abort. Section 3.
3. ~~The iPhone backboardd~~ -- creates the server via `serverWithOptions:`
   before testing for a display, tolerates headless, imports only the two
   shader-cache Metal calls. Section 2.

Still open:

4. The exact SPTM panic for a bare `sgx` node (4.4); a `pmemsave` of the SPTM
   data pages at the parked PC would settle it. Only worth doing if someone
   wants to keep the node.
5. Whether the software path needs the M2 scaler for the internal panel
   (section 6). `CA::WindowServer::Display::iosurface_accelerator_supports_*`
   exist on device; their callers were not read.
6. Cosmetic: the virtual call site that invokes the `new_server` vtable slot
   (`+0x720`) was not located by pattern (`mov x17,#0x720`, `ldr [..,#0x720]`,
   `add ..,#0x720` all miss); the slot contents (1.3) make the answer
   independent of it.
7. Not measured: CPU compositing throughput under TCG, and what
   `SWContext::function_supported` declines on a real SpringBoard tree.
