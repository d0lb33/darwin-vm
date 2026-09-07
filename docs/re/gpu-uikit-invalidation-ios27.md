# Respect UIKit invalidation: remove the black glass covering draws

2026-09-07, exact iOS 27 build 24A5430a, iPhone17,3/T8140. Continuation of
[the half-float/mip milestone](gpu-uikit-glass-ios27.md). Same isolated
`CA_UIKIT_EFFECT_GUEST1` boot, QEMU PID 88732, fresh guest processes and signed
Data revisions. SPTM/TXM, native SMC, migrated disk ancestry, kernel/tree,
display/input and software fallback remain unchanged. No debugger or NVMe.

**Observed:** guest job **1788782637908079** renders a translucent glass region
using the original guest shaders through our Metal driver. It completes
**10 passes/21 draws**, exits normally and retires all resources. The black
rectangle was introduced by the test harness forcing private effect layers
dirty. Removing that forced invalidation fixes the visible black covering
draws; the driver does not discard operations or special-case shaders/layers.

The new shared-target image SHA-256 is
`cc838d49d5b13922e064e8f63c46565c40d86dbd39703780c89dfe5b58fbeafe`.
It was posted unretouched in chat. **Independent exact-guest glass pixel
acceptance remains unproven.** The original CPU layer oracle and generic
runner verifier remain false. This is offscreen, not displayed or system-wide
Liquid Glass, and not a sustained performance result.

## Localization before changing behavior

The compile-only `DVM_RENDER_DIAGNOSTIC` worker records 2D bound input mips and
stored targets with GPU blit copies. It is never a guest capability or command.
Limits are 16 MiB per capture and 128 MiB per worker. Native completion precedes
file writes; raw texels, format, dimensions, stride, resource handle and phase
are retained. Padding and half-float data remain in the raw files. The analyzer
labels half-float PNGs as clamped previews, never raw guest screenshots.
Input snapshots include bound mips even if a shader does not read them; they
are not shader access traces. Instrumentation changes scheduling and cannot
measure performance.

`CA_UIKIT_GLASS_INTERMEDIATE1` replays original black-output job
1788781168420476. All **205 replies match**, with 44 snapshots/4,674,560 bytes.
The 192×128 RGBA16Float target and BGRA8 blur mip chain contain scene colors.
Black appears only in final composition pass 9. This rejects the hypothesis
that the entire backdrop input was black, within this captured submission.

`CA_UIKIT_GLASS_DRAWS1` additionally reuses the existing store/load command
split to snapshot individual draws. It records 100 snapshots/13,956,096 bytes.
The strict replay stops at render commit 26298 because the diagnostic reply
adds `nativeCommandBuffers:23`; other non-timing fields match. The strict
failure remains recorded. Its final captured image is byte-identical to the
original guest output, independently checked in `localization.json`.

| Zero-based draw | Pipeline | Observation |
| --- | --- | --- |
| 16–18 | 45, `glass_background_sdf_all_lph` | Translucent glass appears; no opaque black pixels in the effect's 240×80 interior rectangle |
| 19–20 | 47/49, `fixed_frag_lph_cpf` | Translucent result remains; snapshot 85 |
| 21 | 53, `fixed_frag_lph_cpf` | First opaque black covering pixels appear; snapshot 92 |
| 22 | 55, `fixed_frag_lph_cpf` | 18,686 opaque black interior pixels; snapshot 99 matches the original final image |

The prior `UIKitDisplay` helper called `setNeedsDisplay` on **every** layer,
then `displayIfNeeded`. Its own before/after logs show previously clean
CASDFLayer instances acquiring raster contents. The corrected helper only
calls `displayIfNeeded`, respecting UIKit's invalidation. In the new exact
guest audit both CASDFLayer instances keep `contents=nil`, while `glassBackground`
and `vibrantColorMatrix` filters remain. The guest no longer submits those
final two covering draws. Its new final image matches old diagnostic snapshot
85 byte for byte. This is an observed intervention, not an inference from
private class names alone.

## Native window and reference scope

The initial native-window capture attempts (`COMPOSITOR1/2`) timed out in the
computer-use tool despite a live probe. The harness ran a nested `runUntilDate`
inside a main-queue callback. Refactoring the window control to return to the
UIApplication event loop, then dispatch the offscreen rendering later, made
`COMPOSITOR3` inspectable immediately (0.0853 seconds). Its actual window
screenshot visibly contains translucent glass over the checker. This is a
**native host compositor observation**, not an iOS screenshot or byte match.
The image is in the conversation's native tool output; `window-observation.json`
records its 31,560-byte capture and scope. `gpu.png` in that directory is the
separate offscreen output and must not be presented as the window capture.
The optional 20/45-second hold is bounded; normal host probes retain their
30-second execution deadline, extended only by that explicit hold.

There are two different host comparisons, both retained:

- `CA_UIKIT_GLASS_INVALIDATION_NATIVE1/FORWARDED1`: corrected invalidation,
  former synchronous/nested-loop setup, identical supported predicates and
  host AIR. All three frames match exactly at SHA-256
  `4b447272c4cc49273e7b1fc61080d7331ea81595e7c7ba22557a3a3b14f606a3`.
- `CA_UIKIT_GLASS_EVENTLOOP_NATIVE1/FORWARDED1`: natural application event loop,
  same intended fixture/predicates/AIR. **Comparison fails**: 54,099 channels
  differ by more than two, maximum 204, bounds [38,127,282,361]. Visible
  differences include glass appearance and button text color. No missing API
  or descriptor rejection was logged. Actual input equivalence has not been
  established across this lifecycle. The native control currently overrides
  methods on the native device class process-wide, whereas the forwarder uses
  an explicit device; this is a possible confound, not the established cause.

Do not weaken the oracle or retreat to the synchronous control as proof of
normal-window compatibility. Next reference experiment: capture/compare layer
and raster inputs under the natural lifecycle, and scope the native capability
control to the offscreen device so it does not change UIKit's other rendering
work. Once inputs agree, any remaining pixel difference becomes a meaningful
forwarding failure to replay. The existing no-effect glyph-reference tool is
explicitly limited to its seven-layer fixture; it is not a glass oracle.

## Regression and reproduction

Fresh no-effect guest job **1788782892498731** retains the previously verified
image hash `394fc03ad61980e02e3aed45e13e86c9a640f386fea88ee4321f5c5dd244b385`,
3 passes/10 draws and clean retirement. This is an unchanged-output check;
its original CPU comparison remains false. New glass capture replay
`CA_UIKIT_GLASS_INVALIDATION_REPLAY1` matches all **190** replies. The focused
72-test host suite passes, including existing compute/copy and ownership
controls, under `CA_UIKIT_INVALIDATION_TESTS1/tests.log`.

Final red control **1788783122153344** passes exact pixels and zero live
resources with the current production worker. Recovery observer
`runner-recovery-1788783122153344-invalidation.json` verifies fresh native
scanout and D594/A408 completion. `CA_UIKIT_INVALIDATION_RECOVERY1.jsonl`
records Home 2/2 ACKs, zero fresh failures/timeouts and a changed frame.
The interactive VM remains running for further fresh-process revisions.

Small records, raw intermediate texels, images and failure evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-uikit-invalidation-20260907-part1`.
Full signed guest packages and diagnostic workers:
`/Users/jdolbe1/dvm-artifacts/gpu-uikit-invalidation-ios27`.
Indices record source, byte length and SHA-256. Final red/recovery records
are in the adjacent `gpu-uikit-invalidation-20260907-part2` directory.

```sh
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
# Compile-only instrumentation; never install this worker in a VM session.
mkdir -p NEW_DEBUG/textures
xcrun clang -DDVM_RENDER_DIAGNOSTIC -fobjc-arc -fobjc-arc-exceptions -O1 \
  -Wall -Wextra -Werror -Wno-deprecated-declarations tools/gpu/driver_host.m \
  -framework Metal -framework Foundation -o NEW_DEBUG/driver_host
DVM_RENDER_CAPTURE_DIR="$PWD/NEW_DEBUG/textures" python3 tools/gpu/replay_driver.py \
  ORIGINAL_JOB --worker NEW_DEBUG/driver_host --library EXACT_GUEST_AIR \
  --out NEW_DEBUG/replay
python3 tools/gpu/analyze_render_debug.py NEW_DEBUG/textures
# Add DVM_RENDER_CAPTURE_DRAWS=1 for split-draw localization; the extra
# nativeCommandBuffers reply field intentionally fails strict replay.

python3 tools/gpu/run_uikit_host.py NEW_WINDOW --effect glass --window \
  --window-hold 45 --frames 1
# Select only this UIKitProbe.app with the computer-use tool during the hold.

python3 tools/gpu/build_consumer_package.py BASE NEW_BUILD --frames 1 \
  --uikit --uikit-external-reference --uikit-effect glass --uikit-window \
  --renderer-flags 2
# Sign/stage with the unchanged persistent-runner procedure in the V16 note.
python3 tools/gpu/analyze_uikit_capture.py NEW_GUEST_JOB
```
