# Actual compositor scanout and larger copied textures — 24A5430a

This continues the existing isolated `codex/metal-driver-ios27` worktree.
The guest, registry kernel, SPTM/TXM, native SMC, migrated backing chain and
software fallback are preserved. Dynamic backboardd restart remains deferred.
These runs use disk boots of disposable children, with no debugger or injected
test scene. They are not evidence of universal Metal or Liquid Glass correctness.

## Exact color contract and implementation

Targeted analysis of the exact dyld cache resolves the observed RGhA A408:

- QuartzCore `CA::Render::iosurface_color_tags_for_colorspace`,
  `0x18452bf18`: comparisons at `0x18452bff8` and the following block match
  CoreGraphics sRGB/extended-sRGB constants `0x1e05b1d18/0x1e05b1db8`.
  `0x18452c018` selects transfer 13; the stores at `0x18452bfa8/fac` pair
  it with primaries 1. Linear/extended-linear uses transfer 8 at `0x18452c050`.
- IOSurface `createTransferFunctionFromStruct`, `0x1bffbf78c`, reads bulk
  attachment byte `+0x3c`. Jump table `0x1bffbf8dc` sends 13 to
  `0x1bffbf888` (sRGB), 8 to `0x1bffbf864` (linear), and has distinct PQ/HLG
  entries. The retained disassembly and decoded table preserve this evidence.
- QuartzCore `iosurface_set_colorspace`, `0x18452b588`, stores the copied
  CGColorSpace property list through `IOSurfaceSetValue` at `0x18452b5dc`
  and the tags through `IOSurfaceSetBulkAttachments2` at `0x18452b63c`.
  The first import's property-list query is absent in RGHA_GUEST1, below;
  that does not override the later actual A408 tags.
- IOMobileFramebuffer `_kern_SwapSetLayerEDRCompensation`, `0x22a390e88`,
  stores the enabled byte at object `+0x564` for layer zero. `_kern_SwapEnd`,
  `0x22a391334`, sends object `+0x18`, length `0x6e0`, at `0x22a391350/358`.
  The kernel copies this prefix verbatim into A408 at `0xfffffff00a0c9088`.
  Thus primary EDR enable is wire `+0x54c`; the observed value is zero.

The parser supports the observed nonplanar, untiled RGhA profile: 8-byte
elements, 1×1 pixel groups, sRGB transfer 13/primaries 1, EDR compensation
disabled, bounded dimensions/row/allocation/DVA. Existing BGRA handling stays
separate. Other transfer functions, plane/tile arrangements and enabled EDR
compensation are rejected. Other compression flags, matrix/curve processing,
extended-range display and general DCP color behavior remain unknown.

QEMU converts finite, already sRGB-encoded binary16 components to SDR BGRA8;
it clips finite out-of-range values and rejects NaN/Inf before presentation.
No extra sRGB encoding or exposure normalization is applied. This is a CPU
scanout conversion, not an additional host GPU pass or a no-copy display claim.
The host GPU still writes directly into the actual registered guest IOSurface.

The older purgeability preview assumed linear RGB. It was explicitly a
diagnostic, not a display oracle; its transform is inappropriate for this tagged
scanout. Its original file and failed acceptance classification remain intact.

## Independent host tests

`probe_rgha_colors.m` asks native CoreGraphics to convert a padded 256×256
sRGB RGBA16Float image to BGRA8. It covers all 63,488 finite binary16 patterns
in one channel, fixed other channels and opaque alpha. The QEMU conversion
differs in 160 channels by at most one byte; no larger difference occurs.
This validates the scoped host conversion, not Apple's full DCP color pipeline.
QEMU's six swap tests also cover malformed extents, unsupported transfers,
EDR/tile rejection, channel ordering, row padding and nonfinite rejection.

## CA_RGBA_SCANOUT_GUEST1: three actual presentations, then a byte limit

BUILD2 / V21 frontend, HOST4 purgeability backend, QEMU SHA256
`bb246584c8e1cf8553bef1ebb876cd923f2aa1bd3e850910c5cced908db443d2`.
The requested eight-presentation run stopped after 97.748 seconds at the first
new descriptor rejection. It completed 325 host requests with zero host errors.

Actual backboardd PID 73 imported two fullscreen surfaces, IDs 2 and 3:
24,211,456-byte logical allocations, 24,215,552-byte mapped spans each.
Audit records 8/10 report no color-space property list at import time;
records 9/11 prove successful retained page registration.

| Guest RPC | Render passes / draws | Host GPU time | Host service time |
| --- | --- | --- | --- |
| 256, staged 128,559 bytes | 21 / 58 | 1.918 ms | 8.756 ms |
| 267 | 14 / 44 | 0.930 ms | 2.330 ms |
| 292 | 14 / 44 | 0.432 ms | 1.923 ms |

These are startup samples, **not sustained pacing or guest end-to-end latency**.
Host request-arrival gaps are 156.265 and 206.491 ms and include guest scheduling
and intervening work. Native scanout's DMA/conversion/console times are 6.637,
7.333 and 6.148 ms. GPU-only time is not a proxy for scrolling performance.

`stderr.log:22112/22139/22165` records real RGhA presentations through native
DART/display, alternating DVA `0x10000000000`, `0x10001738000`, then the first
again. Lines `22118/22145/22171` record subsequent successful D594 completions.
Every swap ID is zero, so acceptance must correlate **ordered occurrences**,
not a set of previously completed IDs. Two focused tests cover reuse, missing,
failed and mismatched completions.

The new final-only witness retains the normal DMA and converted allocations
after presentation, replacing the previous witness on the next frame. It does
no additional pixel copy, readback or hash in the timed loop. On VM stop it
exports the actual A408 request, source RGhA span and converted BGRA bytes.
This opt-in diagnostic retains approximately 36 MB, briefly twice that during
replacement; it is not a production memory measurement or guest resource lease.

Final source-to-BGRA conversion has **zero differing components**. Its RGB
maximum is now 1, opaque alpha, and small finite negative ringing clips to zero.
The original screenshot comparison fails: 372,825 RGB component differences.
Inspection identifies a separate QEMU PPM writer defect: it writes pixman's
3,540-byte padded rows for a 1,179-pixel RGB image requiring 3,537 bytes.
The file has 9,048,240 pixel bytes instead of 9,040,572. Discarding exactly the
three padding bytes per row gives **zero differences** from captured BGRA.
This diagnostic is retained separately from the failed original screenshot.
The writer fix emits exactly `width * 3`; a fresh run must validate that fix.

Audit record 12 is the next exact failed contract:

```
GPU_LOAD_TEXTURE_REJECT reason=descriptor type=2 width=1280 height=932 depth=1 format=30 storage=0 usage=1 options=0 levels=1 samples=1 array=1 compression=0 surface=0 plane=0
```

Format 30 (RG8Unorm) is already supported. Its 2,385,920-byte allocation exceeds
V21's 1 MiB ordinary texture budget. This is not an unknown shader or method.

## V22 coherent allocation/transfer batch

The supported contract raises ordinary texture allocation to 4 MiB, keeping
the 32 MiB total ordinary-resource budget, 16 MiB private-texture limit,
separate imported-page budget, 1 MiB buffers and 32 KiB transfer chunks.
Legacy unchunked reads stay bounded to 1 MiB; larger reads fail explicitly.
There remains one active chunked upload, committed atomically before GPU use.
CPU row/image spans remain bounded to 4 MiB, so a maximum-size allocation
does not imply arbitrary additional caller padding is supported.

Focused backend tests cover exact 1280×932 RG8 and maximum 4 MiB BGRA
allocation, ordered chunks, GPU blit/completion, final bytes, full-read
rejection, release/accounting and an over-limit descriptor. Native Metal API
validation passes for those backend contracts. The frontend test compares six
native/forwarded GPU copies, with repeated reuse, RG8 padded rows and partial
updates before/after completion. Pixels and retirement match. This is host
contract evidence, not a new guest rendering claim.

All 28 driver tests and 81 project tests pass. Applying Apple's host Metal API
validation wrapper to the custom Objective-C frontend still fails its Apple
`MTLTextureImplementation` class check; this known host-wrapper incompatibility
is kept separate from backend validation and exact arm64e guest loading.

## Follow-up exact runs and current priority

**CA_RGBA_SCANOUT_GUEST2** (BUILD3, HOST2, QEMU
`df3efb8588fda07ce83fdcee929dd0174e677055d5d3ceb3004ac4aae7d09ab6`)
completes four real render batches/presentations/D594 replies. Its final
source→BGRA and BGRA→PPM comparisons both have **zero differing components**.
This validates the corrected PPM writer on the actual native console.
The eight-frame test still fails: request 335 passes the large-image byte
validation but fails `object table is full`, code 28. The live-object inventory
contains exactly 128 entries, including 42 distinct function requests and 39
distinct pipeline requests. Recorded releases account for 19 buffers, 18
textures and four libraries; reported ordinary allocated sizes total 8,175,232
bytes. This is evidence of the fixed object bound, not proof of long-run leak
freedom. V22 now negotiates 256 objects. A focused native backend test fills
that bound, rejects the next object, releases/replaces a handle and retires all
objects with zero remaining resource bytes. Total byte budgets stay unchanged.

**CA_RGBA_SCANOUT_GUEST3** (BUILD4, HOST3, same fixed QEMU) passes that boundary:
424 host requests, zero host errors, 141 live objects at the next failure.
Request 314 allocates the actual 1280×932 RG8 image as handle 169. Seventy-three
captured chunks commit all 2,385,920 bytes at request 404. The third real render
batch, request 409, binds it at fragment texture slot 3 in pass 10, target 164,
and completes **27 passes / 65 draws** on host Metal. The earlier two batches
complete 21/58 and 21/59. These three native presentations all receive D594;
final source→BGRA→console again matches exactly. Final GPU times are 2.340,
1.417 and 1.445 ms; these are not sustained or guest end-to-end measurements.
The eight-frame count is unmet, and the optional Home test was never sent
because this run fails before the fourth completed presentation.

The exact current failure follows request 424, successfully creating the
unspecialized `compute_average_luma` function (type 3):

```
GPU_LOAD_SYSTEM_MISSING class=DVMDevice selector=newComputePipelineStateWithDescriptor:error:
```

This names an entry-point/descriptor contract, not an unavailable shader.
The existing luminance workload already executes that guest shader, but the
frontend currently implements only function-based compute pipeline creation.
The next coherent batch must address descriptor validation and forwarding,
related pipeline properties, and compute/render command ordering. Current
command routing selects a compute-only or render/blit path from the first
encoder; mixed render/compute ordering is not implemented. Do not add a
selector-only success shim or silently discard descriptor options.

Bounded static follow-up resolves the direct private call at QuartzCore
`0x1844f3828` and retry `0x1844f3918`, in `sub_1844f3698`. Descriptor builder
`sub_1845abce0` sets label at `0x1845abebc`, compute function at
`0x1845abec8`, optional maximum threads 1024 at `0x1845abee4` when kind byte
equals 10, and an optional archive at `0x1845abf14` when its owner field is
present. These are static branches, not an observed dump of this descriptor.
Captured function constants are empty in request 424. Runtime descriptor
values, archives, reflection and the subsequent actual dispatch combination
remain unobserved. Related public selector references also exist in the
inventory; their presence does not establish execution or receiver identity.

## Reproduction and next acceptance

The bounded runs require eight matched presentations/completions or stop on
the first concrete failure. Console delivery now passes for the final captured
frames. Compositor semantic correctness, input dispatch/recovery, native alias
retirement and sustained pacing/memory remain separate checks.

```sh
bash tools/gpu/build_host_driver_tests.sh /tmp/dvm/CA_RGBA_SCANOUT_HOST2
/tmp/dvm/CA_RGBA_SCANOUT_HOST2/test_large_texture_frontend
DVM_DRIVER_BUILD=/tmp/dvm/CA_RGBA_SCANOUT_HOST2 \
  python3 -m unittest discover -s tools/gpu -p test_driver_host.py -v
DVM_RGBA_ORACLE=/tmp/dvm/CA_RGBA_SCANOUT_ORACLE1 \
  qemu-sptm/build/tests/unit/test-darwin-iomfb-swap
python3 tools/gpu/run_system_boot.py \
  /tmp/dvm/CA_RGBA_SCANOUT_INSTALL3/warm-manifest.json \
  --worker /tmp/dvm/CA_RGBA_SCANOUT_HOST2/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry-inputs/library-cache \
  --tag CA_RGBA_SCANOUT_GUEST2 --seconds 180 --min-presentations 8
python3 tools/gpu/verify_rgha_scanout.py /tmp/dvm/CA_RGBA_SCANOUT_GUEST2
```

Use fresh tags. Installation follows `gpu-resource-purgeability-ios27.md` with
BUILD3/STAGE3. A copied control manifest repins only the rebuilt owned QEMU;
all other input hashes and the disk backing chain are verified unchanged.
GUEST3 uses BUILD4/STAGE4/INSTALL4 and HOST3, adding
`--home-after-presentations 4`. The checked Home probe requires both native
dispatch edges, drained queues, stable helper identity and a later completed
presentation within 15 seconds. It is still unexercised and does not claim an
independent UI-response oracle. Automated boot bounds remain separate from
interactive development sessions, which keep their existing unlimited lifetime.

Durable diagnostics, raw final scanouts, generated uploads and expanded command
batches: `/Users/jdolbe1/dvm-artifacts/research/gpu-rgha-compositor-20260907`.
The final GUEST2/3 QEMU executable, intermediate guest/host bootstraps and host
color oracle (GUEST1 QEMU is retained by hash/log only):
`/Users/jdolbe1/dvm-artifacts/research/gpu-rgha-compositor-20260907-inputs`.
The inherited registry kernel and exact shader cache remain pinned in the
earlier `gpu-compositor-import-20260907-registry-inputs` package. Full guest RAM
and disposable disks are excluded. `preserve_evidence.py --compositor-scanout`
admits only bounded final A408/RGhA/BGRA/PPM files whose hashes match their
verification ledger, including intentionally retained failed comparisons.
