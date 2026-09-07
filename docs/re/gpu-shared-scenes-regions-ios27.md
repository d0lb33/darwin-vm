# Shared displayed scenes and public texture regions

2026-09-07; exact iOS 27 24A5430a, iPhone17,3/T8140. Solo work, isolated
disk child, unchanged SPTM/TXM, native SMC, migrated backing chain and kernel/
device-tree/helper from `gpu-quartzcore-handoff-ios27`. No debugger or NVMe GPU
transport. This continues `gpu-shared-pacing-ios27.md`.

## Exact guest acceptance

`CA_SHARED_SCENES1` reached runner readiness at 106.370 seconds. All six jobs
passed; the first four rendered directly into the same screen-sized IOSurface
4, retained by supervisor PID 68. Each new process received a Mach-port alias,
acquired and retired each frame's lease around GPU and native DCP completion,
then returned a verified alias. All final resources retired.

| Job | PID | Workload | Result |
|---|---:|---|---|
| 1788764264627470 | 379 | Half-opacity moving layer, 8 displayed frames | Pixels, GPU/native completion and handoff pass |
| 1788764322256227 | 430 | Clipping plus scale transform, 64 displayed frames | Pass |
| 1788764364327367 | 474 | Two-color CGImage moving behind an opaque layer, 128 displayed frames | Pass |
| 1788764446173541 | 557 | Same image scene, 1,024 displayed frames | Pass |
| 1788764849291996 | 824 | New region-capable driver: red CARenderer control plus 2D partial transfers | Pass; staged at 776.682 seconds |
| 1788764883665179 | 838 | Original installed red control | Pass |

The partial-transfer job's PID and the installed control's PID are recorded in
their `result.json` files. The full-screen scenes are ordinary CALayer trees;
production driver code contains no scene/shader-name selection. The final
image's **12,432,384-byte actual DCP DMA export** equals the independently
verified IOSurface bytes, SHA-256
`8865e81f234242f038577ffa1c9ad6e6ad078e621ee7045119cf7a00d4d133a3`.
Native display completion and fresh input ACKs passed after the installed
control. `recovery-wake-after.png` was visually inspected: the normal software
lock screen returned, showing native 80% charging state.

The VM remained alive past 600 seconds while idle between tests and accepted
a new signed driver afterward. `interactive-after-600.json` records the live
owned PID/argv and null session deadline; the late staged job supplies stronger
evidence than that snapshot alone. Explicit stop ended the session at
**862.602 seconds**, with `passed=true`, `kept_paused=false`, and the owned
process reaped. This proves this interactive session, not general endurance.

## Timing and independent rehearsals

No verification readback occurs inside the displayed batch. The final full
pixel oracle is outside timing; only the half-opacity region allows one RGB
code of quantization tolerance. Markers, alpha and other colors remain exact.
Negative tests reject out-of-region tolerance, altered marker/alpha bytes and
a scene identity taken from an untrusted guest claim.

| Scene / frames | Steady work median / p95 ms | Native average fps | Largest native gap ms | Absolute misses |
|---|---|---:|---:|---|
| Alpha / 8 | 10.193 / 10.905 | 58.683 | 18.093 | 0/6 |
| Clip/scale / 64 | 9.000 / 10.841 | 59.918 | 23.737 | 2/62 |
| Image / 128 | 10.024 / 14.715 | 59.977 | 44.842 | 10/126 |
| Image / 1024 | 9.872 / 12.475 | 60.028 | 91.922 | 44/1022 |

Setup and first two frames are separate. The longer image run's first frame
took 464.581 ms; its largest steady work interval was 26.956 ms. The disparity
with the largest scanout gap is not attributed to GPU execution without further
evidence. Logical resource counts/bytes stayed flat during sampling and ended
at zero; guest footprint changed -16,360 bytes from samples 2 to 1024. These
results neither establish smooth 60 Hz nor explain the older offscreen abort.

`test_shared_scene_native.m` rendered the same test layer definitions with
native **macOS** QuartzCore and native Metal, at seven positions for each scene.
All 21 full-screen images passed the separate Python pixel oracle, including
both clip boundaries and overlap. This tests scene geometry/oracle consistency;
it does not reuse guest AIR or prove forwarding. Exact-guest jobs above use the
unchanged 24A5430a QuartzCore library and supply that evidence separately.

## General driver implementation

`DVMTexture` previously rejected every transfer with a nonzero origin or a
smaller extent. It now validates and implements bounded 2D/3D region reads and
updates, with row/image pitches, linear-buffer origins, and overflow-safe
range checks. Shared display IOSurfaces retain their explicit lock/lease CPU
access contract; this change does not bypass it.

No new wire operation or larger limit is advertised. An ordinary partial update
merges into an unsubmitted CPU image when one exists; otherwise it obtains
completed native contents through the existing full-transfer operation before
merging. This preserves GPU-produced pixels outside the update. Partial reads
copy only requested rows/slices and preserve destination padding. Linear views
update their retained client storage directly. In-flight access remains rejected.
This is correctness-first API support; partial wire transfers remain a future
performance improvement.

Host frontend/backend tests require real native GPU red output followed by a
partial green update, exact preservation outside that rectangle, padded reads,
pending full-plus-partial 3D updates, linear client aliasing and overflow
rejection. The full focused suite passed 50 tests; the additional linear case
passed the rebuilt real-GPU executable. Compute/copy and previous rendering
controls remain passing. Exact-guest `verify_texture_regions.py` separately
verifies a 2D texture's captured host bytes and guest padding witness alongside
the real CARenderer control. GPU-written preservation and 3D/linear region cases
are currently host evidence, not exact-guest evidence.

One package failed to link due to compiler-generated `_memset_pattern16`.
The added link stub was verified against this guest's
`/usr/lib/system/libsystem_c.dylib` export before staging; no boot was spent
on that missing stub. The failed build log is retained.

## Coverage and next contract

| Feature | Status |
|---|---|
| Traditional Metal profile v5, existing bounded rendering/compute contracts | Unchanged advertised scope |
| Shared full-screen alpha, image, clipping and scaling | Exact guest GPU execution, final pixels, native presentation, successful reuse verified |
| Partial 2D transfers | Implemented; host and exact guest transfer evidence |
| GPU-written preservation, 3D and linear partial transfers | Implemented; host evidence |
| Interactive idle lifetime and later revision | Exact guest session over 600 seconds verified |
| Group opacity/intermediate targets, curved/stencil masks, backdrop filters | Still untested in this shared scene suite |
| General IOSurface import, multiple processes/queues, global device discovery, system compositor | Not implemented/verified by this milestone |
| Original Liquid Glass, complete Metal families/Metal 4, general source compilation | Not supported claims |

The next consequential test is group opacity or a backdrop operation requiring
intermediate targets. Ordinary textures remain bounded to 512 dimensions and
1 MiB, while the screen surface uses a separate fixed allocation contract.
Whether exact QuartzCore tiles that workload within these limits or requests
larger/intermediate resources must be observed; it is not proof of impossibility.
Normal global discovery remains deferred until resource and command contracts
can support that consumer without breaking the software fallback.

## Reproduction and durable artifacts

Evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-shared-scenes-20260907`.
Continuation package: `/Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27`.
Its control manifest references the existing immutable handoff package; no new
mutable test disk replaces the migrated baseline. It retains installed and
runtime builds, signed region revision and the verified shared-scene revisions.

Build shared tests with `build_consumer_package.py BASE NEW --shared-surface
--frames 128 --scene 3 --hz 60`; scene 1 is alpha and 2 is clipping/scaling.
Use `--frames 1 --regions` for the ancillary region probe. Link with
`sign_linked_revision.py NEW/DVMProxy.bundle LINKED --parent
PACKAGE/installed-build/dvm-gpu-load`. Run the manifest with `--interactive` and
the usual MMIO/consumer/runner/display flags from `gpu-surface-handoff-ios27.md`.
Stage with matching frame/scene/rate, then run `verify_runner_job.py`,
`verify_surface_handoff_batch.py`, `report_shared_pacing.py`, or
`verify_texture_regions.py` as appropriate. Native rehearsal compilation uses
`-DDVM_CA_SCENE=1` (then 2/3), Metal/Foundation/QuartzCore/CoreGraphics frameworks.
Record native recovery before `runner_control.py TRIAL --stop` and verify the
final DCP export afterward. Automated regressions keep the 600-second cap.
