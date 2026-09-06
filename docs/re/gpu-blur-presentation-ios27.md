# Screen-sized resident blur and presentation acceptance

**Result: displayed GPU correctness acceptance passed; the consistent-60-Hz
performance gate did not pass.** Two fresh boots each execute 33 full-screen
blur/conversion frames through the custom driver and host Metal, deliver
every identified frame through the guest's normal DCP path, verify exact
final pixels and resume native display/input. Steady end-to-end median/p95
are 14.972/24.061 ms in run 1 and 31.894/103.786 ms in run 2; observed
displayed-submission throughput is 58.26/s and 22.39/s respectively.
This is repeated resident imagery, not Liquid Glass UI FPS or a general
shared-texture/system-wide Metal implementation. The initial no-scanout
interpretation was wrong: the marker witness compared padded allocation
size with logical pixel size. The corrected evidence is retained below.

## Contract before implementation

Follow-up preregistration: compare the unchanged PRESENT_CONTRACT_BUILD4
guest on fresh children using the same rebuilt QEMU, with the new
`DARWIN_DCP_IOMFB_DISPLAY_STATE` producer disabled/enabled. Require three
ordered markers, matching native completions and successful mode-zero waits
before installing/running the 33-frame GPU acceptance. Stop at the first
error or existing deadline. The producer is scoped to successful active
scanout: publish availability and submitted ID through the DART into the
derived observer region, then send D594. No kernel return/field patches.
Do not infer sleep/blanking or checkpoint correctness from this experiment.

Contract refinement before the next guest build: the second observer is
**current displayed swap**, not "last completed swap". In `a0bbf34-f54`,
mode 0 keeps waiting when that ID matches even if no queue item remains;
mode 1 returns when the item has retired. The guest framework's own
`IOMobileFramebufferWaitSurface` explicitly supplies mode 1 at
`0x22a3bf7c4`. Both modes retain the display-availability preflight.
PRESENT_CONTRACT_BUILD5 and PRESENT_DRIVER_BUILD3 adopt that mode for
surface reuse. The initial state producer log said `completed=0`; that
label was incorrect and is corrected to `current=0` without changing bytes
or publication ordering. Keep the mode-0 trial as a separate control.

Exact shared ABI: `a0cf6e4-f4` sets the observer base to heap+0x80000 and
loads offset/length `(0x80000,0x40000)` from `0x7996770`. Accessors
`a0ce3ac`, `a0d4764`, `a0d4bc0` and H17P `9188768` pass that region to
the observer allocator. `9188ba4-bb4` registers the first two 4-byte
observers consecutively through `9188ef4`, targeting framebuffer+0x58f4
and +0x58f8. The first is the availability word used by the NoPower gate;
the second is compared against the waited swap ID. Static disassembly is
reproduced with `inspect_present_wait.py --include-h17p` in
PRESENT_WAIT_STATIC3. Availability=1 after successful synchronous scanout
is an explicit bounded model inference, tested below, not a recovered
complete firmware power-state enumeration.

The previous witness compared logical scanout bytes (stride × height,
12,432,384) to the padded IOSurface allocation (12,435,456), so it could
never recognize the frame. The parser already produces the logical size;
the corrected marker test now covers that exact padded input. Independent
`verify_present_pattern.py` inspection of PRESENT_CONTRACT_GUEST3/final.png
checks all 3,013,524 RGB pixels against CPU frame 1: **zero wrong pixels**,
RGB SHA256 `1161455f82d2df8bb1589c47b83939de776f59893f5eda6c80007c0f6c5a2a62`.
This corrects the earlier no-scanout interpretation; it is not GPU blur proof.

Continue from 1e421ea on codex/metal-driver-ios27, exact iOS 27 24A5430a,
iPhone17,3/T8140, six-vCPU TCG. Preserve SPTM/TXM, native SMC/original powerd,
software rendering, migrated disk ancestry and native display/input. Use fresh
disposable children of BLUR_DRIVER_INSTALL2; no debugger or NVMe GPU transport.

The acceptance workload is repeated 1184×2560 two-pass compute_simd_blur_5
from this guest's unmodified QuartzCore AIR, GPU-side conversion/cropping into
1179×2556 BGRA, and actual presentation through the existing guest display path.
Separate all library/pipeline/allocation/input-upload/display setup from the
batch. Retain resources; at least 32 sequential timed frames after one first-use
frame. No CPU pixel reads, hashes or equality checks for verification inside the
timed batch. Any copy required to deliver pixels to the display stays timed and
must be reported. Check final pixels against the CPU oracle after the batch.

Report GPU duration, submit→completion, output delivery, swap→completion,
end-to-end latency, first frame, steady median/p95/max, slow frames over
16.667/33.333 ms, and sustained batch wall throughput. Device completion and
swap return are not by themselves proof that our frames reached the display;
correlate output identity with the normal DCP scanout and final displayed pixels.
Do not count unrelated native presentations as accepted workload frames.

Correctness acceptance requires all submitted frames to complete and be
identified on the normal display path, exact final pixels (with any documented
format conversion tolerance), safe resource retirement, and native input/display
recovery. Report performance separately: near-interactive screen processing
requires sustained end-to-end p95 below 16.667 ms for the 60 Hz target; completing
a slow batch is a correctness pass only, not a performance pass. A 32-frame
sample is an initial gate, not a production tail-latency characterization.

Stop on an explicit API error, failed contract/bounds/identity/generation,
GPU error, stale/corrupt final pixels, missing workload presentation, native
input/display regression, failed retirement, panic, per-request 15-second
transport deadline, 60-second no-progress deadline or bounded overall runtime.
Keep a failed attempt and identify the exact failed contract before choosing
another route. No hidden readback fallback in the measured path.

## First prerequisite: guest presentation API

The prior blur IOSurface was offscreen. Earlier displayed host pixels used a
host debugger to patch an existing native surface and do not prove a guest API
submission. Test the exact guest IOMobileFramebuffer wrappers on one newly
allocated, owned screen-sized surface before building resident GPU plumbing.
This prerequisite uses three CPU patterns, not the acceptance GPU workload.

Static evidence: /tmp/dvm/welcome-static/IOMobileFramebuffer; full disassembly
saved in /tmp/dvm/BLUR_PRESENT_CONTRACT1/IOMobileFramebuffer.disass.
GetMainDisplay 0x22a395f2c calls iomfb_get_display/Open; GetDisplaySize
0x22a3bf234 dispatches to kern_GetDisplaySize (CGSize output at 0x22a391fa4).
SwapBegin 0x22a3911b0 writes a uint32 swap ID (kern at 0x22a391260).
kern_SwapSetLayer 0x22a3918f4 embeds its complete signature with two CGRects
passed by value, IOSurfaceRef, layer index and flags. SwapEnd 0x22a391750
calls the existing selector-5 path. SwapWaitWithTimeout 0x22a3bf77c forwards
two uint32 arguments and a double seconds value (0x22a3c233c–0x22a3c2358).
These signatures establish how to make the calls, not that the service will
accept this client or that a successful return guarantees display scanout.

## Initial outcomes

PRESENT_HOST1 proves a host segment: the exact guest AIR's two blur passes plus
an explicit host-authored BGRA conversion kernel write directly into a Metal
buffer backed by an owned file mapping. All 33 commands complete without a
verification read inside the batch; final-only CPU comparison reports zero
wrong pixels. A four-pixel GPU-written marker reserves frame identity for the
later presentation witness. This does not yet prove guest access or display.
Run tools/gpu/run_present_host_probe.py with fresh --out and the pinned --air.

PRESENT_CONTRACT_GUEST1 reaches post-display readiness at 160.67 seconds (445
native presentations and a stable fresh input ACK). Its ordinary eight luma
GPU submissions verify first. The independent display calls then return:
GetMainDisplay=0, GetDisplaySize=0 (1179×2556), SwapBegin=0 (swap ID 0),
SwapSetLayer=0 (owned IOSurface ID 7), SwapEnd=0xe00002d1. It stops immediately;
SwapWait and frames 2/3 are untested. The runner correctly marks the trial failed.
This is a new surface's submission failure, not a shader or transport failure.

The existing surface-cache ledger describes an exact matching gate:
[allocation cache contract](surface-cache-and-completion.md). Rechecking the
current MMIO_BOOT_BUILD4 BootKC confirms the getter call at 0xfffffff00a0b918c
and cmp w0,#0x700 at 0xfffffff00a0b9190 (bytes 1f001c71), followed by b.ne.
Bytes and current BootKC SHA are saved in BLUR_PRESENT_CONTRACT1/cache-gate.json.
The first probe omitted kIOSurfaceCacheMode. Next change only its allocation
request to 0x700 using the actual exported key. This is a test of that hypothesis;
it changes no existing mappings, validation branches or native return values.

## Follow-up presentation failures

| Trial | Single change / control | Observed result |
|---|---|---|
| PRESENT_CONTRACT_GUEST2 | Request cache mode 0x700; same frozen QEMU and BootKC as trial 1 | Begin=0, layer=0, **end=0**, wait=**0xe00002e3** (`kIOReturnNoPower`). Stops at frame 1. |
| PRESENT_CONTRACT_GUEST3 | Add `RequestPowerChange(fb,1)`; original defective witness | Power request=0, begin=0, layer=0, end=0, **wait still 0xe00002e3**. Stops at frame 1. Later independent PNG comparison proves the entire CPU pattern was displayed. |

All three are CPU-pattern presentation prerequisites following eight verified
GPU luma submissions. They are not blurred GPU presentation runs. Trial 3 uses
PRESENT_CONTRACT_BUILD4, PRESENT_CONTRACT_STAGE3, PRESENT_CONTRACT_INSTALL3 and
PRESENT_BOOT_BUILD2. Its post-display gate opens at 129.79 seconds after 85
native presentations, ten seconds of stable reader state and a fresh input ACK.
The helper opens the main display successfully and obtains 1179×2556 geometry.
`RequestPowerChange` returning success does not establish completed power state
or the polarity/ownership semantics of its argument. The later request with
argument zero is not reached on these failures. No explicit power request was
needed to keep the independent native display presenting before the probe.

**Proven:** the explicit 0x700 allocation request removes the observed
`SwapEnd` cache-mode rejection in these trials. **Disproven within scope:**
that allocation change alone, or additionally requesting power with argument
one immediately before submission, is sufficient for this direct-client
presentation test. **Untested:** a correctly initialized/powered direct client,
native compositor adoption, or a corrected DCP state producer. Failure of this
particular client sequence does not disprove guest API presentation in general.

## Exact static wait contract and next diagnostic

`inspect_present_wait.py` pins the current BootKC hash and emits disassembly
plus `contract.json` in PRESENT_WAIT_STATIC1. The selector-6 entry at
0xfffffff00829a308 resolves to 0xfffffff00a0e5d7c (three scalar inputs). It
passes the client, swap ID, mode and timeout through framebuffer vtable +0x590.
The DCP implementation at 0xfffffff00a0bbd6c dispatches through +0x8b8 to the
wait body. For mode zero, its gated preflight is 0xfffffff00a0c6fcc.

At 0xfffffff00a0c70f8, preflight refreshes the observer at framebuffer+0x5e00
if it exists. At 0xfffffff00a0c7104–0xa0c7110, framebuffer+0x8c bit zero set
and uint32(framebuffer+0x58f4)==0 lead to 0xa0c7154. That selects
`0xe00002c2 | 0x21 = 0xe00002e3` and returns before the pending-swap search.
The observer helper at 0xa0bc734 copies a uint32 from observer+0x10's source
to observer+0x20's destination. Its producer/registration was unresolved at
that point; the follow-up above identifies the shared region.

This is **static evidence of a matching failed contract**, not a runtime trace
proving that branch ran or that a particular DCP field is missing. Generic
kernel sites returning the same error were also found; the error code alone
does not identify its origin. Do not patch the return, force the cached word
nonzero, or use wait mode 3/4 to sidestep this check and call it completion.

The next experiment selected at that point was to resolve the observer's registration and
source, then instrument that source at the existing device boundary during
one owned CPU-pattern submission. Record whether the observer is absent,
returns zero throughout, or changes with actual power transitions, and whether
our surface reaches A408. If the source belongs to an unimplemented device
state contract, implement only its demonstrated transitions; if state is valid,
compare direct-client setup with the native compositor's setup. Require one
identified actual scanout and native completion before retrying all 33 GPU
frames. This dependency takes priority over more shader optimization.

## Active-display contract experiments

All are fresh disk boots of isolated children, exact unchanged BootKC/DT,
SPTM/TXM and native SMC. No debugger or NVMe GPU transport.

| Trial | Configuration | Observed outcome |
|---|---|---|
| PRESENT_STATE_CONTROL1 | Fixed witness; producer off; unchanged mode-0 helper | Own frame 1 displayed and final pixels exact; end=0, wait=0xe00002e3. 358 native scanouts and 358 D594 returns. Stops at 174.051 s. |
| PRESENT_STATE_ACTIVE1 | Same frozen binary/helper; producer on | First observer-region read is `0/0`; successful scanout publishes `1/0`. Own frame 1 displayed and final pixels exact; end=0, mode-0 wait does not return. Stops at 199.957 s on 60-second workload no-progress deadline; 648 native scanouts and 648 D594 returns. |
| PRESENT_STATE_SURFACE1 | Producer on; mode 1 matching guest WaitSurface | **Three ordered own scanouts, three successful end/wait returns**, same IOSurface DVA `0x100017f8000`. Final full pixel oracle: zero wrong pixels. Completes at 145.136 s. |

The mode-0 wait's supplied timeout was 2.0 seconds. Its wrapper multiplies
by 1000 at `0x22a3c233c` (constant double 1000 at `0x22a3cdc70`), but this
run did **not** enforce a two-second wall deadline. The external no-progress
deadline stopped it. Do not present the private timeout argument as a proven
hard deadline. Mode 1 still uses the same NoPower preflight; it does not use
the mode-3/4 alternate paths. The native D594 callback has already retired
the queue item before SwapEnd returns in this synchronous model.

`presentation-contract.json` and `pixel-verification.json` in the surface
trial contain the correlated returns and full CPU oracle. Final frame 3's
RGB SHA is `366902f4e53195aacc9a9efbd5fb92497a295b92b714c36f7641cb1f6865f9d0`.
The scanout implementation copies guest pixels to its own allocation and
then to `darwin_fb.c`'s owned console pixels before publication/D594. Its
returned success, frame identity and subsequent native callback—not the
wait API name alone—support reuse for this synchronous, one-surface case.
Multiple outstanding GPU/display operations, blank/sleep transitions and
live checkpoint state remain untested.

One setup attempt, PRESENT_CONTRACT_INSTALL4, exited before guest execution:
`dvm-gpu-shm: requires paired owned RAM file and exact DT ranges`. It passed
the transport DT to the restore installer without an owned RAM backend.
No guest disk writes occurred. The successful INSTALL5 used the original
restore-install manifest, then rebound the resulting immutable child to the
transport BootKC/DT. This was an installer configuration error, not a DCP
or guest service-loading failure.

## Host segment, with final-only verification (prior measurement)

PRESENT_HOST2 uses Apple M5 Max, the exact AIR pinned above and a host-authored
conversion-only kernel. Each frame runs both guest blur passes and converts to
1179×2556 BGRA in a `newBufferWithBytesNoCopy` buffer over an owned file mapping.
Input/intermediate/output textures, pipelines and buffer persist across frames.
The input is generated and uploaded once on the host from a fixed nonce;
this is repeated unchanged imagery, not changing UI content or guest uploads.
Four GPU-written pixels contain the workload/frame marker. The final CPU oracle
checks all output pixels, including these marker pixels. Row padding is not
part of the pixel oracle.

PRESENT_HOST2 buffers timing samples until after the batch, so even timing-log
printing is outside it. Setup is separate; frame 1 is separate from frames 2–33.
Final validation runs only after the batch timestamp and reports zero wrong
pixels. The conversion writes shared RAM directly; no `getBytes` readback is
performed for any measured frame.

| Host-only measurement | PRESENT_HOST2 |
|---|---:|
| Process setup, including library/pipelines/allocation/input upload | 45.968 ms |
| First frame | 8.830 ms |
| Steady submit/complete median / nearest-rank p95 / max | 0.495 / 0.758 / 5.348 ms |
| Steady GPU execution median / p95 / max | 0.280 / 0.549 / 4.995 ms |
| Steady batch wall time, 32 frames | 21.471 ms |
| All 33 frames, batch wall time | 30.310 ms |
| Steady frames over 16.667 / 33.333 ms | 0 / 0 |

This small host sample is not controlled for other host activity, cold system
compiler caches, thermals or sustained tails. PRESENT_HOST1 is retained as an
earlier sample (162.737 ms setup, 5.590 ms first frame); its timing print calls
were still inside the batch. Neither sample includes guest transport, the
12,432,384-byte guest copy, IOSurface locking, native swap or display completion.
Therefore no guest end-to-end median/p95/throughput is reported for this change.

## Accepted bounded plumbing and remaining scope

`--mmio-present` is an opt-in bounded resident-workload extension of DVMProxy,
not general MTLTexture shared backing. One library plus one resident workload
own three textures, two compute pipelines and one shared output buffer. Host
tests execute all 33 frames, reject invalid/stale/out-of-order operations,
check final pixels only after the batch, and retire all tracked resources.

Mode 3 keeps control requests/replies below 64 KiB, moves replies to 2 MiB and
reserves 3 MiB onward in the existing owned 16 MiB RAM for BGRA output. Other
transport modes retain their layouts. The worker accepts the RAM path only
from its host launcher, never from the guest. Output is published only after
actual GPU command completion. The guest's delivery is a timed copy
into a cache-0x700 IOSurface; direct GPU backing of that IOSurface is untested.

The opt-in QEMU witness inspects four pixels already read for normal DCP
scanout, retains that existing allocation and exports only the last marked
frame when the owned VM stops. It adds no extra pixel readback, hash or full
copy for verification per frame. It does not inject pixels or bypass display
submission. The acceptance peer requires markers 1–33 in order, matching swap
IDs, final exported pixels matching the GPU mapping and CPU-verified guest
IOSurface, resource retirement, and later native display/input recovery.
The repaired witness is now validated by PRESENT_STATE_CONTROL1 and
PRESENT_STATE_ACTIVE1: each exported CPU frame 1 after actual normal scanout.

One outstanding frame, repeated guest surface reuse, synchronous GPU/display
completion and final export are exercised by the guest acceptance below.
Live GPU checkpoint migration remains blocked; no checkpoint-safety claim
follows from this work. The state producer remains opt-in because full power,
blank/sleep transitions and asynchronous/multiple-outstanding lifetimes are
not established by an active-screen batch.

## Complete displayed GPU batch

PRESENT_GPU_GUEST1 uses PRESENT_DRIVER_BUILD3 installed through
PRESENT_DRIVER_STAGE2/PRESENT_DRIVER_INSTALL1 and the frozen
PRESENT_STATE_BOOT4/state.json. QEMU SHA256 is
`6fc5d215620311c7e1d49611130fd1024005e28ab813750ec41a1e9d5f280b26`.
Its BootKC is still `ed3ef577af60140ebfca494cdd3ab52ec97f3bddbd5c07a9020263742630df23`;
DT is still `79c53207fd921ee6d1f10c7dfa4791fbd28628c347fa717279df4ea1bc642ed4`.

**Observed in each run:** all 33 commands run both exact-guest QuartzCore blur passes
and the host conversion pass, with actual Metal command completion. All
33 GPU-written markers reach normal guest A408 scanout in order. Final
guest IOSurface, GPU shared output and retained normal-scanout bytes match
exactly: 12,432,384 bytes, SHA256
`bb5cc065f82ba2b8586d5774441f68d8841d5696bf71046e1db784f53c3f4c31`.
The final-only CPU oracle reports zero wrong pixels. No per-frame readback
or pixel hash is performed for verification. The 12.4 MB guest delivery copy
and ordinary QEMU DART/console copies remain part of the measured path.
Resources retire to zero. Original software rendering and native input
continue after the batch; fresh post-batch scanout/D594 bytes and a fresh
input ACK are independently recorded. Zero first XNU panics are observed.

| Measurement (milliseconds unless stated) | PRESENT_GPU_GUEST1 |
|---|---:|
| Guest setup, including display/library/pipelines/resources/input | 850.172 |
| First frame, end to end | 37.993 |
| Steady end-to-end median / p95 / max | 14.972 / 24.061 / 32.511 |
| Steady guest draw RPC median / p95 / max | 2.181 / 4.663 / 10.298 |
| Steady output delivery median / p95 / max | 7.943 / 16.516 / 18.630 |
| Steady native swap/completion median / p95 / max | 4.372 / 9.262 / 14.095 |
| Steady GPU execution median / p95 / max | 0.374 / 2.688 / 2.830 |
| Steady host service median / p95 / max | 0.718 / 3.000 / 3.134 |
| All 33 frames / 32 steady frames, batch wall time | 585.778 / 547.278 |
| Steady frames over 16.667 / 33.333 ms | 12/32 / 0/32 |
| Guest steady displayed submissions/s | 58.47 |
| Independent host scanout submissions/s (frame 1→33) | 58.26 |
| Host scanout interval median / p95 / max | 15.699 / 25.950 / 33.167 |

PRESENT_GPU_GUEST2 repeats the identical guest/QEMU payload and fresh parent
child with the final integrated post-batch log-offset witness. It also passes
all 33 displayed frames, native completion, final-only exact pixels and
resource retirement. Its final-byte SHA is
`a1cc863174aa6a20cb8589c658f8a57ac31fb39669b03c76f68329cae0559931`;
the independent nonce makes different output hashes expected.

| Repeat measurement (milliseconds unless stated) | PRESENT_GPU_GUEST2 |
|---|---:|
| Guest setup / first frame | 3308.455 / 272.934 |
| Steady end-to-end median / p95 / max | 31.894 / 103.786 / 198.636 |
| Steady draw RPC median / p95 / max | 2.922 / 9.453 / 13.059 |
| Steady output delivery median / p95 / max | 12.961 / 64.048 / 99.098 |
| Steady native swap/completion median / p95 / max | 14.116 / 72.316 / 97.301 |
| Steady GPU execution median / p95 / max | 0.376 / 0.836 / 1.076 |
| Steady host service median / p95 / max | 0.857 / 1.497 / 1.675 |
| All 33 frames / 32 steady frames, batch wall time | 1695.452 / 1420.877 |
| Steady frames over 16.667 / 33.333 ms | 32/32 / 15/32 |
| Guest / independent host displayed submissions/s | 22.52 / 22.39 |
| Host scanout interval median / p95 / max | 34.369 / 119.331 / 148.819 |

Both runs have zero XNU panics and no display-state publication failures.
Post-batch input ACK counters rise 58→63 and 68→73; native presentation
counters rise 138→280 and 192→264. Because counters can be published late,
both also require newly appended scanout and D594 completion log bytes after
the completion gate. Their `post-batch-native.json` contains that evidence.

The repeat's delivery and swap tails are substantially worse despite similar
GPU execution medians. The evidence places that variability outside the
shader segment but does not isolate host scheduling, guest background work,
copy/cache costs or kernel display work. Do not report the faster trial as
consistent 58 FPS, discard the slower trial, or claim a proved cause without
a controlled experiment.

These are 32 steady samples of unchanged resident imagery with changing
frame markers. They are not paced to vsync, do not exercise changing UI
uploads and do not establish Liquid Glass or scrolling FPS. GPU time is
nested inside draw time; stage medians/percentiles must not be summed.
The guest uses CLOCK_MONOTONIC; host scanout timestamps provide an independent
wall-clock throughput observation. Setup and first use are separate, but
system compiler caches, thermals and unrelated host activity are uncontrolled.
The historical guest CPU screen blur was about 2.86 seconds through IOSurface
unlock; its workload/layout/timing differed, so it is not a matched displayed
CPU/GPU ratio for this new run.

**Proven within scope:** active display-state publication plus native surface
retirement supports this repeated displayed GPU workload. **Disproven within
scope:** either sample meets the preregistered p95<16.667 ms target. **Untested:**
system-wide Metal/CoreAnimation integration, actual Liquid Glass composition,
changing input, direct shared IOSurface backing, full display power lifecycle,
multiple outstanding frames and live GPU checkpoint restore.

The smallest next implementation is an owned IOSurface whose backing the
host GPU can write directly, eliminating the measured guest delivery copy.
First establish its mapping, cache/coherency, GPU→DCP completion ordering,
and unmap/retirement contracts for one reusable surface. Do not infer those
from this copy-based result or optimize the already-small shader segment.

## Reproduction, validation and artifacts

Current complete acceptance, using a new disposable child of the pinned
installed parent (the runner handles readiness, completion, post-batch native
recovery and final scanout export):

```sh
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py /tmp/dvm/PRESENT_STATE_BOOT4/state.json --tag PRESENT_GPU_REPRO --seconds 240 --driver-present --driver-mmio --driver-wait-display --driver-worker /tmp/dvm/PRESENT_DRIVER_BUILD3/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
python3 tools/gpu/report_present_batch.py /tmp/dvm/PRESENT_GPU_REPRO
python3 tools/gpu/verify_present_contract.py /tmp/dvm/PRESENT_STATE_SURFACE1
```

To reconstruct a new installation, build with `build_driver.sh --mmio-present`,
stage it with `prepare_driver_update.py` against the installed helper's exact
preimage, and run `run_guest_install.py` using its original warm install
manifest. Then use `prepare_display_state_trial.py` to freeze the freshly
built QEMU and unchanged transport BootKC/DT, hash all source/input artifacts,
and emit paired control/state manifests. The trial uses `state.json`.
Do not give the restore installer a transport DT without its RAM backend.

The first GPU run used the read-only `watch_present_recovery.py` alongside
the runner to capture new scanout/D594 log bytes after its completion gate;
that same offset-based witness is now integrated in the runner. It avoids
treating delayed publication of a presentation counter as a new frame.

Historical host-only and failed-prerequisite commands follow.

From this checkout (use fresh output tags; scripts refuse existing outputs):

```sh
python3 tools/gpu/run_present_host_probe.py --out /tmp/dvm/PRESENT_HOST_REPRO --air /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
python3 tools/gpu/inspect_present_wait.py --bootkc /tmp/dvm/PRESENT_BOOT_BUILD2/bootkc --out /tmp/dvm/PRESENT_WAIT_REPRO
bash tools/gpu/build_driver.sh /tmp/dvm/PRESENT_BUILD_REPRO --mmio-present
DVM_DRIVER_BUILD=/tmp/dvm/PRESENT_BUILD_REPRO python3 -m unittest discover -s tools/gpu -p 'test_*.py' -v
python3 tools/gpu/run_guest_load.py /tmp/dvm/PRESENT_CONTRACT_INSTALL3/mmio-manifest.json --tag PRESENT_CONTRACT_REPRO --seconds 240 --driver-mmio --driver-wait-display --driver-worker /tmp/dvm/PRESENT_CONTRACT_BUILD4/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
```

The last command reproduces the **failed CPU-pattern prerequisite** using the
pinned installed helper, not the new GPU batch. It makes a new disk child and
rebinds the witness output to that child's directory. Source snapshots,
manifest hashes, launch arguments/environment, shared-RAM audit, driver RPC
records, stderr, input status and result are under each trial directory.
PRESENT_DRIVER_BUILD2 compiles/signs/import-checks the new full workload but
was not installed or booted after the failed prerequisite.

Final validation: 64 GPU-tool tests run, 63 pass and one separate legacy surface
integration test skips because its explicit worker was not supplied. The
resident Metal tests execute real GPU work. All 79 project tests pass. The
rebuilt QEMU's three IOMFB swap cases pass, including the padded-allocation
marker regression. Both final GPU boots and the CPU prerequisite complete.
Post-trial `verify_trial_inputs.py` checks all 45 backing-chain entries,
including the migrated baseline, and all seven pinned boot inputs. BootKC/DT
hashes remain unchanged. No baseline disk, SPTM/TXM, native SMC policy,
software renderer or unrelated VM was modified.

Current durable evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-dcp-displayed-batch-ios27/`.
It contains the logs, owned RAM output, source snapshots, static excerpts,
metrics, validation logs and file-hash index; disk overlays and proprietary
firmware stay in their original local artifact directories. The earlier failed
prerequisite/host-only archive remains at
`/Users/jdolbe1/dvm-artifacts/research/gpu-blur-presentation-ios27/`.
