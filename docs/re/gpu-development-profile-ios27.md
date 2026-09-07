# Development limits and actual Home transition — 2026-09-07

Exact guest: iOS 27 24A5430a, iPhone17,3/T8140, preserved SPTM/TXM,
native SMC, migrated disk lineage and software fallback. This is a fresh disk
boot of an isolated child, no debugger or RAM restore. The independent
backboardd-reload branch was not merged or modified.

## Shared development contract V28

| Bound | Previously committed V26 | V28 |
| --- | ---: | ---: |
| Ordinary logical resource bytes | 64 MiB | 512 MiB |
| One private texture | 32 MiB | 128 MiB |
| Live objects | 256 | 4096 |
| Operations per render/blit encoder | 256 | 4096 |
| Ordered passes per submission | 64 | 256 |

These are bounded development allowances on a 128 GiB host, not native GPU
family capability claims. Guest and host negotiate the same profile and use
the same constants. Resource ownership, retirement, validation, shader bounds,
completion deadlines and the 2 MiB serialized submission limit remain enforced.
CPU-transfer and imported-page contracts were not expanded by this change.
Logical byte charges, native Metal allocation sizes and process RSS are distinct.
Cap failures now report requested/live logical bytes and the aggregate ceiling.

The prior `CA_TEXTURE_VIEW_PACING1` failure required 68,698,622 simultaneous
logical bytes. A V27 96 MiB intermediate experiment (`CA_BUDGET_GUEST1`)
completed 494 GPU/presentation/completion batches, then stopped at the frontend
256-operation limit. It never exceeded the old allocation ceiling and had an
input-helper restart after four timeouts. It does not prove the enlarged budget
or sustained responsiveness. Its final source-to-console delivery verified.

## CA_DEV_HOME1 — observed results

Durable evidence and pinned build/image inputs:
`/Users/jdolbe1/dvm-artifacts/research/gpu-development-profile-20260907`.
Run records: `evidence/CA_DEV_HOME1/{result,work-summary,allocation-budget,
home-transition,scanout-verification}.json`, `driver-host.jsonl`, `stderr.log`,
`input-status.json`, before/after pixels and 429 timestamped transition thumbnails.

- Registered actual backboardd and executed 493 host GPU batches: 1155 render
  passes, 8003 draws and 12 compute dispatches. All 493 presentations received
  native D594 completion; 2215 RPCs, zero host rejection.
- Two passes exceeded 256 operations; maximum 304. Peak ordinary logical bytes
  67,840,126 exceeds the old 64 MiB ceiling. Both former limits were exercised.
- Peak ordinary native allocation 70,914,432 bytes; imported spans 48,431,104
  bytes. Ordinary logical charges fell to 11,911,650 at the end; 79,696,248
  cumulative logical bytes were released. This is not a long-term leak proof.
- Both Home edges dispatched, queues drained, later display completed and the
  post-target observation lasted 30.018 seconds. Zero input timeouts. The helper
  had restarted once before this test; PID164/epoch3 remained stable during
  the Home check. Do not describe the entire boot as restart-free.
- Final 1179×2556 RGhA source → BGRA conversion → console: zero conversion and
  display differences. This proves delivery, not complete scene correctness.
  Wallpaper remains black; some icons initially show placeholders.

## Visible transition and timing limits

The Home down/up UART records are epoch3 sequences44/45, usage64. QEMU logs
their source clock, actual framebuffer delivery and D594 handling. Thumbnails
sample already-delivered CPU pixels at every sixth pixel; there is no added
GPU readback. Capture overhead across 429 frames: 221–429 microseconds,
p95 317 microseconds. This diagnostic run is still instrumented.

Visual review of thumbnails28–32 identifies the lock-to-Home transition:
clock disappearance, enlarged icons moving into position, then the Home grid.
The first unmistakable change in this selected phase is at 1404.564 ms after
Home-down record preparation. Earlier banner changes occur, but their cause
is uncertain: **this is not proven earliest input feedback latency**. Thumbnail
sampling also cannot establish physical display refresh timing.

The four presentation gaps within that visible phase are 53.020, 174.732,
647.621 and 223.362 ms. The latter large gaps occur while icons are visibly
moving, so this is evidence of a slow transition, unlike idle lock-screen gaps.
Only four intervals: no representative steady-animation percentile claim.
Startup, later icon population and idle are excluded. GPU batch times over the
whole run (33–3503 microseconds after first use) do not explain or measure the
complete response path. Locating the long delays is the next performance task;
shader tuning is not yet justified by this evidence.

## Reproduction and host checks

Host build: `tools/gpu/build_host_driver_tests.sh <new-host-directory>`.
Set `DVM_DRIVER_BUILD` to it and run:
`python3 -m unittest discover -s tools/gpu -p test_driver_host.py -v`.
33 tests pass, including 4096-operation GPU execution with exact red output,
4097-operation atomic rejection, 4096-object capacity/reuse, 512 MiB budget
exhaustion/reuse and three allocation cycles modeling the observed working set.
Two initial test failures were stale V26 limit expectations; their failed log
is retained alongside the corrected passing log. 81 project regressions pass;
three allocation/presentation audit tests pass.

Rebuild QEMU in this worktree (`qemu-sptm/build`, `make -j18`) before pinning
the binary. The evidence package pins the rebuilt tracing binary, driver and
installed child; QEMU device semantics and the boot kernel remain unchanged.

```sh
python3 tools/gpu/run_system_boot.py <package>/control.json \
  --worker <package>/host/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry-inputs/library-cache \
  --tag <unique-tag> --seconds 240 --min-presentations 128 \
  --home-after-presentations 64 --observe-seconds 30 --trace-home
python3 tools/gpu/report_compositor_pacing.py <run>
python3 tools/gpu/audit_compositor_budget.py <run>
python3 tools/gpu/report_home_transition.py <run> --first 28 --last 32
python3 tools/gpu/verify_rgha_scanout.py <run>
```

The last two tools require NumPy/Pillow. Select bounds from each new run's
actual images; 28–32 are specific to CA_DEV_HOME1. Stop on ownership, completion
or compositor failures; do not reuse an uncertain session. All owned test VMs
were explicitly stopped. Existing unrelated VMs were left untouched.
