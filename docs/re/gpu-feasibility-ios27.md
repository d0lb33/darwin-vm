# GPU feasibility for the existing iOS 27 VM

2026-09-05. Exact scope: **iOS 27.0, 24A5430a, iPhone17,3/T8140**,
with its existing SPTM/TXM, migrated Data lineage, software renderer,
and display/input path. This is a feasibility assessment, not a GPU driver
or a claim of useful application acceleration.

**Result:** an unmodified QuartzCore iOS AIR library runs on host Metal.
Its checked pixels can reach an existing guest IOSurface and the existing
DCP display. These are independently proven segments. Automatic guest Metal
discovery, a forwarding plugin, its transport, and accelerated checkpointing
remain untested end to end. No observed contract currently disproves all
three architecture options below.

## Baseline and isolation

Both project guides were read in full before project work. Parent branch
`codex/gpu-feasibility-ios27` was created in
`/Users/jdolbe1/Downloads/darwin-vm-gpu-feasibility` directly from local
`main`, `99e012c`. The QEMU worktree uses the same branch name at main's pinned
`a280b461b534d03d94ec3a8c16ed75ab052fc67f`; it has no code changes or rebuild.
Ignored `firmware` is a symlink to the existing artifacts.

The main checkout and GPU-research checkout both had uncommitted work. Neither
was edited. Three pre-existing VMs were observed at startup; no monitor,
debugger, signal, disk write, or input action was directed to those VMs.
The artifact namespaces for this assessment start with `GPU_FEAS_`.

Recent history establishes distinct milestones:

| Commit / source | Evidence established; scope limit |
| --- | --- |
| `c033aee`, `3a6f4b4`, `59aec44` | Warm-boot SEP work, native A408 descriptor/ID evidence, and correct witness-CPU selection for multicore checkpoint verification. |
| `8200632`, `934331c` | Six-core visible display and native window restores; measured **presentation submissions**, not necessarily distinct frames or application FPS. |
| `7b5481c`, `ab5b282`, `99e012c` | Rendered Home lineage, native input development, UART/display-completion fixes, and runtime evidence. A rendered-RAM restore is not an independent disk boot. |
| Main's uncommitted `docs/re/warm-boot-stability.md`, read at task start | Fresh migrated-disk boot reached Early boot complete but remained black; a rendered-Home restore continued presenting. Input v5 autostart/ACKs were observed; Settings' bitmap-image trap remained unresolved. This assessment does not claim to fix these issues. |
| GPU branch `549148a` | Host PV device/display creation and owned-memory mapping tests, plus an inventory of the exact guest and a different PCC reference. No guest GPU command execution. |
| Later **uncommitted** GPU-research draft/code | PV host port and zero-MMIO stock-guest boot evidence, followed by strong impossibility/compiler claims. Those conclusions were hypotheses, not results established by `549148a`. Several are corrected below. |

The rendered control is
`/tmp/dvm/checkpoints/NATIVE_HOME_UART_FIXED1/manifest.json`. Every restore
verified its input hashes and immutable backing chain and created a fresh qcow2
child. The tested System guest used these unchanged inputs:

| Input | SHA-256 |
| --- | --- |
| Pinned UART-fixed QEMU | `9d39357bd771a1089725654afc077de8925cdceca456b3561a0fd2def760abbd` |
| Existing six-core `DISPLAY_SMP6.bootkc` | `da1e254ab81e31adae87c049da295b582dabbd4ba46096fc58f3e5467fc6e02c` |
| SPTM | `b0fd274d3009ccfbc9902e99ef441a2231fb852931f8b31233e9bc0f55207048` |
| TXM | `b8617cfca055a03711247ad9652f3cfdb026889dcca2eb1436496df4cb61a398` |
| `firmware/bootkc` used for static inventory/control shell boot | `dc0f5b6a6fa848053c301949c8376c216c6223c047203b93e408a93d3440f906` |

The first restore inherited the checkpoint's touch-event output setting;
it used `-display none` and received no host input. Subsequent restores
explicitly used unique touch-event paths. Existing input helper/data contents
were not rewritten. All VMs created by this assessment were terminated after
collection. The diagnostic disk, RAM snapshot and shader copies stay outside git.

## Experiment 1: shader reuse before AGX lowering

**Proven by new runtime execution, narrowly scoped.** This is a guest UI
library, not a host-authored triangle or shader. The original System image
`~/dvm-artifacts/aea/out/094-13182-141.dmg` was attached read-only through
`tools/rootfs/safe_attach.sh`, three specific resources were copied, and the
image was immediately detached. Its SystemVersion plist says 27.0/24A5430a;
`firmware/info` identifies the iPhone17,3 IPSW. No migrated disk was mounted.

`GPU_FEAS_SHADER1/libraries/provenance.json` preserves guest paths, sizes,
headers, hashes and the copied SystemVersion plist:

| Exact guest resource | Bytes | SHA-256 |
| --- | ---: | --- |
| `/System/Library/Frameworks/QuartzCore.framework/default.metallib` | 10,960,816 | `a08bbc54de744c0a422e10323e995d769b581c6d6ec9322aeab9dd07d6ddf0ab` |
| `/System/Library/PrivateFrameworks/RenderBox.framework/default.metallib` | 1,946,716 | `030448dbaa3924799d9d80cdf3c5c631617cfe4f7f32190cb7377481af29289e` |
| `/System/Library/PrivateFrameworks/RenderBox.framework/archive.metallib` | 955,712 | `3a25eae958ab5157ee75222702bad1eea96f0126011082c3dce6f24d64a65d34` |

QuartzCore's fat container has an MTLB/AIR slice at file offset `0x30`,
length `0x294984`, SHA-256
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
The second slice is a Mach-O GPU archive. `extract_air.py` copies the MTLB
slice **without changing its bytes**, checks each function blob against its
embedded SHA-256, and extracts AIR only for inspection. The format parser
uses the documented reverse-engineering layout from
[MetalLibraryArchive](https://github.com/YuAo/MetalLibraryArchive).

The executed entry is **`read_write_surf_compute`**, also referenced by the
exact QuartzCore image's string at `0x18481455f`. Its AIR module is SHA-256
`4293ebacceed9fec060945b9aacb32c746b84c7f7ee675c987fa8aeaf617555b`.
`llvm-dis` successfully decodes it as
`target triple = "air64_v29-apple-ios27.0.0"`, AIR 2.9 / Metal 4.1.
It reads a half4 from source texture 0 at `gid`, writes it to destination
texture 1 at that coordinate, and checks source width/height first. This
provides the independent CPU oracle; the name alone was not the oracle.
The disassembly is `GPU_FEAS_HOST_FINAL1/read_write_surf_compute.ll`.

On **Apple M5 Max, macOS 27.0/26A5421a**:

* `newLibraryWithData:error:` loads both the intact fat resource and the
  byte-identical AIR-only slice. No target/platform patch, MSL recompilation,
  custom AIR compiler, or host-authored shader is used.
* Both expose 249 QuartzCore functions. Of 54 kernel functions, 39 create
  ordinary compute pipelines and 13 create tile pipelines. Two requiring
  specialization constants were deliberately skipped by the final inventory.
  This is pipeline creation, not execution coverage for all 52.
* `read_write_surf_compute` creates a compute pipeline and executes into a
  **host IOSurface-backed BGRA8Unorm texture**. The test runs three distinct
  patterns on each of three freshly created texture/surface generations.
  It dispatches 72x56 threads over a 64x48 texture to exercise bounds checks.
* Every destination is filled with `0xa5` before submission. The negative
  control differs from the expected image. After `commit` and
  `waitUntilCompleted`, command status is 4 (`Completed`); both texture
  readback and locked IOSurface CPU storage equal all **12,288 expected
  bytes**, zero mismatches, in all nine runs. Input alpha is 255.
* Running this on the fat container and the AIR slice in separate processes
  produces the same result. RenderBox's default library loads and exposes
  109 functions, but its functions have not been pipeline/execution tested.

`GPU_FEAS_HOST_FINAL1/commands.json` records each command, return code and
elapsed time; corresponding `.stdout/.stderr` and `hashes.json` are retained.
The earlier exploratory records are in `GPU_FEAS_SHADER1`.

### Exact failures, with corrected scope

| Experiment | Observed failure | Interpretation / follow-up |
| --- | --- | --- |
| Initial unspecialized `backdrop_capture_compute_tile_lpf` compute pipeline | SIGABRT, `validateWithDevice:1623: failed assertion 'Compute Pipeline Descriptor Validation ... function backdrop_capture_compute_tile_lpf cannot be used to build a pipeline state. Use newFunctionWithName:constantValues:... to get the specialized function'` | Probe omitted required constants. Final inventory skips two such functions; their specialized pipelines/execution remain **untested**. Original stderr: `GPU_FEAS_SHADER1/QuartzCore.framework.default.metallib.stderr`. |
| 13 tile functions passed to the ordinary compute-pipeline API | `AGXMetalG17X`, code 3: `Encountered unlowered function call to air.load.implicit_imageblock.v4f16. Verify device or GPU architecture supports this feature` (some use `air.store.implicit_imageblock.v4f16`) | All 13 succeed with `newRenderPipelineStateWithTileDescriptor:options:reflection:error:`, BGRA8Unorm and one sample. **Disproven as a compatibility blocker for pipeline creation**; tile execution is untested. Both results are recorded per function. |
| RenderBox archive / QuartzCore Mach-O slice passed as MTLLibrary | `MTLLibraryErrorDomain`, code 1, `Invalid library file` | Both are accepted by **`newBinaryArchiveWithDescriptor:error:`**. This was the wrong object/API type, not proof that their GPU code is incompatible. Archive instantiation does not prove a pipeline cache hit or execution of archived code. |

Thus “a custom forwarding plugin necessarily needs a new shader compiler” is
**disproven within this iOS AIR → this host Metal scope**. Broader shaders,
function constants, framebuffer fetch execution, private formats, argument
buffers, linked functions, dynamic libraries and other host GPU generations
are not covered. A copy shader is a small UI-stack operation; useful UI
speedup and full compositor semantics remain unmeasured.

## Experiment 2: exact guest discovery and integration contracts

This portion is **static evidence**, not a successful plugin installation.
Inputs: exact dyld cache UUID `58C54E82-C171-300E-AEEE-06DF937AA565`,
IOGPUFamily 162.11, IOSurface 402.8. Complete disassemblies and matching-source
excerpts are under `/tmp/dvm/GPU_FEAS_CONTRACT1/raw/`; its README records
commands and hashes.

### Discovery is a class match, not merely a category property

Metal at `0x1a54fcdc4` calls `IOServiceMatching("IOAcceleratorES")`, then
`IOServiceGetMatchingServices` at `0x1a54fcdd4`. The exact guest IOKit
wrapper at `0x18efe509c` moves the supplied name into x1, forms
`"IOProviderClass"` in x0 at `0x18efe50a0-0x18efe50a4`, and tail-calls
`MakeOneStringProp` at `0x18efe50a8`. Raw witness:
`GPU_FEAS_CONTRACT1/raw/13-exact-IOServiceMatching.txt`, reproduced with
`ipsw dyld disass CACHE --symbol _IOServiceMatching --no-color`.
In Apple's XNU reference
`IOService::serviceMatching` sets **IOProviderClass** and `matchPassive`
checks **metaCast**. `IOMatchCategory` is separately used for driver
arbitration. The source witness is XNU `f6217f891ac0bb64f3d375211650a4c1ff8ca1ea`,
`iokit/Kernel/IOService.cpp:6882-6900,7820-7827,3947-3954,4092-4100`.
That reference is older than this guest, so it is identified as reference
source rather than exact-guest runtime observation.

The **exact guest** IOGPUFamily metaclass constructors independently show:
`IOAcceleratorES` registration at `0xfffffff009fb9204`, size `0x88`,
metaclass storage `0xfffffff00b87db98` returned at `0xfffffff009fb91e8`;
`IOGPU` registration at `0xfffffff009fb926c` passes that storage as its parent,
size `0x2e8`. Thus IOGPU derives from IOAcceleratorES.

A plain IOService with only `IOMatchCategory=IOAcceleratorES` is not a
supported discovery recipe. The inspected Metal discovery path does **not**
separately test IOGPU inheritance. A direct IOAcceleratorES subclass could
satisfy this particular matcher, but this small base class resides in the
IOGPUFamily module: avoiding the full IOGPU device/user-client hierarchy
would not automatically remove the binary/module dependency.
A simpler service behind an explicitly instantiated/interposed plugin is
another architectural possibility; it bypasses automatic discovery and does
not prove system-wide registration.

### Actual minimum registration and narrow-workload calls

| Stage | Exact evidence / measured calls |
| --- | --- |
| Select bundle/class | `getMetalPluginClassForService` `0x1a54fd054`: registry `MetalPluginName` / `MetalPluginClassName`, System Extensions bundle path; per-service call at `0x1a54fce38`. Filesystem existence and class resolution are separate gates. |
| Instantiate | `processPendingCreateIOAccelServiceRequests` `0x1a54fdde4`: `acceleratorPort`, `deviceClass`, allocate class, `initWithAcceleratorPort:`; nil skips registration. |
| Register | `MTLAddDevice` `0x1a54ff634`: `MTLDevice` and `MTLDeviceSPI` conformance, then `initLimits` at `0x1a54ff6d4`, `initFeatureQueries` at `0x1a54ff6e0`, `initWorkarounds` at `0x1a54ff6ec`. The private protocol check does not itself exercise every declared selector. |
| Reusable base? | `_MTLDevice` implements `initLimits` (`0x1a5502d88`), feature initialization (`0x1a5503d74`), and no-op workarounds (`0x1a550b524`). Its `initWithAcceleratorPort:` (`0x1a55f6b38`) calls `doesNotRecognizeSelector:` and returns nil: simply inheriting it without a concrete initializer fails that contract. Inherited initializers' additional queries still need tracing. |
| Stock concrete path | Exact ObjC metadata: `AGXG17PDevice → AGXG17FamilyDevice → IOGPUMetalDevice → _MTLDevice`. IOGPUMetalDevice owns an IOGPU device object. Reusing that stock implementation carries its concrete user-client ABI; this is not a requirement proven for a new implementation. |
| Offscreen copy workload | The successful host harness exercises library loading, function lookup, compute pipeline creation, queue/command-buffer creation, texture allocation/upload, compute encoder state/texture binding/dispatch/end, commit/completion wait/status, and readback. A forwarding implementation needs these behaviors plus its own ID/ownership/error protocol. |
| Surface variant | Host harness also exercises `IOSurfaceCreate`, Metal's `newTextureWithDescriptor:iosurface:plane:`, CPU lock/unlock and readback. Guest IOSurface allocation/import, guest cache policy and fences are separate runtime contracts. |

This is a bounded starting interface, not a claim that those methods cover
QuartzCore startup. Publishing a partly implemented device globally could
make CoreAnimation select Metal and abandon the currently working software
fallback. Keep initial experiments process-local/opt-in; preserve the default
no-accelerator configuration. Unsupported operations must fail explicitly.

### Signing, loading and kernel requirements

A native plugin needs the correct **iOS** Mach-O platform/runtime linkage,
its Objective-C class/metadata, the observed bundle path and registry
properties, an accepted code signature, and whatever trust/library-validation
policy applies in its actual process. The host SDK lacks MTLDeviceSPI and
private IOGPU/IOAccelerator headers; recover the needed exact declarations or
use verified runtime lookup. macOS plugin binaries are not automatically iOS
plugin binaries.

The project has a concrete executable precedent: `tools/input/build.sh`
creates/signs an iOS helper, and the input provisioning tools add its code
hash to a **derived** trust cache and install it in a disposable guest child.
That establishes executable provisioning in this lineage. It does not prove
that a plugin dlopen or a new kernel service passes the same policy.

For automatic enumeration, someone must create/register the requisite kernel
class and any user client/transport it needs. A direct IOAcceleratorES
subclass still needs correct base layout/vtable/metaclass bindings and a
verified kernel loading/prelink path under the retained SPTM/TXM.
AuxKC-related strings and stripped symbols do not establish either successful
loading or impossibility. No custom kext/AuxKC/plugin signing load was attempted
here, so no invented AMFI/TXM rejection is reported. A bundle-load marker,
class resolution, initializer result and precise kernel/user-client failure
are the next required runtime evidence.

## Experiment 3: host pixels → guest IOSurface → existing display

**Proven runtime segment via diagnostic copy.**
`GPU_FEAS_PRESENT1` restored the rendered control into its own disk child,
pinned QEMU, six CPUs, 12 GiB, identical SPTM/TXM and existing DCP settings.
It used the existing saved display state; this is not a new disk-boot result.
No guest instruction, kernel code or device model was patched.

`host_surface_patch.py` attaches **only to the owned QEMU PID**, validates the
pinned binary hash, and stops at an instruction verified in that binary's
`iomfb_scanout` (`+80`, after the dart-disp0 lookup). At the native A408
submission it validates the measured primary BGRA profile: 1179x2556,
stride 4864, allocation 12,435,456, DVA **`0x10000bfc000`** and the optional
surface flags. The source implementation is
`qemu-sptm/hw/arm/darwin_iomfb.c:495-525,1342-1344`.

With the QEMU BQL held and guest CPUs stopped, the probe replaces **only**
the 64x48 rectangle at (100,100). It translates each affected page through
**dart-disp0/SID 0**, then uses QEMU `address_space_write` to copy bytes from
the completed host-Metal result into existing guest surface backing. It
reads those GPAs back through `address_space_read_full` and checks equality.
It then steps out through the **normal** scanout, which DMA-reads that guest
backing and calls the existing display sink, and pauses the VM for capture.
It does not write the host display buffer directly.

| Witness | Result |
| --- | --- |
| Completed host BGRA → guest GPA readback | 12,288 bytes, SHA-256 `ec8d1d034b2edcd412187274f6e703b23165b80f879509ab3fa4f790f8a02656`, equal. |
| Existing DCP scanout → QEMU PNG rectangle | All 9,216 RGB bytes equal; SHA-256 `cb0fdb84cbe8b176228bf84cddc1b53fc30bf364a76642f2b8e04e17aa04622d`. |
| Negative control | Original rectangle SHA-256 `5e9d5b686a0c4122c77d69144a5874dcbbcd05c1b4717bb11d8d730cc17a6659`, different. |

Evidence: `GPU_FEAS_PRESENT1/patch-result.json`, `guest-readback.bgra`,
`original-rectangle.bgra`, `after.png`, `screen-verification.json`,
`pixel-patch6.lldb.log`, and `scanout-disassembly.log`.
The frame shows the existing Search/status UI plus the diagnostic square.
There is no claim that the full UI frame was host-GPU rendered.

Earlier attempts failed **before a guest pixel write**: optimized/inlined
helpers were not available by name; a source-line breakpoint had no unique
location; LLDB's default expression mode rejected local C++ constructs.
Exact transcripts are `pixel-patch.lldb.log` and `pixel-patch2..5.lldb.log`;
structured failures are `patch-attempt1.json` and `patch-attempt5.json`.
Resolving actual symbols/offsets and selecting C++17 produced the successful
sixth attempt. These are tooling-contract failures, not IOSurface failures.

This disproves the earlier claim that no portion of host→guest presentation
can be tested without a PV mapper or working GPU command-submission stack.
It does **not** prove allocation of a new guest IOSurface, zero-copy import
of a host surface, compressed/multiplane/protected surfaces, or racing GPU
writers. The existing guest has already allocated and submitted the surface.

## Experiment 4: concrete state boundary

After the successful copy, `create_checkpoint.py` captured
`GPU_FEAS_PIXELS1` (8,437,677,397-byte stream; migration 3.059 s), froze that
**disposable** disk and terminated its source VM. Two separate processes,
`GPU_FEAS_PIXELS_R1` and `GPU_FEAS_PIXELS_R2`, restored that same immutable
snapshot into two fresh children. Both matched the recorded witness CPU/PC.

Before resuming either guest:

* The displayed rectangle matched all 9,216 RGB bytes above.
* An independent HMP DART walk from MMIO `0x412300000`, SID 0, DVA
  `0x10000c72d90` read 228,864 bytes spanning the rectangle's rows. Extracting
  256 bytes per 4864-byte row matched all 12,288 original BGRA bytes.
* After resume, each recorded **`D594 nested completed`** through the
  existing guest completion path. Each was paused and terminated after
  condition-bounded collection. No XNU panic appeared in these runs.

Each restore directory contains `restore-report.json`,
`surface-rows.bin.json` (PTE/GPA witnesses), `screen-verification.json`,
`state-verification.json`, the screenshot and runtime logs.

**This proves copied RAM pixels and the existing display/completion state
survive two fresh-process restores. There was no live guest GPU device or
host Metal object in that VM to serialize.** The host command was completed
before injection; no command queue, pipeline, MTLTexture/IOSurface handle,
shared event, compiler cache or GPU completion callback was migrated.
The host harness separately recreated surfaces/queues in new processes and
reused surfaces after completion, not across VM suspension.

Major untested state contracts for either accelerated route:

1. Stop accepting new work, then drain guest submissions, host GPU queues,
   worker threads, completion callbacks and IRQ bottom halves before RAM
   serialization. Test an intentionally delayed command at the boundary.
2. Preserve guest-visible IDs, ownership, mappings, fences and pending
   completions; reject stale IDs with generations. Recreate host objects in
   the destination process and replay library/pipeline/resource descriptions;
   pointer values and IOSurface IDs are not a portable checkpoint format.
3. Keep the guest surface alive until host writes finish; make completion
   visible only after pixel visibility; retain the existing rule that scanout
   copies before D594 can release the mapping. Test producer/consumer races,
   reuse, cancellation, errors and reset, not just sequential success.
4. Track **all** host-originated writes in guest RAM dirty accounting. This
   diagnostic uses QEMU's GPA write path. A framework holding a raw RAM pointer
   can bypass it; a private-mmap bounds test is not a dirty-log or DMA test.
5. Prove pending IRQ delivery exactly once after restore. An API named
   suspend/resume and an interrupt callback counter establish neither a drain
   nor restored guest completion semantics.

The existing PV implementation explicitly installs a migration blocker
(`apple-gfx.m:889-895` in the GPU research worktree). Its `darwin-gpu` class
has no VMState assignment (`darwin_gpu.c:272-283`), and its backend only has
reset/destroy, no save/restore hooks (`darwin_gpu_pvg.m:208-225`). Accelerated
checkpointing in that implementation is **disproven as currently supported**;
its implementability remains untested. Detailed source audit:
`/tmp/dvm/GPU_FEAS_PV_STATE1/audit.md`. Do not remove the blocker on the
strength of this copied-pixel checkpoint test.

## Architecture route verdicts

| Route on the retained guest | Verdict | Proven pieces and major unresolved dependencies |
| --- | --- | --- |
| Adapt Apple's PV stack | **Untested end to end** | Historical host creation/map tests and device skeleton exist. Stock exact guest did not bind the added nodes or issue MMIO in the earlier boot. A compatible/adapted guest kernel service, userspace PV plugin, mapper and exact protocol remain absent from the demonstration. PCC's IOGPUFamily 130.12 / IOSurface 393.5.7 vs this guest's 162.11 / 402.8 implies ABI work, not impossibility. Actual RAM DMA, IRQ, reset and state contracts remain open. No other guest was booted for this assessment. |
| Custom guest plugin forwarding to host Metal | **Untested end to end; shader and copied-presentation segments proven** | Unmodified guest AIR reuse works. The plugin can in principle forward library bytes, function names/constants, resource descriptions and a bounded command stream. Required unknowns: plugin load/signature, registration/private initialization, guest resource/cache semantics, transport, lifetime/fences, capability reporting and application method coverage. Automatic discovery needs the class contract above; a full IOGPU subclass has not been shown necessary. |
| Interception/reuse before lowering | **Untested end to end; available pre-lowering input proven** | Exact QuartzCore AIR and named function are available. A process-local wrapper around library/pipeline/encoder APIs could reuse them before stock AGX compilation; registering a global accelerator could be deferred. Actual interposition under this guest's dyld/signing policy, private direct-call bypasses, ObjC/PAC behavior, and workload coverage remain untested. |
| Reuse stock AGX after lowering | **Untested** | Stock plugin/compiler/user-client coupling is static evidence. The host API does not generally accept arbitrary raw AGX command streams. However, both supplied archive objects instantiate on this host; archive execution/cache-hit compatibility was not tested. No categorical AGX-emulation/no-reuse conclusion follows from strings or archive-load results alone. |
| Keep current software rendering | **Proven existing fallback; new control boot/restore checks passed** | Preserves the working display/input lineage. Independent fresh disk-to-UI startup and Settings stability remain separate open work. No acceleration speedup is claimed. |

## Smallest next implementation, after those dependencies

Start with a **process-local guest plugin-load and one-operation forwarding
harness**, using a fresh child of this exact migrated guest. Give its bundle
initializer and concrete device initializer unique observable markers. Load
it explicitly in the harness first; record platform/signature/dyld errors
verbatim. Exercise only the already-verified QuartzCore AIR copy through a
small host request/reply protocol with bounded lengths, resource IDs and an
explicit completion. Use copied ordinary buffers initially, then the existing
IOSurface allocation/readback interfaces. Reuse `metal_copy_probe.m`'s oracle
on the returned bytes. Unsupported calls must be logged and rejected.

This is smaller than a global device because it can test the loader, the
concrete `_MTLDevice`-derived object and its narrow methods without first
solving a new kernel service. Its explicit instantiation would **not** prove
normal Metal discovery. In parallel planning, the next discovery experiment
is a minimal IOAcceleratorES-derived provider/loadability probe, or a verified
process-local interception of the default-device call. Choose between those
only after observing the relevant loader contract. Neither requires assuming
a new shader compiler.

Before expanding to system-wide CoreAnimation, establish which additional
selectors and resource capabilities that exact workload actually invokes,
and make device publication opt-in so unsupported workloads can retain the
software path. Then test correctness and frame time on repeatable UI actions.
No amount of success on this copy kernel establishes useful UI acceleration
until those workload, synchronization and performance gates pass.

## Reproduce and evidence locations

All commands run from this assessment worktree. Use fresh tags/directories.
The original firmware/shader binaries, extracted AIR and RAM streams must
remain outside git.

```sh
# Read-only resource acquisition; detach only this attachment.
bash tools/rootfs/safe_attach.sh attach \
  /Users/jdolbe1/dvm-artifacts/aea/out/094-13182-141.dmg --readonly
python3 tools/gpu/extract_ui_libraries.py \
  /Volumes/RaveSeed24A5430a.D47DeveloperOS /tmp/dvm/GPU_FEAS_NEW_LIBS
bash tools/rootfs/safe_attach.sh detach /Volumes/RaveSeed24A5430a.D47DeveloperOS

# Host-only, all commands/return codes/hashes and checked GPU output.
python3 tools/gpu/run_host_checks.py \
  /tmp/dvm/GPU_FEAS_NEW_LIBS /tmp/dvm/GPU_FEAS_NEW_HOST

# Actual observed control restore; future runs should also override the event path.
python3 tools/restore_checkpoint.py \
  /tmp/dvm/checkpoints/NATIVE_HOME_UART_FIXED1/manifest.json \
  --tag GPU_FEAS_PRESENT1 --out /tmp/dvm/GPU_FEAS_PRESENT1 \
  --leave-paused --display none --gdb-port 1627
```

For the successful diagnostic, the recorded owned PID was **19823**, not a
PID to reuse. `pixel-patch6.lldb.log` contains the full LLDB command transcript:
attach owned PID; pass SIGUSR1/2 through; import `tools/gpu/host_surface_patch.py`;
call `install(debugger, HOST_BGRA, OWNED_OUTPUT_DIRECTORY, OWNED_PID)`;
continue the host. Once `GPU_SURFACE_READY` appears, issue `cont` through that
VM's HMP socket. At the one-shot patch stop, `thread step-out` executes the
existing scanout; `host_surface_patch.pause(debugger)` calls the pinned
QEMU's `vm_stop(RUN_STATE_PAUSED)`; delete breakpoints and detach. The helper
requires the pinned QEMU hash because its `+80` breakpoint/register witness
is build-specific. The observed `install` arguments are in the transcript.
Check `patch-result.json` before claiming success.

```sh
python3 tools/hmp.py /tmp/dvm/GPU_FEAS_PRESENT1.restore.sock \
  'screendump /tmp/dvm/GPU_FEAS_PRESENT1/after.png -f png'
# Run with a Python that has Pillow (the bundled runtime was used here).
python3 tools/gpu/verify_present.py /tmp/dvm/GPU_FEAS_SHADER1/air.bgra \
  /tmp/dvm/GPU_FEAS_PRESENT1/after.png /tmp/dvm/GPU_FEAS_PRESENT1/screen-verification.json

python3 tools/create_checkpoint.py --tag GPU_FEAS_PIXELS1 \
  --out /tmp/dvm/GPU_FEAS_PIXELS1 --monitor /tmp/dvm/GPU_FEAS_PRESENT1.restore.sock \
  --pid-file /tmp/dvm/GPU_FEAS_PRESENT1/qemu.pid \
  --launch-manifest /tmp/dvm/GPU_FEAS_PRESENT1/launch.json \
  --disk /tmp/dvm/GPU_FEAS_PRESENT1/disk.qcow2 \
  --serial-log /tmp/dvm/GPU_FEAS_PRESENT1/serial.log --timeout 60

# Repeat with R2 and a distinct port/path; inspect the result before resuming.
python3 tools/restore_checkpoint.py /tmp/dvm/GPU_FEAS_PIXELS1/manifest.json \
  --tag GPU_FEAS_PIXELS_R1 --out /tmp/dvm/GPU_FEAS_PIXELS_R1 \
  --leave-paused --display none --gdb-port 1628 \
  --model-env DARWIN_TOUCH_EVENTS=/tmp/dvm/GPU_FEAS_PIXELS_R1/touch.jsonl
python3 tools/re/dart_read.py --monitor /tmp/dvm/GPU_FEAS_PIXELS_R1.restore.sock \
  --mmio 0x412300000 --sid 0 --dva 0x10000c72d90 --size 228864 \
  --out /tmp/dvm/GPU_FEAS_PIXELS_R1/surface-rows.bin
```

The DVA above belongs to the **observed** surface, not a universal allocation
address. Derive it from each native A408 witness. Compare 48 successive
256-byte rows at stride 4864 against the host BGRA, capture/verify the PNG,
then resume until `D594 nested completed` (20-second deadline here), pause
and quit the owned guest. No further debugger injection occurred on restores.

A durable copy of the small text/JSON/pixel evidence (excluding firmware, AIR
modules, disk images and RAM streams) is at
`~/dvm-artifacts/research/gpu-feasibility-ios27-20260905/`;
`evidence-index.json` hashes each retained file. Original experiment paths
remain in the records.

Other evidence: exact-guest static commands in
`GPU_FEAS_CONTRACT1/README.md`; PV source audit in `GPU_FEAS_PV_STATE1/audit.md`;
host final `commands.json`, per-command output and hashes in
`GPU_FEAS_HOST_FINAL1`; exploratory failures and extraction provenance in
`GPU_FEAS_SHADER1`.

Validation: 30 existing host tests pass; shell syntax checks pass; both host
Objective-C probes compile with `-Wall -Wextra -Werror` (SDK deprecation
warnings disabled for reflection API usage). The first test run had one
missing-submodule-file error; initializing the isolated pinned QEMU worktree
resolved it. New host execution/IOSurface oracles and both checkpoint pixel
checks pass. `GPU_FEAS_BASE1` is the separately required 60-second restore
ramdisk control, using the pinned QEMU and existing firmware SPTM/TXM, with
no persistent disk attached:

```text
serial lines : 303
xnu panics   : 0
reached shell: yes
```

Its exact launch is `GPU_FEAS_BASE1/launch.json`; verdict/log paths are in
`GPU_FEAS_SHADER1/baseline-probe.log`. This shell control does not substitute
for independent migrated-disk UI startup or GPU acceleration acceptance.
