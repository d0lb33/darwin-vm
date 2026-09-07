# Boot-time Metal registration in the exact iOS compositor

2026-09-07. Exact iOS 27 build 24A5430a, iPhone17,3/T8140.

## Current result

**Proven:** the real backboardd process loads our arm64e Metal driver during
boot and the exact guest's ordinary `MTLCreateSystemDefaultDevice()` returns
that device after `MTLAddDevice`. This is per-process boot registration in
backboardd, not stock IOAcceleratorES enumeration or universal app discovery.
No injected CALayer, replacement Metal factory, debugger, or test-helper
consumer participates in this experiment.

**Current boundary (GUEST12):** the compositor requests an existing full-screen
RGBA16Float IOSurface. Its ownership and mapping are outside our fixed owned
BGRA-pool import contract. Shader/LUT initialization progressed through 53
successful host requests; no render/blit submission occurred.

**Not yet proven:** a compositor GPU submission, GPU-produced system UI
pixels, or preserved display/input during accelerated composition. Prior
CARenderer/UIKit helper results do not establish those outcomes.

## Evidence and failed contracts

| Trial | Observed result | Scope / response |
|---|---|---|
| CA_SYSTEM_BOOT_GUEST1 | No registration record before the boot deadline; read-only process inspection found no backboardd instance. | Console log redirection was changed to an ordinary mobile-owned log path. The specific launch error was not captured; do not claim a proven console errno. |
| CA_SYSTEM_BOOT_GUEST2 | Backboardd DYLD failure: `incompatible architecture (have 'arm64', need 'arm64e')`. | Rebuilt the plugin and its exact-cache link stubs for arm64e. Guest version and backboardd instruction sections unchanged. |
| CA_SYSTEM_BOOT_GUEST3 | `GPU_LOAD_SYSTEM_REGISTERED pid=73 registry=4294968234`; then `-[DVMQueue setSubmissionQueue:]: unrecognized selector`. | Exact constructor also calls `setCompletionQueue:`. Implemented retained caller queues, ordered submission, callback delivery, and bounded command buffers together. |
| CA_SYSTEM_BOOT_GUEST4 | Registration again; queue-pair exception passed; `-[DVMQueue setGPUPriority:]: unrecognized selector`. No render submissions. | Exact constructor also requests background priority. The two optional setters now explicitly return NO; GPU priority tuning remains unsupported. |
| CA_SYSTEM_BOOT_GUEST5 | Registration, exact QuartzCore library load and two private RGBA16Float mipmapped allocations succeeded; then `-[DVMCommand blitCommandEncoder]: unrecognized selector`. | Exact caller requests texture copies and optional mipmap generation. No GPU submission yet. |
| CA_SYSTEM_BOOT_GUEST6 | Blit encoder reached; overlapping/alias guard rejected the warm-up self-copy before submission. | Exact call uses nonoverlapping regions of the same texture. Native comparison verified the narrower overlap check. |
| CA_SYSTEM_BOOT_GUEST7 | Nonoverlapping self-copy passed; allocation rejected format 554. | BGR10_XR, R8 and RG8 allocation/render/mipmap support was validated together. No committed GPU work. |
| CA_SYSTEM_BOOT_GUEST8 | 25 successful host requests; `-[DVMDevice newLibraryWithFile:error:]` missing. | Implemented file → URL → unchanged MTLB slice loading and requested-path audit. |
| CA_SYSTEM_BOOT_GUEST9 | 26 requests, 48.670 s; exact HDRProcessing AIR request rejected by the single-library host configuration. | Guest path `/System/Library/PrivateFrameworks/HDRProcessing.framework/default.metallib`; 3,172,860 bytes, SHA256 `f7e1436f9e05d74fa0ed1227f0d49a3511bedf960e49ced2c31fe759d3e0eb3b`. Added explicitly provisioned libraries selected by digest, not guest path. |
| CA_SYSTEM_BOOT_GUEST10 | 29 successful host requests, 48.773 s; QuartzCore and HDRProcessing libraries load. | Next rejected descriptor: type 0, width 3072, height/depth 1, format 23, storage 0, usage 1, one level. This is a shared sampled 1D R16Uint LUT. |
| CA_SYSTEM_BOOT_GUEST11 | 35 successful host requests, 49.798 s; integer 1D LUT allocations passed. | Captured the related float LUT batch: R16Float (25), R32Float (55), RG32Float (105), widths 1024/4096. No committed GPU work. |
| CA_SYSTEM_BOOT_GUEST12 | 53 successful host requests, 91.196 s; all observed HDR LUT allocations passed. | Next failure: real IOSurface 2, 1179×2556, RGBA16Float (115), shared storage, usage 5. The driver only imports its own BGRA pool. No GPU submission or verified system frame. |

First records: `/Users/jdolbe1/dvm-artifacts/research/gpu-system-boot-20260907-first`.
Registration and queue records: `/Users/jdolbe1/dvm-artifacts/research/gpu-system-boot-20260907-registration`.
Blit, loading-probe and HDR records: `/Users/jdolbe1/dvm-artifacts/research/gpu-system-boot-20260907-blit-hdr`.
Its diagnostic index excludes full RAM/disks. `execution-inputs/index.json` separately records copied signed bootstrap binaries, host workers and shader resources.

The first GUEST1 diagnostic initially assumed a wrong DRAM address and read
zeros. Those reads are invalid evidence. Correct managed DRAM starts at
`0x10000000000` and spans `0x300000000` bytes; the managed RAM file supports
read-only postmortem inspection without another provisioning boot.

## Mechanism and scope

`system_bootstrap.m` is compiled together with the existing driver frontend
and MMIO/shared-RAM transport. It is added as an LC_LOAD_DYLIB dependency to
an isolated copy of backboardd, using verified empty header padding. The
builder verifies that all instruction sections retain their original hashes.
The executable keeps its original entitlements and adds only platform status,
the dedicated transport entitlement and its IOKit client exception. Both
binaries are signed and their hashes are added to the disposable boot trust
cache. Backboardd remains the mobile launchd service.

The constructor checks the process name, opens the existing transport,
negotiates the supported guest/host profile, obtains the real registry ID,
adds MTLDeviceSPI protocol identity and calls the guest's exported MTLAddDevice.
It then requires pointer identity from the ordinary default-device factory.
Protocol identity is not a claim of complete private-interface compatibility.
Unsupported selectors still raise an exception; a bounded uncaught-exception
record now reaches the host audit ring directly.

Static exact-cache evidence: MTLAddDevice `0x1a54ff634`; public default factory
`0x1a54bfc28`; submission/completion setters at QuartzCore `0x184588ce4` and
`0x184588cf0`. Priority/background setters at `0x184588da0` / `0x1845891c4`
have ignored return values. The exact IOGPU priority implementation returns
success as a boolean (`0x20b8e62e0` / `0x20b8e62e4`). Returning NO therefore
declines an optional policy; it does not fake successful scheduling changes.

QEMU, kernel, DT, SPTM/TXM, native SMC and migrated disk ancestry are pinned
from the existing validated manifest. This change does not modify them.
The original software image remains untouched, and original backboardd and
launchd-cache copies are retained inside each disposable installation.
Before registration, nil-device software selection remains available.
Transparent fallback after an accelerated compositor failure is unproven;
recover by discarding the experimental child and using the software parent.

## Reproduction

From this worktree, with Xcode-beta selected through DEVELOPER_DIR:

```sh
python3 tools/gpu/build_system_bootstrap.py \
  /tmp/dvm/CA_UIKIT_INVALIDATION_RED_BUILD1 \
  /Users/jdolbe1/dvm-artifacts/extract/bin/backboardd \
  /tmp/dvm/CA_SYSTEM_BOOT_BUILD12
python3 tools/gpu/prepare_system_bootstrap.py \
  /tmp/dvm/CA_SYSTEM_BOOT_BUILD12 \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-render-ready-ios27/launchd.plist \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-handoff-ios27/tc \
  /tmp/dvm/CA_SYSTEM_BOOT_STAGE12
python3 tools/gpu/run_guest_install.py \
  --manifest /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/control.json \
  --stage /tmp/dvm/CA_SYSTEM_BOOT_STAGE12 --tag CA_SYSTEM_BOOT_INSTALL12 --mmio-restore
python3 tools/gpu/run_system_boot.py \
  /tmp/dvm/CA_SYSTEM_BOOT_INSTALL12/warm-manifest.json \
  --worker /tmp/dvm/CA_SYSTEM_LUT_HOST12/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /tmp/dvm/CA_SYSTEM_LIBRARY_CACHE10 \
  --tag CA_SYSTEM_BOOT_GUEST12
```

Each tag/output must be new. The installer verifies preimages before changing
its own child and checks backing-chain hashes afterward. The boot harness
stops on the first captured failed compositor contract or its boot deadline.
Its positive stop condition records rendering and native presentation as
separate observations, explicitly leaving pixel verification false.
This is an automated boot experiment; it does not impose a lifetime on the
separate persistent interactive GPU session.

## Validation

Focused host tests passed for queue FIFO, configured submission and completion
targets, retained callback order, per-queue bounds, unsupported priority
rejection, resource ownership/error handling, writeback-before-completion,
capability negotiation, and the existing two-pass public Metal workload.
The scheduling unit is a host CPU test, not guest GPU evidence.

The next acceptance is actual compositor rendering and verified output,
followed independently by native presentation/input and sustained pacing.
Generic compositor IOSurface import and synchronization remain major
unresolved dependencies; existing owned-pool tests do not cover arbitrary
client or display IOSurfaces.

## Coherent resource batches and layered validation

V18 adds ordered blit passes, private 2D mip generation, nonoverlapping
same-texture copies, aligned buffer fills/copies, and the observed warm-up
color formats. Render/blit counters are separate. An invalid later operation
prevents earlier operations in its uncommitted batch from executing.
IOSurface and buffer-backed texture blits still reject unsupported ownership.
The exact warm-up caller is `CA::OGL::MetalContext::warmup_shaders(bool)` at
`0x1846de50c`; encoder creation `0x1846deb6c`, copy `0x1846debd8`, mip generation
`0x1846dec00`. Encoding these calls is not evidence that they were committed.

V19 added the observed shared, sampled R16Uint 1D resource; V20 covers the
related R16Float/R32Float/RG32Float requests captured in GUEST11. Their packed CPU
transfer semantics use zero native 1D row/image pitches and read-only 1D
fragment bindings. Arrays, LUT render targets, private/mipmapped 1D,
and LUT linear views remain unsupported. All other established bounds
remain in force. Host capability negotiation verifies that the advertised
native allocations succeed; frontend and backend require the same version.

Host results, explicitly separate from guest rendering:

- `CA_SYSTEM_BLIT_HOST8/blit-test.log`: 48 frames, 96 native-equal outputs,
  six color formats, buffer writeback, retirement and atomic rejection pass.
  The same suite passes with V19 in `CA_SYSTEM_LUT_HOST11/blit-test.log`.
- `CA_SYSTEM_LIBRARY_TEST9.log`: requested file and URL both load the exact
  QuartzCore AIR on native Metal (249 functions); a missing file performs no RPC.
- `CA_SYSTEM_LIBRARY_REPLAY10/result.json`: all 26 captured GUEST9 requests
  replay in 0.0353 s with Metal validation enabled. Only the explicitly selected
  previously failed request 26 changes to success. This proves the HDR library
  loads on host Metal, not that its shaders have executed in the guest.
- `CA_SYSTEM_LUT_HOST11/lut-test.log`: eight native-versus-forwarded GPU frames,
  width 3072, independent per-pixel integer oracle, partial CPU updates/reads,
  and final resource retirement pass. The test shader is host-authored.
- `CA_SYSTEM_LUT_HOST12/lut-corrected-test.log`: all four LUT formats at
  the observed widths pass 40 GPU frames, native-equal pixels, a separate
  pixel oracle, partial updates/reads and resource retirement. An initial
  test-data failure treated enum 55 as paired half floats; the SDK identifies
  it as R32Float. Correcting the test representation fixed the oracle. The
  driver already used the correct 4-byte native format. Initial failure and
  corrected result are retained separately.
- V20 backend suite: 20 tests pass with native Metal validation enabled;
  the corrected integer linear-layout negative control also passes. Existing
  runner transport (9) and MMIO peer (7) unit tests pass.

The combined native/custom-frontend tests do not enable the global Metal
object-validation wrapper: an exploratory run rejected the custom Objective-C
texture because it is not a native `MTLTextureImplementation`. Backend-only
protocol tests do enable native Metal validation. That wrapper incompatibility
is a host harness limitation; it is not an exact-guest driver failure.

Additional host library provisioning uses `build_library_cache.py`, taking
unchanged MTLB slices and writing `<sha256>.metallib` plus a manifest. The host
validates length and SHA256 again before `newLibraryWithData:`. Unknown libraries
remain explicit failures; this is not general guest library-byte upload yet.
The raw HDR file copied read-only from exact 24A5430a firmware is byte-identical
to the requested guest hash. The firmware was attached only through safe_attach
and detached after the selected reads. No migrated disk was mounted or edited.

```sh
python3 tools/gpu/build_library_cache.py /tmp/dvm/CA_SYSTEM_LIBRARY_CACHE10 \
  /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  /tmp/dvm/CA_SYSTEM_LIBRARIES10/HDRProcessing.framework.default.metallib \
  /tmp/dvm/CA_SYSTEM_LIBRARIES10/RenderBox.framework.default.metallib
MTL_DEBUG_LAYER=1 python3 tools/gpu/replay_driver.py \
  /tmp/dvm/CA_SYSTEM_BOOT_GUEST9 \
  --worker /tmp/dvm/CA_SYSTEM_LIBRARY_HOST10/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /tmp/dvm/CA_SYSTEM_LIBRARY_CACHE10 \
  --expect-repaired-seq 26 --out /tmp/dvm/CA_SYSTEM_LIBRARY_REPLAY10
```

## Bounded backboardd revision-loading detour

One isolated attempt, `CA_SYSTEM_LOAD_GUEST1`, was made, then stopped as
requested. The ordinary boot bootstrap remains the GPU development route.

Observed in actual backboardd PID 73: development IOServiceOpen type
`0x44564d4c` returned zero, MMIO mapped, and the host delivered all package
chunks (11 successful transport requests including setup). The copied guest
log then reports exactly:

```
GPU_LOAD_SYSTEM_EXCEPTION name=NSInternalInconsistencyException reason=system-revision directory errno=2
```

This failed the staging-directory contract before NSBundle/dlopen. ENOENT
establishes an absent path component during directory creation; it does not
establish a signing, TXM, arm64e loading or sandbox-policy rejection. The
installer attempted to prepare the staging parent on its disposable Data
child. Its relationship to backboardd's runtime filesystem namespace is still
unverified. The next loading experiment would inspect/create that exact parent
in backboardd's own namespace before attempting the already-fetched bundle.
There was no second attempt in this detour.

The loaded-at-boot bootstrap and additional opted-in loader entitlements are
separate from the staged revision. The staged revision has distinct Objective-C
class names and an OOP-JIT linkage signature tied to this backboardd's complete
CodeDirectory hash. Host signature verification passed; the staged hash is
absent from the boot TC. No claim of guest signing acceptance follows from
those host checks. `build_system_revision.py` preserves this build recipe.

The cached log was read without modifying guest memory; it is a log artifact,
not a coherent state snapshot. The owned VM was explicitly quit at 105.43 s;
the harness then recorded the resulting `MMIO notification disconnected` as
an orchestration error. That disconnect was cleanup, not the loading failure.
The constructor's caught exception was in its guest stderr log rather than the
host audit ring, which is why this probe required bounded cached-log inspection.

**Untested:** arm64e dynamic loading in backboardd, choosing a staged revision
as its registered device, restart into another revision, display/input recovery,
and resource ownership recovery. The test runner's child/waitpid ownership
protocol does not quiesce launchd-owned backboardd, its globally retained
compositor objects, pending GPU work, or native display leases. No safe
compositor retirement/restart contract has been implemented or claimed.
No active driver was unloaded and no replacement process reused its resources.
Continue GPU functionality on isolated boots rather than build that restart
infrastructure as a prerequisite.

## Next implementation boundary: compositor IOSurface import

GUEST12's exact audited request is:

```
GPU_LOAD_TEXTURE_REJECT reason=surface-descriptor type=2 width=1179 height=2556 depth=1 format=115 storage=0 usage=5 options=0 levels=1 samples=1 array=1 compression=0 surface=2 plane=0
```

The rejection happens in the guest frontend before any host import request.
This disproves only compatibility with the **current fixed BGRA owned-pool
import API**. It does not show that the host cannot render into a suitable
RGBA16Float allocation, or that the iOS IOSurface cannot be imported through
an extended ownership protocol. Full-screen half-float composition needs about
24.1 MB of pixel storage before row padding; the current ordinary-resource
limits and fixed presentation pool do not cover this allocation as-is.

The smallest next change should establish one existing compositor IOSurface
as a retained, pinned, explicitly owned transport resource. Before advertising
that import, verify the actual pixel-format/row/plane/allocation metadata,
map or alias its registered pages on the host, and keep both the guest surface
and its native backing alive until GPU and display retirement complete.
RGBA16Float rendering, any final display conversion, and release/reuse must
be checked independently. Do not satisfy the method with the unrelated BGRA
pool or silently down-convert the requested resource.

This may reuse parts of the existing shared-pool page registration, but the
pool tests do not prove arbitrary IOSurface pinning or compositor/DCP lifetime.
Those are the major unresolved contracts for the next bounded experiment.
Staged backboardd restarts are not a prerequisite; use another disposable boot.
