# QuartzCore group opacity and private intermediate textures

Exact guest: iOS 27 24A5430a, iPhone17,3/T8140. The trials below use fresh
children of the immutable native-SMC handoff disk, the existing scoped runtime
loader, original SPTM/TXM, and the pinned QEMU `fc49f9c` binary. No kernel,
device-tree, installed helper, migrated parent or QEMU changes were needed.
Software rendering and normal device discovery are unchanged.

## Implemented contract

Profile v7, `bounded-private-targets-v7`, targets the traditional Metal
resource/render/compute protocols within the coverage matrix. It is not Metal
4.1 conformance and does not implement the `MTL4*` submission APIs.

- One serial execution queue accepts up to 32 committed command buffers.
  Uploads are resolved after predecessor GPU completion/writeback, preventing
  an old pending CPU texture image from overwriting a predecessor's GPU output.
  An earlier failure cancels already queued dependent work. Resources remain
  retained through completion; failed shared jobs prohibit further pool reuse.
- Private textures use actual host `MTLStorageModePrivate`; CPU upload/read
  access is rejected before execution. Texture axes are bounded to 4096,
  private images to 16 MiB, copied images and buffers to 1 MiB, and the combined
  ordinary/shared allocation ledger to 32 MiB. No private CPU blit path is
  implemented. Format, usage, one-level/one-sample and allocation checks remain.
- Related compression/format capability queries return false from the shared
  profile because the supported formats are uncompressed/lossless. Guest
  resource `responsibleProcess` is an acknowledged opaque signed-32-bit value
  encoded as unsigned bits. It does not charge work to a host PID or impersonate
  one. Default zero means no guest attribution was supplied.
- Failed host verification now preserves the successful/failed process exit,
  raw capture and a failed acceptance result instead of throwing away the job
  record. This does not weaken shared-resource failure handling.

## Exact guest observations

Evidence root: `~/dvm-artifacts/research/gpu-group-opacity-20260907-part1`.

`CA_GROUP_GUEST2/runner-jobs/1788766593349364` (guest PID 324) passes independent
64×64 group-opacity verification: four frames, eight native GPU render passes,
14 draws, private intermediate allocations, three verified output checkpoints,
and zero final objects. Final output SHA-256:
`0eb4a7cf93a5f4b15758b46e21427311c8b53850fe9559a185bd8f20825544ef`.
The exact guest AIR remains
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.

The scene is a red moving child behind a green child, inside a parent with
opacity 0.5, over blue. Overlap must be composited once at group opacity.
Scene selection exists only in test code; the driver/backend do not branch on
scene identities or shader names to obtain these results.

Earlier failures are retained, not rewritten:

| Trial/job | Observed result |
| --- | --- |
| GUEST1 / 1788765386943641 | Missing `DVMDevice supportsLossyCompression` |
| GUEST1 / 1788765847574825 | Related capability batch proceeds; final group pixels wrong |
| GUEST1 / 1788766087850219 | Private texture allocation proceeds; missing `DVMTexture setResponsibleProcess:` |
| GUEST1 / 1788766257812470 | Guest reports correct pixels; old verifier incorrectly assumes one render target and throws. Post-hoc verification passes; the original overall trial remains failed |
| GUEST2 / 1788766618513099 | Screen-sized scene fails, 408,960 bad final pixels; shared-job guard stops reuse and reaps owned VM |
| GUEST3 / 1788767155351989 | Audit-capable diagnostic revision, eight displayed frames, 572,544 bad final pixels; no intermediate allocation RPC or descriptor rejection was recorded. Shared failure stops reuse |

GUEST3 and its diagnostic build evidence are preserved under
`~/dvm-artifacts/research/gpu-group-opacity-20260907-part2`.

## Static evidence and the size hypothesis

Disassembly and resolved selector calls are preserved under
`~/dvm-artifacts/research/gpu-group-opacity-static-20260907`.

In exact `CA::OGL::MetalContext::create_surface_with_properties`:
`0x1844f774c` reads the context limit at `+0xe10`; `0x1844f7750` through
`0x1844f7758` compare both requested dimensions against it. The failure path
returns null at `0x1844f7784`, with the diagnostic string
`Surface %d x %d is too large` referenced at `0x1844f7cbc`.
`new_metal_context` calls the resolved `maxTextureWidth2D` selector at
`0x184589b28` and stores the result to `+0xe10` at `0x184589b2c`, subsequently
capping it at 8192. The profile used in GUEST3 returned 512.

This explains how an oversized intermediate can fail before any driver
allocation call. GUEST4 / **1788767486669749** (PID 306) validates the fix:
with real v7 allocation support and reported limits, exact QuartzCore submits a
**256×2560 BGRA8 private texture**, then passes all 16 displayed frames and the
final 3,013,524-pixel oracle. The intermediate occupies 2,621,440 bytes, exceeding
both the former height limit and the former 1 MiB transfer-derived byte limit.
The shared IOSurface remains surface 7, with verified successful ownership
return. Output SHA-256:
`929f69a07c3d3a55ef74452cffbeebaadbeae6ff41cb50aad640611b9c96217e`.

This is a resolved allocation limitation for the tested group-opacity path.
It does not establish general IOSurface import, every QuartzCore effect, or
Liquid Glass. The 16-frame run includes slow frames and is too short for a
sustained pacing claim; setup and first-use costs remain separate.

## Host validation and an attribution correction

`CA_GROUP_PRIVATE_TEST_BUILD1` plus `CA_GROUP_PRIVATE_TESTS2.log`: 55 focused
host tests pass. They cover real GPU predecessor/dependent rendering, queue
capacity and cancellation, actual private storage, prohibited CPU transfers,
size/live-budget bounds and reclamation, process metadata, pixel verifiers,
runner collection failures, and deadline policy. This build is a host test
artifact, not an installed guest helper.

`CA_GROUP_PRIVATE_FORWARD1` passes normal host QuartzCore forwarding with
explicit exact-guest AIR substitution. Earlier HOST builds 1–6 used
`DVM_CA_NATIVE=1`: their forwarding branch still used the proxy device, but the
native test completion path inserted an extra empty fence and skipped resource
counters. Its initial one-in-flight failure therefore came from that test
fence, not proof that guest QuartzCore itself required concurrent submissions.
`CA_GROUP_FORWARD_RUN7` and `CA_GROUP_PRIVATE_FORWARD1` use the ordinary
forwarding completion path. The new bounded FIFO is separately verified by a
real two-command GPU dependency test and transport failure/capacity tests.
Native macOS scene checks remain host rehearsals, not exact-guest evidence.

## Reproduction

Use the durable `gpu-quartzcore-regions-ios27/control.json` and installed build
for the interactive runner command in `gpu-surface-handoff-ios27.md`. Incremental
revision commands, with fresh output directories:

```sh
python3 tools/gpu/build_consumer_package.py BASE BUILD --frames 16 --scene 4 --shared-surface --hz 60
python3 tools/gpu/sign_linked_revision.py BUILD/DVMProxy.bundle LINKED --parent /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/installed-build/dvm-gpu-load
python3 tools/gpu/runner_control.py TRIAL --bundle LINKED/DVMProxy.bundle --mode data --development --test package --frames 16 --scene 4 --shared-surface --surface-handoff --hz 60 --expected pass --worker BUILD/driver_host
python3 tools/gpu/verify_runner_job.py TRIAL/runner-jobs/JOB
```

Use `--frames 4 --scene 4` without shared flags for the offscreen case. The
installed supervisor stays byte-identical. Do not reuse a failed shared trial;
use a new disposable child after its owned process has been reaped. Healthy
interactive sessions remain alive for subsequent fresh-process revisions;
explicit cleanup is `runner_control.py TRIAL --stop`.

## Displayed reuse milestone

`CA_GROUP_GUEST4` passes three code-distinct runtime packages in the same VM:
16 frames/job 1788767486669749/PID 306, 128 frames/job 1788767615126397/PID 344,
and 1,024 frames/job 1788767666184738/PID 384. All 1,168 frames have verified
native presentation and successful handoff of surface 7 from supervisor PID 68.
The 1,024-frame batch contains 1,024 render submissions and 17 private texture
allocations. There is no per-frame verification readback; final pixels are
checked after the timed batch. Final DCP DMA bytes equal the independently
verified IOSurface: 12,432,384 bytes, SHA-256
`74de9c5f281206229aa1c46545056ced52fa8862f3f8cb29f3a107a3bf8ab29c`.

The 1,024-frame pacing result is 60.005 completed fps, median work 11.352 ms,
p95 19.354 ms, maximum 419.089 ms, with 310 absolute deadline misses. Maximum
native scanout gap is 433.050 ms. First-use frames cost 1659.184 and 57.575 ms;
setup is separately 257.290 ms. These are measured tails, not smooth 60 Hz.
Frame 578 accounts for the large outlier: render 6.781 ms, retirement 409.180 ms.
Host retirement RPC seq 4578 takes 51.709 microseconds; the next host acquisition
arrives about 409.6 ms afterward. Completion delivery/guest scheduling remains
unresolved; this is not evidence for a shader optimization. Resource accounting
ranges from 12,959,744 to 15,581,184 bytes as intermediates are created/released.
Guest resident memory increases 327,680 bytes across the sampled batch; all
consumer objects are released afterward. This is bounded-run evidence, not a
leak-free endurance claim.

The installed red control (job 1788767709446905/PID 413) passes; native display
completion and fresh input ACKs pass afterward. The inspected recovery image
shows the normal software lock screen and charging battery. A native Home
submission has two successful HID dispatches; its before/after images are equal,
so it is not evidence of navigation.

`capture_runner_scanout.py TRIAL --job 1788767666184738` briefly pauses only the
idle owned VM to trigger QEMU's one-shot final DMA export, verifies it, and
resumes. The pause/collection takes 1.634 seconds outside every timed batch.
This is not checkpoint/migration support, and QEMU only exports once per VM.
The subsequent fresh-process installed control 1788767893272035 passes.
The interactive VM stays live for further UIKit development; no session expiry
or automatic teardown is claimed for this evidence snapshot.

Durable runtime packages: `~/dvm-artifacts/gpu-quartzcore-group-ios27`.
GUEST4 evidence and final host tests: research package
`gpu-group-opacity-20260907-part3`. Use `runtime-build/driver_host` with the
shared linked packages; historical `offscreen-linked` uses `offscreen-build`.

The next priority is real UIKit view/layer content and visual effects, followed
by normal system-compositor adoption. Pacing limitations remain recorded; they
must not be hidden by average throughput or weakening the deadlines.
