# Framebuffer reads and the first verified UIKit image

2026-09-07, `codex/metal-driver-ios27`. Exact guest remains iOS 27
24A5430a, iPhone17,3/T8140, with SPTM/TXM, native SMC, migrated data and
software compositor fallback. All guest jobs below ran in fresh processes in
the existing isolated `CA_UIKIT_GUEST3` session; no reboot, debugger, baseline
modification or QEMU rebuild was needed.

## Contract and bounded proof

Question: can we implement the current-fragment color-attachment read that
QuartzCore selects with `isFramebufferReadSupported`, including ordered
overlapping draws and retained contents? Stop at a pixel/order failure or an
unsupported host, before advertising the feature.

V11 (`color-attachment-feedback-v11`) exposes this one capability through the
existing single-color-attachment render protocol. The backend requires native
`supportsFamily:MTLGPUFamilyApple2`; otherwise negotiation returns
`host lacks programmable color-attachment blending`. Apple's
[Metal feature tables](https://developer.apple.com/metal/feature-sets/) list
programmable blending from Apple2. No full GPU family, raster-order-group,
tile, memoryless or multi-attachment support is advertised by our driver.

`test_framebuffer_read.m` executes the actual frontend/backend through JSON
requests. Its harness-only shader reads `[[color(0)]]` and applies a changing,
noncommutative integer recurrence. BGRA8 and RGBA8 each run four retained
frames, two passes per frame, eight overlapping scissored draws per pass.
All 4,096 pixels per frame match independent CPU arithmetic exactly, including
alpha. It checks 128 draws, 16 passes, completion, zero live objects/bytes, and
rejection of a simulated unsupported native device. This is host execution
evidence, not a guest-authored shader compatibility claim.

`CA_FRAMEBUFFER_READ_TEST2` and the final
`CA_FRAMEBUFFER_HOST_TESTS1/test_framebuffer_read.log` pass. TEST1's original
retirement assertion used the wrong statistics key; only the corrected runs
establish retirement.

## Native versus forwarded host controls

With V11 capability predicates matched, `CA_UIKIT_HOST_FRAMEBUFFER1` and
`CA_UIKIT_HOST_NATIVE_FRAMEBUFFER1` agree byte-for-byte on all three frames;
the prior early-frame/corner corruption is absent in this control. Static
content can obscure an opacity change, so `--animate` explicitly moves the
card and changes its alpha in frame 1, restoring it in frame 2.

`CA_UIKIT_HOST_FB_ANIMATE1` and `CA_UIKIT_HOST_NATIVE_FB_ANIMATE1` agree exactly
on all three frames and both have a changed middle image. Frame 0/2 SHA256:
`9845e1fdaf690d01ffcad40a9c26686fa8fce5fe1ce66e5fe6ea4bb7aa85bd52`;
frame 1: `c72dfd5828349dcb67c0f7e4b1fa8f15abfc54ee05304a10daf5790a60c205f3`.
These are Catalyst controls using host fonts and shaders, not iOS images or
sustained pacing evidence.

The captured animated submission replays all **206 requests exactly** without
validation (`CA_FRAMEBUFFER_REPLAY_CONTROL1`). With Metal API and shader
validation enabled (`CA_FRAMEBUFFER_VALIDATION_REPLAY1`), strict replay stops
at request **128**, `read`, offset 98,304, length 32,768. Six channels differ
by one byte, none by more than two. The worker exits zero with no validation
diagnostic, but strict replay remains **failed**, with 127 matching requests.
The cause of those rounding differences is untested. Do not turn the absence
of a validation diagnostic into an exact-replay pass.

## Exact guest UIKit acceptance

V11 job **1788774764622058** (BUILD16) selects captured function constant
`fc_framebuffer_fetch=01` and executes the unchanged exact-guest AIR. Its
image is identical to the previous V10 guest image: the host fallback fix did
not fix the guest CPU/GPU comparison. That job remains failed.

BUILD17 job **1788775472591201** adds actual layer geometry logging. Its
seven layers have scales `[1,2,1,1,2,1,2]` and continuous corner curves. The
uniform-scale and circular-corner hypotheses were wrong. This job also remains
failed, even though a later independent reference matches its image.

The original guest CPU `renderInContext:` comparison is not a matching GPU
rasterization oracle. Native host controls also disagree with their CPU
images. `analyze_uikit_sampling.py` independently predicts all three text
regions within one byte from the actual uploaded 2x A8 glyphs, vertex colors
and bilinear sampling. Its circular checker-clip hypothesis still leaves
errors and is explicitly diagnostic only.

The new native composition reference consumes **inputs only**: original
guest A8 glyphs, their captured colors/bounds and actual layer scales/curves.
`consumer_uikit_raster_scene.inc` replaces host text rasterization with those
glyph images and lets native UIKit/QuartzCore independently create its own
render commands. No final guest image, guest command stream, proxy device,
capability override or library substitution is used to produce that reference.
The shared scene fixture supplies the background/checker geometry.

BUILD18 job **1788775984328876**, guest PID **2292**, is the first clean
offscreen UIKit acceptance result:

- Actual guest UIKit/CARenderer: **3 GPU passes, 10 draws**, exact unchanged
  2,705,796-byte AIR SHA256
  `8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
- Linked revision SHA256
  `9bf4239d83c19534f83f7a95852597a5517b0def13973a442fb1dd4c1316ec33`.
- Guest process exits zero, completion observed, all GPU resources retired.
  Host-observed execution: **1.036 seconds**; not a frame pacing result.
- All **614,400 channels** match `CA_UIKIT_HOST_GUEST_RASTERS4` within one
  byte, none beyond the unchanged acceptance threshold of two.
- Guest GPU SHA256
  `394fc03ad61980e02e3aed45e13e86c9a640f386fea88ee4321f5c5dd244b385`;
  independent native reference SHA256
  `538064cf995407ce12e8507a21d8a178e7cab866893bb60cae207dfd2c933d80`.

`--uikit-external-reference` records the original CPU mismatch and an explicit
`status=pending` marker, then allows normal resource retirement. It does not
accept pixels. `analyze_uikit_native_reference.py` reconstructs output from
the checked capture, verifies input provenance/native execution and requires
clean guest completion/retirement before writing the separate
`uikit-native-reference.json`. That file reports **verified=true**. The raw
runner result remains false because its generic red-scene verifier is not
the UIKit reference; the original CPU comparison remains false (43,567
channels above two, max 67). Neither historical failure is rewritten.

This proves the pictured **offscreen UIKit scene through our custom Metal
driver**, including text, image sampling, a button and continuous rounded
clipping. It does not prove displayed UIKit, interaction with the button,
system-compositor adoption, general Metal compatibility or Liquid Glass.

## Display and pacing remain separate

V11 shared group-opacity job **1788775211180385** runs 64 displayed frames
and passes ownership transitions, final shared-IOSurface pixels, native
frame/swap identity and D594 completion, fresh-process handoff and retirement.
It is the existing CARenderer control, not the UIKit scene above. Its
`final_scanout_export_checked=false`: no full final DCP pixel dump was taken
for this job. Do not upgrade scanout/completion events to a bytewise display
comparison.

Setup 217.862 ms; first two frame work times 272.746/30.130 ms. The remaining
62 frames average 60.335 completed fps, work p95 9.470 ms, p99/max 21.730 ms;
two absolute deadline misses and one work interval beyond 16.67 ms. Native
scanout averages 59.889 fps, interval p95 20.351 ms, max 29.119 ms. Final
resources retire; shared resource bytes do not grow during the batch.
This short control does not establish smooth native UIKit performance.

## Reproduction and evidence

Use fresh output directories and the existing isolated runner. `BASE` is an
existing MMIO consumer build; `PINNED_HELPER` is its installed signed helper.

```sh
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
tools/gpu/build_host_driver_tests.sh HOST_TESTS
HOST_TESTS/test_framebuffer_read
python3 tools/gpu/run_uikit_host.py NATIVE --frames 3 --animate --native-contract --native-library HOST_FAT_LIBRARY
python3 tools/gpu/run_uikit_host.py FORWARDED --frames 3 --animate --forwarded-library HOST_FAT_LIBRARY
python3 tools/gpu/analyze_uikit_host.py NATIVE FORWARDED --output COMPARISON.json
python3 tools/gpu/build_consumer_package.py BASE BUILD --frames 1 --uikit --uikit-external-reference --renderer-flags 2
python3 tools/gpu/sign_linked_revision.py BUILD/DVMProxy.bundle LINKED --parent PINNED_HELPER
python3 tools/gpu/runner_control.py /tmp/dvm/CA_UIKIT_GUEST3 --bundle LINKED/DVMProxy.bundle --mode data --development --test package --frames 1 --expected observe --worker BUILD/driver_host
python3 tools/gpu/analyze_uikit_capture.py JOB
python3 tools/gpu/analyze_uikit_sampling.py JOB --export-glyphs GLYPHS
python3 tools/gpu/run_uikit_host.py NATIVE_REFERENCE --guest-rasters GLYPHS
python3 tools/gpu/analyze_uikit_native_reference.py JOB NATIVE_REFERENCE
DVM_UIKIT_JOB=JOB DVM_UIKIT_NATIVE=NATIVE_REFERENCE PYTHONPATH=tools/gpu python3 -m unittest test_uikit_reference
```

Frontend contract/capability/render-writeback and framebuffer execution tests
pass. The 59-case Python backend/consumer/shared-render/blur suite passes;
six native-reference acceptance/negative tests pass. A direct invocation of
the owned-surface executable without its required fixture failed `test session`;
the proper Python fixture subsequently passed. The failed BUILD17 child is
also a negative control: matching reference pixels cannot promote it to pass.

Small records and complete capture payloads:
`~/dvm-artifacts/research/gpu-uikit-20260907-part7/index.json`, **820 records,
129,365,338 bytes**. Original source paths and hashes are recorded in that
index; native reference metadata deliberately identifies its source job.
The unretouched image shown in chat is
`1788775984328876/uikit-gpu.png` under that directory. Executables/shaders,
full guest RAM and disks are excluded from this record set.

Next bounded implementation: put this actual UIKit consumer on the existing
owned shared surface, require native presentation/ownership return and final
pixels, then run changing frames with setup separated from sustained pacing.
System discovery, compositor adoption and Liquid Glass remain unresolved
dependencies beyond that milestone.

The validated incremental BUILD18 and linked revision are separately preserved
in `~/dvm-artifacts/gpu-uikit-v11-ios27`; `milestone.json` records their hashes.
