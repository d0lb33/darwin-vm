# Process-local Metal frontend → host Metal, 24A5430a

This is the first compute frontend implementation on `codex/metal-driver-ios27`.
It is an explicitly loaded, signed guest bundle implementing a small subset of
public Metal selectors and structures. It is **not yet a system-discovered
Metal driver**, and SpringBoard, QuartzCore and Liquid Glass do not use it.
The retained software UI renderer is still the normal display path.

Latest result: native SMC and original powerd remain integrated on the retained
lineage. Boot-time initialization now depends on a host Metal bootstrap handshake,
not guest display readiness. The runner checks native display/input separately
after the GPU workload. See “Boot-time startup dependency” below for cold-boot
validation. Global Metal discovery and Liquid Glass acceleration remain unfinished.
Historical commands that used the former display gate now need
`--driver-wait-display`; `--driver-late-launch` already implies that legacy mode.

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

## Retained-lineage SMC migration and readiness isolation

`METAL_DRIVER_SMC_INSTALL1` ran the existing native-battery guarded installer
on a fresh child of `METAL_DRIVER_INSTALL5`. It verified the guarded powerd
and BUILD12 launchd cache, restored original powerd
(`31254642770ac63ce22a55a5717246cf2289297f478f7ab753d15b6f88436268`),
and removed only the virtual publisher job/files. `finalize_driver_smc.py`
verified the original parent chain, all pinned inputs, successful install
marker, sealed child and exact launchd-job delta. The resulting top child
hash is `4a0b85577bf3ab39500db100e7a601c8c047a0318ce6fc35362c66e1ace71058`.
The native SMC/SPMI system tree from the durable default is extended only
with NS6; original SPTM/TXM are hash-equal to the retained baseline.

`METAL_DRIVER_SMC_RUN1` used unchanged BUILD12 guest binaries. Native IOPS
reported an internal battery, Current Capacity 80, Max Capacity 100,
Is Charging 1, Is Present 1 and AC Power (serial lines 752–790). The captured
lockscreen is legible with the green charging icon; native input pings continue.
The host released readiness at peer elapsed 120.165 s. No guest READY followed,
so the new explicit 30-second acknowledgement deadline stopped the run.
SMC alone did not resolve this observed startup failure.

BUILD13 adds a posix_spawn supervisor with waitpid exit/signal reporting,
interactive scheduling matching tools/input/dvm_hid.c, and readiness read/sleep
traces. Both scheduling calls returned zero in parent PID68 and child PID115
in `METAL_DRIVER_SMC_RUN2`. Both verified a 64 MiB live limit; the spawned
child initially had 3376 MiB, unlike the launchd parent's 6 MiB.
All 43 readiness reads returned successfully. The last marker at host 25.196 s
was `POLL n=43 phase=sleep-enter t=23.029463`; no sleep-return or child-exit
marker followed while native input pings continued. The run was interrupted
for this explicit no-progress condition (diagnostic-stop.json), not counted
as a GPU or latency pass. This narrows the observed boundary to sleep or task
scheduling/lifetime; it does not prove the kernel timer is defective.

BUILD14 keeps the supervisor and scheduling but replaces the readiness-loop
usleep with sched_yield after each synchronous namespace read. Read traces
are sampled after the first eight iterations to bound logging overhead.
This is a diagnostic wait policy with potentially material CPU cost, not an
accepted production idle strategy. Transport completion polling and the Metal
completion callback contract remain unchanged. `prepare_driver_update.py`
stages driver-only updates, checks every old binary and cache preimage before
any writes, and requires the old battery publisher to be absent.

Reproduction inputs and logs are under `/tmp/dvm/METAL_DRIVER_SMC_BASE1/`.
Build/install mapping: BUILD13 → STAGE8 → INSTALL6 → SMC_RUN2;
BUILD14 → STAGE9 → INSTALL7 → SMC_RUN3. Both builds passed strict imports,
signatures and all 11 driver host tests; the 79 project regressions passed.

SMC_RUN3 / BUILD14 **passed** eight two-pass submissions with no debugger, RAM
restore or diagnostic pauses. It verified every guest intermediate/final value,
zero host resources and supervisor `exit=0 signal=0`. Work including upload,
encoding, completion and readback was 39,093–73,299 µs; the CPU reference was
520–1,148 µs. This tiny reduction is slower through the driver. The unchanged
SMC_RUN4 repeat stopped progressing after the 2,048-read sample and failed
the 30-second host-readiness acknowledgement deadline. Therefore removing
usleep is **not a repeatable startup fix**, nor proof of a timer defect.

BUILD15 batches dirty texture uploads, buffer snapshots, encoded commands and
requested buffer readbacks into one submission RPC. The host validates every
transfer and dispatch before mutating storage or committing GPU work; malformed
second commands/uploads/readbacks leave the first upload unapplied. Returned
CPU shadows are published only after all readback entries validate. One in-flight
command buffer remains the supported limit. Standalone texture reads flush a
pending CPU upload first. Real host execution and negative atomicity tests pass.

BUILD16 / INSTALL8 / SMC_RUN5 added live memory-priority queries and an explicit
priority-80 control. Parent PID68 moved from priority10/state0x98 to 80/0x98;
child PID115 moved from 180/0x80 to 80/0x80. The helper again stopped after
the 2,048-read sample and failed readiness; no GPU timing was produced. CPU
scheduling settings, memory-band priority and activity tracking are separate
contracts. The control did not establish a cure.

BUILD17 removes the priority override and holds an explicit XPC transaction
for each process's work. SMC_RUN6 observed the parent change from priority10,
state0x98 to priority40, state0xb8, including the dirty/activity flag. The child
retained priority180/state0x80. The activity contract worked, but the readiness
loop again stopped after the 2,048-read sample. This too failed readiness and
did not execute the new batched GPU path in the guest.

Source contracts: [Apple xpc_transaction_begin](https://developer.apple.com/documentation/xpc/xpc_transaction_begin%28%29),
[XNU memorystatus definitions](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/kern_memorystatus.h),
and [one-PID priority queries](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/kern/kern_memorystatus.c).
Runtime flags above are observed; attributing the stall to idle exit, jetsam,
a blocked I/O call or kernel scheduling remains unproven.

`--driver-failure-snapshot` now captures all 12 GiB under one pause on failure
and inspects both driver processes plus control processes before teardown.
Its diagnostic-only readiness progress bound is 35 seconds, allowing sampled
read traces but stopping a stalled loop before the normal display deadline.
No guest memory, registers or call state are modified.


## Native-SMC failure snapshots and batched driver status

BUILD17 / SMC_DIAG1 captured both live helper processes in a complete paused
12 GiB image. The worker's saved user PC resolves by an exact 64-byte shared
cache match (slide 0x16de0000) to `libsystem_kernel:_swtch_pri+8`; its caller is
`libsystem_pthread:_cthread_yield+36`, and the helper return site follows
`_sched_yield`. This establishes the saved call boundary, not a defective timer
or dead worker. BUILD18 removed that explicit yield. SMC_RUN7 still failed
before GPU readiness, so this was not a fix.

SMC_DIAG2 keeps BUILD18 and adds only a bounded QEMU ANS trace (limit 65,536;
default remains eight). It records 64,926 occurrences each of submit, backend
entry/return, DMA, CQE, IRQ and queue-head acknowledgement; the trace limit was
not exhausted. The last request (CID38) returned zero, DMA/CQE writes succeeded,
and the guest moved its completion head from seven to eight. This excludes an
unpublished completion for that recorded request, not every possible transport
bug.

The paused CPU0 registers identify the worker's actual kernel stack:
SP `0xffffffe9c1bb7300`, FP `0xffffffe9c1bb7330`, PC `0xfffffff02abec81c`.
Walking from that live FP yields 17 return addresses through the namespace
memory-descriptor preparation path and `_Xio_connect_method`. With the project
kernel slide 0x20000000, the live instruction is a load in a time/accounting
sequence called from memory-pressure accounting, not an ANS wait instruction.
The worker's raw scheduler state is 4 and wait_event is zero. One CPU sample
cannot establish a persistent spin, scheduler starvation or deadlock. The
namespace user call had not returned to the helper's sampled logging boundary.

`analyze_driver_failure.py` reproduces the live frame walk and shared-cache
slide offline from the frozen image. It also recovers the actual proc names:
the first stack-capture version accidentally used a stack filename as the
next process's report label. The raw proc confirms PID68 is the supervisor;
PID115 is the worker. The collector variable reuse is fixed. Original reports
are retained, with corrections in the derived analysis, not rewritten.

BUILD18's batched protocol passed all 11 real-host/negative driver tests, but
these failed early-launch runs did **not** execute it in the guest. They are
not latency measurements. SMC_RUN3 remains the successful unbatched native-SMC
run; its 39–73 ms tiny workload was slower than its 0.52–1.15 ms CPU reference.

A final isolated startup control, STAGE13 / INSTALL11, changes only the driver
job to RunAtLoad=false, StartInterval=180, LaunchOnlyOnce=true. Guest binaries
remain BUILD18. The host still requires native display plus stable fresh HID
acknowledgements, and has a separate bounded activation deadline. This removes
the long early-boot polling interval from the experiment; it is not an approved
production activation mechanism or a relaxation of the GPU output oracle.

Reproduction for this control:

```sh
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/METAL_DRIVER_BUILD18 --build /tmp/dvm/METAL_DRIVER_BUILD18 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/METAL_DRIVER_STAGE12/system.tc --out /tmp/dvm/METAL_DRIVER_STAGE13 --start-interval 180
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/METAL_DRIVER_INSTALL10/warm-manifest.json --stage /tmp/dvm/METAL_DRIVER_STAGE13 --tag METAL_DRIVER_INSTALL11
python3 tools/gpu/run_guest_load.py /tmp/dvm/METAL_DRIVER_INSTALL11/warm-manifest.json --tag METAL_DRIVER_SMC_LATE1 --seconds 300 --driver-worker /tmp/dvm/METAL_DRIVER_BUILD18/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib --aux-poll-ms 1 --driver-late-launch
python3 tools/gpu/analyze_driver_failure.py /tmp/dvm/METAL_DRIVER_SMC_DIAG2 --cache-dir /Users/jdolbe1/dvm-artifacts/extract/dyld
```

Artifacts under `/tmp/dvm/METAL_DRIVER_SMC_BASE1` include exact build/install
logs; each trial records launch.json, result.json, serial, worker and device
logs. Full RAM remains in each diagnostic run's failure-snapshot/ram directory.
All installers verify the complete parent chain again after shutting down their
owned VM. Native SMC, original powerd, SPTM/TXM, input job and software renderer
remain on the retained migrated lineage.


SMC_LATE1 **passed** with BUILD18: native readiness at 109.601 s, worker READY
at 203.015 s, eight exact guest-output checks and clean supervisor exit by
203.482 s. The first poll read ready=1. There was no RAM restore, debugger or
pause during execution. The final screenshot was inspected: the software
lockscreen remains legible with the native charging icon. This screenshot is
not a GPU-rendered lockscreen.

Batched work including upload/encoding/completion/readback was
9.282–27.278 ms
(median 11.451 ms), with a CPU-reference
median of 0.543 ms. All eight
GPU-path timings were slower than the tiny CPU reference. Seven fit 16.667 ms;
this is not a UI-frame or scrolling result. Launch timing differs from the
unbatched comparison, so the difference is not a controlled batching speedup.
The current path uses 21 RPCs including creation/retirement, of which eight are
batched submissions; persistent host resources retire to zero.


The next implementation dependency is a render-target/encoder path through this
same guest frontend, with explicit IOSurface ownership and completion handling.
Global Metal discovery and the private interfaces exercised by QuartzCore are
still unresolved; this implementation does not make the lockscreen or Liquid
Glass use the host GPU. The disk-backed polling transport, CPU shadows and
single in-flight buffer are experimental limits. A shared-memory command queue
with completion notification, resource lifetime rules and reset handling remains
necessary to evaluate sustained UI workloads. Existing NS6 checkpoint blocking
remains in force; none of these cold-boot successes establishes migration of
live host Metal objects. The software display stays available.


SMC_LATE2, the unchanged BUILD18 / INSTALL11 repeat, **passed** with eight
verified submissions and zero live host resources, finishing at 200.162 s.
End-to-end work was 8.613–28.870 ms, median 11.535 ms; CPU-reference median
was 0.523 ms. Across both late-launch trials all 16 outputs and resource
retirements passed; 14/16 tiny-work timings fit 16.667 ms, and all 16 were slower
than their CPU reference. Two successes establish this bounded configuration,
not arbitrary-start reliability or a near-native UI performance claim.

Final checks: 79 project regression tests and all 11 driver tests passed;
Python compilation, shell syntax and git diff checks passed. QEMU trace change
is commit `eb8b65b` atop the native-SMC QEMU merge. It does not change device
completion behavior. The successful late trials used the rebuilt immutable
native-SMC binary without the longer diagnostic trace; exact binary hashes
are recorded in final-binary-provenance.json.

Durable evidence for this phase:
`/Users/jdolbe1/dvm-artifacts/research/gpu-metal-driver-native-smc-20260906/`.
The archive preserves successful and failed trial records, source snapshots,
installation provenance and measurements with SHA-256. Full RAM/disks, Apple
libraries and executables remain outside the archive; the large DIAG2 ANS log
is represented by its full SHA-256, exact stage counts and final trace records
in failure-analysis.json. Both owned late-test VMs were shut down. The original
migrated baseline and unrelated VMs were not modified.


## Boot-time startup dependency

The default driver runner previously held the ready word at zero until native
presentation and HID readiness. The guest helper then performed repeated
synchronous namespace reads before it loaded the bundle or created its device.
That dependency belonged to the benchmark harness, not Metal. A future system
device must be available to its UI consumers before those consumers render.
The earlier long-wait failures do not establish that a custom device cannot
initialize during boot.

The host worker now has an optional versioned bootstrap frame, emitted only
**after** MTLCreateSystemDefaultDevice and newCommandQueue succeed. The boot
peer requires that frame before publishing the ready word and starting QEMU.
Bootstrap does not consume a guest command sequence; the first guest RPC is
still sequence one. Missing/failed bootstrap fails before guest launch and
cannot publish ready. The exact guest shader identity and output verification
remain mandatory.

`run_guest_load.py --driver-worker ...` now uses that host-ready startup by
default. The guest remains the existing RunAtLoad helper on INSTALL10, without
StartInterval, RAM restore, debugger, new kernel patch, or register/memory writes.
BUILD19's guest executable and bundle are **byte-identical** to BUILD18; only
the host worker changed. Original SMC/powerd, SPTM/TXM, retained migrated disk
chain and input/display jobs are unchanged. No guest reinstall was needed.

The runner independently requires native presentation and ten seconds of stable
input identity with fresh acknowledgements after the GPU run and clean child
exit. It observes the existing input-status presentation counter instead of
reading the entire growing QEMU stderr file on every mailbox poll. The counter
comes from `darwin_fb_scanout` → `darwin_input_presented` in hw/arm/darwin_fb.c
and hw/arm/darwin_input.c. This removes unnecessary host work; there is no
controlled measurement attributing the historical stalls to log scanning.

Success conditions: two independent fresh-disk boots, eight exact two-pass
submissions each, zero live host objects, child exit zero, observed zero native
presentations at GPU completion, then native display/input readiness. Failure
bounds are host bootstrap reads of 15 seconds, guest startup acknowledgement
within 60 seconds, 60 seconds without GPU progress, and 180 seconds total.
These boot-loaded trials measure availability and correctness under boot load;
they must not replace the post-boot latency comparison.

Reproduction (new output tags/directories required):

```sh
bash tools/gpu/build_driver.sh /tmp/dvm/METAL_DRIVER_BUILD19
DVM_DRIVER_BUILD=/tmp/dvm/METAL_DRIVER_BUILD19 python3 -m unittest discover -s tools/gpu -p 'test_driver_*.py' -v
python3 tools/gpu/run_guest_load.py /tmp/dvm/METAL_DRIVER_INSTALL10/warm-manifest.json --tag METAL_DRIVER_BOOT1 --seconds 180 --driver-worker /tmp/dvm/METAL_DRIVER_BUILD19/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib --aux-poll-ms 1
```

BOOT1 passed: helper READY at 14.497 s, bundle loaded at 15.491 s, device created
at 15.493 s, all eight GPU runs complete at 16.825 s, clean child exit at 16.919 s.
The captured display status at GPU completion had presents=0 and input state I.
Native display and stable fresh input acknowledgements passed at 108.454 s. The
final image was inspected and shows the working software lockscreen with native
charging. It is not a GPU-rendered lockscreen.

This fixes boot availability for the current explicitly loaded frontend by
removing its inappropriate display prerequisite. It does not diagnose a general
kernel timer/scheduler fault, prove indefinite idle-session liveness, or implement
system device discovery. Completion polling and the narrow compute subset remain;
the future command transport still needs event-driven completion and reset rules.


BOOT2 repeated the same configuration successfully: helper READY at
14.166 s, eight exact GPU submissions complete at
16.031 s with presents=0, then native display/input
verification at 108.396 s. Both trials used
ordinary RunAtLoad on INSTALL10 and shut down their owned VM afterward.
The original backing chain was re-hashed and verified. All 79 project tests
and 13 driver tests passed; the runner also rejected an explicitly delayed
parent before creating any VM artifacts. Python compilation, shell syntax and
diff checks passed. No QEMU source or guest binary changed in this fix.

Durable reproduction records, including exact source/binary hashes, raw logs,
output oracles and images:
`/Users/jdolbe1/dvm-artifacts/research/gpu-metal-driver-boot-start-20260906/`.

## Shared-memory transport follow-up (2026-09-06)

The bounded boot-integrated mapping/doorbell and exact-AIR Metal experiments now
pass without the auxiliary NVMe transport. Two post-display runs measure median
full-work times of 7.621/8.878 ms versus 0.489/0.538 ms for the CPU reference; this
tiny workload still does not benefit from offload. See the [shared-memory ledger](gpu-shared-memory-transport-ios27.md)
for guarded boot patches, failures, runtime proof, checkpoint rejection and
remaining production-driver dependencies. System software rendering stays the
fallback; this is not global UI or Liquid Glass acceleration.
