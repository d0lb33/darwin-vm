# Client storage, image scenes and indexed dependencies

2026-09-06–07, continuing `gpu-quartzcore-sequences-ios27.md`. Exact guest:
iOS 27 **24A5430a**, iPhone17,3/T8140, original SPTM/TXM, native SMC,
migrated disk lineage. Isolated worktree `codex/metal-driver-ios27` and
disposable disk child; no baseline writes. No debugger or NVMe transport
assists these GPU jobs. Work was solo.

## Target and coverage

The target is the traditional `MTLDevice` / `MTLCommandBuffer` /
`MTLRenderCommandEncoder` API available to this exact iOS 27 QuartzCore.
Compiling against SDK 27 is **not** Metal 4 conformance. MTL4 command APIs,
complete GPU families, arbitrary Metal applications and a guest source-shader
compiler are not advertised. Profile v5 is `bounded-client-storage-v5`.

| Feature / path | Implemented | Evidence / remaining limit |
|---|---|---|
| Runtime revisions, fixed supervisor, fresh processes | Yes | Exact guest: all scene revisions in one disk boot |
| Half-opacity compositing | Yes | Native host, forwarded host, exact guest; independent oracle permits only one RGB quantization code of tolerance |
| Axis-aligned clipping and scale transform | Yes | Native/forwarded host and exact guest; exact pixels |
| Nearest-filtered two-color CGImage layer | Yes | Exact guest 4/512/2048-frame checks; all pixels at frames 0, 1, final |
| Arbitrary rotations, curved/stencil masks, backdrop filters | Not established | No new acceptance evidence |
| Client-owned CPU buffer storage | Yes, 16 KiB pointer/length alignment, ≤1 MiB | Host test proves identical `contents` pointer, GPU writeback, linear view and exactly-once deallocator |
| Private read-only linear texture constructor | Yes, delegates validated 2D view | Host test checks backing ownership, image pitch and CPU-to-texture contents |
| Bufferless client texture capability | Explicitly false | Negotiated profile; no such allocation API supported |
| Device-wide read-only linear alignment | Yes | Maximum of the negotiated, supported-format power-of-two alignments; not a host GPU-family claim |
| Writable/index aliases and generated indices | Yes, serialized dependent draws | Host GPU test proves generated indices, stable index snapshot, pixel correctness and rejection of invalid generated indices before dependent draw |
| Invalid later static command | Rejected before any GPU execution | Host test with a bad later writable binding |
| 22 scalar capability queries | Yes | Exact guest job 1788756752501613 plus host contract tests |
| Screen-sized shared-RAM render target | Host allocation and backend lease tests pass | See `gpu-shared-render-target-ios27.md`; not yet connected to guest IOSurface/CARenderer |
| Generic IOSurface import / system compositor discovery | Not implemented | Next integration dependency; software compositor retained |
| Long-run stability and 60 Hz pacing | Not passed as a reliability claim | One SIGABRT in four 2,048-frame attempts; three instrumented repetitions passed, all had deadline misses |

The scene switches exist only in test code. Production driver code has no
scene or shader-name branches for these render operations. Existing compute
whitelists remain a separate, narrower contract; these rendering results do
not establish general compute support.

## Exact static evidence and general fixes

The initial image rehearsal stopped at
`DVMDevice supportsBufferlessClientStorageTexture`. Exact cache metadata
declares this as a BOOL property; the retained
`CA_EXACT_METAL_OBJC.txt` under `research/gpu-quartzcore-render-ios27` records
it at lines 1588/2259. The allocation batch was checked against exact
QuartzCore `MetalContext::update_image`, **0x184506574**:

* **0x184506770** calls `deviceLinearReadOnlyTextureAlignmentBytes` and
  **0x184506774..0x18450678c** checks data-pointer and row-pitch divisibility.
* **0x184506798** calls `newTextureWithDescriptor:` on the copied path.
* **0x1845068a4 / 0x184506904** call full region/slice/image-pitch uploads.
* **0x184506a24** calls `newBufferWithBytesNoCopy:length:options:deallocator:`;
  **0x184506aa0** calls the private linear-texture constructor.

Disassembly and resolved selector stubs are in `CA_IMAGE_STATIC1`. Related
mipmap and texture-view calls also exist in that function; those paths remain
unsupported rather than silently accepted.

An initial implementation using `NSMutableData bytesNoCopy` failed pointer
identity in `CA_CLIENT_TEST1/test2.stderr`: the returned mutable bytes had a
different address. The final buffer holds the caller's pointer explicitly,
uploads its current contents before submission, publishes GPU writeback before
completion, and invokes its deallocator only after the final reference.
This is guest CPU aliasing with transport copies, not direct host mapping.
Rejected allocation requests never take ownership. Linear views retain their
buffer; command buffers retain the referenced resources.

The next rehearsal failed because an index buffer also appeared in a writable
vertex binding. The backend now preflights the full command batch, then splits
only batches with that dependency into completed draw segments. It preserves
the single-sample color target with store/load, restores encoded state, reads
GPU-produced index values at the completed boundary, checks index/vertex
bounds and uses a native immutable index snapshot for the next draw. A draw's
own writable aliases cannot race the index fetch. Shader data races on other
addresses are not made valid by this implementation.

This split is restricted to our existing single-color, no-depth/no-MSAA/no-tile
contract. Logical pass counts remain logical; `nativeCommandBuffers` records
the split count. If generated indices are invalid, earlier GPU work may
already have completed, but the dependent draw does not execute and the guest
command reports an error. Guest upload caches are invalidated on that error.
The host tests cover both that partial-execution failure and preflight rejection
of a statically invalid later binding before any GPU work.

## Runtime scene results

Durable evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-client-storage-scenes-20260906`.
`CA_SCENE_GUEST1` booted from a disposable disk child using the previous
validated QEMU `fc49f9c`; readiness took 99.258 s. Original SPTM/TXM/native
SMC and the GPU package's opt-in kernel/SMP adapter were retained.

| Job | Guest PID | Scene / frames | Result |
|---|---:|---|---|
| 1788756292005992 | 290 | Installed red control | Pass |
| 1788756292064231 | 321 | Half opacity / 4 | Pass |
| 1788756292121528 | 326 | Clip + scale / 4 | Pass |
| 1788756292178708 | 327 | Image / 4 | Pass |
| 1788756451467247 | 375 | Image / 512, 60 Hz | Pixels/completion/retirement pass; pacing misses |
| 1788756524671305 | 450 | Image / 2048 | **Failed: SIGABRT after 1210 completed frames** |
| 1788756708783371 | 618 | Image / 2048, diagnostics | Pass |
| 1788756752501613 | 657 | 22 queries + red control | Pass |
| 1788756794122815 | 691 | Image / 2048, diagnostics | Pass |
| 1788756794179800 | 712 | Image / 2048, diagnostics | Pass |
| 1788757108628669 | 859 | Final installed red control | Pass |

The runner was explicitly stopped and its QEMU process reaped. Ten of eleven
jobs passed independent verification; the required failed job remains failed.
The continuation package is
`/Users/jdolbe1/dvm-artifacts/gpu-quartzcore-v5-ios27`: its `control.json`
retains the validated boot inputs and QEMU, `driver-build` contains the new
backend and fixed helper, and `guest-validated-linked` preserves the exact
instrumented bundle used for the three successful 2048-frame jobs. Its bundle
executable SHA-256 is
`6d18df12a400922909586c42b564286b238c3a3b191d2d3e8a334a8384b00532`.

All successful scene jobs require actual native Metal status 4, agreement
between guest/host draw counts, CRC-checked guest pixel witnesses, independent
host pixel verification and zero final resources. Reading only three images
does not prove every unobserved intermediate pixel; every frame's GPU
completion and command order are recorded.

Native host image rendering passed. Forwarded **macOS** QuartzCore with
explicit iOS AIR substitution produced 1,024 transparent pixels where image
content was expected, even after successful GPU execution. That mismatch is
preserved in `CA_IMAGE_HOST3`; its cause is not established. The unmodified
exact-guest image commands passed, and all 65 captured requests replayed on a
fresh host worker in 0.111 s. Do not generalize a host rehearsal failure to
the guest, or report the rehearsal itself as passed.

## Pacing, memory and the unresolved abort

Image workload, guest wall time from layer update through driver completion;
setup and first use are separate. Final pixel checks are outside timing.

| Frames / job suffix | Setup / first ms | Steady median / p95 / max ms | Work >16.67 ms | Late deadlines |
|---|---|---|---|---|
| 512 / 467247 | 196.625 / 1826.566 | 8.878 / 17.298 / 621.460 | 28/510 | 151/510 |
| 2048 / 783371 | 163.444 / 646.748 | 8.366 / 11.636 / 165.674 | 31/2046 | 124/2046 |
| 2048 / 122815 | 96.653 / 621.711 | 6.930 / 10.106 / 45.034 | 8/2046 | 50/2046 |
| 2048 / 179800 | 106.856 / 369.737 | 6.828 / 8.463 / 108.997 | 7/2046 | 36/2046 |

Each successful 2048-frame paced interval was about 34.094 s. These are
offscreen 64×64 workloads and do not establish smooth screen-sized display.
Input/display recovery activity occurred near the last repetition, so its
timing is not an otherwise-idle-host benchmark.

The failed job's last host record is a successful `stats` response after
1,210 passes / 3,803 draws with 13 objects and 540,680 resource bytes. The
guest exited with signal 6 at 22.108 s, without a captured driver error.
The next package added an uncaught-exception reporter and a test-only SIGABRT
backtrace handler; three repetitions passed without invoking it. **This does
not fix or explain the abort.** The entire runner experiment retains failure
status because that required job failed. Do not relabel it to make the overall
run green. Further failures should use the preserved diagnostics or crash
report, not another uninstrumented boot.

Guest `TASK_VM_INFO` before/after the first successful 2048-frame scene:
resident 5,816,320→35,782,656 bytes; physical footprint
4,359,600→6,407,720 bytes. The two repetitions ended at similar footprints
(6,309,416 / 6,407,720 bytes); these are snapshots, not a leak proof.
The host worker's sampled RSS peaked at 19,968 KiB in the instrumented run.
`host-memory.json` records the sample interval and PID. The earlier failed
run's single sample was taken **after** completion and is excluded as a
running-memory measurement. Logical host resource bytes exclude the frontend's
CPU shadows and do not equal total process/GPU memory.

## Presentation dependency and next implementation

`CA_LINEAR_TARGET1/probe.stderr` proves a host Metal buffer-backed 2D BGRA
render target at **1179×2556, stride 4864, allocation 12,435,456 bytes**.
A native render clear completed with status 4; all **3,013,524** pixels in
the original aligned pointer were correct. This is an allocation/alias test,
not a guest shader or QuartzCore result. It disproves the assumption that this
host necessarily needs a copy from a separate render texture into shared RAM.

The next implementation should bind this allocation to the existing owned
guest pool/IOSurface and retain it through DCP completion. Unresolved contracts
are obtaining that owned mapping from the transport in the normal driver,
initial CPU contents and GPU-write visibility, surface/descriptor validation,
and keeping GPU completion separate from display retirement. Arbitrary guest
IOSurface imports, other processes' surfaces and global device registration
remain outside the proven path.

Two passive display checks timed out while the guest display was off. The
`activeWake` check was started **before** sending Home: it passed fresh native
scanout, native D594 completion and fresh input ACKs. `active-wake.json`
also records changed screenshot hashes and no dispatch failures. This is
preservation of the software display/input path, not GPU system composition.

## Reproduction and checks

```sh
bash tools/gpu/build_host_driver_tests.sh NEW_HOST_BUILD
xcrun clang -fobjc-arc -O1 tools/gpu/test_linear_render_target.m -framework Metal -framework Foundation -o NEW_HOST_BUILD/test_linear_render_target
NEW_HOST_BUILD/test_linear_render_target 1179 2556 4864
DVM_DRIVER_BUILD=NEW_HOST_BUILD PYTHONPATH=tools/gpu python3 -m unittest tools.gpu.test_driver_host tools.gpu.test_consumer_verify tools.gpu.test_runner_transport -v
python3 tools/gpu/build_consumer_package.py BASE_BUILD NEW_BUILD --frames 2048 --scene 3 --hz 60
python3 tools/gpu/sign_linked_revision.py NEW_BUILD/DVMProxy.bundle NEW_LINKED --parent BASE_BUILD/dvm-gpu-load
python3 tools/gpu/runner_control.py TRIAL --bundle NEW_LINKED/DVMProxy.bundle --mode data --development --test package --frames 2048 --scene 3 --worker NEW_BUILD/driver_host
python3 tools/gpu/observe_runner_memory.py TRIAL JOB
python3 tools/gpu/verify_runner_job.py TRIAL/runner-jobs/JOB
```

Scene IDs: 0 opaque moving layers, 1 half-opacity, 2 clipped/scaled, 3 image.
All 35 targeted regressions passed. The final writeback test additionally
checks the bad-later-binding preflight case. Link-only framework/Objective-C
stub additions are checked against exact-guest exports before staging.
