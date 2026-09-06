# Exact-guest blur workload feasibility

## Preregistered guest comparison

This experiment follows binary-submission commit a2d98f9, preserving exact iOS
27 24A5430a iPhone17,3/T8140, native SMC/original powerd, SPTM/TXM, migrated disk
ancestry, existing display/input and software rendering. No QEMU, BootKC or DT
change is needed. Owned disposable children only; no debugger or NVMe command
transport. The existing experimental MMIO boot service is reused.

Use exact QuartzCore AIR compute_simd_blur_5 twice (horizontal then vertical),
with five weights [1,4,6,4,1]/16 and EDR scale one. This is a separable blur,
not the full Liquid Glass recipe or a compositor. Positive sampling offsets
shift the result two pixels per axis; padded input supplies all filter taps.
No unsupported boundary rule is inferred from out-of-bounds texture reads.

Four output sizes: 64×64, 256×256, 512×512, and 1184×2560 (32-aligned coverage
of the 1179×2556 display; extra pixels are included in timings). Each size has
one first-use frame plus sixteen subsequent frames. Two different pseudorandom
input images alternate, with CPU results and every GPU output verified exactly.
The CPU uses a separable vector implementation with half rounding after each
FMA, matching the AIR arithmetic; input generation is outside timed work.
The initial plan used the same compiler flags as the current frontend (-O1).
The final measured helper uses native FP16 vector FMA at -O3, following the
CPU control correction below; the frontend and executor remain -O1. It gets
an explicit, read-back-verified 256 MiB process allowance for the two inputs,
two reference images, scratch, output and IOSurface. The luma build keeps 64 MiB.

Run CPU batches first, then a continuous GPU batch. GPU frames go through the
existing public Metal frontend plus a bounded imageblock/blur extension, owned
shared RAM, doorbell, host Metal, full texture readback, guest assembly and BGRA8
conversion/delivery into an owned IOSurface. Both CPU and GPU use the same
IOSurface conversion. That surface is offscreen: normal DCP presentation of the
blur and global Metal discovery are explicitly outside this experiment.

The existing 1 MiB texture / 2 MiB framed transport bounds remain. GPU output is
tiled at up to 256×256 with 32-pixel source padding and intermediate extra rows.
Resident texture handles are reused across frames; contents are uploaded and
outputs delivered each frame. Tile counts are 1, 1, 4, 50, for 952 submissions
and 1904 actual GPU dispatches per completed guest run. Tiling overhead and extra
halo work remain in the measurement. After all sizes, every resource must retire.

Separately measure the host-only ceiling using whole-image textures retained
across frames. Keep its one-time upload distinct; record command completion,
GPU duration and CPU readback separately. Its GPU-resident completion time
excludes guest transport/IOSurface presentation and is not an end-to-end result.

Primary gate: do repeated post-display guest runs show lower median full GPU
work (through IOSurface delivery) than equivalent CPU work plus the same output
delivery at useful sizes? Report every first frame, median, p95 and maximum of
the remaining 16, slow frames over 16.667/33.333 ms, bytes and tile/RPC counts.
Report measured GPU batch wall throughput including validation and separately
the service-time reciprocal; do not label the latter observed display FPS.
The harness hashes/verifies each full frame outside its work timer, and includes
that overhead in batch wall time. Sixteen samples do not establish robust p99.

Stop immediately on wrong pixels/IOSurface hash, failed bounds/state/sequence/
CRC, unexpected host response, native readiness regression, failed retirement,
GPU error, panic or per-request deadline. Global guest boot+work deadline is
300 seconds, with the existing 60-second no-progress limit. Preserve failed
runs rather than silently replacing them. Two successful fresh guest runs are
the repeatability target. Do not optimize transport in this experiment merely
to turn a negative result into a positive one.

## Static contract and first experiment

Source AIR SHA remains
8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364.
`BLUR_CONTRACT1/compute_simd_blur_5.ll` comes from llvm-dis of the unmodified
function AIR. Its target is air64_v29-apple-ios27.0.0. Uniform offsets are:
src_offset 0, dst_offset 4 (unused by this function), fill_offset 8, group_size
12, horizontal 16, half edr_scale 18. Texture indices 0/1; uniform/weights
buffer indices 0/1. It reads five half weights, uses SIMD shuffle/fill and half
FMA, stores into an explicit half4 imageblock and writes a 32×32 block.

Observed host contract: threadExecutionWidth 32, 1024 threads per group,
setImageblockWidth:32 height:32, 8192 imageblock bytes. H fill=(32,0), V=(0,32),
group_size=(32,32), scale=half(1). H writes W×(H+32), V writes W×H. Both 64×64
initial patterns and 256×256 pseudorandom patterns match the CPU reference
bit-for-bit. This is execution evidence, not merely successful pipeline creation.
`blur_host.h`, `blur_reference.h` and `blur_wire.h` retain the derivation and
bounded layouts. Full command state crosses BLQ1 and is validated before uploads;
unknown handles, geometry or uniform/weight/imageblock state must fail closed.

## Reproduction commands

All output paths/tags must be fresh. Build3 is the first complete blur build;
Build1/2 retain compile diagnostics (duplicate header definitions, preprocessor
syntax, and missing malloc/empty-dictionary imports), not guest executions.
The host frontend harness completed all 952 submissions and retired to zero
before the first guest boot (`BLUR_DRIVER_HOST1/client.log`, `host.log`).

```sh
/opt/homebrew/opt/llvm/bin/llvm-dis /tmp/dvm/GPU_FEAS_SHADER1/air/compute_simd_blur_5.air -o /tmp/dvm/BLUR_CONTRACT1/compute_simd_blur_5.ll
bash tools/gpu/build_driver.sh /tmp/dvm/BLUR_DRIVER_BUILD3 --mmio-blur
DVM_DRIVER_BUILD=/tmp/dvm/BLUR_DRIVER_BUILD3 python3 -m unittest discover -s tools/gpu -p 'test_*driver*.py' -v
python3 -m unittest discover -s tools/tests -v
bash -n tools/gpu/build_driver.sh tools/probe.sh tools/re/setup_gate_probe.sh tools/re/setup_gate_sweep.sh
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/MMIO_BINARY_BUILD1 --build /tmp/dvm/BLUR_DRIVER_BUILD3 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/MMIO_BINARY_STAGE1/system.tc --out /tmp/dvm/BLUR_DRIVER_STAGE1
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/MMIO_BINARY_INSTALL1/warm-manifest.json --stage /tmp/dvm/BLUR_DRIVER_STAGE1 --tag BLUR_DRIVER_INSTALL1
python3 tools/gpu/prepare_mmio_manifest.py /tmp/dvm/BLUR_DRIVER_INSTALL1/warm-manifest.json /tmp/dvm/MMIO_BOOT_BUILD4 /tmp/dvm/BLUR_DRIVER_INSTALL1/mmio-manifest.json
python3 tools/gpu/run_guest_load.py /tmp/dvm/BLUR_DRIVER_INSTALL1/mmio-manifest.json --tag BLUR_GUEST1 --seconds 300 --driver-mmio --driver-wait-display --driver-worker /tmp/dvm/BLUR_DRIVER_BUILD3/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
python3 tools/gpu/summarize_blur.py /tmp/dvm/BLUR_GUEST1 --output /tmp/dvm/BLUR_GUEST1/verification.json
python3 tools/gpu/run_blur_host_sweep.py --out /tmp/dvm/BLUR_HOST_SWEEP1 --air /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
```

The blur workload uses up to 120×512-byte audit slots between offsets 0x1000
and 0x10000, without moving the request payload. The ordinary luma build keeps
its 64-slot limit; the peer selects the bound from the pinned worker build's
transport-mode.txt. Blur emits frame-level metrics instead of 952 per-RPC audit
lines. Payload CRC/session/sequence checks and completion polling are unchanged.
Host evidence retains metadata and hashes for every tile, reconstructs each
full output independently of the guest, and compares its hash to the guest's
verified output. One raw request/reply is kept per tile shape. Audit CRC and
final zero-resource counters are rechecked by summarize_blur.py.

## CPU control correction after BLUR_GUEST1

The original -O1 float-conversion reference is not the final CPU comparator.
BLUR_GUEST1 verified every 64/256/512 output but stopped at 187.56 seconds on the
60-second no-progress rule. Its last marker was screen-size allocation/setup at
127.33 seconds; no screen-size blur reached the host. Native input remained R.
This run is retained as incomplete, not counted as a successful full sweep.

Inspection showed repeated half↔float conversions in the reference. A native
FP16 vector-FMA implementation matches its output exactly; host assembly
`BLUR_CONTRACT1/cpu-control.s` contains fmla.4h. On the 256² host control it took
81 us versus 212 us for the conversion reference. That is host evidence only.
Before the next guest trial, the CPU benchmark was switched to native FP16 FMA
and the blur helper was compiled with -O3 (frontend and executor remain -O1). The exact
shader, wire format and transport algorithms are unchanged. Explicit progress
markers every two screen CPU iterations prevent a healthy long batch from
looking like a stalled process; the 60-second no-progress and 300-second global
limits remain. This change tests a stronger CPU baseline, not a transport fix.
First-use and slow-frame reporting still includes every measured frame.

## Completed guest measurements

BLUR_GUEST2 and BLUR_GUEST3 both pass, using BUILD4 and INSTALL2, in
203.85 and 198.77 seconds including boot. Each starts a fresh process and a new
disposable disk child, waits for ten seconds of stable native presentation and
a fresh input ACK, then runs the workload. The actual launch.json (not the
inherited manifest's historical restore arguments) has no debugger, restore or
auxiliary NVMe drive. The final native lockscreen remains visible; input state R,
ACKs and presentation counters progress. The blur IOSurface remains offscreen.

Every run verifies all 68 full RGBA16F images bit-for-bit against the guest CPU
reference, their BGRA8 IOSurface hashes, and independently assembled host tile
hashes. Each executes 952 submissions / 1904 dispatches, with only 17 resource
creations, then zero live resources and zero resource bytes. Source and guest
disassembly are retained; the measured guest CPU actually executes fmla.4h.
This comparator is our optimized separable implementation with exact half-FMA
semantics, not a measurement of Apple's production software renderer. The guest
runs six vCPUs under TCG; native FP16 describes the guest instructions, not
native host CPU execution or HVF. These CPU/GPU ratios do not predict a future
HVF guest baseline. GPU tiling
adds halo work and serial submissions that the whole-image CPU implementation
avoids. Helper -O3 can also affect transport code: this is not an isolated
transport speedup comparison against the older -O1 luma results.

All following times are milliseconds. CPU and GPU totals include conversion and
copy into the same owned guest IOSurface, through unlock. Medians exclude frame
zero. Hashing, equality checks, and logging are outside the work timer, but are
included in observed batch throughput.

| Output | CPU median, run 2 / 3 | Driver→host GPU median, run 2 / 3 | CPU/GPU ratio, run 2 / 3 |
|---|---:|---:|---:|
| 64×64 | 3.90 / 4.10 | 13.80 / 5.69 | 0.28 / 0.72 |
| 256×256 | 74.71 / 60.20 | 30.77 / 29.29 | 2.43 / 2.05 |
| 512×512 | 308.68 / 226.72 | 115.48 / 116.14 | 2.67 / 1.95 |
| 1184×2560 | 2835.22 / 2859.61 | 1401.17 / 1470.62 | 2.02 / 1.94 |

| Output | GPU first frame, run 2 / 3 | GPU steady p95=max, run 2 / 3 | Steady frames >16.667 ms, run 2 / 3 | Observed GPU batch frames/s, run 2 / 3 |
|---|---:|---:|---:|---:|
| 64×64 | 64.78 / 21.62 | 37.03 / 7.95 | 4/16 / 0/16 | 51.51 / 136.59 |
| 256×256 | 33.30 / 30.65 | 41.16 / 44.68 | 16/16 / 16/16 | 27.17 / 28.47 |
| 512×512 | 860.41 / 106.63 | 204.84 / 169.00 | 16/16 / 16/16 | 5.34 / 7.15 |
| 1184×2560 | 1661.84 / 1189.98 | 2221.00 / 3450.16 | 16/16 / 16/16 | 0.61 / 0.54 |

With only sixteen steady samples, nearest-rank p95 is the maximum. Raw samples,
CPU first frames, CPU/GPU p95, and >33.333 ms counts remain in each
verification-final.json; no samples are discarded. The batch rate uses all
seventeen frames and their verification/logging, not reciprocal median latency.
First-frame timing begins after library/pipeline/resource creation and input
preparation. It therefore does not measure cold application startup. Individual
setup RPC host-service durations and audit events are retained, but no separate
end-to-end guest setup timer was added. The 860 ms first 512² GPU frame in run 2
and 3.45 s slow screen frame in run 3 remain in the evidence.

Each full run transfers 584,257,958 request bytes and 457,411,422 reply bytes
across 988 RPCs. In run 2, a screen frame's median aggregate RPC time is 785.65 ms,
including 103.75 ms of doorbell wait across fifty tiles. Guest BGRA8 conversion
and IOSurface delivery alone takes 243.79 ms median. These intervals overlap
(RPC includes doorbell), and medians should not be summed as a decomposition.
The measurements establish substantial work outside the host shader; they do
not individually attribute every residual millisecond to copies versus CRC,
Objective-C, CPU scheduling or the Python peer's evidence collection.

## Host retained-resource ceiling

BLUR_HOST_SWEEP2 runs the same unmodified shader as a whole image on Apple M5
Max, seventeen frames per mode and size. Upload-every-frame and resident-input
with readback verify every frame. Resident-no-readback uploads once, retains
input/intermediate/output textures, waits for actual command completion each
frame, and verifies the final pixels after the batch. Intermediate frames in
that third mode explicitly have verified=false; completion alone is not a
pixel oracle. All checked outputs match exactly. This repeated unchanged image
isolates resident processing capacity; it does not model changing UI inputs.

| Output | Upload+GPU+readback median | Resident input+GPU+readback median | Resident, no readback median | Resident GPU execution median |
|---|---:|---:|---:|---:|
| 64×64 | 0.172 | 0.176 | 0.168 | 0.023 |
| 256×256 | 0.328 | 0.280 | 0.181 | 0.029 |
| 512×512 | 1.192 | 0.739 | 0.249 | 0.046 |
| 1184×2560 | 8.750 | 7.490 | 1.012 | 0.287 |

Screen-sized resident-no-readback completes sixteen follow-on frames in
15.03 ms batch wall time, including per-frame host logging and excluding final
validation. That final readback takes another 6.331 ms. Ordinary screen-sized
per-frame readback costs 6.39–6.45 ms median. Per-process host library/pipeline/
texture/allocation setup takes 22.5–25.0 ms, separately logged in stderr. Raw
first-use and steady samples are preserved, including all mode ordering. Modes
run sequentially in one process per size, so thermal/scheduling/order effects
remain possible. BLUR_HOST_SWEEP1 is also retained; its resident mode still
read back every frame and is not the no-readback proof.

These are host-only costs. They exclude guest transport, resource sharing,
IOSurface mapping, conversion, scanout and display synchronization; they must
not be presented as achieved VM FPS or near-native Liquid Glass performance.

## Assessment and smallest next implementation

- **Proven within this workload:** exact-guest QuartzCore blur shader reuse,
  public Metal frontend encoding of the bounded two-pass imageblock contract,
  MMIO command/completion transport, exact pixels delivered to an owned guest
  IOSurface, resource reuse and retirement, and repeated larger-workload CPU
  crossover (1.94–2.67× at 256² and above).
- **Disproven within this implementation and measurements:** the current tiled
  upload/readback/conversion path providing interactive screen-sized blur.
  It takes 1.40–1.47 seconds median, with roughly 0.54–0.61 observed batch
  frames/s. The 64² offload is also slower than the guest CPU in both runs.
- **Untested:** full Liquid Glass composition, normal DCP scanout of this blur,
  system-wide Metal discovery/QuartzCore use of the plugin, general render-pass
  and shader contracts, direct shared image backing, cross-process fences,
  safe reuse under multiple outstanding frames, and live GPU checkpoint state.

Continue with a bounded reusable-image path. Texture *handles* are already
reused here; recreating fewer handles alone will not address the bottleneck.
First retain a full-image input, intermediate and output on the host and issue
one compact command for both passes. Prove reuse without per-frame bulk traffic.
Then convert the output to native BGRA on the host and deliver it once per frame
into owned shared output backing, avoiding guest half-float conversion and
fifty per-tile round trips. Repeat this same pixel/latency comparison after each
step. Keep the CPU fallback for workloads below the measured crossover.

Major unresolved contracts: a screen-sized RGBA16F image is 24.25 MB, exceeding
the current 16 MiB owned mapping and 1 MiB texture limit. Either bounded chunked
initial upload into host-resident allocations or a deliberately enlarged owned
mapping needs explicit bounds and lifetime accounting. Shared output needs a
verified allocation/IOSurface relationship, CPU/GPU visibility rules, completion
fences and generations, dirty-region ownership, and safe retirement after the
last consumer. Final IOSurface→DCP delivery needs an independent experiment;
shared RAM alone does not establish zero-copy presentation. Keep live GPU
migration rejected until resource drain/recreation is tested.

The evidence justifies that small resource experiment. It does not justify
promising native scrolling or committing to a complete driver yet. Stop that
next experiment on stale pixels, cross-frame contamination, bounds/sequence/
CRC failure, stalled completion, native display/input regression or failed
retirement; do not hide a failed sharing contract behind a readback fallback.

## Final reproduction and preservation

The stronger CPU build was installed through the owned guest installer after
checking every preimage. No baseline disk was mounted or modified. The complete
40-layer backing chain and seven boot inputs were rehashed after both final
runs: all 47 match (`blur-baseline-after.json`). SPTM/TXM and native SMC inputs
remain pinned. QEMU source stayed at bf4e43d; the already rebuilt MMIO_BOOT_BUILD4
executable, BootKC and DT were reused unchanged, so no new QEMU build was needed.

```sh
bash tools/gpu/build_driver.sh /tmp/dvm/BLUR_DRIVER_BUILD4 --mmio-blur
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/BLUR_DRIVER_BUILD3 --build /tmp/dvm/BLUR_DRIVER_BUILD4 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/BLUR_DRIVER_STAGE1/system.tc --out /tmp/dvm/BLUR_DRIVER_STAGE2
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/BLUR_DRIVER_INSTALL1/warm-manifest.json --stage /tmp/dvm/BLUR_DRIVER_STAGE2 --tag BLUR_DRIVER_INSTALL2
python3 tools/gpu/prepare_mmio_manifest.py /tmp/dvm/BLUR_DRIVER_INSTALL2/warm-manifest.json /tmp/dvm/MMIO_BOOT_BUILD4 /tmp/dvm/BLUR_DRIVER_INSTALL2/mmio-manifest.json
python3 tools/gpu/run_guest_load.py /tmp/dvm/BLUR_DRIVER_INSTALL2/mmio-manifest.json --tag BLUR_GUEST2 --seconds 300 --driver-mmio --driver-wait-display --driver-worker /tmp/dvm/BLUR_DRIVER_BUILD4/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
python3 tools/gpu/run_guest_load.py /tmp/dvm/BLUR_DRIVER_INSTALL2/mmio-manifest.json --tag BLUR_GUEST3 --seconds 300 --driver-mmio --driver-wait-display --driver-worker /tmp/dvm/BLUR_DRIVER_BUILD4/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
python3 tools/gpu/summarize_blur.py /tmp/dvm/BLUR_GUEST2 --output /tmp/dvm/BLUR_GUEST2/verification-final.json
python3 tools/gpu/summarize_blur.py /tmp/dvm/BLUR_GUEST3 --output /tmp/dvm/BLUR_GUEST3/verification-final.json
python3 tools/gpu/run_blur_host_sweep.py --out /tmp/dvm/BLUR_HOST_SWEEP2 --air /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
DVM_DRIVER_BUILD=/tmp/dvm/BLUR_DRIVER_BUILD4 python3 -m unittest discover -s tools/gpu -p 'test_*driver*.py' -v
bash tools/gpu/build_driver.sh /tmp/dvm/BLUR_COMPAT_BUILD1
DVM_DRIVER_BUILD=/tmp/dvm/BLUR_COMPAT_BUILD1 python3 -m unittest discover -s tools/gpu -p 'test_*driver*.py' -v
python3 -m unittest discover -s tools/tests -v
```

All paths are historical reproduction examples; choose fresh names to rerun.
Final checks: 40 driver tests, including actual Metal output and rejection before
resource mutation, pass against the measured build and a fresh default-mode
compatibility build; 79 project tests pass. Default-mode compatibility was built
and tested on the host, not rebooted in the guest this time. Shell syntax,
Python compilation and diff whitespace checks pass. The host-only no-readback
probe was added after BUILD4; it is separately compiled and source-pinned in
BLUR_HOST_SWEEP2, and does not change the measured guest executable.

Measured BUILD4 SHA256:

| Artifact | SHA256 |
|---|---|
| dvm-gpu-load | 3ee6c3e7627b66d74ca5c6278bc08198b6fe3ad593dc57088591019d6e2f6062 |
| driver_host | 0911fa9f9c228628a8579f78efb4671381e8ef87489e9c2005abff55c2136bf8 |
| DVMProxy | 538d47aa2d4e40222b4cb4d16dc027b43e9589e39100d9bddf99f9222375ca1b |

Durable archive:
`/Users/jdolbe1/dvm-artifacts/research/gpu-blur-feasibility-ios27-20260906/`.
It retains incomplete BLUR_GUEST1, both successful guest runs, both host sweeps,
static AIR disassembly and CPU control, build/install ledgers, test logs,
frame/audit evidence and source snapshots. Owned 16 MiB transport RAM and bounded
raw tile frames are included with --mmio-frames; full guest RAM, disks, Apple
libraries/shaders and executables are excluded. The archive index records source
paths and SHA256 for each retained file. The final verifier also checks exactly
17 resource creations; it was rerun against both completed runs after collection.
