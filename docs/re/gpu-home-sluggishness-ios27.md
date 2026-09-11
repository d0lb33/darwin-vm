# Why the accelerated home screen is sluggish — 24A5430a, 2026-09-07

Question asked: with the backboardd Metal driver registered, what makes the
iOS home screen sluggish — the driver we built, or TCG? Guest: exact iOS 27
24A5430a, iPhone17,3/T8140, the `gpu-cold-warm-home-20260907/control.json`
package (V28 driver built into backboardd, `driver_host` V28 worker, pinned
QEMU `6557fe86…`), fresh disposable disk children, no RAM restore, no
debugger. Host: M5 Max, 18 cores, macOS 27. Another guest (the user's
`CELL_SERVICE3` session, `-name "DVM Input Manual"`, ~60% of one core) ran
throughout and was not touched; treat every host number as slightly inflated.

Durable evidence: `~/dvm-artifacts/research/gpu-home-sluggishness-20260907/`
(`PERF_HOME1/`, `PERF_HOME2/`, `manifests/`). The disposable originals were
`/tmp/dvm/PERF_HOME1` and `/tmp/dvm/PERF_HOME2`. Tools written for this:
`tools/gpu/perf_window.py`, `tools/gpu/report_rpc_gaps.py`,
`tools/gpu/report_gesture_timeline.py`, `tools/gpu/crc_encode_bench.c`, and
`capture_home_trial.py --input` for traced swipes/taps.

## Answer

The host GPU is not the problem. In every window the Metal worker's service
time, GPU execution included, is 0.6-2.7% of wall time and the worker process
uses 0.5-0.9% of one core. Three things are, in this order:

1. **Guest CPU under TCG doing work that is not the driver.** Between a frame's
   `renderSubmit` and the next frame's first RPC the compositor spends 16.6 ms
   on the lock screen and 28-38 ms during a page scroll, with no host request
   outstanding. After a swipe, the first compositor request arrives 465 ms
   (warm page) to 570 ms (cold page) after the first touch record, and a cold
   page has a 2.25 s window with **no** compositor requests at all while
   SpringBoard rasterises the new icons. Tapping Settings, the launch
   transition starts 2.6 s after the tap. The four efficiency-cluster vCPUs run
   at 62-75% each the whole time (300% of the host for an idle home screen).
2. **The driver's guest side.** Every request and reply is JSON with base64
   payloads, CRC-32'd bit-serially in the guest
   (`system_bootstrap.m:9-12`, used at `driver_mmio_transport.inc:102` and
   `:118`), then spin-waited on MMIO. Measured: 3.9-4.8 ms of guest time per
   44 KB icon-texture chunk (a cold page swipe uploads 165 of them, 0.73 s),
   5-7 ms before each `renderSubmit` during a scroll, and about 1 ms floor per
   tiny RPC. The whole encode path costs about 0.3 ms per chunk natively at
   the bundle's `-O1`; which part of it dominates under TCG is not measured
   (section 4). On a 7-RPC lock-screen frame the inter-RPC gaps are roughly
   8 of 29 ms.
3. **The host session daemon, which was growing.** `session_cli.py serve`
   scanned every RPC record of the session twice per loop iteration
   (`generation_counts()` and `SessionPeer.status()`) and ran that
   bookkeeping between every single pump. The gap between two consecutive
   tiny RPCs grew from 0.97 ms to 6.6 ms over 21.6k records (401 s) while the
   host service stayed at 0.06 ms; per-frame intervals grew from 29 ms to
   54 ms with identical guest work. Fixed in this branch (incremental counts,
   burst pumping): flat 1.4-1.6 ms across 10k records, daemon CPU 29% -> 5%.

So: TCG is the multiplier on everything, the driver puts a large amount of
work (encoding, CRC, spinning) on the slow side of that multiplier, and the
development daemon added a leak-shaped latency on top. The Metal work itself
is negligible.

## 1. Sessions and what each measured

| Session | Daemon | State reached | Windows |
|---|---|---|---|
| `PERF_HOME1` | unpatched | lock screen only (two Home presses at +130 s only woke the cover sheet; a swipe put the display to sleep) | 20 s lock-screen window with `sample`; daemon `sample`; 401 s / 21,614 RPC journal |
| `PERF_HOME2` | patched | home screen via the automatic Home press 64 presentations after the first frame (same as `CA_DEV_HOME1`) | two 30 s idle windows with `sample`; cold swipe to page 2; warm swipe back; Settings tap; 508 s / 10,235 RPC journal |

Reaching the home screen only worked by pressing Home within seconds of the
first frame, from the boot cover sheet that still shows the charging banner.
Two Home presses two minutes later did not unlock, and a 250 ms 20-point swipe
up was followed by `A484 display power 1 -> 0`. This is the same wall
`gpu-cold-warm-home-ios27.md` hit.

## 2. Host side is cheap and stays cheap

`perf_window.py` (`ps -M` per thread, `cputime` per process, macOS `sample`):

| Window | QEMU % | vCPU 0-3 each | daemon % | worker % | frames/s | frame p50 | RPCs/frame | host service/frame |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lock screen, `PERF_HOME1` | 255 | 62% | 29.2 | 0.9 | 14.0 | 41.9 ms | 7 | 1.78 ms |
| home idle 1, `PERF_HOME2` | 303 | 74% | 4.9 | 0.5 | 10.3 | 100.0 ms | 2 | 1.07 ms |
| home idle 2, `PERF_HOME2` | 300 | 73% | 5.1 | 0.5 | 10.1 | 99.9 ms | 2 | 1.10 ms |

vCPUs 4 and 5 sit at the WFI return as documented in `tcg-idle-profile.md`.
GPU execution per submission (`gpu_us` in the reply) is 33-250 µs; the
`renderSubmit` host service of 0.9-1.5 ms is JSON parsing, encoding and
`waitUntilCompleted`. Two host-side exceptions worth knowing: `function` and
`renderPipeline` cost 5-8 ms each when a new shader appears (the Settings
launch created 10 functions and 9 pipelines in 500 ms, 138 ms of host
service), and the first `renderSubmit` of a scroll took 9.2 ms.

The 100 ms idle cadence is not a limit. The thumbnails show 0.005-0.008% of
sampled pixels changing per frame (a status-bar-sized animation), and the
same guest presented lock-screen frames every 17-19 ms in `CA_DEV_HOME1`.

## 3. Where the guest time goes, per frame

`report_rpc_gaps.py` on `driver-host.jsonl` (host timestamps; a "gap" is
previous completion -> next reception, i.e. everything the guest and the
transport do between two requests).

Lock screen, `PERF_HOME1`, first 2,000 records vs last 2,600:

| | first 2,000 | last 2,600 |
|---|---:|---:|
| frame interval p50 | 29.0 ms | 54.3 ms |
| host service per frame | 1.73 ms | 2.03 ms |
| submit -> first RPC of next frame (guest compositor work) | 16.6 ms | 17.5 ms |
| RPCs per frame | 7 | 7 |
| bytes CRC'd per frame | 11.2 KB | 11.6 KB |

Everything that grew is inter-RPC gap, i.e. host daemon latency (section 5).
At the start of the session a frame is 16.6 ms of compositor work + about
7 ms of RPC round trips + about 2 ms host service + a few ms of encoding.

Warm page scroll, `PERF_HOME2` `swipe2`, per animation frame (13 frames at
41 ms p50): `writeRenderBuffer` of 36-44 KB arrives 28-38 ms after the
previous submit, a 100-200 B buffer 1 ms later, `renderSubmit` of 6-10 KB
5-7 ms after that (the guest building and CRC'ing the command stream), host
service 1.5-2 ms. About a fifth of a scroll frame is the driver's guest-side
encoding; the rest is UIKit/CA layout under TCG.

Cold page scroll, `swipe1`, 250 ms bins after touch-down: 0-0.5 s three small
frames; 0.5-1.75 s resource creation and 165 `writeTextureChunk` (20 icon
textures, 44 KB base64 each, 3.9 ms p50 gap, 0.22 ms service); 1.5-2.75 s the
page's frames (30 identical frames at 30 ms); **2.75-5.0 s no compositor
request at all**; 5.0-5.5 s another 11 chunks and the icon labels. The
2.65 s presentation gap is the guest rasterising icons, not the driver.

## 4. The driver's guest-side cost, quantified

Guest-side gap between two consecutive chunks of the same texture (nothing
but reply decode, `subdataWithRange`, base64, JSON, bit-serial CRC, memcpy
and the doorbell in between):

| Run | chunk -> chunk p50 | n |
|---|---:|---:|
| `CA_DEV_HOME1` (offline, run_system_boot.py) | 4.05 ms | 267 |
| `PERF_HOME1` bucket 0 (unpatched daemon) | 8.69 ms | 72 |
| `PERF_HOME2` (patched daemon) | 4.13-4.79 ms | 437 |

`crc_encode_bench.c` (same CRC code, host native, `-O1` like the bundle):
bit-serial CRC 5.5 ns/byte, so 0.24 ms per 44 KB chunk and 20.9 ms for the
3.55 MB of base64 that `CA_DEV_HOME1`'s 648 ms Home hitch uploaded (88
chunks); a table CRC is 3.4x cheaper, base64 itself is 0.003 ms per chunk.
The Foundation part of the path (`subdataWithRange`, `NSJSONSerialization`,
the reply parse) was not benchmarked. How the 3.9-4.8 ms splits between the
CRC loop and the Foundation code is **not known**: on this host TCG runs a
tight integer loop at 1.02x native but pointer chasing at 1.45x and
load/store at 2.79x (`hvf-performance-checkpoint.md`), and ObjC/JSON code is
the latter kind, so the CRC may well be the minority. The whole path is the
driver's either way; the honest split is a revision B with a table (or libz)
CRC staged through `backboardd-driver-reload-ios27.md`, measured with
`report_rpc_gaps.py`.

The spin-wait (`driver_mmio_transport.inc:109-114`) burns the calling vCPU
for the whole RPC: two MMIO reads plus `clock_gettime` per iteration, each
MMIO read taking the BQL (`system/physmem.c:3192`, no `lockless_io` on this
region). At 20-70 RPC/s and about 1 ms per RPC that is 2-7% of one vCPU plus
BQL churn; `host_mutex_wait` is 11-13% of every busy vCPU's samples. Small
next to the encoding cost, but it is why the daemon's latency (section 5)
turned directly into guest CPU.

The 1 ms floor per tiny RPC (`resourceProcess` -> `resourceProcess`, 0.69-1.0
ms with the patched daemon, host service 0.03-0.05 ms) is guest ObjC/JSON
work under TCG plus four host hops (QEMU main loop, Python daemon, worker
pipe, back). Splitting it further needs QEMU-side doorbell/done timestamps;
not done.

## 5. The daemon leak-shaped latency, and its fix

`report_rpc_gaps.py` growth table, p50 of the guest-side gap in ms (n):

| records | `PERF_HOME1` rp->rp | `PERF_HOME1` wrb->submit | `PERF_HOME2` rp->rp | `PERF_HOME2` wrb->submit |
|---:|---:|---:|---:|---:|
| 0-2k | 0.97 (569) | 3.69 (290) | 1.01 (251) | 1.62 (373) |
| 4-6k | 1.70 (738) | 3.96 (376) | — | 1.57 (902) |
| 8-10k | 2.63 (724) | 3.94 (384) | 0.69 (12) | 1.40 (659) |
| 12-14k | 3.47 (707) | 4.47 (382) | | |
| 20k+ | 6.61 (463) | 6.84 (201) | | |

`resourceProcess` host service stayed 0.056-0.082 ms throughout, and
`submit -> resourceProcess` (guest work) stayed 16.3-17.9 ms, so the growth is
host-side. The daemon `sample` (`PERF_HOME1/daemon-sample.txt`, 8 s, no RPCs
in flight): 4,906 of 6,576 samples in `select`, **975 in `builtin_sum` over a
generator** — `generation_counts()`'s scan of every record, called from
`publish_status()` every iteration — and the loop ran at 32.5 iterations/s,
i.e. 11 ms of work per 20 ms `select` timeout. `SessionPeer.status()` did a
second full scan for `host_errors`. Because `pump()` served one request per
iteration, every RPC of a burst paid the bookkeeping.

Fix in this branch: `Session.render_submissions_total()` and
`SessionPeer.scan_host_errors()` scan only new records, and `serve()` drains
a burst (`select` with 2 ms timeout, up to 64 pumps) before the bookkeeping.
Same package, same worker, same pinned QEMU; only the daemon changed. 30
session tests and 84 project regressions pass.

## 6. Input path

`input-status.json` after the swipes: helper ack 31.7 ms mean, 78 ms max per
event; HID dispatch 2.2 ms last, 24.8 ms max; 21 of the moves coalesced.
Neither swipe produced a frame during the 345-398 ms drag; the first moving
frame came 464 ms (warm) and 781 ms (cold) after the first touch record,
already in the deceleration animation. The events reach the guest at roughly
30 ms each and the gesture is processed as a burst; whether the remaining
delay is the helper, IOHIDEventSystem or SpringBoard needs guest-side
timestamps that this run did not have.

## 7. TCG categories (macOS `sample` of QEMU, vCPU 0-3 average)

| Category | lock screen | home idle |
|---|---:|---:|
| translated code and other helpers | 40.8% | 34.3% |
| MMU translation / TLB fill | 12.0% | 10.4% |
| translated-block lookup | 11.6% | 11.0% |
| host mutex wait (BQL) | 11.1% | 12.7% |
| memory access slow path (incl. MMIO) | 7.3% | 7.9% |
| host condition wait | 6.7% | 4.9% |
| fp16 conversion softfloat | 2.9% | 6.3% |
| fp/SIMD helpers softfloat | 2.5% | 5.8% |

The home screen spends 12% of vCPU time in emulated half-float and SIMD, the
lock screen 5%: icon and RGBA16F surface handling in the guest. The rest is
the same TCG profile as `tcg-idle-profile.md`.

## 8. Not the cause, and other observations

* Not the host Metal work (section 2), not the IOMFB/DCP path (D594
  completion 0.4-1.4 ms after every presentation, no DCP samples), not the
  10 Hz idle cadence (content-driven).
* HMP `stop` on this QEMU exports 36 MB of RGhA/BGRA witness per stop
  (`darwin_iomfb.c gpu_present_stopped`), so vCPU PC sampling perturbs the
  guest; it was disabled after the first window.
* Settings launch: content stays black because the driver rejects the app's
  fullscreen texture 95 times — `GPU_LOAD_TEXTURE_REJECT reason=descriptor
  type=2 width=1179 height=2556 format=80 storage=0 usage=1`. A coverage gap,
  not a performance item.
* The lock screen presents 0-60 frames/s depending on what animates; a
  3.2 s presentation gap on the lock screen coincided with zero RPCs and is
  idle, not a stall.

## 9. What to do next, in order of measured payoff

1. Guest driver revision B (through the reload mechanism): table or libz
   CRC-32 (`libz.1.dylib` is in the shared cache), raw bytes for
   `writeTextureChunk` / `writeRenderBuffer` through an owned staging region
   of the 16 MiB shared RAM instead of base64 inside JSON, one request per
   texture instead of one per 32 KiB. Expected: the 0.73 s per cold page and
   the 5-7 ms per scroll frame mostly disappear. Re-measure chunk -> chunk and
   wrb -> submit gaps with `report_rpc_gaps.py`.
2. Replace the MMIO spin with an interrupt or at least `WFE`/`usleep`, so a
   waiting RPC does not cost a vCPU and BQL round trips.
3. Keep the daemon fix; move the peer's RPC path off the bookkeeping loop
   entirely (thread or process) so no future bookkeeping can land in it.
4. The remaining floor is compositor/SpringBoard code under TCG: the TCG
   items in `tcg-idle-profile.md` (TB lookup, ASID flushes, BQL) apply, and a
   guest thread census during a scroll would say which process to look at.

## Reproduction

```sh
# Session with the automatic Home press 64 presentations after the first frame.
python3 tools/gpu/session_cli.py start <manifest> --session /tmp/dvm/TAG \
  --worker <V28 driver_host> --library <QuartzCore.metallib> --library-cache <cache> \
  --home-after-presentations 64 --min-presentations 128 --boot-seconds 360
python3 tools/gpu/perf_window.py --session /tmp/dvm/TAG --out /tmp/dvm/TAG/win-idle \
  --label idle --seconds 30 --host-sample 10 --report
python3 tools/gpu/capture_home_trial.py --session /tmp/dvm/TAG --out /tmp/dvm/TAG/swipe1 \
  --seconds 6 --input "--px --hold-ms 250 --steps 20 swipe 1000 1400 200 1400"
python3 tools/gpu/report_gesture_timeline.py /tmp/dvm/TAG/swipe1    # from tools/gpu
python3 tools/gpu/report_rpc_gaps.py /tmp/dvm/TAG/run/driver-host.jsonl
cc -O1 -o /tmp/dvm/crc_bench tools/gpu/crc_encode_bench.c && /tmp/dvm/crc_bench
```

The manifest must set `DARWIN_DCP_TRANSITION_TRACE_DIR` to the session's
`run/` directory and `DARWIN_INPUT_TIMING=1`; `manifests/` holds the two used.

## 10. Follow-up (2026-09-07, later): staged transfers, RAM polling, fast TCG build

The three "next payoff" items above were implemented in this order and each
was measured against a same-window baseline, because two other workloads
shared the host during these runs (`spotlightknowledged.updater` at ~200% of
a core after the evidence copy above, and another agent's HVF matrix from the
arm-native-experiments worktree). Absolute numbers therefore differ slightly
from sections 2-5; the comparisons below were taken back to back.

### 10.1 Raw bytes through a shared-RAM staging region (contract 29)

Design, all in `tools/gpu/`: the guest copies raw payload bytes into an owned
1.875 MiB region of the mode-3 shared RAM at `0x20000` (unused: the framed
request stays under 64 KiB at `0x10000`, replies start at `0x200000`), and the
framed JSON request carries `staged{offset,length,crc}` instead of a base64
`data` field. The Python peer (`driver_mmio_peer.py`) verifies the CRC-32 with
zlib and hands the unchanged worker the same base64 field it always accepted,
so `driver_host.m` only needed its per-request bounds raised (`kMaxFrame`
4 MiB, chunk and buffer writes up to `DVM_STAGING_BYTES`). The peer publishes
a descriptor at `0x340` (`DVMSTAG1`, offset, bytes, flags) before READY;
`openMMIO()` reads it and `system_bootstrap.m` installs a `stagedTransport`
block on the device; `DVMUploadTextureChunks` sends one request per texture
and `DVMUploadBufferChanges` one request per dirty span. Both fall back to the
old 32 KiB base64 chunks without a descriptor. The guest CRC is table-driven
(`system_bootstrap.m`). `DVM_TRANSPORT_FLAGS` (bit 0 staging, bit 1 RAM
polling) selects the descriptor flags for A/B runs.

Package: `STG_BUILD1` -> `STG_STAGE1` -> `/tmp/dvm/STG_INSTALL1/warm-manifest.json`
(same registry kernel, disk lineage and pinned QEMU `6557fe86…` as the V28
package; guest bundle and `STG_HOST1/driver_host` built from this tree).
Host and peer tests: 34 + 9 pass, including a 333 KB single-request icon and
a 64 KiB staged buffer write through the real worker.

Same-window results (`BASE_HOME3` is the unchanged V28 package):

| | `BASE_HOME3` (base64 chunks) | `STG_HOME1` (staged) | `STG_HOME2` (staged + RAM poll) |
|---|---:|---:|---:|
| requests per icon texture (p50) | 11 | 1 | 1 |
| guest-side ms per icon texture (p50 / p90) | 57.9 / 165.8 | 4.0 / 8.2 | 6.4 / 17.6 |
| host service ms per icon texture (p50) | 2.39 | 1.34 | 1.50 |
| framed JSON traffic | 155 KB/s | 28 KB/s | 30 KB/s |
| `writeRenderBuffer` -> `renderSubmit` gap (p50) | 1.58 ms | 1.23 ms | 1.28 ms |
| idle home screen: QEMU %, daemon %, frames/s | 310, 4.2, 10-11 | 310-318, 4.3, 10-12 | 313-315, 4.3, 10-12 |
| cold page swipe: first moving frame | 573 ms | 628 ms | 623 ms |
| warm swipe back: first moving frame / p50 interval | 258 ms / 39.4 ms | 270 ms / 36.6 ms | 252 ms / 36.7 ms |

So the transport cost that section 4 quantified is gone: an icon costs 4-6 ms
of guest time instead of 46-58, a cold page issues about 10 upload requests
instead of 110-165, and framed traffic drops 3.4-5x. What the user sees
barely moves: first movement after a swipe and the per-frame scroll cost are
UIKit/CoreAnimation work under TCG (section 3), and the cold page's silent
icon rasterisation (section 3) is untouched. This is the ordering the
diagnosis predicted. The remaining base64 users are the small `upload` op
(textures at or under 32 KiB, inline submit uploads), `readRenderBuffer`
replies, and `renderStageChunk` for submissions over 60 KB; all are rare.

### 10.2 RAM polling instead of the MMIO spin (flag bit 1)

With bit 1 set, `call:` waits on the reply header's sequence word in shared
RAM (`0x80+16`), which the peer writes before it acks QEMU, executing
`yield` between reads and checking `REG_ERROR` and the timeout only every
1024 iterations; it then reads `REG_DONE` once, so the reply is still gated
on QEMU's CRC check and the ack's ordering. Under TCG this removes the
per-iteration MMIO exit and BQL round trip (`system/physmem.c:3192`).
Measured effect: none visible. Frame timing (`STG_HOME2` vs `STG_HOME1`
above) and the swipe-window TCG categories are within noise
(`host_mutex_wait` 11.4% vs the baseline's 10.6%, `memory_access_slow_path`
5.3% vs 4.9%, all vCPUs). At 20-115 RPC/s with about 1 ms per RPC the spin was
2-7% of one vCPU, and that is what the change saves; it is kept on because
under HVF every MMIO poll would be a full exit. A userspace `wfe` cannot
sleep at EL0 under TCG (it is a yield) and `wfi` is trapped, so a real
interrupt would need a blocking selector in the kernel shim plus an AIC
vector on the transport node; not done here.

### 10.3 TCG items from `tcg-idle-profile.md`

Two of the listed items are bounded enough to try in a session; the rest are
not, and are left as they were listed.

**O3/LTO build** (`tools/build_qemu_fast.sh`): the pinned package binary
already carries the inline-PAuth and lock-free counter changes (it boots
the restore ramdisk to a shell in 3.53-3.76 s, the figure the idle-profile
note recorded for the fast build, not the 4.4 s it recorded for plain O2).
`build-fast` ranks 1.5-4% faster (`tools/time_boot.py --dtree firmware/dtree
--repeat 3`, two alternating rounds: 3.70/3.59 s vs 3.76/3.74 s medians),
below what a home-screen window can resolve. Not repinned.

**Cheaper jump-cache invalidation** (`accel/tcg/tb-jmp-cache.h`,
`translate-all.c`, `cpu-exec.c`): `tcg_flush_jmp_cache()` now bumps a
per-cache epoch instead of clearing every entry, and entries hit only when
their epoch matches; specific-entry invalidation and the `CF_INVALID` check
are unchanged. This removes the 1 MiB-per-flush cost that made the 2^16
cache a loss before, and lets the size be measured on its own:

| jump cache | restore boot to shell, median of 3-4 (pinned binary interleaved) |
|---|---:|
| pinned (2^12, clearing flush) | 3.53-3.64 s |
| epoch flush, 2^12 | 3.61 s (neutral) |
| epoch flush, 2^13 | 3.65 s |
| epoch flush, 2^14 | **3.44 s** (about 5% better) |
| epoch flush, 2^15 | 3.51 s |
| epoch flush, 2^16 | 4.05-4.13 s (15% worse: footprint, and 4x larger per-page clears) |

The tree now defaults to 2^14. Validation: `STG_HOME4` (same staged package
and flags as `STG_HOME2`, QEMU = this tree's O2 build with the epoch cache)
booted to the home screen with 951 presentations and no failures; the three
`tools/re/smp_smoke.py` modes this worktree has (plain, `--wfe`,
`--cross-cluster`) pass (`followup/smp_smoke.log`); the `--pauth-cache verify`
mode lives only in the tcg-idle-perf worktree.
On the home screen it is neutral: the vCPU 0-3 translation-block-lookup
share is 8.4-10.9% against 9.5-10.2% for the pinned binary, idle QEMU stays
at 310-312%, and warm swipes are within the run-to-run spread (first moving
frame 207/268 ms, 33-40 ms per frame). The 1.45M live TBs of an idle guest
(`info jit`) simply do not fit any direct-mapped cache; the boot-time win is
real but small.

**Not attempted, still listed:** an ASID-tagged softmmu TLB (MMU refill
after ASID-change flushes, 9-11% of busy vCPU time here), the BQL re-take on
every `cpu_exec` exit and the exclusive sections for broadcast TLBI
(`host_mutex_wait` 11-15%), and native fast paths for the fp16/SIMD softfloat
helpers (14-18% on the home screen). Each is a QEMU-core change with its own
correctness argument, not a session-sized item.

### 10.4 What the follow-up changed about the answer

Nothing in the ordering. Removing the driver's guest-side encoding and the
MMIO spin took the transport out of the picture (icon uploads 11x cheaper,
framed traffic 3-5x lower) and left first-movement latency, scroll frame
time and cold-page rasterisation where they were, because those are UIKit,
CoreAnimation and SpringBoard code executing under TCG. The evidence for
this section is under `~/dvm-artifacts/research/gpu-home-sluggishness-20260907/followup/`
(sessions `STG_HOME1`, `STG_HOME2`, `STG_HOME4`, `BASE_HOME3`, the time_boot
logs, the contract-29 bundle and worker, and the epoch-cache QEMU).
