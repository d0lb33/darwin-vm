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
| Discovery | Explicit process-local device factory | Exact guest signed bundle load and CARenderer use | Normal plugin discovery, service-global context, system compositor adoption |
| Capabilities | Versioned forwarding profile; original 20 queries plus compression/format batch | Exact guest original 20 and observed lossless-format path; host profile mismatch rejection | Full GPU-family predicates remain false; static query coverage is not a runtime trace |
| Libraries | Requested guest URL/data identity, exact AIR forwarded to configured host library | Exact unmodified AIR, real function specialization and render execution | Host currently needs matching local AIR cache; general multi-library delivery pending |
| Functions/render pipelines | Named/indexed constants; general vertex/fragment pipeline descriptors within documented bounds | Exact QuartzCore specialized vertex/fragment pair; 65 constants | Linked functions, archives, writable fragment bindings, tile/MSAA/depth targets |
| Command encoding | Direct indexed/nonindexed draws, 31 buffer slots, 16 fragment textures/samplers; bounded FIFO of 32 command buffers on one execution queue | Exact changing scenes and 64×64 group opacity with private intermediate targets; host queued GPU dependency and cancellation tests | Multiple execution queues, indirect commands, fragment buffer writes and general barriers remain unsupported |
| Compute | Existing luma/blur and bounded compute submission | Exact compute/copy controls and managed blur | Pipeline allowlist remains a development restriction; replace with general validated reflection before claiming broad compute |
| Resources | Dirty buffer spans, partial 2D/3D transfers, 6 texture formats (A8 sampled only), read-only linear views, private GPU textures and opaque guest process metadata | Exact 2D transfer/control and private intermediate allocation; host GPU dependency, linear/3D transfers, private CPU-access rejection | General IOSurface import/planes, heaps, memoryless storage, private blit transfers; texture axes 4096, private images 16 MiB, copied images 1 MiB |
| Presentation | Owned shared-page IOSurface render target with native retirement and fresh-process handoff | Exact displayed alpha, clip/transform and image CARenderer batches; actual DCP bytes, display/input recovery | Exact 1,024-frame screen group-opacity path now passes; general surface registration and system-compositor adoption unknown |
| Synchronization | Serial RPC, bounded FIFO, completion callbacks, strong resource retention; failed shared jobs prohibit reuse | Exact consumer completion and native retirement; host predecessor GPU-write visibility and dependent failure cancellation | Multiple execution queues, events/fences, reset; timestamp clock translation |
| Checkpoint | Active GPU migration blocked | Copied-pixel historical checkpoints only | Live host resources and executing commands cannot be checkpointed |
| Iteration | Fresh-process supervisor, signed package staging, incremental build and captured-submission replay tools; opt-in test-kernel RX mapping exception | Exact guest installed controls; boot-trusted driver staged on Data executes actual CARenderer with verified pixels; 27-request host replay | Two code-distinct runtime revisions now pass real guest CARenderer in one uninstrumented boot using native OOP-JIT signatures plus opt-in xART fixture; global loading and checkpoints remain outside scope |
| Performance | Separate setup, frame work, native presentation and pacing measurements | 1,024 displayed image frames average 60.028 fps, p95 work 12.475 ms; 44 absolute misses and maximum native gap 91.922 ms | Smooth native Liquid Glass/scrolling remains unproven; average throughput is not smooth pacing |

## Current bounded experiments

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
3. **UIKit content:** the actual guest UIKit layer tree now submits three host
   GPU passes/ten draws, and bounded V8 transfers capture the full 320×480
   target. V10 fixes row alignment and adds A8 sampling, so all labels draw. Pixel
   orientation is corrected with an explicit CARenderer coordinate option. The
   asymmetric nearest-image control passes all pixels and retirement. Full
   UIKit comparison still fails at image/text edges; native-versus-forwarded
   UIKit rendering is the next control. This is an offscreen diagnostic, not a displayed
   UIKit or Liquid Glass pass. See [UIKit evidence](gpu-uikit-ios27.md).

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
