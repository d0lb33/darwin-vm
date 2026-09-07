# UIKit window, half-float and mip rendering evidence

2026-09-07; continuation of [material blur](gpu-uikit-effects-ios27.md).
Exact guest: iOS 27 build 24A5430a, iPhone17,3/T8140. All guest jobs below
ran as fresh processes in the same isolated `CA_UIKIT_EFFECT_GUEST1` VM,
PID 88732, with the pinned QEMU/kernel/tree/helper, SPTM/TXM, native SMC and
disposable disk child unchanged. No debugger, NVMe transport or baseline edits.

The V16 driver executes the guest's original glass shader path. **Correct
Liquid Glass appearance is not established:** the effect region is black.
The active-window native Catalyst control also has a black region; forwarding
matches that native control exactly. This is evidence against attributing
the appearance to forwarding alone, not proof that the driver is complete
or that CARenderer cannot render glass.

## Exact failed contracts and changes

| Experiment | Observed result | Scope of resolution |
| --- | --- | --- |
| BUILD2, job 1788780320058351 | Window attachment creates `CABackdropLayer` with `glassBackground`, plus `CASDFLayer`; rejects private 192×128 format 115, usage 65541 | V14 accepts private RGBA16Float block-write color targets and render pipelines |
| BUILD3, job 1788780549351811 | Next rejection: BGRA8 192×128, usage 5, five mip levels | V15 implements private 2D mip allocation, metadata, total byte accounting and render attachment level |
| Host `CA_UIKIT_GLASS_FORWARDED2` | Sequence 91 fails `fragment resource ownership/usage` when earlier mips are sampled while a later mip of the same allocation is rendered | V16 forwards application-guaranteed disjoint mip access; native Metal probe establishes this binding is valid |
| BUILD5, job 1788781168420476 | 10 passes, 23 draws, no descriptor rejection, process exit 0, all resources retired | Actual glass shader execution; black appearance remains failed |
| BUILD6, job 1788781830592548 | Same output after adding lifecycle/accessibility diagnostics | Exact guest scene attached; application object absent, Reduce Transparency and Reduce Motion both false |

The final two guest images have SHA-256
`a7019ba38b2d4bde3eee91db9d2f5561075998010b4124da25b9f6ac684a4d04`.
BUILD5 capture includes `variable_blur_downsample_frag_lph` at request 26258
and `glass_background_sdf_all_lph` at 26262. These are original guest AIR
functions, not host replacements. The guest AIR SHA-256 remains
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
The CPU-layer comparison and generic job verifier remain **false**, including
`missing guest pixel oracle`; successful process/retirement is a separate
acceptance check. These offscreen images are not DCP screenshots.

V16 is a bounded contract: private 2D RGBA8/BGRA8/RGBA16Float, legal mip count
through floor(log2(max dimension))+1, axes up to 4096, private image budget
16 MiB and existing aggregate budget 32 MiB, counting every mip. Attachment
and scissor bounds use the selected mip's dimensions. Shared/managed/3D mip
chains, automatic mip generation, texture views and CPU transfers to private
mips remain unsupported.

The same-allocation exemption applies only to a private destination mip above
zero. **Shader reflection cannot identify a dynamic source LOD.** As with
native Metal, the application must avoid reading the destination subresource.
The driver does not claim to detect all same-subresource hazards. Native hazard
tracking and serialized completion remain in place; this is not general
in-place texture feedback or a new shared-resource ownership guarantee.

## Layered verification

`CA_UIKIT_GLASS_MIP_TESTS2/tests.log`: 72 focused tests pass. Negative controls
cover illegal mip counts, storage/type/format combinations, total byte budget,
attachment level and mip-relative scissor bounds before submission. The
private texture test checks every output pixel across eight reuses of each
of three formats, including signed and above-one RGBA16Float values and four
different destination mips. All resources retire.

`CA_NATIVE_MIP_ALIAS1/result.log`: public native Metal, API and shader
validation enabled, passes 8 frames each for BGRA8 and RGBA16Float. Each frame
paints mip 0, reads the previous mip while rendering mips 1–3, converts the
final mip to a shared output and verifies every pixel. It is a native semantic
probe, not guest evidence. Source: `tools/gpu/test_native_mip_alias.m`.

The final host comparison uses separate uniquely identified Catalyst apps and
begins rendering from `sceneDidBecomeActive:`. Both report application state 0,
scene state 0, light style, normal contrast and accessibility toggles false.
`CA_UIKIT_GLASS_ACTIVE_NATIVE1` uses native Metal with the supported contract
predicates and host AIR; `CA_UIKIT_GLASS_ACTIVE_FORWARDED1` uses the forwarding
driver with the same host AIR. All three frames match byte for byte, SHA-256
`746bf081e640f74435b4a6147c1304921a761bf9637a20ab55a7176edbf17e45`.
This comparison does not substitute shaders in the guest.

The unrestricted native/default-library control `CA_UIKIT_GLASS_ACTIVE1`
also renders the whole UIWindow tree after scene activation and still has a
black glass region. Earlier window-root and pre-activation controls likewise
show black. These controls disprove only the tested explanations (simply
missing window, inactive host scene, Reduce Transparency, or choosing the
content root rather than window root). They do not supply a successful native
glass reference. Normal system-compositor window output is still untested.

Replay results are deliberately distinct:

- `CA_UIKIT_GLASS_GUEST_REPLAY2`: all 205 captured BUILD5 requests reproduce
  their original replies under default backend execution.
- `CA_UIKIT_GLASS_GUEST_REPLAY1`: with API/shader validation, strict comparison
  stops at read request 26305 after 98 matching requests. Seven channels differ
  by one, none by more, in 32768 bytes at offset 163840.
- `CA_UIKIT_GLASS_HOST_REPLAY1`: validation replay similarly stops at request
  110, two one-byte channel differences. These strict failures are not promoted
  to passes; neither record reports an API/shader validation fault.

## Lifecycle failures and static evidence

The host harness required an actual app bundle and scene delegate. Retained
failures: HOST2 has no NSApplication; HOST3 has no bundle identifier; HOST4
traps in `___UIApplicationEvaluateRuntimeIssueForNoSceneLifecycleAdoption_block_invoke+700`.
The targeted `UIKitProbe-2026-09-07-062451.ips` is preserved. HOST1/HOST5
compile diagnostics and the initial guest NSRunLoop link failure are also
retained. SDK availability warnings are suppressed only around UIKit header
imports; exact guest exports are independently checked after linking.

The guest helper is not a normal UIApplication application. BUILD6 audit
1112–1114 reports application absent, scene state 0, attached/key/visible
window, 320×480, Reduce Transparency 0 and Reduce Motion 0. Do not equate
that with a fully connected interactive app lifecycle.

Static exact-cache disassembly of `-[UIVisualEffectView _interceptGlassEffect:]`
resolves the call at 0x1848a5164 to `_setGlassEffect:` and at 0x1848a5190 to
`_setGlassContainerEffect:`. This supports the filter inspection; it does not
trace runtime execution. `GlassBackgroundFilter::can_render` at 0x1844a1f74
tests a field loaded from [x4+0x10]; its semantic meaning has not been proved.
Neither this branch nor private method lists establish impossibility.

## Recovery, artifacts and next experiment

Final V16 red control, job **1788781875445951**, passes exact 64×64 red pixels,
one pass/draw and zero live resources. Recovery observer
`runner-recovery-1788781875445951-glassv16.json` verifies new native scanout and
D594/A408 completion. `CA_UIKIT_GLASS_RECOVERY1.jsonl` records Home 2/2 ACKs,
zero fresh failures/timeouts and a changed frame; the unretouched after image
shows the software lock screen and charging indicator. Historical cumulative
two input timeouts/three rejections remain unchanged. No displayed glass or
new pacing claim is made. The interactive VM remains alive between tests.

Durable small evidence and images:
`/Users/jdolbe1/dvm-artifacts/research/gpu-uikit-glass-20260907-part1`;
red/recovery records are in `gpu-uikit-glass-20260907-part2`.
Each index records source path, size and SHA-256. Rebuildable BUILD5/BUILD6,
red control, signed bundles and the targeted crash/static records are in
`/Users/jdolbe1/dvm-artifacts/gpu-uikit-glass-v16-ios27`.

Next bounded question: does the same native UIKit glass view look correct
through the normal window compositor while its CARenderer output is black?
Capture only our probe window, then compare its backdrop/filter inputs with
the offscreen submission. Stop after one controlled comparison; if both are
black, inspect the exact effect setup; if only offscreen is black, inspect
CARenderer's backdrop/SDF inputs and render options. Do not optimize shaders
or run long displayed glass batches until a correct reference is established.
The exact failed appearance contract is still unknown, not a demonstrated
driver or architecture impossibility.

```sh
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
python3 tools/gpu/run_uikit_host.py NEW_NATIVE --effect glass --window \
  --frames 3 --native-contract --native-library HOST_AIR
python3 tools/gpu/run_uikit_host.py NEW_FORWARDED --effect glass --window \
  --frames 3 --forwarded-library HOST_AIR
bash tools/gpu/build_host_driver_tests.sh NEW_TEST_BUILD
MTL_DEBUG_LAYER=1 MTL_SHADER_VALIDATION=1 NEW_TEST_BUILD/test_native_mip_alias
DVM_DRIVER_BUILD=NEW_TEST_BUILD PYTHONPATH=tools/gpu python3 -m unittest \
  test_driver_host test_driver_binary test_consumer_verify \
  test_shared_consumer_verify test_render_staging_capture -v
python3 tools/gpu/build_consumer_package.py BASE NEW_BUILD --frames 1 \
  --uikit --uikit-external-reference --uikit-effect glass --uikit-window \
  --renderer-flags 2
python3 tools/gpu/sign_linked_revision.py NEW_BUILD/DVMProxy.bundle NEW_LINKED \
  --parent /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/installed-build/dvm-gpu-load
python3 tools/gpu/runner_control.py /tmp/dvm/CA_UIKIT_EFFECT_GUEST1 \
  --bundle NEW_LINKED/DVMProxy.bundle --mode data --development --test package \
  --frames 1 --expected observe --worker NEW_BUILD/driver_host
python3 tools/gpu/analyze_uikit_capture.py JOB_DIRECTORY
```
