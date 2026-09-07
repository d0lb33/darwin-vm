# Texture-view development checkpoint — 2026-09-07

This is an implementation checkpoint, not a new exact-guest milestone. The
last guest failure remains `DVMTexture
newTextureViewWithPixelFormat:textureType:levels:slices:` in
`CA_COMPUTE_DESCRIPTOR_GUEST4`; see `gpu-compute-descriptor-ios27.md`.
That run created `compute_average_luma`'s pipeline but dispatched no compute.

Contract V26 adds owned native same-format 2D texture views with contiguous
mip ranges and slice 0. Nested views retain their parents, preserve relative
level metadata, and share allocation ownership. Views do not add allocation
bytes to accounting. Render, compute and blit validation now follows view
ancestry when checking overlapping resources. General reinterpretation,
arrays, planes and swizzles remain unsupported; general `textureViews` remains
false, with the narrower contract described separately.

## Host evidence

Durable build, source snapshot and logs:
`/Users/jdolbe1/dvm-artifacts/research/gpu-texture-views-20260907-commit-host`.
Prior experiments, including failed expectations, are in `prior-experiments/`.

`test_texture_views.log` verifies 24 changing render/sample frames across
RGBA8, BGRA8 and RGBA16F, including a 1216×2560 allocation, four mip levels,
nested views, edge samples, exact expected output, completion and zero final
live resources. Its additional shared-view test checks CPU alias writes and
a partial update preserving preceding GPU writes. These use harness-authored
shaders through the frontend/backend on macOS; they are not guest AIR tests
or DCP presentation evidence.

`native-probe.log`, collected with Metal API validation, observes immediate
parent identity, relative mip levels and equal allocation sizes for nested
views. It also disproves an earlier residency assumption: private roots report
Volatile (3) while their views report NonVolatile (2). The implementation
therefore returns separate native view and allocation-root states. A view's
metadata alone cannot establish safe allocation reuse.

Reproduction (choose a new output directory):

```sh
bash tools/gpu/build_host_driver_tests.sh "$out"
DVM_DRIVER_BUILD="$out" python3 -m unittest discover -s tools/gpu -p test_driver_host.py -v
DVM_DRIVER_BUILD="$out" python3 -m unittest discover -s tools/gpu -p test_shared_render.py -v
"$out/test_texture_views"
python3 -m unittest discover -s tools/tests -v
xcrun clang -fobjc-arc -O1 -Wall -Wextra -Werror tools/gpu/probe_native_texture_views.m -framework Metal -framework Foundation -o "$out/probe_native_texture_views"
MTL_DEBUG_LAYER=1 "$out/probe_native_texture_views"
```

The checkpoint rebuild passed 30 backend tests, 6 shared-render tests (including
the owned-surface frontend with its fixture), and 81 project tests. The
focused texture-view, purgeability, imported-surface, ordered compute/render,
blit, private-texture and submission-queue executables passed. The initial
direct invocation of `test_owned_surface_frontend` lacked its required RAM
session fixture and failed `test session`; use its `test_shared_render.py`
harness, not the executable alone. Full outputs are retained.

## Remaining work before exact-guest acceptance

- Finish view-purgeability failure handling: quarantine the allocation if a
  transport exception occurs after sending a state change, matching the
  existing non-view implementation.
- Verify native per-view initial residency metadata and frontend reuse checks
  independently from the root's state. Add private-root Volatile/view
  NonVolatile tests and reacquisition checks.
- Add direct overlapping-view rejection and imported-view lifetime tests;
  current passing imported-surface tests cover the existing non-view path.
- Run V26 inside the isolated exact guest, capture the actual view request,
  then require compute dispatch, verified pixels, completion and display/input
  preservation. No V26 guest boot, sustained pacing or checkpoint claim is
  made here. The previously validated V25 boot package remains unchanged.

Do not mistake this checkpoint or its implemented selectors for completed
system-wide acceleration or universal Metal compatibility.

## Follow-up: residency and alias contracts

The follow-up source now queries the native initial view residency rather than
copying its parent's state. The frontend checks allocation residency and its
own view state, and catches a transport exception after a view state change,
quarantining the root before any subsequent recovery RPC or alias creation.

Evidence and current build sources:
`/Users/jdolbe1/dvm-artifacts/research/gpu-texture-views-20260907-residency-host`.

- `backend-tests2.log`: 31 passing backend tests. The exact guest AIR luma
  reduction now reads a real texture view; three batches produce exact
  expected output. Nested overlapping blits are rejected without submission;
  copying between distinct absolute mip levels succeeds. Parent release is
  rejected while views remain.
- `purgeability-fix2.log`: native private RGBA16F root volatility blocks view
  reuse despite its NonVolatile hint; reacquisition permits reuse. An injected
  exception after the native state change blocks subsequent root/view use.
  The earlier test used a small BGRA allocation whose hint did not become
  volatile (`test_purgeability_frontend1.log`); this was a test assumption,
  not evidence that all private allocations share one residency behavior.
- `test_imported_surface_frontend2.log`, `3.log`, `4.log`: imported views
  render through the frontend, preserve their original IOSurface and retain
  their parent mapping. Final-alias retirement, lost-reply quarantine and
  backend-only Metal validation pass. Kernel registration and DCP are mocked
  in these host tests, not claimed as new guest evidence.
- `test_texture_views0.log`: the existing 24-frame exact-pixel checks pass.

The boot bootstrap, kernel and device model are unchanged. Dynamic backboardd
reload development continues in a separate worktree; none of its pending
lifecycle changes is required for this GPU implementation batch.

## Exact guest: CA_TEXTURE_VIEW_GUEST1

A fresh disposable disk boot (no saved RAM or debugger) reaches the 64-frame
condition in 102.271 seconds. All 802 host RPCs succeed. There are 64 completed
GPU batches, 753 render passes, 2,267 draws, two compute dispatches, and 64
native presentations with 64 D594 completions. No reload tooling is involved.

RPC325 creates view169 of parent155: private RGBA16Float, 576×64, level0/count1,
slice0/count1, allocatedSize311296 and native residency2. In RPC330 the actual
guest encodes `compute_average_luma` pipeline168 with groups3×2×1,
threads32×32×1 and 16384 bytes threadgroup memory, then `compute_sum_luma`
pipeline171 with groups1×1×1, threads1×1×1 and 16 bytes threadgroup memory.
The batch contains 18 render passes plus these two dispatches. It completes
with native status4 and reports buffer88 written. Its GPU time is 2343.375µs.
This is actual compositor work, not an injected test scene or host rehearsal.

`scanout-verification.json` independently verifies final source→conversion→
console delivery with zero conversion or display differences at 1179×2556.
The actual screenshot still has a dark clock on a black background. Delivery
correctness does not prove full Liquid Glass appearance or scene semantics.
The luma buffer has not been compared with an independent exact-guest oracle.
This run did not inject input; earlier input recovery remains separate evidence.

GPU batch times: first1988.750µs, subsequent33.792–2343.375µs; host batch
service878.208–8460.708µs. These exclude uploads, guest scheduling and display;
64 startup presentations do not establish sustained frame pacing. Recorded
allocation peaks:136 handles,8175232 ordinary native bytes,48431104 imported
mapped bytes.75 late one-second RSS samples peak at31136KiB worker and
5626080KiB QEMU; these are not leak or full-retirement proofs.

Durable run evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-texture-views-20260907-guest/CA_TEXTURE_VIEW_GUEST1`.
Sibling `control.json` pins the preserved installed child and unchanged boot
inputs. Reproduce with `run_system_boot.py control.json`, the residency-host
`driver_host`, the exact QuartzCore library and existing library cache from
`gpu-compute-descriptor-ios27.md`, a unique tag, `--seconds 180` and
`--min-presentations 64`. The next acceptance check is longer displayed work
with input recovery and a scene/luma correctness oracle, not more loader work.

## Longer observation: CA_TEXTURE_VIEW_PACING1

The same installed guest and host driver, plus QEMU log-only source timestamps,
were run with `--seconds 240 --min-presentations 64
--home-after-presentations 32 --observe-seconds 30`. The Home check passed:
helper PID86/epoch2 stayed stable, both edges dispatched, queues drained,
error counters did not increase, and display completion followed. This is
dispatch/recovery evidence, not input-to-first-visible-change timing.

The run completed64 GPU/display batches (627 render passes,2070 draws and
four compute dispatches), then stopped at95.836s on RPC972: allocation of
another1216×2560 private RGBA16F usage65541 texture exceeded the aggregate
64MiB resource budget. A previous1216×2560 private texture209 was still live;
tracked native ordinary allocation peaked at45896064 bytes before rejection.
The requested image has24903680 logical bytes. This is a concrete bounded
allocation failure, not evidence that view creation or shader execution failed.
The intended30-second post-target observation did not finish.

QEMU timestamps are appended at framebuffer delivery and native D594 handling;
they do not represent physical display refresh. `report_compositor_pacing.py`
pairs repeated swap IDs by order and refuses incomplete or mismatched samples.
Across this startup/input interval, scanout takes 5.232–7.641 ms (p95: 6.565 ms), and delivery-to-D594 takes
0.434–2.432 ms.
These measurements locate work but do not establish perceptual pacing. The
largest inter-presentation gap is1215.257ms after frame6, during startup.
The raw overall median40.796ms includes unknown frame demand. Neither that
median nor idle gaps are automatically missed frames or recurring stutter.

Evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-texture-views-20260907-pacing/CA_TEXTURE_VIEW_PACING1`.
Final source-to-console pixel delivery was verified separately after stopping.
Next: account for the observed simultaneous private allocations, test bounded
budget/release behavior, then use a known visible transition with before/after
state verification, input-to-first-visible-change timing and transition-only
frame gaps. Do not promote the current mixed startup/input interval to a
steady-animation performance result.

Wallpaper is a separate integration lead: `lock-screen-wallpaper.md` records
the MercuryPosterExtension Metal renderer and its shader libraries. Our
bootstrap remains backboardd-only, so its success does not establish a device
in MercuryPosterExt. The extension's current runtime device result and selected
API path still need verification. A dark clock over a missing black wallpaper
is not by itself proof of incorrect clock shading.
