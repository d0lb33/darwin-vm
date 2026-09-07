# Displayed QuartzCore pacing and dirty buffer uploads

2026-09-07. Exact iOS 27 24A5430a, iPhone17,3/T8140, original SPTM/TXM,
native SMC, isolated disk children of the retained migrated package. No
debugger or NVMe GPU transport. QEMU fc49f9c and the installed helper remain
unchanged from `gpu-surface-handoff-ios27.md`.

## Observed results

Evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-shared-pacing-20260907`.
`index.json` records the diagnostic files; `revision-binaries.json` separately
pins the staged signed bundles. `CA_PACED_COMPARE1/matrix.json` passed all five
jobs, independent pixel/completion checks, native display/input recovery and
final DCP DMA equality. The owned VM stopped and was reaped. The second Home
screenshot was inspected: the normal software home screen returned.

Actual guest CARenderer rendered into the retained screen-sized IOSurface.
Every frame acquired a lease, executed host Metal, sealed after GPU completion,
submitted native IOMFB, waited for native completion, then retired its lease.
Pixels were checked only after each timed batch; four ordinary one-pixel
CALayers supplied the DCP frame marker. This workload changes a solid background
and marker layers; it does not establish general system composition or glass.

The comparison alternated old full-block uploads (A), trimmed uploads (B),
old again, then a longer trimmed batch, in one boot. First two frames were
excluded from steady measurements, with absolute targets and no frame skipping.

| Job | Revision / frames | Work median / p95 / max ms | Native average fps | Work over 16.67 ms | Absolute deadline misses |
|---|---|---|---:|---|---|
| 1788763388140370 | A / 256 | 13.987 / 21.916 / 35.383 | 59.987 | 51/254 | 154/254 |
| 1788763510894245 | B / 256 | 11.599 / 17.793 / 41.254 | 59.992 | 16/254 | 47/254 |
| 1788763519154261 | A / 256 | 13.215 / 23.110 / 51.258 | 59.742 | 40/254 | 125/254 |
| 1788763526694089 | B / 1024 | 11.286 / 18.812 / 55.483 | 60.067 | 94/1022 | 227/1022 |

These average rates **do not prove smooth 60 Hz**. In the longer B run the
largest native scanout interval was 43.526 ms and accumulated deadline lateness
reached 105.198 ms. Guest audit/memory sampling is outside each work interval
but affects subsequent absolute targets. Setup/first-use remains separate:
first frames in this comparison took 1.384–1.606 seconds. Full distributions,
setup, stage intervals and resource samples are in `pacing-summary.json`.

Observed ordinary buffer upload bytes fell from 32,768 to a mean of 1,187.53
per steady frame. RPC count stayed five. Mean completion/seal time fell from
7.52–7.56 ms in A to 4.78–4.81 ms in B. This supports the benefit of trimming
these uploads; it is not a universal speedup claim for other scenes.
The longer B run retained a constant logical object/byte count during sampling,
ended at zero resources, and guest footprint grew 131,096 bytes between sampled
frames 2 and 1024. This short run is not a general leak proof.

`CA_PACED_GUEST1` remains an **overall failed trial**: four individually verified
jobs finished, but the previous 600-second lifetime expired during analysis
before the final installed control/recovery/explicit stop. Its 30 Hz job averaged
30.015 fps with a 329.445 ms maximum native interval. No timeout result has been
relabeled successful. Interactive sessions now use `--interactive`; automated
matrices retain 600 seconds.

## General implementation and scope

`driver_guest.m` now sends the contiguous changed span of each cached 32 KiB
buffer block. The backend already accepts bounded arbitrary offsets/lengths.
First use still uploads every byte; cache updates occur only after ACK; failure
and GPU writeback keep their previous invalidation rules. No scene/shader-name
branch was added. `test_dirty_buffer_range.c` passed 2,000 cases under ASan/UBSan,
including holes, endpoints, unchanged storage and reconstructed bytes.

The host audit drain now captures each CRC-checked raw slot before ACK. Offline
verification checks sequence, session, observed retention window, CRC, framing,
and the suffix still present in final RAM. More than two wraps, corrupted old
slots, missing sequences and mixed sessions/capture modes have negative tests.
Old short captures retain their original final-ring verification path.

| Coverage | Status |
|---|---|
| Traditional Metal profile v5 rendering subset | Implemented; no new capabilities advertised |
| Shared screen target, repeated fresh driver processes | Exact-guest verified |
| 256/1024 displayed solid-background batches | Pixels, GPU/native completion, ownership and final retirement verified |
| Smooth 60 Hz, long endurance, crash recovery | Unproven; slow frames remain |
| Shared-target alpha, image, clipping/transform scenes | Next exact-guest coverage batch; prior 64×64 offscreen evidence is separate |
| Arbitrary IOSurface import, device discovery, system compositor, original Liquid Glass | Not implemented/verified by this milestone |
| General compute, Metal 4, complete GPU-family conformance | Not advertised |

## Reproduction

Use the durable `gpu-quartzcore-handoff-ios27/control.json` and installed helper.
Build with `build_consumer_package.py BASE NEW --shared-surface --frames 1024
--hz 60`, sign using `sign_linked_revision.py` against the installed helper,
then stage via `runner_control.py` with the matching frames/rate and
`--shared-surface --surface-handoff`. `report_shared_pacing.py JOB...` independently
checks captured job evidence before summarizing it. `run_shared_matrix.py`
accepts the retained comparison plan and performs sequential verification,
installed control, native input/display recovery, explicit stop and final DMA
comparison. Its recorded plan uses historical `/tmp` build paths; use the
preserved signed job bundles and a matching rebuilt backend when those expire.

The backend retains macOS Metal and shared host pointer dependencies. The
guest wire contract remains explicit resource/command data and is suitable
for another backend implementation; Windows/Linux execution is untested.
