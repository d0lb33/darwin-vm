# Screen-sized resident blur and presentation acceptance

**Result: acceptance not passed.** Host repeated blur/conversion into owned
shared RAM is proven. The separate guest presentation prerequisite fails at
the native completion contract; no identified workload scanout was observed.
The 33-frame displayed guest GPU batch remains untested. This is not achieved
VM FPS, Liquid Glass acceleration, or a general shared-texture implementation.

## Contract before implementation

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
| PRESENT_CONTRACT_GUEST3 | Add `RequestPowerChange(fb,1)`; passive scanout marker witness in rebuilt QEMU | Power request=0, begin=0, layer=0, end=0, **wait still 0xe00002e3**. No marked scanout or final export. Stops at frame 1. |

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
to observer+0x20's destination. Its producer/registration still needs tracing.

This is **static evidence of a matching failed contract**, not a runtime trace
proving that branch ran or that a particular DCP field is missing. Generic
kernel sites returning the same error were also found; the error code alone
does not identify its origin. Do not patch the return, force the cached word
nonzero, or use wait mode 3/4 to sidestep this check and call it completion.

The smallest next experiment is to resolve the observer's registration and
source, then instrument that source at the existing device boundary during
one owned CPU-pattern submission. Record whether the observer is absent,
returns zero throughout, or changes with actual power transitions, and whether
our surface reaches A408. If the source belongs to an unimplemented device
state contract, implement only its demonstrated transitions; if state is valid,
compare direct-client setup with the native compositor's setup. Require one
identified actual scanout and native completion before retrying all 33 GPU
frames. This dependency takes priority over more shader optimization.

## Host segment, with final-only verification

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

## Implemented but not guest-accepted plumbing

`--mmio-present` is an opt-in bounded resident-workload extension of DVMProxy,
not general MTLTexture shared backing. One library plus one resident workload
own three textures, two compute pipelines and one shared output buffer. Host
tests execute all 33 frames, reject invalid/stale/out-of-order operations,
check final pixels only after the batch, and retire all tracked resources.

Mode 3 keeps control requests/replies below 64 KiB, moves replies to 2 MiB and
reserves 3 MiB onward in the existing owned 16 MiB RAM for BGRA output. Other
transport modes retain their layouts. The worker accepts the RAM path only
from its host launcher, never from the guest. Output is published only after
actual GPU command completion. The guest's proposed delivery is a timed copy
into a cache-0x700 IOSurface; direct GPU backing of that IOSurface is untested.

The opt-in QEMU witness inspects four pixels already read for normal DCP
scanout, retains that existing allocation and exports only the last marked
frame when the owned VM stops. It adds no extra pixel readback, hash or full
copy for verification per frame. It does not inject pixels or bypass display
submission. The acceptance peer requires markers 1–33 in order, matching swap
IDs, final exported pixels matching the GPU mapping and CPU-verified guest
IOSurface, resource retirement, and later native display/input recovery.
The witness itself has **not yet been validated on an observed marked scanout**.

One outstanding frame and resource lifetime through synchronous GPU completion
are exercised by host tests. Guest scanout lifetime/reuse and the final export
contract remain untested end to end. Live GPU checkpoint migration remains
blocked; no checkpoint-safety claim follows from this work.

## Reproduction, validation and artifacts

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

Validation: 62 GPU-tool tests run, 61 pass and one separate legacy surface
integration test skips because its explicit worker was not supplied. The
resident Metal tests execute real GPU work. All 79 project tests pass. The
rebuilt QEMU's IOMFB swap unit test passes; default-mode luma execution and
normal native scanout continue in trial 3. Post-trial verification hashes all
43 backing-chain entries, including the migrated baseline, and verifies the
unchanged BootKC/DT hashes. No baseline disk, SPTM/TXM, native SMC policy,
software renderer or unrelated VM was modified.

Durable evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-blur-presentation-ios27/`.
It contains the logs, owned RAM output, source snapshots, static excerpts,
metrics, validation logs and file-hash index; disk overlays and proprietary
firmware stay in their original local artifact directories.
