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
| Capabilities | Versioned forwarding profile and 20 related scalar getters | Exact guest all 20; host mismatch rejection | Full GPU-family predicates remain false; wider feature queries incomplete |
| Libraries | Requested guest URL/data identity, exact AIR forwarded to configured host library | Exact unmodified AIR, real function specialization and render execution | Host currently needs matching local AIR cache; general multi-library delivery pending |
| Functions/render pipelines | Named/indexed constants; general vertex/fragment pipeline descriptors within documented bounds | Exact QuartzCore specialized vertex/fragment pair; 65 constants | Linked functions, archives, writable render bindings, tile/MSAA/depth targets |
| Command encoding | Direct indexed/nonindexed draws, state and binding serialization, one in-flight command | Exact 64×64 red CALayer, one indexed draw, exact pixels, zero objects | Offset updates, bulk bindings and unbinding implemented; host changing-scene first frame passes. Next prewarm pipeline requires read/write vertex-buffer bindings, currently rejected |
| Compute | Existing luma/blur and bounded compute submission | Exact compute/copy controls and managed blur | Pipeline allowlist remains a development restriction; replace with general validated reflection before claiming broad compute |
| Resources | Owned buffer shadows, 5 texture formats, allocation/protection metadata, read-only linear views | Host alias coherence/bounds/parent lifetime; exact CARenderer allocation | Dirty tracking, general IOSurface import/planes, heaps/private/memoryless storage |
| Presentation | Separate managed shared-page IOSurface extension with native retirement | Earlier exact displayed blur and input recovery | CARenderer output is currently offscreen; generic render target adoption unknown |
| Synchronization | Serial RPC, completion callbacks, strong resource retention | Exact single-consumer completion and retirement; host failure tests | Multiple queues/in-flight buffers, events/fences, reset; timestamp clock translation |
| Checkpoint | Active GPU migration blocked | Copied-pixel historical checkpoints only | Live host resources and executing commands cannot be checkpointed |
| Iteration | Fresh-process supervisor, signed package staging, incremental build and captured-submission replay tools; opt-in test-kernel RX mapping exception | Exact guest installed controls; boot-trusted driver staged on Data executes actual CARenderer with verified pixels; 27-request host replay | Newly signed revision still rejected by AMFI after scoped CT gate; System remount denied; stopped at user two-fix limit |
| Performance | Separate first-use and host GPU timing; prior sustained managed-blur controls | Prior displayed blur missed target pacing; red draw is not a benchmark | Native Liquid Glass/scrolling and sustained CARenderer performance unproven |

## Current bounded experiments

1. **Persistent staging/loading:** boot one isolated runner, require existing
   native display plus stable input/fresh ACK, then stage the trusted driver and
   a newly signed revision through controlled staging paths. The scoped kernel service and helper `get-task-allow` obtain stock TXM approval. The user then allowed only two further fixes. Fix 1 reaches a narrowly scoped ad-hoc CT acceptance branch, but the revision still fails subsequent signature validation. Fix 2 corrects the exact observed RX mmap denial for the entitled/debugged child: a boot-trusted driver staged on Data now executes real CARenderer and verifies pixels. The new revision still fails before mapping. **Stopped at the two-fix limit**; this remains an iteration blocker, not a failure of existing GPU rendering. See [kernel evidence](gpu-development-loader-ios27.md). Each job
   execs the pinned helper in a fresh PID and uses a fresh host worker. Require
   real GPU output for successful loads; retain exact dyld/signing or process
   termination evidence for failures. Stop an individual child after 90 seconds;
   stop the session explicitly or at its recorded overall deadline. Do not
   interpret the absent trust-cache hash as proof that loading must fail.
2. **Changing scene:** one retained renderer/target with changing geometry and
   overlapping opaque layers. Host rehearsal passes the first three-draw frame after the offset/bulk-binding batch. Subsequent QuartzCore prewarming aborts on a vertex `vertex_buffer` at index 1 reported as read/write by host reflection (`CA_SEQUENCE_HOST4`). Implement buffer coherence before accepting writable inputs; compare native and forwarded host output, then run the exact guest suite in the persistent runner. Add transparency, images, clipping and transforms to the same suite.

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
