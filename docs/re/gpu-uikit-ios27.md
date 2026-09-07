# UIKit consumer and bounded texture transfers

This is an incomplete exact-guest UIKit experiment, not system-wide acceleration
or Liquid Glass. The guest remains iOS 27 24A5430a, iPhone17,3/T8140 with original
SPTM/TXM, native SMC, migrated disk ancestry and the software display fallback.
Only isolated runner children and signed test packages are used.

## Current result

V10 restores text backing-store conversion and native A8 sampling. An explicit
CARenderer coordinate option produces upright actual guest UIKit output, but
its strict CPU comparison still fails at image/text edges. The separate
asymmetric nearest-image test passes every pixel, completion and retirement.
See the final section for current job identities and reproduction. Earlier
sections below retain the failures that led to these contracts.

## Initial observed output

`CA_UIKIT_GUEST1`, job `1788768863676793`, PID 313, executes actual UIView,
UILabel, UIImageView and UIButton creation/layout. CARenderer consumes the
view's layer tree through the explicit custom queue; host Metal completes three
passes and seven draws using the verified guest QuartzCore AIR. The 320×480
offscreen target contains the rounded card, checker image and button background,
but none of the three text labels. The checker orientation also differs from
the independent guest `CALayer renderInContext:` reference.

**Pixel correctness fails:** 119,731 BGRA channels differ by more than two;
maximum error 235. The guest exits 1 with
`GPU_LOAD_ERROR package=uikit-pixel-reference`. There is no successful resource
retirement verdict for that failed process. The target is not a DCP screenshot.

GPU BGRA SHA256:
`9d1dc0f31e6831c645851df354dbb22aed09f5ac159ba73f549e5072ebae2bf2`.
CPU reference BGRA SHA256:
`5722b48f7dad9fe5114a1c86a059faabb3715f11eadc8dc9a864e3c4beb07693`.
The PNG conversion only reorders BGRA to RGBA; it does not flip, scale or
retouch pixels. Both images are retained under
`~/dvm-artifacts/research/gpu-uikit-20260907-capture1`.

Job `1788769304944246`, PID 625, adds explicit `setNeedsDisplay` and layer
metadata. The labels acquire nonnil backing contents, but output remains byte
identical, still three passes/seven draws. No `GPU_LOAD_TEXTURE_REJECT` appears
in either job. Thus a missed redraw request is disproven within this workload;
the backing-content conversion contract remains under investigation.

The same VM then passes the installed red-layer control, job
`1788769319830715`, PID 643: one pass/one draw, exact 64×64 red, zero resources.
This proves another fresh process can use the established driver after the
failed UIKit comparison; it is not a UIKit correctness pass.

## V8 transfer contract

The previous `CA_GROUP_PRIVATE_GUEST4` UIKit attempt reached actual GPU work,
then requested 614,400 texture bytes in one read. Base64 encoding exceeded the
presentation runner's 65,536-byte reply envelope. The exact terminal contract
was `ValueError: host reply length`, at 917.69 seconds; it was not the removed
600-second interactive session limit. Earlier successful group-opacity jobs
remain valid, but that overall VM session ended unsuccessfully.

V8 adds at most 32,768 raw bytes per texture transfer. A single bounded staging
transaction requires contiguous offsets and a matching token. Incomplete
uploads block ordinary GPU submission/read access; abort preserves the previous
native contents. Native replacement happens only after the final chunk. Large
guest texture reads are assembled from bounded replies. Both the GPU target
and independently uploaded CPU reference now transfer successfully in the exact
guest, exposing the actual pixel mismatch instead of stopping at framing.

`test_texture_chunks_bound_replies_and_commit_atomically` checks native
320×480 storage, rejected partial access/order, abort preservation, complete
pattern transfer and replies below 64 KiB. The 56-test host suite passed; its
log is preserved in `gpu-uikit-20260907-part2/CA_UIKIT_TRANSFER_TESTS.log`.

Limitations: each ranged read currently reads the full native texture before
slicing its reply; this is final diagnostic readback, not a per-frame fast path.
Large linear-buffer uploads and aggregate small-upload packet budgets are not
generalized by this change. Large completed CPU uploads precede render-batch
validation; batch rejection does not undo those completed CPU writes.

## Reproduction and iteration

Build the UIKit consumer with the installed iPhoneOS SDK, not macOS iOSSupport
headers. The generic proxy/backend retain their existing cross-build inputs.

```sh
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
  python3 tools/gpu/build_consumer_package.py BASE_BUILD NEW_BUILD --frames 1 --uikit
python3 tools/gpu/sign_linked_revision.py NEW_BUILD/DVMProxy.bundle SIGNED_BUILD \
  --parent /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/installed-build/dvm-gpu-load
python3 tools/gpu/runner_control.py LIVE_TRIAL --bundle SIGNED_BUILD/DVMProxy.bundle \
  --mode data --development --test package --frames 1 --expected observe \
  --worker NEW_BUILD/driver_host
python3 tools/gpu/analyze_uikit_capture.py LIVE_TRIAL/runner-jobs/JOB
```

UIKit currently uses an observational job plus an independent diagnostic image
analyzer. The runner's old red-scene verifier is not a UIKit acceptance oracle.
Explicit UIKit acceptance metadata, replay completeness and independent pixel
validation must be completed before promoting it to a required passing test.

Preserved records, audit slots, transfer RAM and build/signing metadata:
`~/dvm-artifacts/research/gpu-uikit-20260907-part2` (90,904,666 bytes, 558 files).
Executables remain in isolated build paths; no disk images are included.

One diagnostic revision, job `1788769375385205`, accidentally sent the multiline
`CFCopyDescription` of a CGImage through the one-line audit channel. The owned
VM stopped with `ValueError: audit record format`. The final CRC-valid slot
contains embedded newlines, independently identifying an instrumentation bug.
The diagnostic now replaces CR/LF before writing; no audit validation is relaxed.
Recovery testing uses a fresh disposable child `CA_UIKIT_GUEST2`.

Recovery job `1788769474200919` stages at 108.494 seconds after runner readiness
at 108.202 seconds. The corrected single-line descriptions identify the UILabel
backing objects as `CABackingStore` with `[560 92] A8`, `[504 60] A8` and
`[300 36] A8` buffers; the image is a 16×16 CGImage. Pixels remain byte-identical
to GUEST1. The subsequent installed control `1788769474258550`, PID 333, passes
with zero resources. These two collected jobs and the exact signed revision are
preserved in `gpu-uikit-20260907-part3` (37,788,963 bytes, 145 files).

Static evidence is retained in `gpu-uikit-static-20260907`: exact
`CABackingStoreRetainFrontTexture` at `0x1844d4060` calls the internal getter at
`0x1844da684`. That getter can construct a render image via
`CA::Render::Shmem::copy_image` at call site `0x1844da6d4`, or an IOSurface-backed
surface at `0x1844da85c`. This is evidence that conversion paths exist, not a
trace proving which gate this workload hits. The next experiment must locate
the skipped backing-store conversion before changing advertised capabilities.


## Alignment and alpha-image contracts (V9/V10)

Exact `CA_UIKIT_GUEST2` jobs `1788770030849435` and `1788770294601412`
separate two failures. Before V9, UIKit had valid A8 backing stores but
`CA_copyRenderValue` returned nil. The diagnostic called the original method
and returned its unchanged result. At `0x184589dfc`, QuartzCore calls
`iosurfaceReadOnlyTextureAlignmentBytes`, then `CADeviceSetMinimumAlignment`
at `0x184589e00`. The latter (`0x1845198f0`) raises both base and row alignment.
Our former answer, 16384, confused kernel page registration with texture row
alignment. `Shmem::copy_image` (`0x1844d9fec`) computes row extents using this
global alignment and rejects an extent beyond the backing store at
`0x1844da314..318`. Recorded text stores used 64-byte row alignment.

V9 advertises 64 for the owned BGRA texture contract, while retaining 16384
for no-copy buffer registration. The backend checks its actual native linear
BGRA alignment can honor that profile. In the next exact process, backing
conversion succeeds and QuartzCore requests three previously absent
`MTLPixelFormatA8Unorm` textures: 560×92, 504×60 and 300×36. V9 explicitly
rejects these unsupported allocations; that is the next observed contract.

V10 implements A8 sampled textures, including byte transfers and explicit
rejection of render-target or shader-write usage. Host tests pass (57 tests,
`CA_UIKIT_ALPHA_TESTS.log`). Exact job `1788770463905676` executes three passes
and ten draws, adding all three labels. Job `1788770972721116`, with tracing
removed and CPU reference drawing moved after GPU capture, produces identical
pixels: GPU SHA256 `85ecdaa036062287b74d392c4316e2032af94cd2c2f98056792df570c6fd00cd`.
The comparison still fails 122460 channels: image/text content is vertically
inverted. These are failed offscreen tests, not UIKit acceptance.

Records and build metadata are preserved in
`~/dvm-artifacts/research/gpu-uikit-20260907-part4` (166392293 bytes, 766 files).
Static disassemblies are in `gpu-uikit-static-20260907`. The capture is
`gpu-uikit-20260907-capture2/uikit-gpu.png`.

## Asymmetric orientation control and replay

`test_layer_orientation.m` uses a plain CALayer with a 2×2 four-color image,
scaled with nearest filtering. The previous displayed image scene was one row
high and could not test vertical orientation. Native macOS and forwarded
macOS (explicit exact-guest AIR substitution) have identical whole GPU arrays,
SHA256 `c0688c7060cde5f9db041714c4728a2a3d4c62a99327d6b6bf11b9aa04f220a6`.
Their CPU arrays also match each other, but differ from GPU at 516 pixels near
filter boundaries. Native CPU rendering is therefore not an exact oracle for
this scaled-image filtering test. The strict mismatch remains recorded.

Exact `CA_UIKIT_GUEST3` job `1788771646765672` has opposite CPU/GPU corner colors
and 768 differing pixels; both plain guest layers report contentsAreFlipped=1,
whereas macOS reports 0. This rules out UIKit-specific backing stores as the
sole cause. `CARenderer` reads `kCARendererFlags` into its private +0x58 field,
passes it to Metal context creation (`0x1847d1364`) and Render::Update creation
(`0x1847d1a7c..9c`). Update stores it at +0x12c (`0x184430624`). Bit 1 participates
in the orientation XOR at `0x184453490..4b8`. This is static evidence for a
coordinate-convention experiment, not a complete specification of every flag.

Setting only flags=2 in job `1788771989336267` corrects all four guest corner
colors and leaves 516 full-image differences, consistent with the separate
native filtering difference. No driver, upload bytes or shaders were flipped.
The control still exits failure under its unchanged full-image comparison.
`--renderer-flags 2` is explicit test metadata, not a production default.

The initial diagnostic used an invalid audit log prefix. Its formatting error
terminated `CA_UIKIT_GUEST2` at 2086 seconds. That trial remains failed; this was
a test-harness error, not GPU completion or guest panic. The corrected probe
uses checked GPU_LOAD prefixes. `CA_UIKIT_GUEST3` is a fresh disposable disk
boot. Its first installed control mistakenly requested a package-only export
and fails `runner-test-entry`; that failed required job is retained. Subsequent
V10 red/resource control `1788771813998363` and 16-frame shared group-opacity
control `1788771865472188` pass, including native presentation and handoff.
The session as a whole cannot be called a clean regression pass.

Chunked texture writes now preserve their complete payloads and hashes in the
submission capture. Exact unchanged UIKit job `1788771934222766` reproduces the
same failed pixels. `replay_driver.py` then reproduces all 113 requests and
output replies in 0.173 seconds without guest execution. Replay success means
faithful reproduction of the failure, not pixel correctness.


### Coordinate correction and exact nearest-image acceptance

The UIKit option experiment (`CA_UIKIT_GUEST3/1788772031565602`, bundle SHA256
`303568471e9ed91c46057b35b504930d8ccfef53674f4fe82045c18a1ab766c3`)
corrects image/text orientation. The untouched GPU output is preserved as
`gpu-uikit-20260907-capture3/uikit-gpu.png`, SHA256 of raw BGRA
`394fc03ad61980e02e3aed45e13e86c9a640f386fea88ee4321f5c5dd244b385`.
Three passes/ten draws still differ from the CPU reference by 43567 channels
(maximum error 67, mean 1.164743). Background pixels match; differences cluster
at image filtering and text edges. The full UIKit comparison remains failed.
No production texture/shader flips or altered pixel tolerance were introduced.

The nearest-image control must also request nearest interpolation in its
independent CoreGraphics context: CALayer's filter setting alone does not set
that context's interpolation quality. With `kCGInterpolationNone`, both native
and forwarded macOS controls match their CPU array exactly, and their complete
GPU arrays match each other. This corrects the control's workload definition;
the UIKit CPU reference and tolerance remain unchanged.

Exact job `1788772224818847` (PID 716, signed bundle
`919f88354e94bb0d7814743e81cef48f60afaf3c563cae895ddc41b4bcda567a`)
then passes the asymmetric image control. `analyze_orientation_capture.py`
independently checks all 4096 captured GPU and CPU pixels against the analytic
four-color geometry, exact AIR witness, GPU completion, successful guest exit
and zero remaining resources. Expected BGRA SHA256:
`8a70c5d2c28140292bdf2c2f3ea41d534d4314d8023a3a8fa65c96f534187025`.
The runner's older red-scene verifier is not used to certify this different
scene. The separate report certifies only this offscreen nearest-image scope.

Reproduction additions (same signing/staging recipe as above):

```sh
python3 tools/gpu/build_consumer_package.py BASE NEW --frames 1 --orientation --renderer-flags 2
python3 tools/gpu/analyze_orientation_capture.py TRIAL/runner-jobs/JOB
python3 tools/gpu/build_consumer_package.py BASE NEW --frames 1 --uikit --renderer-flags 2
python3 tools/gpu/replay_driver.py TRIAL/runner-jobs/JOB --worker WORKER --library AIR --out NEW_REPLAY
```

Next question: which UIKit residuals are normal CPU-versus-GPU rasterization
differences, and which arise in forwarding? A native-versus-forwarded full
UIKit host comparison can separate that contract without weakening the failed
exact-guest test. System-wide discovery, compositor integration, general
IOSurface imports, Liquid Glass and sustained UIKit pacing remain unverified.


Current completed jobs, source/build metadata, native controls and replay
records are preserved in `~/dvm-artifacts/research/gpu-uikit-20260907-part5`
(164428641 bytes, 846 files). The live development VM intentionally remains
available between tests; it has no global 600-second deadline.

Native orientation control build (host evidence only):

```sh
xcrun clang -fobjc-arc -O1 -Wall -Wextra -Werror -Wno-deprecated-declarations tools/gpu/test_layer_orientation.m -framework Foundation -framework Metal -framework QuartzCore -framework CoreGraphics -o OUT/test
OUT/test OUT
```

The forwarded host variant additionally compiles `driver_guest.m` with
`DVM_ORIENTATION_FORWARDED` and `DVM_CA_REHEARSAL`, links IOSurface, and requires
both `DVM_DRIVER_LIBRARY` and `DVM_REHEARSAL_AIR` to identify the exact guest AIR.
It remains a host rehearsal even though it uses the guest's shader library.
