# UIKit on the owned display surface

2026-09-07, exact iOS 27 24A5430a, iPhone17,3/T8140. This extends the
[verified offscreen scene](gpu-framebuffer-feedback-ios27.md) to actual native
IOMFB/DCP presentation. The custom device remains process-local. The system
compositor and lock screen retain software rendering; Liquid Glass and normal
Metal device discovery are still untested.

## Implementation and isolation

`consumer_shared_probe.inc` now accepts the actual UIKit view tree through
the existing owned shared IOSurface. QuartzCore scales its 320×480 logical
content by three onto a 1179×2556 target. `--uikit-animate` alternates the card
position and opacity. The production driver/backend needed no scene or shader
special cases and no new capability claims. The normal acquire → GPU seal →
native swap/wait → retire sequence is unchanged. There is no per-frame target
copy or verification readback. Final pixels are checked after the timed batch.

`--uikit-reference` pins a native full-screen reference and its manifest/log
hashes before queueing, then stages immutable copies with the job. Native
UIKit/QuartzCore composes the actual guest glyph inputs, with recorded layer
scales and continuous corners, through native Metal. It does not consume the
guest's output or command stream. The checker requires the current guest to
produce the same three A8 inputs, checks the complete frame within the existing
two-byte threshold, and separately checks ownership, completion and retirement.
Reference failure is not a GPU pass.

The old host runner cached its verifier modules. It could not accept the new
scene without restarting. `RunnerPeer` now runs `verify_runner_job.py` in a
fresh process for each completed job with a 30-second verifier deadline.
Changing host verification code therefore does not require another boot.
Missing/failed/expired verification stays failed; shared failures still stop
pool reuse. This change required one new runner session, not a guest driver
loading workaround.

The old `CA_UIKIT_GUEST3` was explicitly stopped and reaped. Its overall
session still reports the historical required-test failure; later individual
passing jobs do not erase it. New **CA_UIKIT_DISPLAY_GUEST1**, QEMU PID
**85605**, booted a fresh child of the same pinned runtime-loader package.
SPTM/TXM, native SMC, migrated ancestry and display/input were retained. QEMU
was not rebuilt/relinked, and no other VM or baseline was modified. Its
interactive lifetime remains unlimited; readiness/test/GPU/verifier deadlines
are separate. The session remains available for further tests.

## Exact guest results

| Job | Guest PID | Workload | Result |
| --- | --- | --- | --- |
| 1788777028338455 | 304 | Original installed 64×64 red control | Pixels/completion/retirement pass through fresh verifier |
| 1788777100058322 | 347 | Three displayed UIKit frames | Full-frame reference, ownership, native completion, handoff and retirement pass |
| 1788777246280128 | 476 | 128 displayed changing UIKit frames at 60 Hz | Final pixels, all ownership epochs/completions, handoff and retirement pass |
| 1788777312203978 | 554 | 1,024 displayed changing UIKit frames at 60 Hz | Same checks pass; 1,024 actual render submissions, three passes each |

All jobs read the unchanged exact-guest AIR, SHA256
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
Signed revisions were staged from Data into fresh guest processes; no debugger
or NVMe GPU transport was involved. BUILD2/3/4 linked binary hashes are:

```text
01bf7d575f04e27129d65eaf8e367d0876be101fc374211ed48abef7ba76948d
d9376dda3179aa661a23eff6b369f55d852a9860a3e520377fd940589a846e01
ef2230d415f3bf620d5297b8ef0bcd0dd3e1e79aaecdfad91abf9b1e38c88d19
```

The 1,024-frame capture has 512 odd frames with 15 draws targeting
`[1,14,1]` and 512 even frames with 17 draws targeting `[34,33,1]`. This is
actual changing GPU work, including private intermediates, not merely changed
frame markers. Final pixels match the native reference within **one byte per
channel**, with zero channels beyond two. Intermediate frame pixels are not
read back individually. The final tightly packed guest image hash is
`2f124e981ce39f7cbdbb3cb5b4e3f124ec78903a6d1aae94aeb2f800e29397b8`.

For the first three-frame job, the actual **12,432,384-byte DCP pixel DMA
export** equals the verified shared IOSurface byte-for-byte, including stride.
SHA256: `c2f00aab9c303f30fc42575cb2fecc7009217151dd47df0e9176fa2970f1c10b`.
The bounded one-shot export paused/resumed the VM in 8.660 ms. The 128/1,024
jobs have native per-frame scanout identities/completions and final shared
pixels; their final DCP bytes were not exported again. The QEMU export is
one-shot, and those results retain `final_scanout_export_checked=false`.

The unretouched DCP image shown in chat is preserved as
`~/dvm-artifacts/research/gpu-uikit-display-20260907-first-dcp/scanout.png`,
alongside raw bytes and provenance. The only conversion removes row padding
and reorders BGRA channels for lossless PNG; no scaling/cropping/retouching.

## Pacing, memory and fallback

Setup/first-use costs are excluded from the sustained statistics, but reported:

| Measurement | 128 frames | 1,024 frames |
| --- | ---: | ---: |
| Setup | 930.101 ms | 884.245 ms |
| First two frame work times | 261.359 / 116.109 ms | 222.767 / 60.918 ms |
| Measured frames | 126 | 1,022 |
| Completed throughput | 60.068 fps | 59.998 fps |
| Mean frame work | 14.107 ms | 13.345 ms |
| p95 / p99 / max work | 21.599 / 30.238 / 36.103 ms | 16.781 / 20.602 / 29.743 ms |
| Work beyond 16.67 ms | 14 | 55 |
| Absolute deadline misses | 56 | 218 |
| p95 / max native scanout gap | 23.729 / 31.915 ms | 21.965 / 34.177 ms |

The longer batch lasts 17.034 seconds excluding setup/first use. Live resource
objects/bytes do not grow between the frame-2 baseline and frame 1,024.
Guest RSS grows 81,920 bytes and footprint 114,712 bytes; all driver resources
retire afterward. p95 stage times include 4.653 ms encoding, 5.935 ms seal,
and 3.557 ms display enqueue; these separate distributions are not additive
p95 totals. No same-scene CPU performance comparison was run here. Average
60 fps does not establish native scrolling or smooth 60 Hz pacing.

After the short job, normal presentation/completion and fresh input recovery
pass. The first post-1,024 recovery observer **times out**: input ACKs continue
but the display is powered down and its presentation count is flat. A Home
event gets two successful HID acknowledgments and fresh normal presentation/
D594 completion, passing the second observer, but before/after screenshots
remain black. A subsequent native Power event with a five-second observation
shows the actual software lock screen again, including its charging indicator.
`recovery-power-after.png` was visually inspected. The original timeout and
black screenshots remain evidence; a transport ACK alone was not accepted as
visible UI recovery. No guest code or display patch was applied to wake it.

## Validation and reproduction

BUILD1 first fails compilation because the iPhone SDK provides
`IOSurfaceRef.h`, not the umbrella `IOSurface.h`; the portable specific header
fix builds successfully. The reference checker initially missed small A8
uploads embedded in `renderSubmit`; the host fixture caught this before guest
execution, and the checker now handles both captured upload forms. Existing
64-frame shared evidence passes the updated verifier. Runner/deadline and
reference negatives pass; the generic non-UIKit shared consumer also compiles.
No unchanged production GPU tests were rerun just to inflate the check count.

```sh
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
python3 tools/gpu/run_guest_load.py ~/dvm-artifacts/gpu-quartzcore-regions-ios27/control.json --tag CA_UIKIT_DISPLAY_GUEST1 --interactive --driver-mmio --driver-present --driver-consumer --driver-runner --driver-wait-display --driver-worker ~/dvm-artifacts/gpu-quartzcore-regions-ios27/installed-build/driver_host --library-cache ~/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib
python3 tools/gpu/build_consumer_package.py BASE BUILD --frames 1024 --hz 60 --shared-surface --uikit --uikit-external-reference --uikit-animate --renderer-flags 2
python3 tools/gpu/run_uikit_host.py REFERENCE --guest-rasters GLYPHS --display-frame 1024 --display-animate
python3 tools/gpu/sign_linked_revision.py BUILD/DVMProxy.bundle LINKED --parent ~/dvm-artifacts/gpu-quartzcore-regions-ios27/installed-build/dvm-gpu-load
python3 tools/gpu/runner_control.py /tmp/dvm/CA_UIKIT_DISPLAY_GUEST1 --bundle LINKED/DVMProxy.bundle --mode data --development --test package --frames 1024 --hz 60 --shared-surface --surface-handoff --expected pass --worker BUILD/driver_host --uikit-reference REFERENCE
python3 tools/gpu/verify_runner_job.py JOB
python3 tools/gpu/verify_runner_display.py /tmp/dvm/CA_UIKIT_DISPLAY_GUEST1 --after-job JOB_ID
# If asleep, observe native input/wake and actual screenshots separately.
python3 tools/input/native_input.py --run /tmp/dvm/CA_UIKIT_DISPLAY_GUEST1 --frames WAKE --settle 5 --log WAKE.jsonl power
```

Use a fresh tag for a new session, or the existing live runner for revisions.
`GLYPHS` is the captured exact-guest input set documented in the offscreen
assessment. Small records are preserved in
`~/dvm-artifacts/research/gpu-uikit-display-20260907-part1/index.json`
(1,739 files, 270,965,302 bytes), including complete JSON submissions/uploads,
reference inputs/manifests, raw completed-job audit RAM, recovery and failures.
Full guest RAM and disks are excluded.
The final 17 passing verifier/runner tests and generic shared-consumer compile
are in `gpu-uikit-display-20260907-part2`. The validated incremental BUILD4
and linked revision are preserved separately in
`~/dvm-artifacts/gpu-uikit-display-v11-ios27`, with file hashes in
`milestone.json`.

Next functionality target: UIKit visual-effect/backdrop composition and its
related Metal interfaces, followed by normal device discovery/compositor
adoption. This milestone proves one displayed UIKit workload; it does not
prove that SpringBoard uses our device, that the displayed test button receives
input, or that original Liquid Glass works.
