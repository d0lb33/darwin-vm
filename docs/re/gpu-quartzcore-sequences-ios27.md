# Exact-guest QuartzCore sequences and incremental test packages

2026-09-06. Scope: iOS 27 24A5430a, iPhone17,3/T8140, existing
boot-integrated MMIO service, native SMC, original SPTM/TXM and migrated
disk lineage. Isolated worktree `codex/metal-driver-ios27`; disposable disk
children. These are **offscreen CARenderer** results, not system compositor
integration or Liquid Glass presentation.

## Evidence and coverage

Durable evidence root:
`/Users/jdolbe1/dvm-artifacts/research/gpu-quartzcore-sequences-20260906`.
`CA_BINDING_GUEST1/independent-job-verification.json` verifies all eight jobs
from staged binary hashes, guest CRC audit, host command completion, pixel
oracles and final zero-resource accounting. Each job has its own guest PID
and host worker. The VM booted once from disk, gated on real native display
and input readiness, then accepted successive revisions without reboot.

| Contract | Evidence | Status / limit |
|---|---|---|
| Runtime driver and test revision | Jobs 1788753732240457 through 1788754069124239 | Proven in exact guest; fixed helper retained |
| Test export reports through existing helper | `consumer_package.m`, helper `_DVMReport` export, guest CRC audit | Proven; no new supervisor RPC or boot patch |
| 20 negotiated scalar queries | Job 1788753853278755 | Proven, profile `bounded-render-bindings-v4` |
| Fragment bindings 0–15 | Host `test_render_writeback`, guest sequence pipeline creation | Proven bounded contract; slot 16 rejected |
| Read-only 3D textures | Pitched 2×2×2 host roundtrip and native sampling; exact guest color-cube pipeline | Host sampling proven; guest pipeline accepted, not a general 3D workload claim |
| Changing geometry, opaque occlusion, retained resources | 4, 64 and 512-frame exact-guest scenes | Proven at 64×64; checks after frames 0, 1 and final |
| Guest AIR reuse | Every guest consumer verification | Exact SHA `8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364` |
| Writable-buffer completion and cache invalidation | `CA_CACHE_HOST_TESTS1/final-test.log` | GPU writes, multi-chunk publication, malformed completion and reuse tested |
| Captured submission replay | `CA_CACHE_REPLAY512` | 1,571 requests replayed with matching outputs/status/resources; 0.353 s host replay |
| Sustained 60 Hz pacing | Job 1788754069124239 | Not passed: 43 late deadlines in 510 steady frames |
| Display/input after long batch | `CA_BINDING_GUEST1` failed; `CA_EMPTY_GUEST1` after fix | Empty-swap retirement, power-off/wake, fresh scanout/completion and Home frame change proven after fix |
| System device discovery, full compositor, generic IOSurface import, checkpoints | No new acceptance evidence | Untested / incomplete |

The profile advertises the implemented contract, not the host GPU family.
3D textures allow shader reads only, one mip/slice, dimensions at most 512,
and at most 1 MiB framed storage. Arrays, multisampling and general private
storage remain outside the profile. Render targets remain 2D. Reflection
checks the bound texture dimension before execution.

## Timing and buffer reuse

All numbers below are guest monotonic wall time from layer change through
driver completion. Setup and first use are separate. Final verification is
outside the timed batch. The scene retains a renderer, target and queue,
moves a red 16-pixel-wide bar behind a green bar over blue, and independently
checks every pixel in three frames. Each frame has three real GPU draws.

| Exact-guest job | Frames | Setup ms | First ms | Steady median / p95 / max ms | Work >16.67 ms |
|---|---:|---:|---:|---|---:|
| 1788753812592411, before cache | 64 | 95.329 | 1562.911 | 43.023 / 64.871 / 74.317 | 62/62 |
| 1788753984632721, cache | 64 | 141.183 | 519.188 | 7.508 / 10.197 / 12.187 | 0/62 |
| 1788754018848317, old revision again | 64 | 86.611 | 557.648 | 33.201 / 38.262 / 42.936 | 62/62 |
| 1788754069124239, cache, 60 Hz | 512 | 155.229 | 606.834 | 8.321 / 11.832 / 154.641 | 6/510 |

The 512-frame paced interval lasted 8.494 seconds; 43 deadlines were late,
maximum lateness 142.387 ms. Deadline misses differ from work-time misses:
a slow frame can delay following frames. This is not smooth native pacing.

The 64-frame old revision issued 512 `writeRenderBuffer` requests; caching
reduced that to 78. The frontend compares each owned 32 KiB CPU chunk with
its last acknowledged host image, including direct `contents` writes.
First use uploads every chunk. Successful GPU writeback refreshes the cache
only after all chunks validate; compute writes and uncertain errors invalidate
it. Tests verify subsequent upload after a failed readback. This introduces
one extra CPU shadow per buffer (524,288 bytes in this scene), not included
in host Metal resource-byte accounting. Process RSS was not measured.

Host-only 64-frame controls: native macOS CARenderer median 0.254 ms,
forwarded host rehearsal 4.178 ms before caching. Both passed independent
pixels. These ran during a VM boot, and the forwarded host rehearsal uses
explicit guest-library substitution; they are not exact-guest or controlled
near-native performance comparisons. Native resource accounting is unavailable.

## Reproduction

Run from the GPU worktree. A package revision compiles a test export into the
driver while retaining the already installed helper:

```sh
python3 tools/gpu/build_consumer_package.py BASE_BUILD NEW_BUILD --frames 512 --hz 60
python3 tools/gpu/sign_linked_revision.py NEW_BUILD/DVMProxy.bundle LINKED_OUT --parent BASE_BUILD/dvm-gpu-load
python3 tools/gpu/runner_control.py TRIAL --bundle LINKED_OUT/DVMProxy.bundle --mode data --development --test package --frames 512 --worker NEW_BUILD/driver_host
python3 tools/gpu/verify_runner_job.py TRIAL/runner-jobs/JOB
python3 tools/gpu/replay_driver.py TRIAL/runner-jobs/JOB --worker NEW_BUILD/driver_host --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib --out NEW_REPLAY
```

The base runner manifest is
`/Users/jdolbe1/dvm-artifacts/gpu-runtime-loader-main-ios27/control.json`.
Use `run_guest_load.py` with `--driver-mmio --driver-present --driver-consumer
--driver-runner --driver-wait-display` and the original installed v2 backend
for installed controls. Each new package supplies its matching backend using
`--worker`. No NVMe or debugger assists shader submission or runtime staging.
The GPU package retains its opt-in custom boot kernel and SMP adapter; merging
main's PMGR support does not activate native PMGR in this package.

## Empty native swap failure

After the successful batch, native input dispatched home and swipe events,
but screenshots stayed unchanged and presents stayed at 2,237. Do not use
the input helper's `ok` field as proof of display recovery.
`CA_BINDING_GUEST1/stderr.log`, RPC 15892, records:

```
iomfb: scanout unsupported primary BGRA profile
iomfb: display-state failed; A408 retained, no D594
```

After acceptance, a bounded paused read captured only the 4 KiB pending RPC
heap page through dart-dcp, SID 23, MMIO `0x412340000`, DVA `0x10000030000`.
`pending-a408.bin` SHA-256:
`e1fe2e97b9dc3af5e7b5080e335e36df2e345c53716d2645a6e8d39bdd592dfe`.
The decoded main record is present (`+0xfea=0`), all four surfaces are absent
(`+0xfeb..+0xfee=1`), ID at `+0x98` is zero, primary DVA is zero, and absent
descriptor bytes are `aa`. This matches the optional argument packing
documented in `surface-cache-and-completion.md` at native
`0xfffffff00a0c9088..0xfffffff00a0c9128`.

Observed failure: our display-state gate demanded a successful pixel scanout
for an empty swap and withheld its D594. The bounded fix allows normal
retirement of a correctly shaped all-null update, without DMA or publishing
a fictional frame. Whether this restores native power-off/wake is a separate
runtime acceptance test; malformed and mixed-surface requests remain rejected.

### Fix acceptance: `CA_EMPTY_GUEST1`

Rebuilt the isolated QEMU after the previous VM was reaped. Four swap unit
tests pass, including absent descriptors with poison bytes, invalid null
flags, mixed surfaces and truncated inputs. The new disposable disk boot
passed native display/input readiness at 101.266 s. Installed control
1788755026578939 and runtime package 1788755026635263 both passed; the latter
ran 512 frames / 1,536 draws with exact pixels and zero final resources.
Its steady median / p95 / max were 9.207 / 14.430 / 39.648 ms, 10 work-time
misses and 48 paced deadline misses out of 510. These repeat the correctness
result and confirm that sustained pacing still needs work.

The concrete recovery trace in `CA_EMPTY_GUEST1/stderr.log` is:

| Line | Observation |
|---:|---|
| 47160 | A408 RPC 10004 has no surfaces; no pixel DMA |
| 47165 | Native D594 returns status zero and releases that A408 |
| 47175–47176 | A484 power 1→0; display blanked |
| 47698 | A484 power 0→1 |
| 47751 | Real 1179×2556 BGRA scanout resumes |

The acceptance artifacts are preserved at
`/Users/jdolbe1/dvm-artifacts/research/gpu-empty-swap-20260906`.
QEMU fix commit `fc49f9c`; rebuilt binary SHA-256
`fe12b612746b9c413cbc17cb97bfc0550f004b531c2085485ddfde48db8bef6e`.
The continuation manifest is
`/Users/jdolbe1/dvm-artifacts/gpu-quartzcore-v4-ios27/control.json`, with
the matching backend and the exact validated linked package beside it.

`runner-recovery-1788755026635263-wake1.json` independently requires fresh
scanout, successful native D594 and fresh input ACKs. `home-wake.json`
records two dispatched input events, no failures, and differing before/after
frame hashes. The earlier power-button tool alone reported identical frame
hashes, so it is not used as a screenshot proof. The DCP trace establishes
the power transition and later scanout. An installed control was also queued
after wake and passed as job 1788755237120310, PID 411; its final recovery
check also passed. The final screenshot was inspected and shows the native
home screen with charging battery. This screen still uses the software
fallback; the 512-frame GPU scene was offscreen. No debugger was used in this
acceptance boot. The owned VM stopped and was reaped after 262.712 seconds.

Validation for the coherent driver batch: 32 targeted host protocol,
writeback, consumer and runner tests; 81 project host tests; four QEMU swap
tests; 11 independently verified exact-guest jobs across the two boots.
The package builder was additionally exercised incrementally with unchanged
driver/helper dependencies, relinking only the scene package and confirming
that recorded CDHash and trust-cache input match the final signed binary.
Original captured packages retain their original metadata as historical
evidence; runtime acceptance verifies actual staged binary hashes.
