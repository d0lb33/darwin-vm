# Main integration and render-buffer coherence

2026-09-06, `codex/metal-driver-ios27`, exact iOS 27 24A5430a,
iPhone17,3/T8140. This is process-local, offscreen GPU evidence, not system
QuartzCore acceleration or a Liquid Glass performance result.

## Main integration

Local `main` was newer than `origin/main`. Root merge `7d424ad` incorporates
local `main` `f6fe079`; QEMU merge `3533040` incorporates local QEMU `main`
`2c4205d`. Both retain the GPU transport, scanout witness and development-loader
experiment. Machine/meson conflicts retain both PMGR and GPU transport wiring;
IOMFB retains main's power-state behavior and the existing scanout observer.

`cd qemu-sptm/build && make -j18` rebuilt 170 targets successfully. The binary
actually booted has SHA-256
`7e181abe43f832dd199ab4759d2fd70d6da0aaf5db3a212395699630bfd750fc`.
The 81 main host regressions and shell syntax checks passed. See
`GPU_MAIN_SYNC_BUILD1.log` and `GPU_MAIN_SYNC_TESTS1.log` in the evidence package.

Only QEMU changed in this boot comparison. The pinned GPU BootKC/device tree,
trust cache, original SPTM/TXM, native SMC and migrated disk ancestry stayed the
same. Main's native-PMGR implementation is merged, but this GPU package retains
its existing SMP adapter/tree; **native PMGR was not separately enabled or
validated in this package**. Neither the main checkout nor the migrated parent
was modified by the experiment.

`GPU_MAIN_SYNC_GUEST1` passed these fresh-process jobs in one disk boot:

| Job | PID | Work | Process/staging elapsed |
| --- | --- | --- | --- |
| 1788751797639006 | 311 | Installed control | 16.489 s |
| 1788751797699509 | 336 | Data-loaded runtime witness revision | 5.613 s |
| 1788751941356591 | 378 | Installed control afterward | 3.333 s |

Every job submitted the actual guest CARenderer red CALayer, completed a host
Metal draw, verified all 4096 pixels, and retired all objects. The readiness
gate recorded 71 native presentations, ten stable seconds and a fresh input
ACK at 110.166 s. Final input status recorded 1015 presentations, 83 ACKs and
zero timeouts. This proves native presentation/helper readiness, not a reviewed
home screen or gesture. IOMFB log lines 3214–3225 show the new A485/A484 power
replies, including `A484 display power 1 -> 1`. Sleep/wake and post-session
display recovery were not separately accepted in this trial.

## Implemented contract

Profile `bounded-render-buffer-coherence-v3` advertises 31 render-buffer slots
and `vertex-completion-writeback`. The existing 20 scalar capability answers
remain unchanged. Fragment texture/sampler limits remain eight; compute limits
and software fallback are unchanged.

* Pipeline reflection admits writable **vertex** buffers only when the frontend
  requests writeback support. Writable fragment buffers remain unsupported.
* Actual draws identify owned writable allocations; unused pipeline prewarming
  does not cause readback. Writable inline bytes are rejected.
* Vertex stores are ordered before subsequent vertex/fragment consumers using
  a vertex-stage buffer barrier. GPU-written index inputs and simultaneous
  buffer/linear-texture aliases require a separate completed submission.
* After GPU completion, the frontend retrieves written allocations in bounded
  32 KiB chunks, with a 1 MiB aggregate budget. It validates identity, offsets,
  lengths and ownership for every chunk before publishing any CPU shadow.
  Completion handlers run only after publication. Resources remain retained.
* Read-only draws add no buffer readback. This still uploads CPU buffer shadows;
  dirty tracking and broader resident-resource synchronization are future work.

`test_render_writeback.m` uses the real frontend/backend with JSON serialization
and a native host-compiled test shader. It verifies exact red pixels, writes at
both ends of a 65,552-byte buffer, three transfer chunks, repeated CPU/GPU reuse,
completion visibility, vertex slots 8/30, fragment slot 30, rejection of slot 31,
foreign readback, writable inline data and writable fragment pipelines. A
corrupted later chunk cannot partially publish the earlier chunk. All objects
retire. These are **host tests**, not exact-guest shader reuse evidence.

The stronger two-draw test exposed a real failed synchronization contract:
using `afterStages:Vertex|Fragment` left words at 103/207 rather than 106/214.
Changing vertex IDs did not resolve it. Using `afterStages:Vertex` produced
106/214, then CPU/GPU reuse produced 903/221 and 906/228. The failed and corrected
logs are retained under `CA_BUFFER_REVISION3`; final results are in
`CA_BUFFER_HOST_TESTS5/test_render_writeback.log`. Apple documents the restriction
on waiting for fragment/tile stages within a TBDR render pass in
[Synchronizing stages within a pass](https://developer.apple.com/documentation/Metal/synchronizing-stages-within-a-pass).
The observed test, not the API's existence, establishes this narrow behavior.

The integrated driver host suite now has **15 passing tests**, including this
test and the existing compute, resource, capability and failure regressions.

## Exact-guest revision and remaining scene failure

`CA_BUFFER_GUEST1` ran five jobs without rebooting. Installed controls passed
before and after; revisions 3 and 5 loaded from Data using the established
OOP-JIT signature/test-kernel route. Final revision 5, job
`1788752760454599`, PID 414, passed actual guest CARenderer with one draw, exact
pixels and zero live objects in 2.306 s. This is whole-process elapsed time,
**not frame latency**. The following installed control took 1.353 s. Replay of
revision 5's 27 captured requests passed in 44 ms on the host, including generated
uploads, descriptors, constants, output and retirement checks.

This trial deliberately released the GPU worker before UI readiness; its later
revision-5 job ran after observed native display/input activity. It is not a
replacement for the explicitly gated main comparison above. Final status had
2269 presentations and a ready input helper, but also three input timeouts and
four rejected ACKs over the session. No sustained pacing or gesture acceptance
is claimed. Both owned VMs exited and were reaped.

`CA_BUFFER_SEQUENCE_HOST5` is a **host QuartzCore rehearsal with explicitly
substituted guest AIR**, not an exact-guest multi-frame result. Its first frame
has three correct opaque layers and verified pixels. Requests 23 and 25 fail:

```text
code=45 domain=DVMDriverHost
shader binding outside render contract: stage=1 name=img_tex_1C
index=8 type=2 access=0 array=1 textureType=2
```

The client subsequently aborts. This disproves four-frame operation within the
current eight-texture contract, not the overall driver route. Full requests,
constants, generated uploads and replies are in `records.jsonl`. The next
bounded step is to inventory the related reflected texture/sampler requirements,
implement and test an explicit larger binding contract as one batch, then run
the changing scene in fresh guest processes. The pinned helper's built-in scalar
checks expect eight slots: expanded capability tests must use its package-test
entry or a revised helper, rather than return misleading limits to preserve an
old test. Guest package-scene verification and sustained pacing remain pending.

## Artifacts and reproduction

Small evidence, signed staged bundles, checksums and source snapshots:
`~/dvm-artifacts/research/gpu-main-sync-buffer-coherence-ios27/`.
Independent `verify_runner_job.py` checks passed for all eight jobs, including
audit sequence/CRC, staged executable hash, guest pixel witness and host output.

The durable package `~/dvm-artifacts/gpu-runtime-loader-main-ios27/` contains the
rebuilt QEMU, final driver/backend and linked revision. Its `control.json`
references the preserved installed parent; it has no `/tmp` boot dependencies.
The installed parent still contains the old control driver, so use its old
backend for installed jobs and the matching new backend for revised jobs:

```sh
python3 tools/gpu/run_guest_load.py \
  ~/dvm-artifacts/gpu-runtime-loader-main-ios27/control.json \
  --tag GPU_NEXT --seconds 600 --driver-mmio --driver-present \
  --driver-consumer --driver-runner --driver-wait-display \
  --driver-worker ~/dvm-artifacts/gpu-runtime-loader-ios27/driver-build/driver_host \
  --library-cache ~/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib
# From another shell, after the runner inbox exists:
python3 tools/gpu/runner_control.py /tmp/dvm/GPU_NEXT \
  --bundle ~/dvm-artifacts/gpu-runtime-loader-main-ios27/linked-driver/DVMProxy.bundle \
  --mode data --development \
  --worker ~/dvm-artifacts/gpu-runtime-loader-main-ios27/driver-build/driver_host
python3 tools/gpu/runner_control.py /tmp/dvm/GPU_NEXT --stop
```

Host tests: build with `build_driver.sh` or the documented incremental compiler
commands in the source snapshot, then run
`DVM_DRIVER_BUILD=BUILD PYTHONPATH=tools/gpu python3 -m unittest tools/gpu/test_driver_host.py -v`.
For the bounded captured host scene, build with
`DVM_CA_FRAMES=4 bash tools/gpu/build_consumer_rehearsal.sh BUILD`, then use
`run_consumer_host.py --client BUILD/driver_client --worker BUILD/driver_host
--library AIR --out NEW_DIRECTORY`. The current four-frame test is expected to
fail at the recorded texture contract; the failure must not be counted as a pass.
