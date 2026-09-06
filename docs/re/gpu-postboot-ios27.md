# Post-home-screen latency gate — exact 24A5430a

## Merged-main rerun specification

The user superseded the old-QEMU trial before it ran: pull both `main`
branches, rebuild QEMU, then rerun latency. Both pulls were already current:
project `fbc53c9`, QEMU `00c4a4c`. Fresh worktrees are
`darwin-vm-gpu-postboot` / `codex/gpu-postboot-main` and its QEMU
`codex/gpu-postboot-qemu`. The QEMU auxiliary patch reapplied as `0b8137b`;
its only delta from QEMU main is `hw/arm/darwin_ans.c`.

An isolated full build completed using
`../configure --target-list=aarch64-softmmu --disable-pvg --disable-docs`
and `make -j18`. The copied immutable executable is
`/tmp/dvm/GPU_MERGED_QEMU1/qemu-system-aarch64`, SHA-256
`6608ee6df8fe1d23d059f7dea7ec95b1fb1b59933d5cb574b4bd90cc9a759c88`.
No shared executable was rebuilt or replaced. Fifty merged project tests,
six auxiliary protocol tests, and three readiness tests passed.

Source disk baseline: `/tmp/dvm/CLOCK_SOFTWARE_PATCH1/warm-manifest.json`,
sealed `/tmp/dvm/CLOCK_SOFTWARE_INSTALL1/disk.qcow2`, SHA-256
`36bfda15136369b87d761aaf0c1b1659997575a359250a0f35dac1a23b52668b`.
It carries the merged guest patches; source CLOCK observations establish a
visible clock after fresh disk boot, not reliable Home/input readiness.
Keep its RTC-patched BootKC, SPTM/TXM, and runtime environment. Extend its
development DT with the same single auxiliary namespace tuple, verifying
all other properties byte-identical. Prepared manifest:
`/tmp/dvm/GPU_MERGED_BASELINE1.manifest.json`.

The helper stage uses `/tmp/dvm/native-services6/launchd.plist` (SHA-256
`f25de1647f2957b20c2e4f2898b999351b87d323879899d019bb1db206d1d7d2`)
and CLOCK's 3945-entry TC. The resulting TC has 3947 entries. Parent verified
the entire original cache unchanged except the added isolated helper; all
five native input/graphics/RTC/activation/power-publisher entries remain.
Installation and runtime parent: `GPU_MERGED_INSTALL1`; helper build/stage:
`GPU_MERGED_BUILD1` / `GPU_MERGED_STAGE1`.

Matrix tags supersede the older tags below: GPU_MERGED_A1/B1/B2/A2,
5/1/1/5 ms. Readiness, per-request, global deadlines and stop conditions
remain unchanged. If a reviewed image shows the lock screen, parent may
request one native Home press/release through the existing input protocol
using `home_visible: false`, `screen: "LOCKSCREEN"`, `request_home: true`.
Each step requires its ACK, followed by a new sync ACK and 15-second settle
before a new screenshot. That action is entirely before benchmark release;
it does not substitute for observing Home. No repeated gesture attempts,
guest debugger, or snapshot restore. Stop the matrix on its first failure.

The interrupted old `GPU_HOME_INSTALL1` installation completed and was
stopped/read-only. No old GPU_HOME measured VM was started.

## Merged A1 result and revised readiness budget

`GPU_MERGED_A1` stopped at 300.321 seconds with
`TimeoutError: home-screen readiness not verified within 300 seconds`.
Input v6 started at 17.721 s, restarted at 69.797 s, and became ready at
253.818 s; its sync ACK arrived at 253.896 s. Parent reviewed the 268.897 s
image as the native lock screen. That review was consumed at 284.498 s.
The one native Home down/up plus fresh sync completed by 284.553 s. The
second settled image arrived at 299.558 s, leaving only 0.442 s before the
hard gate deadline. Parent's **post-run** visual inspection confirms Home
icons/dock/status clock; it was not a timely release approval. The failure
therefore includes reviewer and settling time, not an inability to reach Home.

No release, bulk read/write, or latency row occurred. Request/reply/gate
pages remained zero, and the owned QEMU exited. The original matrix stopped;
B1/B2/A2 were not run. Evidence: `GPU_MERGED_A1/result.json`, both `ready-*.json`
and PNGs, `review-1.json`, `post-run-image-review.json`, and `aux.raw`.

A separate preregistered matrix, `GPU_SETTLED_A1/B1/B2/A2` (5/1/1/5 ms),
allows 450 host seconds for readiness, 480 guest monotonic seconds for WAIT,
and a 510-second total runner cap plus bounded teardown. This changes only
the readiness budget, not the 64-request workload or performance thresholds.
All input, image, settling, session and byte checks remain. Stop this matrix
on its first failure; no retries. The guest per-request 5-second soft deadline
and host 10-second progress gap remain bounded by the overall cap: a slow,
incomplete batch fails rather than becoming a partial success.

`--aux-readiness-seconds 450` explicitly selects this experiment; default 300
retains the original host policy. Header LE u32 at +152 records guest WAIT
seconds; guest accepts 330 or 480, treats legacy zero as 330, and rejects
other values. Collector checks the header against the reported host budget
and disallows combining different budgets. This needs a freshly signed helper,
not a QEMU change. The immutable rebuilt QEMU remains SHA-256 `6608ee6d…759c88`.
New helper/stage/installed parent: `GPU_SETTLED_BUILD1`, `GPU_SETTLED_STAGE1`,
`GPU_SETTLED_INSTALL1`, each derived again from the sealed CLOCK baseline.

## Settled A1 result: initialization contract unresolved

`GPU_SETTLED_A1` exited **1** at 450.233 seconds with the 450-second readiness
error. Its source manifest SHA-256 is
`33363cedc033ac337e4ac502375b11fd2d49de900d94de6daa2abbf2bf0ff025`;
signed helper SHA-256 is
`417cd3d073f48077dc566692b416edd66f846f488585a0c29701bb41a9e15174`.
Both installations reached the restore shell, emitted the guarded install
success marker, and had 524 serial lines with zero kernel panics.

Observed in `GPU_SETTLED_A1/serial.log:1123..1125`: user-client type 0 opened
with `kr=0`, selector 2 returned block size 4096, and selector 3 returned
16384 blocks. Runner timestamps are 29.113, 29.114 and 29.317 seconds.
There is no `GPU_LOAD_AUX_HEADER`, `GPU_LOAD_AUX_WAIT`, release, bulk I/O,
or latency row anywhere in the complete logs. At 189.979 s native input
became ready, then ACKed sequence 910001 at 189.991 s
(`serial.log:26092,26098`). A read-only, unpaused screenshot
`diagnostic-ui.png` shows the native lock screen and large 3:55 clock;
`stderr.log` contains 2622 presentations, starting at line 36511. Presentation
count is not distinct frames or FPS. No Home gesture was sent in this run:
the gate requires helper WAIT before it requests a review.

Static evidence: `tools/gpu/aux_transport_probe.h:192..202` performs aligned
allocation, initializes the buffer, calls the first synchronous 4 KiB read
at offset zero, then logs HEADER and validates the session before WAIT.
The observed failure is **no completed initial-header stage after successful
capacity query**. A blocked read is a hypothesis; without entry/exit probes
or a live stack, these logs cannot exclude allocation/scheduling delay,
a helper exit, or missing reporting. The configured longer WAIT budget is
consumed only after that stage; no observation proves it was reached.

Parent and Terra independently verified zero request/reply/gate pages and
zero timed samples. Both owned measured QEMUs were reaped; neither matrix
advanced to B1/B2/A2. Post-run SHA checks passed all **26 backing files and
7 QEMU inputs** for each installed manifest, including the immutable rebuilt
QEMU, exact guest BootKC, SPTM/TXM, and the migrated baseline.

| Claim / route | Verdict for this rerun |
|---|---|
| Rebuilt merged QEMU boots the preserved guest and renders native UI | Proven in these bounded disk boots |
| Native Home transition | Proven by MERGED_A1's post-run reviewed Home screenshot; not a timely benchmark approval |
| Delayed 64-request auxiliary latency / 1ms versus 5ms benefit | Untested: neither matrix released a timed batch |
| Completion of SETTLED_A1 initialization within 450s | Disproven within this one pinned run; not proof the read can never succeed |
| Adapted PV, custom forwarding Metal plugin, interception, GPU versus CPU speedup | Untested here; prior shader/display feasibility remains separate |

The next minimal diagnostic is the **first 4 KiB auxiliary read**, with guest
allocation/read entry and exit markers plus scoped NS6 QEMU submission and
completion records and helper-lifecycle evidence. Its pass condition is exact
session bytes returned and every stage correlated. If it stalls, those records
must identify whether the request reached QEMU and whether completion reached
the guest. Also distinguish starting transport after reviewed UI readiness from
starting it early and merely deferring the benchmark: this harness does the
latter. No full plugin implementation or polling optimization is justified by
these zero-sample runs, and this does not disprove GPU acceleration generally.

Reproduction (use a new unused tag; sources/commands also preserved):

```sh
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_SETTLED_INSTALL1/warm-manifest.json --expected-manifest-sha256 33363cedc033ac337e4ac502375b11fd2d49de900d94de6daa2abbf2bf0ff025 --aux-post-boot --aux-readiness-seconds 450 --aux-poll-ms 5 --seconds 510 --tag GPU_SETTLED_NEW_A1
```

Durable evidence: `~/dvm-artifacts/research/gpu-postboot-ios27-20260905`,
including failed runs, complete small logs, images/reviews, raw auxiliary media,
helper/TC, QEMU executable/build log and SHA indexes. Large System/Data and RAM
images are excluded; source manifests retain their lineage/hashes. Terra
collected the trials; parent reviewed screenshots, raw failure state, command
inputs and the entire immutable backing chain. Code/protocol checks: 50 merged
project tests, 12 auxiliary/readiness tests, shell syntax and diff checks passed.

## Preregistered experiment

The user authorized resuming a bounded delayed-start experiment before the
warm-boot merge. Start from the same isolated `codex/gpu-transport-ios27`
worktree and immutable QEMU3 as the earlier latency matrix. Use one newly
installed helper, fresh child disks, exact 24A5430a/T8140/SPTM/TXM and the
unchanged migrated lineage. No other running VMs, host System/Data mounts,
RAM restores, GPU registration or rendering changes.

The helper waits before bulk transfers and the existing 64-request benchmark.
Its only pre-release auxiliary traffic is a 4 KiB read of offset 0x30000
followed by a nominal 100 ms sleep, bounded at 330 guest monotonic seconds.
Host owns that gate; it contains this run's 64-byte random session identity,
`DVMGO001`, and CRC32 of those 72 bytes. A foreign/torn gate cannot release
work. Header offset 144 carries `DVMWAIT1`; old early-start mode stays separate.

Host readiness, entirely before timing:

1. Observe helper WAIT and input READY. Send a fresh no-event input sync.
2. Accept only the exact successful ACK for the pending sequence.
3. Allow 15 seconds without input reinitialization/restart. Helper restart
   fails the trial. This does not establish health of every guest service.
4. Capture the owned VM's native graphics console to `ready-N.png`. Parent
   visually inspects it and writes an atomic `review-N.json` with the image
   name/SHA, session, HOME verdict and explanatory observation. A rejected
   image schedules another candidate after 15 seconds within the same boot.
5. After a positive review no older than 60 seconds, require another fresh
   input sync ACK. Only then publish the gate. No input injection during the
   timed batch; a subsequent input restart fails the trial.

`-display none` hides the host window, not the emulated graphics console:
`darwin_fb.c:400-403` creates the surface and console; `qmp_screendump` in
`ui/ui-qmp-cmds.c` reads that console. Missing/failed images prevent release.
The pinned framebuffer geometry must be 1179×2556, graphics mode. Input ACK
proves protocol liveness, not a completed native touch gesture. HOME pixels
plus these checks are a workload release condition, not proof all boot work
has ended or every UI service is stable.

Deadline composition: readiness and final ACK must complete before 300 host
seconds from launch; the entire run ends by a 360-second loop deadline plus
bounded teardown. The mandatory settling interval is inside the 300 seconds.
After release, require the first request within 15 seconds and subsequent
host progress within 10 seconds; each guest request retains its 5-second
software deadline. Blocking guest calls may overshoot. Missing/incomplete
64-request results fail; deadlines never imply partial success.

Fixed sequential ABBA matrix: GPU_HOME_A1 (5 ms), GPU_HOME_B1 (1 ms),
GPU_HOME_B2 (1 ms), GPU_HOME_A2 (5 ms). Stop the matrix at the first failed
readiness or byte trial, retaining failure artifacts; do not substitute
retries. Host pre-release polling is fixed at 5 ms in both arms. Only after
release does the selected 5/1 ms timeout apply. Compare all 64 samples per
boot, same stage timings and byte oracles as the early matrix. Improvement
gate stays ≥20% median reduction in both pairs, candidate maximum ≤2× its
paired baseline maximum. No cross-clock subtraction, GPU/CPU speedup, FPS,
population tail, or checkpoint claim. Keep early and post-HOME data separate.

Run command, only after the signed build and guarded restore installation:

```sh
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_HOME_INSTALL1/warm-manifest.json --expected-manifest-sha256 MANIFEST_SHA256 --aux-post-boot --aux-poll-ms 5 --seconds 360 --tag GPU_HOME_A1
```

The runner copies the source manifest and its hash, exact argv/environment,
source snapshots, readiness events and the session/image review. The source
manifest carries all backing-chain and QEMU/firmware/trust-cache hashes.
All are checked before boot, and parent rechecks unchanged inputs afterward.
Auxiliary snapshot save/restore remains intentionally blocked. A readiness
failure is evidence about this pinned cold-boot baseline, not proof that
post-boot transport or GPU acceleration is impossible.
