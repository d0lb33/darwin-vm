# Retained IOSurface handoff across fresh guest driver processes

2026-09-07, exact iOS 27 **24A5430a / iPhone17,3 / T8140**. This follows
[the first displayed shared QuartzCore workload](gpu-shared-quartzcore-ios27.md).
Work was solo in `codex/metal-driver-ios27`, with disposable install/test disk
children. No QEMU, kernel, device-tree, SPTM/TXM, native SMC or migrated-base
changes. The GPU package still uses its existing custom BootKC/SMP adapter.
GPU commands used MMIO/shared RAM, without NVMe or debugger assistance.

## Observed result

**Three fresh guest processes loaded distinct driver revisions and reused
the same supervisor-owned IOSurface**, rendering and presenting **23 frames**
through actual guest CARenderer and host Metal. Final pixels were independently
verified after each batch. No per-frame copy into the IOSurface or verification
readback was added. Every job reached native GPU completion, native display
retirement, and zero live driver objects/resource bytes.

Trial `CA_HANDOFF_GUEST1`, QEMU PID **91763**, supervisor PID **68**, IOSurface
ID **7**. The 759-page registration remained byte-identical across all jobs:
`931d25645770a21fea3d14613887380106c196c7b7a6b831d749a1f249173e02`.

| Job | Fresh PID | Frames | Setup | Staging/spawn through result |
|---|---:|---:|---:|---:|
| 1788761284075717 | 302 | 3 | 334.709 ms | 16,319.066 ms |
| 1788761370195899 | 347 | 4 | 343.783 ms | 4,841.977 ms |
| 1788761452058843 | 416 | 16 | 282.390 ms | 1,566.591 ms |

These totals include loading and verification, not just rendering. In the
16-frame job, frame 1 took **446.715 ms**, frame 2 **43.546 ms**, and frames
3–16 **11.653–14.572 ms** each including native display/retirement. This is
an unpaced, short ownership test, **not sustained 60 Hz evidence**. The cause
of the differing first-use costs is not established by this experiment.

The three signed runtime binary SHA-256s were, in order:

```
13fb003d30f76b4eb156f7ab32d81644d17a8705e41d87ac3617e7558fca3cb5
3d40d66ac8095857023747a57c1e8ff7af40264c4e0bb2def9bd3b9f20cfe5dd
1c6194b051bde375348d565547c182dcc7ae8e629ca1211414ea17c4b706d61b
```

Final native DCP DMA bytes matched the independently checked final IOSurface:
**12,432,384 bytes**, SHA-256
`2b3e95e80933333c3c236edebcfe66688b9add441f6ed4bd9b479b1640a96691`.
All **3,013,524 pixels** per final frame matched the scene oracle. Ordinary
driver input buffers still upload; this is shared output backing, not a claim
that every resource or transport operation is copy-free.

The installed 64×64 red CARenderer control then passed in PID **460**, job
**1788761491484319**. `runner-recovery-1788761491484319-activeWake.json`
verified fresh native scanout/D594/input acknowledgements afterward.
The initial 0.5-second Home screenshot remained black; the later two-second
`wake-visible` Home check produced a changed screenshot, visually inspected:
the ordinary lockscreen clock, charging indicator, flashlight/camera and home
bar were visible. Its two input records were dispatched and acknowledged
without failure. This remains software-rendered lockscreen content.
The trial passed, stopped explicitly and was reaped after **374.586 seconds**.

## What changed, and what remains enforced

The prior public `IOConnectMapMemory64` failure was `0xe00002c2` in later
pool-owning processes. Static `boot_transport_shim.cpp` retains the first
`DVMManagedOwner` for the VM's lifetime. That kernel rule is unchanged.

The supervisor now lazily maps the pool and creates one IOSurface at its
original address. Before each shared job, it creates a Mach send right with
`IOSurfaceCreateMachPort`, puts it in an otherwise unused registered-port slot,
and spawns the trusted helper. It restores its original registered ports
immediately after spawning. It refuses an occupied slot rather than replacing
an existing capability. No global IOSurface ID or bootstrap-port replacement
is used for discovery.

The child receives the right with `mach_ports_lookup` and
`IOSurfaceLookupFromMachPort`, clears its inherited registered slot, and
retains the returned surface in its mapping provider. The driver imports that
surface using the existing fixed-layout ownership checks. The supervisor's
original service mapping remains alive across all children.

A job-specific nonce/response in otherwise unused allocation padding is
written before rendering and read by the supervisor after `waitpid`. It is
outside the timed frame loop and visible-pixel region. Independent verification
checks that receipt, the actual captured bytes, surface identity, registration
identity, fresh PIDs and non-overlapping handoffs, alongside GPU/pixel/display
acceptance. A successful port API call alone is insufficient.

A failed shared child stops reuse. The host also refuses the next job while
verification is pending or after a failed shared job. Successful `waitpid`
alone does not establish GPU/display drainage. Guest crash recovery and safe
reclamation after a failed GPU/display lease remain **untested**; the current
policy requires VM recovery rather than handing those pages to another child.

This Mach IPC is entirely within iOS. The guest-to-host protocol still carries
resource handles/owned-page registration, not Mach port names. A future
Linux/Windows host backend would replace host Metal execution and host memory
mapping facilities; this guest-local handoff does not require host Mach IPC.

## Validation and scope

`test_surface_handoff.m` first proved two fresh host processes shared the
original client-address IOSurface bytes, and rejected an occupied slot and a
second receive after clearing the inherited capability. That was a **host
rehearsal**, not guest evidence. `guest-symbols.tsv` then verified all six
Mach/IOSurface exports in the exact guest cache. The trial above established
their runtime behavior on this guest.

Final validation passed **37 targeted tests** covering the driver, compute and
copying controls, managed/shared resources, runner verdicts and handoff evidence,
plus the native two-process IPC test. The **81 repository tests** passed before
boot. A first test invocation used a nonexistent `test_runner.py` filename;
the corrected `test_runner_transport.py` invocation passed and both logs remain
in the evidence. No guest failure was hidden by that command correction.

The target remains the explicit **v5 traditional Metal subset** in
`driver_capabilities.h`: bounded buffers, copied textures, render pipelines,
command encoding and one fixed shared screen surface. No Metal 4 conformance,
general IOSurface allocation, multiple queues, global device discovery,
system compositor integration or original Liquid Glass support is claimed.
The prior long offscreen SIGABRT is not explained by this successful short run.

## Reproduction and retained artifacts

Durable package: `/Users/jdolbe1/dvm-artifacts/gpu-quartzcore-handoff-ios27`.
Evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-surface-handoff-20260907`.
The package retains the sealed installed child, exact helper, latest runtime
build and signed linked revision; all launch inputs/backing files are durable.
Use disposable children, never boot the installed overlay writable.

```
python3 tools/gpu/run_guest_load.py PACKAGE/control.json --tag UNIQUE --interactive --driver-mmio --driver-present --driver-consumer --driver-runner --driver-wait-display --driver-worker PACKAGE/installed-build/driver_host --library-cache /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib
python3 tools/gpu/runner_control.py /tmp/dvm/UNIQUE --bundle PACKAGE/guest-validated-linked/DVMProxy.bundle --mode data --development --test package --frames 16 --shared-surface --surface-handoff --worker PACKAGE/runtime-build/driver_host
```

Interactive development uses `--interactive`: the session has no overall time
limit and can remain ready between tests while results are inspected or a
revision is built. `session-policy.json` records the mode and a null global
deadline before launch. Automated regressions omit that flag and retain the
600-second default; `run_shared_matrix.py` explicitly uses `--seconds 600`.

Both runner modes retain independent safeguards: 300 seconds from launch to
runner readiness, the existing post-release activation and 60-second no-RPC-or-
guest-stage-progress watchdogs, and a 100-second host deadline from staging to
test result (including the guest child's existing 90-second watchdog). Idle
`runnerNext` polling is progress; elapsed time since the last completed test is
not a failure. Individual MMIO requests retain their 15-second completion
deadline. Failed shared-resource verification continues to prohibit further
pool reuse. Session mode changes none of the GPU, native display completion,
ownership or synchronization checks.

When development finishes, use `runner_control.py TRIAL --stop` and wait for the
runner to reap its owned VM. Ctrl-C also executes owned-VM cleanup, but records
an interrupted trial rather than a successful acceptance run. Merely finishing
a test or spending time analyzing it does not stop an interactive VM.
`test_runner_deadlines.py` advances the host clock through 600 seconds and a
day, and independently exercises stalled readiness and active-test deadlines;
this is host policy evidence, not a day-long guest endurance measurement.

Build another revision with `build_consumer_package.py --shared-surface
--frames 3` and `sign_linked_revision.py --parent
PACKAGE/installed-build/dvm-gpu-load`, then stage it in the **same** VM. Check
each job with `verify_runner_job.py`. `verify_surface_handoff_batch.py TRIAL
JOB...` requires at least two distinct revisions/PIDs and one retained surface
and page registration. Run the installed control and display/input recovery,
then `runner_control.py TRIAL --stop`. Compare the final stopped-VM export with
`shared_consumer_verify.verify_scanout_export`.

Next: longer paced displayed batches with first-use costs separated, bounded
timing capture that survives audit-ring wrap, and memory/resource observations.
Then extend the shared target to the broader scene suite. Crash recovery and
multi-surface ownership need their own tests before system-compositor use.
