# QuartzCore consumer and sustained pacing — 24A5430a

This continues the managed IOSurface work in `gpu-managed-pool-ios27.md`.
The exact guest, native SMC, SPTM/TXM, software compositor, and migrated disk
lineage are retained. All runs use child overlays and private RAM files.
No global Metal device is published and no debugger is used.

## Acceptance defined before measurement

There are two independent tests. Passing the replay test is not passing the
QuartzCore consumer test.

1. **Actual consumer:** create `CARenderer` in the guest, supply our process-local
   `MTLTexture` and queue using `kCARendererMetalCommandQueue`, attach a 64×64 red
   `CALayer`, and call begin/render/end. Require actual host GPU submission and
   verified pixels before calling this working rendering. Initialization alone,
   allocation alone, or replaying a QuartzCore shader does not pass.
2. **Pacing control:** retain the existing screen-sized, two-pass guest AIR blur
   and GPU conversion. Run 7,201 frames at 60 Hz and 3,601 at 30 Hz, including a
   separately measured first-use frame. These request at least 120 seconds per
   steady batch. Absolute guest deadlines skip missed slots without catch-up
   bursts. Report skipped slots, completion deadline misses, p50/p95/p99/max,
   and host DCP sink intervals. The pacing clock is not physical vsync.

The strict pacing criterion is zero skipped slots and zero frames completing
after their target plus one period. Slow frames do not terminate collection or
disappear from statistics. Correctness failures, invalid resource transitions,
GPU errors, two-second display wait failures, 15-second transport timeouts,
60 seconds without progress after workload readiness, or the runner's overall
deadline terminate the run. Only one surface is outstanding. Per-frame GPU
readback for verification remains disabled; the final frame is checked afterward.

Timing records occupy a bounded 80-byte × 8,192-entry ledger at MMIO shared RAM
offset `0x210000`, outside request/reply/pixel data. Version, count, stride and
CRC are checked. DCP witness markers now preserve the full frame index through
8,192. The guest, host command ledger and DCP swap identities must agree.

## Observations so far

`CA_CONSUMER_GUEST1` loaded the linked iOS QuartzCore framework, created its
64×64 texture on the host GPU, and entered `CARenderer` initialization. At
139.352 seconds it failed with:

```
GPU_LOAD_ERROR consumer_stage=renderer-init exception=NSInvalidArgumentException reason=-[DVMDevice newDepthStencilStateWithDescriptor:]: unrecognized selector sent to instance 0x102d30740
```

The host ledger contains one successful texture allocation and no GPU
submission. This proves process-local consumer entry, not rendering. It is a
missing implemented selector, not evidence of an iOS signing/loading or kernel
integration rejection. Native display and input readiness preceded the attempt.

The implementation now forwards immutable depth/stencil state descriptors to
actual host Metal objects, including both stencil faces and masks. Invalid
descriptors are rejected before host creation. The macOS-only rehearsal then
requested `supportsMemorylessRenderTargets`, `minConstantBufferAlignmentBytes`,
and `newLibraryWithURL:error:`. These rehearsals are explicitly not guest results.
Those intermediate builds used provisional capability values, superseded by
the contract below. The driver loads bytes from the consumer-requested file.
Only the already verified AIR identity is accepted by the host; no host shader
library is substituted.

`CA_CONSUMER_GUEST7` confirms that **QuartzCore itself**, not the helper's
shader replay function, requested a library that reached the host with
SHA-256 `8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`
and length 2,705,796. The host successfully created the target texture, depth
state and library. Initialization then stopped at `maxFragmentTextures`.
There were still zero GPU submissions. This narrows the next work to the
consumer's device/API contract; it does not establish functioning rendering.

## Coherent capability contract

The provisional getter-by-getter values are superseded by
`tools/gpu/driver_capabilities.h`. Both the frontend validators and host
executor use this versioned profile. The guest negotiates the complete profile
once and rejects a different version or different limits. It does not fetch
the native host's limits and advertise them as its own.

The static inventory contains 20 scalar queries, 17 called by this exact
guest's context constructor; `supportsFamily:` is handled separately. Capture
manager queries such as `supportsDestination:` are not incorrectly assigned to
the device. The macOS rehearsal also observed `vendorName` and
`isFramebufferReadSupported`. Its optional guest-AIR substitution is compiled
only into the host rehearsal, explicitly logged, and forbidden in an iOS build.

| Capability | Forwarding contract and enforcement |
| --- | --- |
| 2D texture extent | 512×512 upper bound, with a separate 1 MiB texture-byte budget, enforced on both sides |
| Buffer extent / binding alignment | 1 MiB; frontend bindings require a multiple of 16 bytes; audited host kernels further restrict bindings, including zero offsets |
| Compute bindings / inline data | Eight binding indices and at most 4,096 inline bytes; these do not advertise a complete GPU family |
| Managed IOSurface backing alignment | 16 KiB, from the exact guest/service-owned page mapping contract; this does **not** advertise general `newTexture…iosurface…` import |
| Fragment textures / samplers / color attachments | **Zero**: general render encoders and these binding APIs have not been implemented. Earlier provisional 16/16/1 values are removed |
| Memoryless, MSAA, ROG, framebuffer read, YCbCr, tile/SIMD features | Disabled at the generic API boundary; the audited resident blur extension remains separately available |
| Unified memory / general IOSurface texture import | Not advertised: ordinary API textures still use copied CPU shadows; the managed resident extension is explicit |
| Binary archives / general shader compilation | Unsupported; the existing audited AIR library and kernel paths retain their narrower contract |

Reported absence of generic features is not a claim that the host GPU lacks
them. In particular, the measured resident workload already runs FP16/SIMD
guest AIR. A full-family predicate would promise more than the implemented
API can honor, so all full-family predicates remain false.

`CA_CAPS_GUEST14` verifies all 20 queries on the exact iOS guest, with exactly
one `capabilities` request in `driver-host.jsonl`. The negotiated profile reports
16-byte constant alignment and zero fragment textures, samplers and color
attachments. The host then creates a texture, depth state and the same exact
guest library. At 25.242 seconds the bounded probe stops with:

```
GPU_LOAD_ERROR consumer_stage=renderer-init exception=NSInvalidArgumentException reason=-[DVMTexture protectionOptions]: unrecognized selector sent to instance 0x1053b64b0
```

This last run deliberately starts at boot without waiting for native display
readiness. It tests the API contract only; its duration is not a rendering or
latency measurement. It produces zero GPU submissions and never reaches the
`renderer=1` marker. Both immutable disk ancestors and all seven pinned boot
inputs are rechecked after the run.

The host-only `CA_HOST14` rehearsal instead reaches a linear-texture request:

```
GPU_LOAD_ERROR consumer_stage=renderer-init exception=NSInvalidArgumentException reason=DVM Metal: linear textures unsupported by forwarding profile: type=2 width=128 height=128 format=30 usage=1 storage=1
```

That descriptor is a 128×128 RG8Unorm shader-read texture with managed storage.
It is a **macOS observation**, not evidence that the iOS path requests the same
descriptor. The driver rejects unsupported linear layouts explicitly instead
of returning a host alignment for an allocation/alias operation it cannot honor.

The capability batch is proven within the bounded forwarding profile. Actual
CARenderer rendering remains untested beyond initialization; the present
implementation fails this consumer acceptance. The failure does not disprove
the custom-driver architecture. The next implementation must inventory resource
metadata (starting with `protectionOptions`) against actual allocation semantics,
then support the resource and render-encoder operations this narrow layer tree
uses. Ordinary render pipelines, vertex/fragment bindings and render-command
submission remain absent. A missing getter is not the only remaining work.

## Long pacing control

`PACED_POOL_60HZ1` completed 7,201 displayed frames and 21,603 GPU dispatches.
Final GPU backing, CPU-verified guest IOSurface and actual DCP sink pixels
match. Ownership rejection, unmap/remap, final guard bytes, retirement order,
and post-batch native display/input recovery pass.

The 7,200 steady frames took **190.115 seconds**, or **37.872 frames/s**.
This fails the defined 60 Hz pacing criterion: 4,207 skipped slots and 3,558
completion deadline misses. Setup was 1,047.048 ms. Steady work latency was
12.955 ms median, 22.726 ms p95, 33.372 ms p99, and 216.955 ms maximum.
GPU execution was 0.374 ms median and at most 0.672 ms. Display wait plus
retirement was 9.827 ms median, 27.650 ms p99, and 195.326 ms maximum.
Start lateness was 2.905 ms median and reached 163.795 ms. These measurements
place the observed delays outside GPU execution; they do not isolate the guest
scheduler, transport and DCP costs from each other.

Two unrelated native-SMC QEMU processes were present in the start inventory.
They were not changed or stopped. Treat this as a recorded shared-host result,
not an isolated-host performance ceiling. No builds or host GPU tests ran
during this timed batch.

`PACED_POOL_30HZ1` also passes the full correctness/ownership/presentation
acceptance: 3,601 displayed frames, 10,803 GPU dispatches, and matching final
pixels. Its 3,600 steady frames took **125.455 seconds**, or **28.696 frames/s**.
It missed the strict pacing criterion with 164 skipped slots and 117 completion
deadline misses. Setup was 652.012 ms. Steady work latency was 13.737 ms median,
24.456 ms p95, 33.022 ms p99 and 256.563 ms maximum. GPU execution was
0.374 ms median and 3.044 ms maximum; display wait plus retirement reached
178.884 ms. Start lateness reached 219.266 ms. No shader tuning was performed.

| Requested pacing | Steady frames | Wall time | Delivered rate | Skipped slots | Completion deadline misses |
| --- | ---: | ---: | ---: | ---: | ---: |
| 60 Hz | 7,200 | 190.115 s | 37.872/s | 4,207 | 3,558 |
| 30 Hz | 3,600 | 125.455 s | 28.696/s | 164 | 117 |

The two metrics for missed work are not added together: skipped target slots
and submitted frames finishing after their own deadline are distinct counters.
Both tests verified two immutable disk ancestors and seven boot inputs before
and after their trials. Neither result establishes near-native UI performance.

## Reproduction

The independently preserved paced control is
`~/dvm-artifacts/gpu-paced-pool-ios27/control.json`. It has its own flattened,
verified disk, pinned rebuilt QEMU, exact boot inputs, driver build and AIR.
The earlier managed-pool and copying fallback packages are retained unchanged.

```sh
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py \
  "$HOME/dvm-artifacts/gpu-paced-pool-ios27/control.json" \
  --tag NEW_PACED_60 --seconds 600 --driver-mmio --driver-present \
  --driver-wait-display --present-frames 7201 --present-hz 60 \
  --driver-worker "$HOME/dvm-artifacts/gpu-paced-pool-ios27/driver-build/driver_host" \
  --library-cache "$HOME/dvm-artifacts/gpu-paced-pool-ios27/QuartzCore.metallib"
python3 tools/gpu/report_present_batch.py /tmp/dvm/NEW_PACED_60
python3 tools/gpu/verify_managed_surface.py /tmp/dvm/NEW_PACED_60 --require-negative
```

Use 3,601 frames and 30 Hz for the second variant. Choose unique tags. The
reported clocks are separate guest-stage and host-DCP clocks. The timer-driven
frame loop does not silently turn late frames into catch-up bursts.

For the opt-in consumer helper, build with:

```sh
DVM_CA_PROBE=1 bash tools/gpu/build_driver.sh /tmp/dvm/NEW_CA_BUILD --mmio-present-pool
```

Install it only through the guarded `prepare_driver_update.py` /
`run_guest_install.py` workflow on a disposable child. It deliberately fails
until actual QuartzCore rendering and output verification succeed. It is not
the default paced-control helper and must not replace the preserved fallback.

Exact guest static evidence: QuartzCore's exported class method
`+[CARenderer rendererWithMTLTexture:options:]` begins at `0x1847d1488` in
the retained dyld cache. Full disassembly and symbol records accompany the runs.

## Evidence and validation

Durable diagnostic records are under
`~/dvm-artifacts/research/gpu-quartzcore-consumer-pacing-ios27/`, with a SHA-256
index. They include the three exact-guest consumer trials, final build sources
and import/signing records, static capability call sites, the host-only
rehearsal, both pacing trials, and final pixels/ownership/timing ledgers.
Long serial, wire and QEMU traces are also retained as losslessly verified gzip
files. Full guest DRAM and disposable disks are excluded from this diagnostic
archive; the separate paced-control package retains the reproducible disk.
The managed surface verifier originally reads the trial's private DRAM file;
after its removal the archive retains the bounded final resource snapshot and
its verification record, not a standalone full-DRAM rerun.

The final source passed 73 GPU tests (one pre-existing optional skip), 79 project
tests, shell syntax checks and `git diff --check`. The rebuilt QEMU passed its
three DCP swap unit tests. Capability tests exercise one-time negotiation,
guest/host mismatch rejection and enforced resource bounds. Depth-state tests
exercise real host Metal allocation, malformed descriptor rejection and object
retirement. The 257-frame host regression checks sequence values beyond the old
8-bit marker and refuses premature final verification.
