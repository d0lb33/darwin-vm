# Auxiliary transport latency experiment — exact iOS 27

## Preregistered experiment (2026-09-05)

Question: does shortening the host UART-loop mailbox poll timeout from 5 ms
to 1 ms reduce verified guest round-trip latency enough to justify keeping
this option? This is a byte transport experiment, not a GPU/CPU comparison.

Keep immutable QEMU3 (`f749c511de6be51b5e308136c9e4df6592e24714fc036211661ae6dda23f2049`),
24A5430a, T8140, SPTM/TXM, the original warm-input-v5 disk lineage, original
input service, and software rendering. Install one signed helper on a fresh
disposable child using the existing restore-VM installer. Never attach
System/Data to the host. Each trial gets a fresh child of that installed
parent and its own 64 MiB auxiliary file. No RAM restore, QEMU changes, or
GPU registration. Other running VMs are outside this experiment.

Fixed ABBA matrix, one guest at a time:

| Tag | Host select timeout | Guest workload |
|---|---:|---|
| GPU_LAT_A1 | 5 ms | 64 ordinary 4 KiB requests |
| GPU_LAT_B1 | 1 ms | identical |
| GPU_LAT_B2 | 1 ms | identical |
| GPU_LAT_A2 | 5 ms | identical |

Each helper begins at its existing Interactive launchd startup point after
the namespace inventory, metadata/session guard, and verified 1 MiB transfer
in each direction. There is no deliberate timeout in this workload. The
original 10-request timeout/recovery mode remains separately available.
These are early cold-boot observations, not a stable UI workload. Request
payload preparation is outside the RTT; response validation is inside.

Guest CLOCK_MONOTONIC measures request-write duration, sum/max reply reads,
sum/max actual nanosleep durations, reply verification, and total RTT. Guest
samples are buffered until batch completion/failure. Host monotonic_ns records
poll start, completed request read, completed validation, and completed reply
publication. Host samples are also buffered. Record actual poll gaps: UART
readability can wake select early, so its timeout is not an exact cadence.
Never subtract host and guest timestamps. Host process CPU time between first
request observation and last response publication measures the runner only;
it excludes QEMU and is not total transport CPU cost.

Stop the matrix on first failed run; do not substitute a retry. Each request
has a 5-second guest software deadline (a blocked call can overshoot), the
runner rejects 10 seconds without progress during the batch, and each boot
has a 180-second global deadline. Stop on error/short I/O, a CRC-valid wrong payload,
nonsequential request, helper restart, panic, UART closure, or incomplete
guest/host sequences 1–64. The runner freezes/captures/terminates only its
owned VM. Repeated reads of the same host mailbox are normal, not duplicate
requests. A read racing publication may have an invalid CRC and is ignored;
it cannot count as a successful request. The guest likewise retries an
incomplete response snapshot within its deadline and counts CRC retries.

Report every sample and per-run min/median/max, plus stage breakdowns and
actual host poll gaps. The prespecified improvement gate is at least a 20%
reduction in median RTT in both adjacent A/B pairs, with all bytes valid and
no candidate maximum greater than twice its paired baseline maximum. These
small, correlated batches cannot establish population tail latency or FPS.
If this gate fails, inspect the measured dominant stage before another change.
Do not promote a GPU path without a subsequent matched rendering comparison
including submission, completion, surface copy, and presentation costs.

Reproduction uses `tools/gpu/build_transport_inventory.sh`,
`prepare_guest_load.py --interactive-load`, `run_guest_install.py`, and
`run_guest_load.py MANIFEST --aux-latency --aux-poll-ms {5,1} --seconds 180 --tag TAG`.
Commands, source snapshots, and results will be preserved with the trial.

## Results and decision

**Proven:** all four trials exchanged and verified all 64 requests/replies,
plus the 1 MiB seed/output in each direction. **Disproven within this fixed
cold-boot matrix:** a repeatable improvement meeting the preregistered gate.
**Untested:** steady UI performance, GPU versus CPU speedup, and any causal
connection between the long guest calls and the current warm-boot changes.

| Run | Poll timeout | Guest RTT min / median / max, ms |
|---|---:|---:|
| A1 | 5 ms | 2.696 / 7.6885 / 2154.028 |
| B1 | 1 ms | 1.097 / 7.8890 / 3727.480 |
| B2 | 1 ms | 1.220 / 3.5675 / 5.866 |
| A2 | 5 ms | 4.117 / 6.8240 / 10.060 |

A2→B2 improves the median by 47.721%; A1→B1 worsens it by 2.608%.
Both paired maximum constraints pass; the overall gate fails on the first
pair's median. Terra's narrative initially miscomputed the first maximum
comparison. Parent review corrected it: 3727.480 < 2 × 2154.028. The collector
already computed the gate correctly, and its CSVs/JSON reproduce byte-for-byte.

The measured stage evidence, not a device/scheduler diagnosis:

* A1 sequence 41: 2154.028 ms total, 2149.035 ms inside guest read calls;
  a single read accounts for 2148.583 ms.
* B1 sequence 10: 3727.480 ms total, 3726.307 ms inside the guest write call.
* A1 sequence 64: one nominal 1 ms nanosleep takes 797.886 ms.
* Host request-read→reply-publication medians are 0.2089–0.2240 ms across
  the four boots. Actual accepted-request poll-gap medians are 7.239/1.519/
  1.519/7.434 ms in matrix order. These confirm that the poll setting changes
  host behavior, but do not explain the multi-second guest-call stalls.
* Runner CPU use over its active request interval is 0.97%, 1.51%, 7.12%,
  3.81% of one CPU respectively. These intervals differ greatly; QEMU and
  guest CPU costs are excluded. This does not establish an energy benefit.

An elapsed user-client call includes scheduling, kernel/driver work, device
emulation, and completion delivery; this instrumentation cannot separate
them. Likewise, the sleep record includes delay returning to the helper.
Guest RTT excludes request preparation and is not a frame time. No samples
were removed to make the 1 ms arm look better.

**Hold further VM experiments until the warm-boot work is merged**, per user
direction. Keep 5 ms as the existing default and 1 ms as an experimental
option. Do not enable a system GPU or replace software rendering. Retest the
same matrix on a fresh branch/worktree from merged `main`, revalidating the
auxiliary namespace patch against that revision and preserving the exact
24A5430a guest, SPTM/TXM and migrated lineage. Add a separately preregistered
post-readiness comparison if needed; do not silently compare these early
boots against a later workload release point. Warm-boot fixes may affect the
stalls, but that remains a hypothesis until the retest.

If long calls remain after that, the next diagnostic is auxiliary-only
timestamps at QEMU submission, block operation, CQ publication, and IRQ
delivery, correlated by command/sequence within the host clock. Compare
with the existing guest call intervals without cross-clock subtraction.
That experiment distinguishes a slow emulated transaction from time spent
before submission or after completion. API names alone cannot resolve it.
Before GPU integration, a matched guest CPU/host GPU rendering comparison
still has to include transfer, synchronization, surface copy and presentation.
The already-proven 64×48 copy kernel is a compatibility test, not evidence
that a small copy is profitable to offload.

## Reproduction and preserved evidence

Installed helper SHA-256:
`26513f7b8184a5935e6bd04011787f7c85f46ae0b5d5ba514fa365e52420afed`.
QEMU remains the immutable QEMU3 hash above; no QEMU source changed.

```sh
bash tools/gpu/build_transport_inventory.sh /tmp/dvm/GPU_LAT_BUILD2 --uc-probe --namespace-entitlement --class-exception
python3 tools/gpu/prepare_guest_load.py --build /tmp/dvm/GPU_LAT_BUILD2 --cache /tmp/dvm/WARM_RUNTIME_STAGE2/launchd-input.plist --system-tc /tmp/dvm/warm-input-v5/system.tc --output /tmp/dvm/GPU_LAT_STAGE2 --interactive-load
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/GPU_CHANNEL_ORIGINAL3.manifest.json --stage /tmp/dvm/GPU_LAT_STAGE2 --tag GPU_LAT_INSTALL2
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_LAT_INSTALL2/warm-manifest.json --aux-latency --aux-poll-ms 5 --seconds 180 --tag GPU_LAT_A1
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_LAT_INSTALL2/warm-manifest.json --aux-latency --aux-poll-ms 1 --seconds 180 --tag GPU_LAT_B1
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_LAT_INSTALL2/warm-manifest.json --aux-latency --aux-poll-ms 1 --seconds 180 --tag GPU_LAT_B2
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_LAT_INSTALL2/warm-manifest.json --aux-latency --aux-poll-ms 5 --seconds 180 --tag GPU_LAT_A2
python3 tools/gpu/collect_aux_latency.py /tmp/dvm/GPU_LAT_A1 /tmp/dvm/GPU_LAT_B1 /tmp/dvm/GPU_LAT_B2 /tmp/dvm/GPU_LAT_A2 --output /tmp/dvm/GPU_LAT_COLLECTION1/ABBA
```

Use new output tags on reproduction; tools reject existing outputs. Both
restore installers (BUILD1 and corrected BUILD2) stopped on the verified
`GPU_LOAD_INSTALLED` marker: 506 serial lines, 0 panics, reached shell yes.
BUILD1 was superseded before any latency trial to add response-race handling.
Host tests: 30 project regressions and 5 auxiliary protocol tests passed,
including a real C/Python incomplete-response injection and the unchanged
10-request timeout/recovery test. Guest imports and signatures verified.

Durable archive:
`~/dvm-artifacts/research/gpu-latency-ios27-20260905/`.
It contains per-run serial/wire logs, source snapshots, manifests, raw owned
auxiliary media, all 256 guest and host samples, Terra collection, parent
replay, preregistration, helper/TC, and the final review. Large System/Data
overlays and RAM are excluded. Existing QEMU3/DT artifacts are also preserved
in the earlier `gpu-transport-ios27-20260905` archive.

Parent verified the complete installed-parent/migrated backing chain and
every pinned QEMU input unchanged after the matrix, original launchd cache
unchanged except the isolated helper, and all four owned QEMU PIDs reaped.
A2 interleaved its one helper-start marker with AMFI text; parent checked
embedded markers and corrected the runner's restart detector after the
matrix. The captured runner remains the exact executed version. The marker
fix needs a new runtime check at retest; it did not alter these measurements.
