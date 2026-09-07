# Guest QuartzCore rendering directly into the displayed shared IOSurface

2026-09-07. Exact **iOS 27 24A5430a, iPhone17,3/T8140**, original SPTM/TXM,
native SMC, migrated disk lineage and software fallback. Solo work in
`codex/metal-driver-ios27`; isolated install/test disk children only. No
debugger or NVMe assistance for GPU commands. This continues
`gpu-shared-render-target-ios27.md`.

## Proven milestone

Actual guest CARenderer submitted **three screen-sized frames / 15 draws**
through the custom Metal driver. Host Metal rendered directly into a
buffer-backed BGRA texture over the kernel-owned scattered page pool.
The guest IOSurface aliased those pages and the existing native IOMFB/DCP
path presented all three frames. The final DCP pixel-DMA export matched the
independently verified guest IOSurface byte-for-byte:

* Geometry: **1179×2556**, row **4864**, backing **12,435,456 bytes**.
* Visible row allocation: **12,432,384 bytes**, **3,013,524 pixels** checked.
* Final SHA-256:
  `712d5e005cc73eb83c9a2dbe2aa178b4446726442a3565d47e348a861fe7f5eb`.
* **No per-frame copy into the IOSurface and no verification readback inside
  the frame loop.** Ordinary render input buffers still use transport uploads.
* Three native Metal completions, status 4; zero final objects/resource bytes.

The scene changes the full-screen layer from red to green to blue. Four
one-pixel CALayers produce the existing frame-identification pattern; they
are rendered by QuartzCore and Metal, not CPU-painted or special-cased by
the driver. Library loading used the original guest's QuartzCore AIR
(`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`).
This is traditional Metal render API coverage, not Metal 4 conformance,
system-wide GPU discovery, Liquid Glass or arbitrary Metal compatibility.

## Runtime evidence and timing

Trial **CA_SHARED_GUEST2**, job **1788759830246225**, guest PID **297**.
The linked bundle SHA-256 was
`e47360b2ff5775937294a676b89d806298d936f333b624dd61fa9a4678666067`.
The subsequent installed red control passed in PID **323**, job
**1788759830303701**. The trial completed successfully and QEMU PID **90283**
was explicitly stopped/reaped. No checkpoint was restored.

| Frame | Render through GPU completion | Native display + retirement | Total | Native GPU time |
|---|---:|---:|---:|---:|
| 1, first use | 1482.299 ms | 14.108 ms | 1496.407 ms | 100.000 µs |
| 2 | 42.225 ms | 4.227 ms | 46.452 ms | 99.083 µs |
| 3 | 13.090 ms | 4.227 ms | 17.317 ms | 98.792 µs |

Setup was **396.680 ms**, separate from the table. Three frames do **not**
establish sustained 60 Hz, tail latency, memory stability or a CPU comparison.
GPU time is in the host Metal clock domain; frame totals use the guest
monotonic clock. The first dynamic process took 18.207 s from staging/spawn
to result; that includes loading, setup, rendering and verification.
Its bundle staging took 114.855 ms. Display readiness was 98.218 s.
The helper/driver bootstrap build recorded 9.253 s; a later driver dependency
rebuild recorded 4.525 s, excluding its test-package relink/signing.

Actual scanout witnesses at DVA **0x100017f8000**:

```
iomfb: gpu-present frame=1 swap=0 ... monotonic_ns=300624241470000
iomfb: gpu-present frame=2 swap=0 ... monotonic_ns=300624289142000
iomfb: gpu-present frame=3 swap=0 ... monotonic_ns=300624306666000
```

Swap IDs were all zero in this narrow native path; the verifier also requires
ordered GPU-written frame IDs and epochs. The guest's mode-1 wait returns,
backend lease transitions, native scanout events and D594 completion are
separate evidence. `shared-scanout-verified.json` then compares the stopped
VM's actual scanout export with the captured and independently checked
IOSurface. It does not substitute a screenshot or a completion API name.

`runner-recovery-1788759830303701-activeWake.json` verifies fresh native
scanout/completion and input ACKs afterward. `active-wake-after.png` was
visually inspected: the ordinary lockscreen clock, battery and controls were
visible. Home dispatched two native input records without failures, though
the before/after screenshot hashes were identical; that input result alone
does not prove a UI transition. The displayed lockscreen still uses the
existing software rendering fallback.

## Implementation and contract

Factory `DVMCreateSharedMetalDevice` accepts an explicit process-local mapping
provider. The provider retains the owning `Namespace`/IOService connection;
the driver retains its mapping and imported IOSurface. The version-1 ABI
exposes only the fixed owned pool. Errors propagate through
`DVMGetOwnedMetalMapping(..., NSError **)`.

`newTextureWithDescriptor:iosurface:plane:` validates original backing address,
allocation size, pixel format, pitch, dimensions, plane count, protection,
storage and usage. Foreign surfaces and unsupported layouts are rejected.
Resource metadata returns the retained original IOSurface. This is an explicit
fixed-layout extension to v5, not general IOSurface import; the latter
capability remains false.

Acquire → one or more ordinary render submissions → seal after GPU completion
→ native swap/wait → retire protects reuse. Completion and display retirement
remain distinct. The frontend's copied texture accessors reject this resource;
its consumer uses IOSurface locking and the explicit lease. Broader public
CPU texture access, general multi-surface allocation and concurrent queues
remain future work.

The full host frontend/backend test used a real IOSurface with the original
registered address, including foreign-backing rejection, native GPU execution,
correct final pixels and exactly-once mapping retirement. All **31 targeted
tests** and **81 repository tests** passed before boot. Four added verifier
tests reject false completion, wrong target/epoch, corrupted pixels even
with a matching reported hash, missing scanouts and a wrong final DMA export.
The final mapping-error API change was rebuilt and passed the focused host
frontend test again.

## Failures retained and remaining ownership dependency

* `CA_SHARED_INSTALL1/2` exited before any guest serial output: QEMU required
  the complete RAM/registration pairing for the existing three-range DT.
  `run_guest_install.py --mmio-restore` now creates isolated 16 MiB control RAM
  plus the 12 GiB sparse DRAM mirror and registration path. The third installer
  reached `GPU_LOAD_INSTALLED`, 557 serial lines, zero panics and a restore
  shell, then stopped. It changed only guarded driver/helper preimages on a
  fresh child. No new QEMU/kernel/DT change was needed.
* `CA_SHARED_GUEST1` job **1788759305363335**, PID **324**, created the shared
  target but submitted no render commands. Seal correctly failed with backend
  code **16**, `shared render seal before GPU completion`. The test's layer
  tree had been created outside an explicit transaction. Constructing it
  inside the same transaction pattern as the working scene suite corrected
  that behavior in the fresh second trial.
* Later processes in the first VM could not map the pool. Job
  **1788759756171151** captured **DVMOwnedMapping -536870206 / 0xe00002c2**
  (`kIOReturnBadArgument`) at `IOConnectMapMemory64`. Static source
  `boot_transport_shim.cpp` stores `DVMManagedOwner` as the first client,
  retains it through the provider property and rejects other clients.
  It does not clear or hand off that owner. The shim's internal
  `kIOReturnNotPermitted` is not the same code observed at the public API.
  A fresh VM allowed the new process to map and render successfully.

The first trial retains failed status because required jobs failed. It is
not rewritten as passing. Both its ordinary installed controls passed; its
native scanout/completion recovery also passed. The prior 2048-frame offscreen
SIGABRT from the preceding milestone remains unexplained by these tests.

**Next dependency:** make the persistent supervisor own the surface and prove
an explicit child handoff, or add a kernel ownership-transfer protocol with
demonstrated GPU drain and task/map lifetime checks. Do not simply remove the
owner comparison or reclaim pages after client close: a still-live task may
retain mappings/IOSurfaces, and GPU/display work may remain outstanding.
Supervisor-held IOSurface sharing is a candidate, not yet verified in this
guest. After that, run longer displayed pacing and the broader scene suite.
Current captured shared-target jobs need a registered initial-pixel fixture
before they can be replayed by the existing host replay tool; ordinary copied
guest submissions remain replayable.

## Durable package and reproduction

Evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-shared-quartzcore-20260907`.
Continuation package:
`/Users/jdolbe1/dvm-artifacts/gpu-quartzcore-shared-ios27`.
It retains the read-only installed child, trust cache, fixed helper build,
latest runtime build and exact guest-validated linked bundle. Existing durable
firmware and the pinned **fc49f9c** QEMU remain referenced; this GPU package
retains its custom BootKC/SMP adapter, not the default package's stock-kernel
native PMGR boot. Native SMC, SPTM/TXM and input are preserved.

`tools/gpu/preserve_shared_baseline.py` reproduced this package from the
sealed installation manifest, installed/runtime builds and passed trial. It
checks the staged bundle, backend hash, guest audit CRCs, final scanout,
installed preimages, boot inputs and backing chain before preservation.
`verification.json` records the subsequent durable-input and preserved-pixel
checks. The evidence directory's `supplemental-bundles.json` pins the staged
executables needed to rerun `verify_runner_job.py` on the preserved jobs;
the general evidence collector intentionally excludes executables.

```sh
python3 tools/gpu/run_guest_load.py PACKAGE/control.json --tag UNIQUE --seconds 600 --driver-mmio --driver-present --driver-consumer --driver-runner --driver-wait-display --driver-worker PACKAGE/installed-build/driver_host --library-cache /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib
python3 tools/gpu/runner_control.py /tmp/dvm/UNIQUE --bundle PACKAGE/guest-validated-linked/DVMProxy.bundle --mode data --development --test package --frames 3 --shared-surface --worker PACKAGE/runtime-build/driver_host
python3 tools/gpu/verify_runner_job.py /tmp/dvm/UNIQUE/runner-jobs/JOB
```

Use at most one pool-owning guest process per VM until handoff is proven.
Run a final installed control, observe native display/input recovery, and stop
through `runner_control.py --stop`. Finally call
`shared_consumer_verify.verify_scanout_export(trial, job)` on the stopped
VM's export. Staging/loading, GPU execution, pixels, display retirement,
recovery and sustained performance remain separate acceptance checks.
