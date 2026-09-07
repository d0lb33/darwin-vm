# GPU driver coverage and iteration plan

Target: exact iOS 27 build 24A5430a, iPhone17,3/T8140; SPTM/TXM, native SMC,
migrated data, display/input and the software compositor remain intact. Work is
on `codex/metal-driver-ios27`, using private artifacts and disposable children.

The intended API target is the exact guest's Metal 4.1-era runtime: first its
traditional `MTLDevice`/library/resource/command-queue/render/compute protocols
used by QuartzCore, then broader application workloads. This is an explicit
development target, **not a claim of Metal 4.1 conformance**. No complete Apple
GPU family is advertised. The new `MTL4*` submission APIs, ray tracing, mesh,
indirect commands and cross-process shared-event contracts are currently
unsupported or unimplemented. Passing QuartzCore will not establish support
for all Metal applications.

The original external API inventory is a checklist, not a complete runtime
specification. `CA_CAPS_GUEST14` superseded its capability-getter failure claims;
`CA_RENDER_GUEST7/8` superseded the single-consumer render unknown. See
[the exact render evidence](gpu-quartzcore-render-ios27.md).

| Area | Implemented | Verified behavior | Unsupported / unknown contract |
| --- | --- | --- | --- |
| Discovery | Explicit process-local factory and opt-in backboardd boot registration through MTLAddDevice | Actual backboardd loads the arm64e driver and its default factory returns that device | Normal global plugin discovery and accelerated system UI output remain unproven |
| Capabilities | V21 forwarding profile; original 20 queries, compression/format batch, framebuffer reads, private half-float/color mip targets, bounded render staging and explicit owned/pinned purgeability policy | CA_CAPS_GUEST14 passes all original queries; exact guest framebuffer-fetch/glass specialization and HDR LUT allocations; host ordered overlap/reuse and unsupported-host rejection | Full GPU-family predicates remain false; absence from a capability-gated runtime path is not evidence of non-requirement |
| Libraries | Requested guest file/URL/data identity; unique bounded MTLB slice; explicit content-addressed multi-library host cache | Exact unmodified AIR specialization/render execution in earlier consumers; GUEST12 loads QuartzCore and HDRProcessing; host replay and malformed-container controls | General library-byte upload and ambiguous multiple AIR slices; HDR library loading does not prove compositor shader execution |
| Functions/render pipelines | Named/indexed constants; owned native function stage metadata, including unspecialized functions; general vertex/fragment pipeline descriptors within documented bounds | Exact QuartzCore specialized vertex/fragment pair; 65 constants | Linked functions, archives, writable fragment bindings, tile/MSAA/depth targets |
| Command encoding | Direct indexed/nonindexed draws, 31 buffer slots, 16 fragment textures/samplers; bounded FIFO of 32 command buffers on one execution queue; atomic staging of render requests up to 2 MiB over 64 KiB MMIO | Exact changing scenes and 64×64 group opacity with private intermediate targets; host queued GPU dependency and cancellation tests | Multiple execution queues, indirect commands, fragment buffer writes and general barriers remain unsupported |
| Compute | Existing luma/blur and bounded compute submission | Exact compute/copy controls and managed blur | Pipeline allowlist remains a development restriction; replace with general validated reflection before claiming broad compute |
| Resources | Dirty buffer spans, partial 2D/3D transfers, bounded color formats and sampled 1D R16Uint/R16Float/R32Float/RG32Float LUTs; private color mip targets; ordered 2D texture copies/mips and buffer fill/copy/writeback | Exact HDR LUT allocations and earlier glass mip allocations; 40 native-equal host LUT frames, 48 blit frames/96 outputs; private CPU-access rejection | General IOSurface import/planes, heaps, memoryless and texture views; imported/buffer-backed texture blits; axes 4096, private images 16 MiB counting mips, copied images 1 MiB |
| Presentation | Owned shared-page IOSurface render target with native retirement and fresh-process handoff | Actual UIKit at 1179×2556: first DCP byte comparison and 1,024 changing frames with final pixels, per-frame native completion, retirement and software lock-screen recovery | General registry now imports actual compositor pages; system DCP presentation, correct HDR/color output and UIKit window interaction remain unverified |
| UIKit effects | Actual UIVisualEffectView material/glass probes; window attachment respecting UIKit invalidation; original guest QuartzCore shaders | Exact guest translucent glass 10 passes/21 draws; removed harness-created black covering draws; paired prepared native/forwarded host scene comparison added in 10ed430 | Independent exact-guest effect oracle and displayed/system-wide glass remain unproven. Historical natural-event-loop comparison failed; host input matching is separate from guest evidence. See [invalidation evidence](gpu-uikit-invalidation-ios27.md) |
| Synchronization | Serial RPC, bounded FIFO, completion callbacks, strong resource retention; failed shared jobs prohibit reuse | Exact consumer completion and native retirement; host predecessor GPU-write visibility, disjoint mip read/write and dependent failure cancellation | Same-allocation mip sampling requires application-guaranteed disjoint subresources; dynamic source LOD is not validated. Multiple execution queues, events/fences, reset and timestamp clock translation remain unsupported |
| Checkpoint | Active GPU migration blocked | Copied-pixel historical checkpoints only | Live host resources and executing commands cannot be checkpointed |
| Purgeability | Native owned-resource transitions with buffer/texture alias state, Empty cache invalidation and lost-ack quarantine | Exact backboardd 256 KiB buffer transitions to Volatile after GPU completion; host discard/reacquire, dirty-write retention and uncertain-state rejection | Exact guest reacquisition/Empty; pinned surface Volatile/Empty rejected; no recovery from uncertain transitions |
| Iteration | Fresh-process supervisor, signed package staging, incremental build and captured-submission replay tools; opt-in test-kernel RX mapping exception | Exact guest installed controls; boot-trusted driver staged on Data executes actual CARenderer with verified pixels; 27-request host replay | Two code-distinct runtime revisions now pass real guest CARenderer in one uninstrumented boot using native OOP-JIT signatures plus opt-in xART fixture; global loading and checkpoints remain outside scope |
| Performance | Separate setup, frame work, native presentation and pacing measurements | 1,024 changing displayed UIKit frames average 59.998 fps, p95 work 16.781 ms; 218 absolute misses, maximum native gap 34.177 ms; no live GPU resource growth | Smooth native Liquid Glass/scrolling remains unproven; average throughput is not smooth pacing |

## Current priority and evidence classes

Latest: [RGhA compositor scanout](gpu-rgha-compositor-ios27.md) proves four
actual backboardd presentations/completions and exact final imported-source to
console delivery. The subsequent V22 run passes the 128-object boundary and renders with an
actual 2.39 MiB uploaded RG8 texture. It stops at the missing descriptor-based
compute pipeline entry point for compute_average_luma, before dispatch. Sustained system pacing,
full ownership retirement, input response and an independent system-scene
color oracle remain separate open gates. The older paragraphs below describe
the evidence sequence; they do not override this latest result.

[Compositor gaps](gpu-compositor-gaps-ios27.md) separates **statically referenced,
runtime observed, implemented, verified, unsupported and unknown** behavior.
It prioritizes existing IOSurface memory, formats/geometry, ownership and
completion, then capability-gated events and additional pipeline/command paths.
Unknown indirect receivers remain unknown. The exact-cache metadata survey is
bounded; it is not a prerequisite to the next actual compositor submission.

GUEST12 registers in actual backboardd and completes 53 host RPCs, but submits
zero render/blit batches. Its current rejected request is a 1179×2556
RGBA16Float IOSurface with shared storage and usage 5. The first pin probe
measured a 9,472-byte row and 24,211,456-byte allocation, exposing the probe's
incorrect whole-page size requirement. The corrected PIN_GUEST2 pins/validates/
completes its 1,478 backing pages three times. A separate native host test
renders the measured layout through scattered file aliases and verifies all
final half-float pixels. The subsequent GUEST21 retained registry connects those actual pages to a host
Metal texture, with no copy. After the alignment and synchronous-submission
fixes, GUEST23 executes 21 actual compositor passes and 58 draws, including its
fullscreen IOSurface. Its purgeability failure is superseded by
CA_PURGEABILITY_GUEST1: the actual owned buffer transitions to Volatile after
GPU completion. The guest submits its RGhA A408 swap, but QEMU rejects that
format and withholds D594. Final raw pixels are captured but color/brightness
correctness, DCP presentation and sustained system pacing remain unverified.
See [the current failed display contract](gpu-resource-purgeability-ios27.md) and the separate
[retained-import contract and evidence](gpu-compositor-import-ios27.md).
See [system boot evidence](gpu-system-boot-ios27.md).

Backboardd dynamic staging/restart is deferred after its single staging-path
failure. Runner revision success is not backboardd loading/recovery evidence.
Interactive sessions have no fixed 600-second lifetime; readiness, per-test,
GPU completion and failed-reuse safeguards remain separate. Automated regression
runs retain their bounded session deadline.

## Earlier consumer milestones (historical sequence)

1. **Persistent staging/loading:** boot one isolated runner, require existing
   native display plus stable input/fresh ACK, then stage the trusted driver and
   a newly signed revision through controlled staging paths. **Proven for the
   scoped development runner:** native OOP-JIT signatures and an opt-in SEP
   xART status record resolve the observed TXM failures; the existing scoped
   kernel mmap support remains necessary. Two distinct Data-loaded revisions
   pass real CARenderer, exact pixels and resource release in one uninstrumented
   boot, including a revision compiled after boot. Wrong subtype is rejected;
   ordinary controls and display/input readiness survive. See
   [runtime-loading evidence](gpu-linked-runtime-loading-ios27.md). Each job
   execs the pinned helper in a fresh PID and uses a fresh host worker. Require
   real GPU output for successful loads; retain exact dyld/signing or process
   termination evidence for failures. Stop an individual child after 90 seconds;
   stop the session explicitly or at its recorded overall deadline. Do not
   interpret the absent trust-cache hash as proof that loading must fail.
2. **Intermediate targets:** displayed alpha, clipping/transforms and image
   scenes pass. Group opacity passes both offscreen and on the owned screen
   IOSurface. GUEST3 made no intermediate allocation request: exact static code
   checks the reported texture width limit before calling Metal. V7 adds real
   larger private allocations and corresponding limits. GUEST4 then creates a
   256×2560 private intermediate and passes 16/128/1,024 displayed frames with verified
   final pixels, native DCP bytes and ownership return. Next: actual UIKit view
   content, controls, text and visual effects in the persistent runner; this
   is not yet a Liquid Glass or system-compositor claim.
3. **UIKit content:** exact guest job 1788775984328876 passes the full 320×480
   offscreen image against independent native composition of captured guest
   glyph inputs and layer metadata, within one byte per channel, with clean
   process completion and resource retirement. Actual UIKit/CARenderer submits
   three GPU passes/ten draws through the driver. The original CPU rasterizer
   comparison remains failed; historical failed jobs are not promoted.
   V11 framebuffer reads remove the reproduced Catalyst fallback corruption;
   native/forwarded changing host frames now match exactly. Plain backend
   replay matches all 206 requests; shader-validation replay stops at six
   one-byte differences and remains a strict failure. The same UIKit scene now
   passes actual display byte comparison and 1,024 changing frames with final
   pixels/ownership/native completion. A native Power wake restores the visible
   software lock screen; the initial idle recovery timeout stays recorded.
   Material blur and original glass shaders now execute through the exact guest
   driver. Window attachment required half-float color targets and mip rendering;
   V16 covers those contracts. Respecting UIKit invalidation removes the black
   covering draws, and the native window compositor visibly renders glass.
   The natural-event-loop native/forwarded comparison still differs; next
   establish equivalent inputs and scope native capability overrides before
   treating that difference as a driver failure. Independent exact-guest glass
   correctness and displayed pacing remain unresolved. See
   [the invalidation intervention and failed comparison](gpu-uikit-invalidation-ios27.md).
   See [glass contracts and exact failures](gpu-uikit-glass-ios27.md) and
   [effect contracts and staging](gpu-uikit-effects-ios27.md). System
   adoption, native window interaction and Liquid Glass remain untested. See
   [displayed UIKit and pacing](gpu-uikit-display-ios27.md),
   [framebuffer and UIKit acceptance](gpu-framebuffer-feedback-ios27.md) and
   [earlier UIKit evidence](gpu-uikit-ios27.md).

The displayed scenes, partial transfers and interactive session evidence are in
[shared scene verification](gpu-shared-scenes-regions-ios27.md). Group-opacity
and FIFO evidence is preserved under
`~/dvm-artifacts/research/gpu-group-opacity-20260907-part1`; the failed GUEST1
trial remains failed even though its last consumer passes post-hoc verification.
See [group-opacity contracts and evidence](gpu-group-opacity-ios27.md).

Main/DCP integration, the failed and corrected vertex barrier experiment,
current evidence and reproduction are in
[main integration and buffer coherence](gpu-main-sync-buffer-coherence-ios27.md).
Writable fragment buffers and GPU-written linear-texture/index aliases still
require additional synchronization work; they fail explicitly today.

Keep correctness, completion, lifetime, display preservation and sustained
performance as distinct verdicts. Capture generated upload bytes, descriptors,
constants and command order for backend replay. Measure build, stage, readiness
and execution durations; analyze failures before spending another boot.

## Portability boundary

The guest/host protocol carries resource IDs, explicit dimensions/options,
shader bytes/constants, operations and completion results, not host pointers or
host task ports. That structure can support other backends. Current execution
depends on Apple Metal, AIR loading and Metal function specialization. Shared
guest page mapping and host Metal buffer/texture aliasing are also currently
macOS implementations. AIR translation or an alternative shader backend on
Linux/Windows is untested; the transport's portability does not solve that
compiler/backend contract. Host absolute timestamps are explicitly identified
and must be translated before system-wide guest timing is trustworthy.
