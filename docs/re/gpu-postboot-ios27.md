# Post-home-screen latency gate — exact 24A5430a

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
