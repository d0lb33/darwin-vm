# Compositor coverage priorities — exact 24A5430a

This is the current implementation queue, not a claim of full Metal coverage.
The older external QuartzCore inventory is a static checklist. Its historical
missing-capability, shader-entrypoint and CARenderer conclusions have been
superseded by CA_CAPS_GUEST14, CA_RENDER_GUEST7/8 and the system boot series.
The target remains the traditional Metal device/resource/render/compute API
used by this Metal 4.1-era guest; complete Metal 4.1 conformance is unproven.

| Priority / behavior | Static evidence | Runtime observed | Implemented | Verified | Unsupported / unknown |
| --- | --- | --- | --- | --- | --- |
| P0: existing compositor IOSurface import | QuartzCore `allocate_iosurface` at `0x18471bf38` creates its surface before querying Metal; see `ca-software-path.md` | GUEST12 requests surface 2, 1179×2556, format 115, shared, usage 5; PIN_GUEST2 measures row 9472, allocation 24211456, RGhA, no planes | Fixed service-owned BGRA import; opt-in retained BGRA/RGBA16Float registry and texture aliases | GUEST21/22/23 register actual pages; GUEST23 executes 21 compositor passes/58 draws including fullscreen handle 28; host alias tests pass | Correct compositor pixels, cache/protection variants, exact native retirement and DCP lifetime remain open |
| P0: allocation geometry and budgets | Exact requested dimensions imply at least 24,108,192 bytes for RGBA16Float | GUEST21 imports a 24,211,456-byte allocation spanning 24,215,552 mapped bytes | Separate 64 MiB registration and 256 MiB mapped-span budget; existing copied/private budgets preserved | Host registry budget/alias tests and exact first fullscreen import | Multiple simultaneous fullscreen surfaces, sustained memory use and general allocation profiles |
| P0: imported-resource completion and lifetime | Native display owns a separate surface lifetime; modern IOKit descriptor prepare/complete is distinct from Metal completion | Pool tests observe GPU completion then native display retirement | Serial host work, queued guest commands, retained descriptors and host final-alias tombstones; opt-in native use-count hold | Owned-pool exact tests; host imported alias retirement/replay tests | Exact existing-surface GPU/DCP lifetime, simultaneous producers, use-count variants, process death and complete/unpin after native aliases disappear |
| P0: purgeability and discard/reacquire | Exact QuartzCore setPurgeableState: selector cell `0x1e05e7210`; Metal/IOSurface resource policy differs | GUEST23 raises after first completed compositor batch; receiver/state not yet captured | KeepCurrent/NonVolatile only; imported ownership failures are sticky | Host lost-retirement-reply fault refuses unpin and subsequent GPU reuse | Native Volatile/Empty semantics, CPU-shadow invalidation, exact receiver/state and pinned-surface policy |
| P0: accelerated scanout format/color | QEMU `darwin_iomfb_swap.c:77` accepts only BGRA | Actual render target is RGhA; its final bytes contain a very dark lock screen; no new native display submission | Existing BGRA display path preserved | Earlier owned-pool display acceptance; new surface pixels captured, not independently verified | Exact accelerated swap descriptor, brightness/color conversion, DCP completion and sustained display/input |
| P1: capability-gated synchronization | `supportsFamily:0x3ef` at `0x18471c1b0` gates `newSharedEventWithOptions:` / `newSharedEvent`; `cbz` at `0x18471c1b4` skips block | No event requirement in current boot trace | Family predicates false; no shared event implementation | No event-path acceptance | Event handles and cross-process timing may be needed for accelerated paths; false predicate is not evidence that events are unnecessary |
| P1: formats, planes and compression | Broader exact-cache scan below; original inventory names YUV, tile and memoryless queries | HDR boot requests 1D formats 23,25,55,105 plus fullscreen 115 | V20 four sampled 1D LUT profiles and bounded color formats; unsupported predicates remain false | Host LUT native comparisons and exact guest allocation; not HDR compositor execution | Planar YUV, compressed/protected surfaces, memoryless/MSAA, tile/imageblock operations; receiver and reachability require targeted inspection |
| P1: shaders and command combinations | Exact warmup copies/mips at `0x1846debd8` / `0x1846dec00` | GUEST12 loads QuartzCore and HDRProcessing; no committed render/blit submission | Content-addressed multi-library cache, specialization, ordered render/blits | Host request replay, LUT and blit pixel tests; earlier exact CARenderer/UI workloads | General library byte delivery, general compute reflection, imported-surface blits and system shader combinations |
| P1: hidden pipeline paths | Exact-cache metadata/capability branch survey below | Only exercised descriptors known | Bounded single-color render profile with private mip targets and framebuffer reads | Earlier guest scenes and host paired comparisons | Indirect receiver types, dynamic selectors, writable fragment resources, attachment/options combinations and broader concurrency |
| P2: discovery outside backboardd | Exact MTLAddDevice export and plugin lookup paths documented in `gpu-system-boot-ios27.md` / `gpu-feasibility-ios27.md` | Actual backboardd registers the driver and gets it from default factory | Opt-in boot dependency, per-process registration | GUEST12 registration and 53 successful host RPCs | Other processes, normal global discovery and general plugin lifecycle |

Dynamic backboardd staging/restart stays deferred. It is not a prerequisite
for the P0 import work. Preserve the boot bootstrap and use disposable boots.

## Bounded investigation and next tests

1. **Pin the actual surface:** query metadata and send its calling-process VA
   and bounded allocation size through the kernel-only pin probe. Reject wrong
   scalar count/null range. Require three successful prepare/page validation/
   complete cycles. Stop on the first unexpected return, page or completion.
   No host page aliases exist in this test. Success proves only this pin cycle.
2. **Register and render:** only after pinning is established, extend the
   kernel-controlled registration to resource IDs and verified descriptor pages.
   Test host texture aliasing for the observed format/row, then actual compositor
   commands, with backing retained until host and native display retirement.
   Failure must quarantine reuse. Pacing and memory growth need separate runs.
3. **Broader static coverage:** bounded scans of QuartzCore, RenderBox,
   UIKitCore, Metal, IOSurface, HDRProcessing and backboardd; retain imports,
   Objective-C metadata/references, shader identities and capability names.
   A selector reference does not identify the receiver or establish execution.
   Mark indirect calls/unknown receivers explicitly and disassemble the paths
   implicated by P0/P1 contracts first. Do not change capabilities to force them.

Current exact boot evidence and durable artifacts are linked from
[system integration](gpu-system-boot-ios27.md). The overall feature scope and
portability limits remain in [driver coverage](gpu-driver-coverage.md).

## First broader survey, 2026-09-07

`scan_compositor_interfaces.py` completed in 15.013 s. It extracted six cache
images for static RE (QuartzCore, RenderBox, UIKitCore, Metal, IOSurface and
HDRProcessing), captured Objective-C declarations/references, strings and load
commands, reverse importers of Metal/IOSurface across the cache, and separate
backboardd imports/metadata. It did not decompile the OS. The matching-line
counts are 891, 168, 1603, 16195, 552 and 365 respectively; these are **not unique
APIs or confirmed Metal calls**. Metadata includes declarations of APIs no
consumer may call. Cached extraction provenance is reused; this run does not
independently re-extract or re-attest the IPSW. Raw shader files and their exact
24A5430a SystemVersion provenance remain in the earlier system-boot evidence.

Targeted follow-up changes the queue in these concrete ways:

- **Shared events remain a real static coverage gap.** Exact QuartzCore
  selector references include `newSharedEventHandle` at `0x1e05e6100`,
  `newSharedEventWithHandle:` at `0x1e05e6108`, and
  `newSharedEventWithMachPort:` at `0x1e05e6110`. Those reference cells alone
  have unknown receivers. Re-read disassembly confirms the device-family gate
  at `0x18471c1b0/1b4`, another family test at `0x18471c250`, and concrete
  event-creation calls at `0x18471c280/28c`. Two nearby authenticated indirect
  calls (`0x18471c20c/228`, slot 0xa8) remain unidentified. Do not infer their
  semantics from neighboring event names.
- **RenderBox has two more IOSurface import call sites.** Direct selector
  stubs resolve `newTextureWithDescriptor:iosurface:plane:` at
  `0x1b2e1693c` and `0x1b2e2bb38`; both pass plane zero. Receivers come from
  object field +0x18 through different owner chains; their precise class and
  the descriptor combinations are not established by these windows. General
  import should not special-case the first backboardd request.
- **Function-pointer capability has another gate.** RenderBox calls
  `supportsFunctionPointersFromRender` at `0x1b2e04fcc`; preceding checks at
  `0x1b2e04fb8/4fc4` can skip the call, and its answer is stored at owner+0x14f.
  The receiver is loaded from stack+0x50 in this window; its origin and later
  use of that bit need targeted follow-up. This query is not currently in the
  forwarding contract and was not runtime observed. `supportsBackgroundAppRole`
  calls at `0x1b2e10184/50fb8` likewise need receiver classification before
  being labelled device capabilities.

These additions inform later event/function/pipeline batches. They do not
justify enabling unsupported family/function-pointer predicates now. Full
render/blit descriptor and payload capture continues through the existing peer.
The retained registry now passes exact compositor page registration and host
GPU execution. [Import evidence](gpu-compositor-import-ios27.md) records the
alignment/submission fixes and GUEST23’s 21 passes/58 draws. Purgeability, correct
pixels and native display ownership remain acceptance gates.

```sh
python3 tools/gpu/scan_compositor_interfaces.py \
  /Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e \
  /Users/jdolbe1/dvm-artifacts/extract/bin/backboardd \
  /tmp/dvm/CA_COMPOSITOR_SCAN1 --extract
ipsw dyld disass /Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e \
  --image RenderBox --quiet > /tmp/dvm/CA_COMPOSITOR_SCAN1/RenderBox.disass
python3 tools/re/cache_objc_calls.py /Users/jdolbe1/dvm-artifacts/extract/dyld \
  /tmp/dvm/CA_COMPOSITOR_SCAN1/RenderBox.disass --all-selectors \
  > /tmp/dvm/CA_COMPOSITOR_SCAN1/RenderBox.calls.txt
```

Survey data and extracted images are preserved under
`/Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-pin/CA_COMPOSITOR_SCAN1`;
the sibling execution-inputs index includes hashes of images and the complete
RenderBox disassembly. Method-reference results retain file/line and guest
virtual addresses rather than merging unknown receivers into a device API list.
