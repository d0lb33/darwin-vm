# Process-local Metal frontend → host Metal, 24A5430a

This is the first compute frontend implementation on `codex/metal-driver-ios27`.
It is an explicitly loaded, signed guest bundle implementing a small subset of
public Metal selectors and structures. It is **not yet a system-discovered
Metal driver**, and SpringBoard, QuartzCore and Liquid Glass do not use it.
The retained software UI renderer is still the normal display path.

## Baseline and isolation

Project main `0d8da5c` and QEMU main `8b46eb6` were the starting points.
Project merge `65297df` retains the earlier GPU feasibility/surface work;
QEMU merge `0de52c4` retains the NS6 auxiliary namespace and its checkpoint
blocker. QEMU was rebuilt in the new worktree with
`--target-list=aarch64-softmmu --disable-pvg`, using `ninja -j 8`.
The immutable test executable has SHA-256
`baf4a15856731f26b14a6f9c6099a8383f3b343f17ff131a08ba538a93dc53d5`.

`prepare_driver_baseline.py` combines the reviewed v15 input disk manifest
(`/tmp/dvm/native-input-reviewed-v15/warm-manifest.json`) with the previously
validated native RTC BootKC/DT from `GPU_DEMO_QEMU1`. It verifies their pinned
inputs and identical SPTM/TXM, adds only NS6 to that DT, and preserves the v15
migrated disk chain. The original top parent is
`/private/tmp/dvm/INPUT_REVIEW_INSTALL3/disk.qcow2`, SHA-256
`caf8dd21d86a04f532915e6812fb8baa6d82fc2f053990887bcadb6bea8ed5c4`.
This is still iOS 27 24A5430a / iPhone17,3 / T8140, with SPTM/TXM.

Each install uses a new qcow2 child and a copied small restore ramdisk. Only
that ramdisk is host mounted through `safe_attach.sh`; System/Data are mounted
inside the owned restore VM. Each execution gets another disposable child.
No existing VM is stopped or repurposed. Installation adds only the helper,
its bundle, its trust-cache entries and its launch service to the disposable
lineage. The launchd `JetsamMemoryLimit` experiment affects only this helper
entry; live readback later showed that key did not raise its 6 MiB limit.

## Implemented contract

- `driver_guest.m`: `MTLDevice`, library/function, compute pipeline, texture,
  buffer, queue, command buffer and compute encoder objects, for the documented
  subset. These are NSObject implementations of public protocols, not Apple's
  private `_MTL*` base-class graph. Unknown methods are not implemented.
- `driver_probe.m`: loads the bundle explicitly with NSBundle/dlopen; obtains
  the device from `DVMCreateMetalDevice`; uses the guest's existing NS6 service
  and `AppleNVMeNamespaceUC` as transport. No new kernel extension, global
  accelerator registration, debugger RPC or guest-memory injection is used.
- `driver_peer.py` and `driver_host.m`: session-bound, CRC-checked, ordered
  framing to a persistent host process holding actual Metal resources. Handles
  have types, are not reused, and are retired after queued work. The worker
  executes Metal; it contains no CPU implementation of the result oracle.
- One asynchronous commit carries two compute encoders. Overlapping command
  buffers on a device are explicitly rejected in this subset. Inline bytes are
  captured at encoding; buffers are uploaded at commit; the second GPU pass
  reads the first pass's buffer without intermediate CPU readback. Final
  readback updates guest shadows before completion handlers are invoked.
- The public subset uses copied storage. It does not promise shared physical
  GPU/CPU memory, arbitrary kernels, heaps, render encoders, fences/events,
  IOSurface aliasing, global discovery or private Metal SPI compatibility.

The worker deliberately accepts only the two audited luma kernels with exact
uniforms, geometry, scratch sizes and resource footprints. This is an audited
execution envelope for a first milestone, not a general shader translator.
It limits frames to 2 MiB, resources to 128 handles / 32 MiB, and individual
transfer storage to 1 MiB. Arbitrary library upload is not exposed: the guest
hash identifies the previously extracted exact AIR cache on the host.

## Workload and evidence standards

AIR is the unmodified 2,705,796-byte slice from this guest's QuartzCore
`default.metallib`, SHA-256
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
The guest reads and hashes its own file; the host independently checks the
cache against that request. AIR target metadata says `air64_v29-apple-ios27.0.0`.

`compute_average_luma` (AIR offset 941671, length 5296) reduces a 64×48
RGBA16Float texture into six float4 partial sums. Dispatch is groups (1,6,1),
threads (8,8,1), with 1024 bytes of threadgroup scratch. `compute_sum_luma`
(offset 946967, length 4752) reduces those to one float4 with groups (1,1,1),
threads (8,1,1), and 128 bytes of scratch. The 20-byte uniform is
`0000000040003000010000000600000001000000`.
The parent inspected the complete LLVM IR to establish bounds and reduction
behavior; a private method list or reflection's minimum element size was not
used as proof of memory safety.

Eight inputs use recorded random nonces, exact half-representable values, and
independent CPU sums. Both intermediate (96 bytes) and final (16 bytes)
outputs are poisoned before submission and checked byte-for-byte. The caller
waits for a completion-handler semaphore *before* `waitUntilCompleted`, so
calling wait or polling status cannot be what drives execution. Objects are
reused across runs, then final host stats must show eight submissions and zero
live resources. Host readbacks must match the guest's printed result bits.

The boot gate requires a native DCP presentation and the existing input helper
in state R for ten seconds. The final peer also requires a fresh ACK and
restarts the interval if the helper PID/epoch changes. This does not establish a visible Home screen,
successful gestures, or native frame cadence. Reported commit/completion times
exclude texture upload and command encoding and are not end-to-end frame
latency. This tiny reduction is correctness evidence, not a CPU/GPU speedup
claim.

## Failed experiments retained

`METAL_DRIVER_RUN1` / BUILD4 reached display/input but made no RPC. An offline
kcdata scan of read-only RAM snapshots found PID 69 `dvm-gpu-load`, exception
11, code `0x6400000000010006`, subcode 0. Apple's published
[XNU resource definitions](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/osfmk/kern/exc_resource.h)
decode type 3 (memory), flavor 1 (high watermark), limit 6 MiB. This is evidence
of the helper's memory-limit exception, not GPU incompatibility. RUN2 with
mapped AIR loading and a helper-specific 64 MiB allowance passed NS6 open,
capacity, header and readiness checks. Those two changes were not separately
ablated, so the precise contribution of each is not isolated.

`METAL_DRIVER_RUN2` / BUILD5 then made no RPC after readiness at 139.341 s.
Two read-only process snapshots found identical saved PC `0x18f2b7ff8`, LR
`0x1b182e494`, and registers. Its return frame `DVMRunLuma+156` is the
`dispatch_data_create` call using the default copying destructor. This locates
an observed setup stall; it does not establish the kernel/QEMU cause. The no-copy variant keeps NSData alive in a destructor block and avoids this
redundant 2.7 MiB copy. RUN1/RUN2 include diagnostic pauses and are not latency samples.

RUN3 / BUILD7 passed transport initialization, but did not print READY after
host readiness at 140.398 s. A surviving PID 69 proc candidate had no threads;
three GiB of RAM yielded no matching helper crash record. The workload was
not reached, so RUN3 does not test whether eliminating the copy fixes RUN2.

The following build additionally uses `memorystatus_control` commands 8/7/8
to read, if necessary set, and verify a 64 MiB active/inactive limit on its own
PID before shader setup. The command layout comes from
[XNU's published header](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/kern_memorystatus.h),
with exact guest return codes/readback required. RUN4 directly observed
`before_active=6 before_inactive=6` despite the launchd plist setting of 64,
then verified both live limits as 64 after command 7. It never changes another
process or a global memory setting. Launchd configuration is not accepted as
proof that the live memory contract took effect.

A host negative test also exposed retained objects after a rejected concurrent
submission. Building Objective-C with `-fobjc-arc-exceptions` makes exception
unwinding release ARC objects; the rejection/retirement test passes with that
flag. Host tests additionally verify a malformed second encoder cannot execute
the valid first encoder, typed/stale handles, bounded frames/storage, upload
failure without NSError, completion without wait-driven progress, and zero
live resources after eight real Metal submissions.

## Remaining implementation gates

1. Automatic device discovery and the minimum private Metal interfaces needed
   by an actual QuartzCore consumer remain untested. Public protocol conformance
   alone is not proof of acceptance. An IOAccelerator personality or a narrow
   loader integration still needs an isolated experiment; IOGPUFamily
   inheritance is not established as mandatory by this work.
2. Render encoders, render targets, blending, samplers, function constants and
   material-specific glass resource bindings are not implemented. Full-screen
   bandwidth, frame scheduling and CPU TCG cost can dominate performance.
3. NS6 is a prototype transport. JSON/base64, full buffer upload/readback and
   serialized RPCs need replacement or batching before any near-native claim.
   Measure a complete frame, not just host GPU execution or commit latency.
4. Guest IOSurface/display integration was proven in earlier experiments. This
   frontend explicitly returns nil for IOSurface-backed textures because initial
   contents and cross-process backing coherence are not yet implemented.
5. Live host resources are outside VM migration state. QEMU refuses checkpoints
   while NS6 auxiliary storage is attached. No driver checkpoint restoration is
   claimed. A future design must quiesce submissions, retire callbacks and
   fences, serialize logical resources, create a new session and reconstruct
   pipelines/resources after restore, with stale handles rejected.

Adapted Apple PV, system-wide custom forwarding, and interception remain
untested end to end. The process-local compute route is a distinct, narrower
contract; it cannot by itself establish near-native Liquid Glass viability.

## Recorded runtime verdicts

| Run | Build / installed parent | Observed result | Scope |
| --- | --- | --- | --- |
| RUN1 | BUILD4 / INSTALL1 | No RPC; helper memory exception; 480 s bound | Failed startup; diagnostic pauses |
| RUN2 | BUILD5 / INSTALL2 | No RPC; same saved state in default AIR-copy path in two snapshots | Failed startup; stopped for no-copy test |
| RUN3 | BUILD7 / INSTALL3 | No RPC; zero-thread proc candidate after readiness | Failed startup; no matching helper crash in scanned 3 GiB |
| RUN4 | BUILD10 / INSTALL4 | **Pass:** 8 submissions, 16 GPU dispatches, 6 persistent resources, all outputs verified, 0 live resources afterward | Functional proof; includes a diagnostic pause, not an uninterrupted latency trial |
| RUN5 | BUILD10 / INSTALL4 | No READY marker after host gate at 125.250 s; no RPC before 480 s bound | Repeatability failure; scanned first 2 GiB lacked a helper proc, not proof of absence from all RAM |
| RUN6 | BUILD12 / INSTALL5 | No READY marker after host gate at 170.222 s; no RPC before 480 s bound | No diagnostic pauses; startup failed before GPU or CPU timing |

RUN4 loaded the signed bundle, created its MTLDevice protocol object, executed
both kernels on the host Apple M5 Max (macOS 27.0 build 26A5421a), and compared
all intermediate/final results in the guest. Readback values also matched the
actual host GPU buffers. It proves this process-local compute contract, not
system Metal registration or UI acceleration.

RUN4 command completion ranged from 786,851 to 6,961,942 µs. Host GPU time
ranged 14–862 µs and **all 62 host request service intervals totaled 73,507 µs**.
That locates most elapsed cost outside the host worker's measured service;
it does not isolate guest scheduling, codec, user-client I/O and polling from
one another. The run was paused for diagnostics and the original completion
metric excludes texture upload/encoding, so these are diagnostic observations.
Do not interpret them as frame time or a CPU speedup.

The final workload additionally measures `work_us` from texture upload through
callback/readback completion and `cpu_reference_us` for input generation,
half conversion and independent CPU sums. The latter does **more** than CPU
reduction alone; it is a conservative CPU comparison, not a tuned software
renderer. `analyze_driver.py` preserves samples, distributions, host service,
source hashes and whether diagnostic snapshots were taken.

## Reproduction

Run from the isolated project worktree. Use new output paths/tags on every
invocation; none of these commands edits the migrated parent.

```sh
bash tools/gpu/build_driver.sh /tmp/dvm/METAL_DRIVER_BUILD12
DVM_DRIVER_BUILD=/tmp/dvm/METAL_DRIVER_BUILD12 python3 -m unittest discover -s tools/gpu -p 'test_driver_*.py' -v
python3 -m unittest discover -s tools/tests -v
bash -n tools/gpu/build_driver.sh tools/gpu/install_guest_load.sh tools/probe.sh tools/re/setup_gate_probe.sh tools/re/setup_gate_sweep.sh

python3 tools/gpu/prepare_driver_baseline.py \
  /tmp/dvm/native-input-reviewed-v15/warm-manifest.json \
  /tmp/dvm/GPU_DEMO_QEMU1/native-manifest.json \
  qemu-sptm/build/qemu-system-aarch64 /tmp/dvm/METAL_DRIVER_BASE1
python3 tools/gpu/prepare_guest_load.py \
  --build /tmp/dvm/METAL_DRIVER_BUILD12 \
  --cache /tmp/dvm/native-services6/launchd.plist \
  --system-tc /tmp/dvm/native-input-reviewed-v15/system.tc \
  --output /tmp/dvm/METAL_DRIVER_STAGE7 --interactive-load --memory-limit-mb 64
python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/METAL_DRIVER_BASE1/warm-manifest.json \
  --stage /tmp/dvm/METAL_DRIVER_STAGE7 --tag METAL_DRIVER_INSTALL5
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/METAL_DRIVER_INSTALL5/warm-manifest.json \
  --tag METAL_DRIVER_RUN6 --seconds 480 \
  --driver-worker /tmp/dvm/METAL_DRIVER_BUILD12/driver_host \
  --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib --aux-poll-ms 1
python3 tools/gpu/analyze_driver.py /tmp/dvm/METAL_DRIVER_RUN6
```

The host worker rejects an invalid command before committing any part of its
batch. Guest transport waits at most 15 seconds between completed namespace
calls; the helper has a 420-second readiness bound and a 30-second callback
bound. A blocked kernel call is bounded by the runner's 480-second wall-clock
limit. Success requires every output, actual host GPU completion and final
zero-resource stats; fallback, missing markers and partial execution fail.
These are correctness stop conditions. Do not proceed to system-wide UI
integration while startup is unreliable or the full workload is slower than
its conservative CPU reference. The smallest next experiment is stage timing
and scheduling/transport isolation on this existing two-pass workload before
adding more shader or rendering interfaces.


The final baseline verification rehashed the 26-entry migrated parent chain
and retained the original top-parent hash recorded above. The final host check
set is 11 driver/protocol/readiness tests plus 74 existing regressions, with
shell syntax checks and strict guest import/signature verification. Build
provenance is carried through staging into the installed warm manifest. Runner
source snapshots come from the build archive, not later working-tree edits.

## Startup investigation stop and SMC synchronization

RUN6 deferred reading the fat shader library until host readiness and released
the fat data immediately after extracting AIR. It still failed before
acknowledging readiness. The guest CPU/work timing additions therefore remain
unexecuted in this run, although the host tests passed. The terminal failure
contract is a host-ready NS6 header that the helper does not acknowledge; the
cause is unresolved. Repeated cold boots without further isolation are stopped.

An offline RUN5 kernel stackshot contains the helper's matching Mach-O UUID,
a 64 MiB current memory limit, and a user frame returning from the readiness
loop's usleep. It describes an earlier captured instant, not the terminal
state, and does not establish a scheduling or timer defect. A proposed QoS
change was neither built nor run; its patch is preserved separately at
`/tmp/dvm/METAL_DRIVER_SMC_SYNC/untested-scheduling.patch`. The checked-in
implementation retains BUILD12 behavior.

The next baseline change is synchronization with the newly merged SMC support
on project and QEMU main, followed by an owned QEMU rebuild. All runtime results
above predate that synchronization; they are not evidence for an SMC-enabled
baseline.

## SMC main integration (2026-09-06)

Fetched both remotes, then merged the newer **local** main branches: project
`533f7e7` into `ba636a9`, QEMU `2ee8f19` into `96fac9e`. The only project
merge conflict was the submodule pointer, resolved to the QEMU merge containing
both SMC and NS6 transport. Original main checkouts were not modified.

The owned QEMU build completed with
`ninja -j 8 qemu-system-aarch64 qemu-img`; executable SHA-256
`577dc57e37246af6bc2bfdd0abafcf78a9000e9c3eb85c9a41c08bf9f4f6f4f5`.
All 79 current project regressions and 11 driver tests passed, along with shell
syntax checks. Driver tests reused the unchanged BUILD12 binaries; this is host
validation, not a new guest GPU execution. Build and test logs are under
`/tmp/dvm/METAL_DRIVER_SMC_SYNC`.

The first restore smoke test accidentally enabled ANS/SEP/DCP in addition to
the default restore SMC/SPMI features. It stopped on the first-contract failure
`[SPTM] VIOLATION_NVME_ILLEGAL_NVMe_QUEUE_ENTRIES_MISMATCH: validate_nvme_queue_entries(nvme_validation.h:182) - queue_entries(0x41)`.
SMC INITIALIZE and native RTC startup had occurred, but no shell was reached.
It is not a valid default-restore regression test. The corrected test uses
exactly run.sh's SMC/SPMI restore tree options and a fresh tag.

The durable native-SMC system package traces through BATT_NB3 and
CLOCK_SOFTWARE_INSTALL1, while this driver's reviewed input parent additionally
contains INPUT_REVIEW_INSTALL3 lineage. Do not silently replace that parent or
restore an old checkpoint against a changed device configuration. The next
system test needs a guarded removal of the old battery publisher/restoration
of original powerd on a disposable child of the retained input lineage, or an
explicitly verified equivalent migration. SMC has not yet been tested as a
remedy for the helper's readiness failure.

Corrected smoke test `METAL_SMC_RESTORE2`: **xnu panics: 0; reached shell:
yes**. SMC INITIALIZE appears in stderr line 39, AppleSMCKeysEndpoint in
serial line 131, and AppleDialogSPMIPMURTC started in serial line 150. The
45-second bounded probe shut down its owned VM. No system disk was attached.
This validates merged restore startup only; it does not establish system UI
or GPU readiness on SMC.

Durable records (including failed runs):
`/Users/jdolbe1/dvm-artifacts/research/gpu-metal-driver-ios27-20260906/`.
The archive index records each source path and SHA-256; disks, RAM, binaries
and Apple shader libraries remain excluded.
