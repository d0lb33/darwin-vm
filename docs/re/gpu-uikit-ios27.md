# UIKit consumer and bounded texture transfers

This is an incomplete exact-guest UIKit experiment, not system-wide acceleration
or Liquid Glass. The guest remains iOS 27 24A5430a, iPhone17,3/T8140 with original
SPTM/TXM, native SMC, migrated disk ancestry and the software display fallback.
Only isolated runner children and signed test packages are used.

## Observed output

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
