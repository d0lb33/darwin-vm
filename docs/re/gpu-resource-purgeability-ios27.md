# Compositor resource purgeability — 24A5430a

This batch follows the first completed actual backboardd compositor submission
in `CA_SURFACE_IMPORT_GUEST23`: 21 passes, 58 draws, then the exception
`DVM Metal: purgeable allocations unsupported`. That run did not identify the
resource handle or requested state. It did not verify native display output.

## Static evidence and native host controls

The exact QuartzCore selector stub at `0x18801d3a0` resolves to
`setPurgeableState:`. Seven direct calls were found in this disassembly:

| Call address | Argument x2 | Meaning |
| --- | --- | --- |
| `0x184470fb4` | 3 | Volatile |
| `0x1844a11ec` | 2 | NonVolatile |
| `0x1844ab4f8` | 3 | Volatile |
| `0x1845047e4` | 2 | NonVolatile |
| `0x1845bb98c` | 3 | Volatile |
| `0x18471da04` | 3 | Volatile |
| `0x1847218a4` | 2 | NonVolatile |

`0x18471da04` is in `CA::Render::FlattenManager::add_free_surface_to_pool`,
starting at `0x18471d8a8`, and sends to the object at `*(x19)` (`surf->surf`).
The concrete Objective-C receiver class remains unresolved. These calls are a
static checklist, not an exhaustive trace or evidence that all receivers are
Metal resources. Indirect calls remain outside this count.

`probe_native_purgeability.m` measures actual host Metal return values under
Metal API Validation. For shared/private 64 KiB buffers and 64×64 BGRA textures,
requests `[1,3,1,2,1,4,1,2,1]` return `[2,2,3,3,2,2,4,4,2]`. The setter returns
the prior state; KeepCurrent queries it. For 16×16 BGRA textures, all those
requests return 2: native Metal retains those allocations as NonVolatile.
The small-allocation cause is not established by the probe; do not fabricate a
Volatile/Empty result merely because the caller requested it.

The SDK's `IOSurfaceRef.h:435–436` explicitly distinguishes IOSurface-backed
texture purgeability: higher-level Metal/OpenGL transitions can be ignored.
The exact guest's `IOSurfaceSetPurgeable` at `0x1bffba7e8` unwraps the surface
and calls `IOSurfaceClientSetPurgeable`; this is a separate contract from Metal
resource state. Neither observation proves that a page-pinned import can be
discarded safely. Native IOSurface use counts also are not GPU fences.

## Implemented forwarding contract (V21)

- Owned buffers/textures forward KeepCurrent, NonVolatile, Volatile and Empty
  to the native allocation, returning both previous and queried current state.
  Buffer-backed texture aliases share their buffer's state.
- The serialized worker finishes prior GPU work before a transition. Resources
  reported Volatile/Empty cannot be reused for GPU work or copied CPU access
  until NonVolatile reacquisition. This does not add concurrent queue support.
- Pending copied CPU writes are flushed before Volatile. Empty invalidates
  texture read caches and buffer upload comparisons. Reacquisition/refill
  cannot reuse stale upload-cache assumptions. Allocation budgets stay reserved;
  budget accounting is not resident-memory measurement.
- A lost/malformed purgeability acknowledgement quarantines that resource and
  its buffer aliases. Further CPU access, transitions and GPU reuse fail; there
  is no in-place uncertain-state recovery claim. Local unsupported pinned
  transitions are rejected before transmission and do not release the pin.
- Pinned IOSurface imports retain their separate ownership contract. V21 accepts
  KeepCurrent/NonVolatile and rejects Volatile/Empty explicitly. This is a scoped
  unsupported operation, not a conclusion that native IOSurface purgeability is
  impossible. A rejected request must not release host aliases or kernel pins.

## Focused host validation

`test_purgeability_frontend.m` verifies retained pending writes, native Empty
reacquisition/refill, buffer/linear alias state, CPU-access rejection while
volatile, cache invalidation, and no repeated upload for unchanged retained
buffers. Small 1D/2D/3D texture cases accept the measured native state and verify
pixels after reacquisition/refill. `test_driver_host.py` has 27 passing tests,
including malformed states and rejection of GPU submissions using Empty
shared/private targets before reacquisition.

The existing render-writeback test passes after dirty-upload helper extraction.
Imported-surface forwarding still verifies eight GPU frames/4,096 final HDR
pixels and final-alias retirement. Its native-backend branch also passes Metal
API Validation. The full synthetic frontend render descriptors are incompatible
with Apple's validation wrapper (which requires `MTLTextureImplementation`);
that assertion is separate from native backend validation and is not a guest
failure. The project suite has 81 passing tests.

These host controls do not establish exact-guest purgeability, correct
compositor colors, DCP presentation/input recovery or sustained pacing.

## Exact guest result: CA_PURGEABILITY_GUEST1

Same registry kernel/QEMU as GUEST23; new arm64e V21 bootstrap, fresh disposable
installation and disk boot, exact guest libraries. No debugger, RAM restore,
NVMe GPU transport, dynamic revision replacement or migrated-baseline write.
Build1's plugin SHA256 is
`3b7b176007b935821a3ef7bb8acd1fc2f5a167cb6da9a3e2f688ae83dcb4e9bf`.

- Backboardd registered as PID 73. Request 55 imports resource 1 as texture 28,
  with the same 1,478 registered pages and RGBA16Float layout as GUEST23.
- Request 255 completes **21 render passes / 58 draws**, native status 4,
  GPU **2,297.125 µs**, host service **9,272.833 µs**. This is one first-use
  compositor batch, not sustained pacing or guest-to-display latency.
  The 128,560-byte staged request has SHA256
  `f741fd2c840320ba7c396d03cf81a0ac44f6b7eb97ac870788fd002970b9b848`.
  Descriptors, constants, generated uploads and command order are retained in
  `driver-host.jsonl`; `expanded-driver-host.jsonl` verifies and expands staging.
- Request 256 identifies the previously failing receiver: owned **262,144-byte
  buffer 35** (created by request 73), requested state 3, previous 2, current 3,
  successful host acknowledgement. Exact-guest reacquisition/Empty and pinned
  IOSurface volatility have **not** been exercised.
- The next failed contract is now in native presentation:
  `stderr.log:21437–21439` records A408 input 4,084 bytes, followed by
  `scanout unsupported primary BGRA profile` and
  `display-state failed; A408 retained, no D594`.
  There were 256 host RPCs, zero host errors, one completed render batch and no
  native presentation. The run stopped at its 180-second readiness deadline;
  `run_system_boot.py` now stops directly on that explicit display failure.

`extract_stopped_swap.py` found one header-matched A408 candidate across the
stopped DRAM file, at physical `0x1001c750000`. It has swap ID 0, null flags
`[0,0,1,1,1]`, FourCC **0x52476841 (RGhA)**, 1179×2556, row 9472,
descriptor size 24,211,456 and surface DVA `0x10000000000`. Its body SHA256 is
`9bcd5b7eec569fdc4054fdb4eba38fd54cc66316926b1785c48bbb06a5cb3bb4`.
This is a stopped-memory candidate, not a separately captured dispatch trace;
its format explains the parser's explicit rejection at
`qemu-sptm/hw/arm/darwin_iomfb_swap.c:77`.

Final imported bytes have SHA256
`007f0ed05f14abea527c80347cca77030382f4635f6d221bb1226a81e998f3f6`.
A clamped linear-to-sRGB diagnostic preview shows a dark lock-screen clock,
date and controls. RGB maximum is 1/256; alpha is one. The preview applies no
exposure normalization and is **not a DCP screenshot or color oracle**.
Later exact transfer-tag analysis shows that this surface is sRGB encoded;
the historical linear-to-sRGB preview must not be used as its color oracle.
[Subsequent RGhA work](gpu-rgha-compositor-ios27.md) records actual native
presentation/completion and final console verification. Full acceleration
acceptance remains incomplete.

## Next bounded implementation

First resolve the observed RGhA swap's format and color metadata, using the
captured descriptor plus targeted exact QuartzCore/IOMFB inspection. Reproduce
the parser rejection on the host, implement only the demonstrated conversion
contract, and test bounds, stride, channel order and color transfer. Then require
actual DCP completion and input recovery. Do not multiply brightness by 256
merely to make this preview look plausible. Multiple frames, memory growth,
native surface retirement and sustained pacing remain separate gates.

The post-boot lost-ack quarantine hardening is host-tested and arm64e-built as
BUILD2; it was not the binary in GUEST1. Its injected dropped-reply tests stop
CPU access through buffer aliases and reject texture GPU work before host
submission. This distinction avoids spending a redundant boot while keeping
the exact-guest evidence pinned to BUILD1.

## Reproduction and durable evidence

Small evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-purgeability-20260907`.
Pinned binaries, swap record and final surface:
`/Users/jdolbe1/dvm-artifacts/research/gpu-purgeability-20260907-inputs`.
Full DRAM/disposable disks are excluded from those packages.

```sh
bash tools/gpu/build_host_driver_tests.sh /tmp/dvm/CA_PURGEABILITY_HOST4
/tmp/dvm/CA_PURGEABILITY_HOST4/test_purgeability_frontend
DVM_DRIVER_BUILD=/tmp/dvm/CA_PURGEABILITY_HOST4 \
  python3 -m unittest discover -s tools/gpu -p test_driver_host.py -v
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
  python3 tools/gpu/build_system_bootstrap.py \
  /tmp/dvm/CA_UIKIT_INVALIDATION_RED_BUILD1 \
  /Users/jdolbe1/dvm-artifacts/extract/bin/backboardd \
  /tmp/dvm/CA_PURGEABILITY_BUILD1 --surface-import
python3 tools/gpu/prepare_system_bootstrap.py /tmp/dvm/CA_PURGEABILITY_BUILD1 \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-render-ready-ios27/launchd.plist \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-handoff-ios27/tc \
  /tmp/dvm/CA_PURGEABILITY_STAGE1
python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/CA_SURFACE_REGISTRY_KERNEL2/control.json \
  --stage /tmp/dvm/CA_PURGEABILITY_STAGE1 --tag CA_PURGEABILITY_INSTALL1 --mmio-restore
python3 tools/gpu/run_system_boot.py /tmp/dvm/CA_PURGEABILITY_INSTALL1/warm-manifest.json \
  --worker /tmp/dvm/CA_PURGEABILITY_HOST3/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry-inputs/library-cache \
  --tag CA_PURGEABILITY_GUEST1 --seconds 180
python3 tools/gpu/extract_stopped_swap.py /tmp/dvm/CA_PURGEABILITY_GUEST1 /tmp/dvm/CA_PURGEABILITY_SWAP1
```

Use new tags/directories when reproducing. The worker directory needs the
existing `transport-mode.txt` (`--mmio-present-pool`). Build1/host3 above name
the measured revision; use matching current frontend/backend builds for new
experiments. The prior import package pins the unchanged QEMU/kernel inputs.
