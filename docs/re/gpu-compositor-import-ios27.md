# Retained compositor IOSurface imports — exact 24A5430a

This is the next step after the temporary descriptor pin probe, not system UI
acceleration acceptance. No different guest, SPTM/TXM change, NVMe transport,
debugger attachment or dynamic backboardd restart is involved.

## Contracts and current evidence

The opt-in `--surface-import` bootstrap enables a separate registry extension
alongside the unchanged V20 rendering profile. It accepts nonplanar shared
BGRA8/RGBA16Float surfaces, one mip/sample/array element, usage 1/4/5, dimensions
up to 4096, row up to 65536, 64 MiB per registration and 256 MiB total mapped
page spans. Copied/private texture budgets are unchanged. No GPU family,
compressed/protected/planar surface, imported blit or imported compute support
is advertised.

The kernel selectors are `0x44565301` register and `0x44565302` retire, each with
three scalar inputs and four outputs. Registration input is `[1, callerVA,
allocationBytes]`; output is `[1, resourceID, allocationBytes, mappedSpan]`.
Retirement input is `[1, resourceID, 0]`; output is `[1, resourceID, modelStatus,
descriptorCompleteStatus]`. The existing pin probe remains `0x44565300`.
The builder checks the exact input BootKC SHA, dispatcher bytes, RX cave and
native virtual-call targets/PAC diversities. The ledger records every patch.

The caller must have the transport entitlement and use the dedicated provider.
Native `IOGeneralMemoryDescriptor` resolves and prepares the calling task's
range. Userspace supplies neither a physical address nor a page list. Prepared
descriptors live on the owning client, retained by the provider; process death
does not authorize ownership transfer. The kernel-only third aperture exports
its page list to QEMU. IDs are monotonic and never reused; the model rejects
duplicate/overlapping pages, including the legacy fixed pool. This does not
establish a general process-port-transfer or recovery contract.

QEMU writes an immutable session-tagged page manifest under the owned
`managed-pages.bin.imports` directory. The host checks identity, bounds,
permissions and unique pages, maps the actual DRAM file pages contiguously,
then creates `MTLBuffer` and texture aliases without a pixel copy. Registration
bytes and mapped page span are deliberately distinct for partial edge pages.

Frontend import and queued retirement run on one serial device executor. The
mapping retains its service connection and IOSurface beyond texture deallocation
until the host releases the final native alias. Only then does the host unmap
and write a session/ID-specific `.retired` tombstone. QEMU validates that record
before permitting native descriptor `complete`. Failed/uncertain completion
quarantines pages and prevents further reuse; it does not guess that unpinning
is safe. Host process exit is not treated as a completion fence.

The second bootstrap revision also holds the native IOSurface use count until
successful retirement. SDK `IOSurfaceRef.h:394-419` describes this as the pool
recycling contract; it is distinct from GPU completion and DCP ownership.
Exact-cache IOSurface metadata contains increment/decrement methods and
category variants; strings at `0x1bffce036/0x1bffce0f9` describe prohibited-use-
count failures. This static evidence does not establish which category Apple's
Metal implementation uses. Display fencing, concurrent producers, native pool
reuse and active checkpoint state still require exact-guest tests.

## Host contract tests

- `surface_registry_test.c`: ASAN/UBSAN checks malformed byte/page extents,
  overlapping/duplicate pages, fixed-pool exclusion, stale IDs, partial edges,
  retirement, slot and byte budgets, and poison state. It exercises the same
  pure-C registry engine as QEMU, not kernel dispatch or filesystem acks.
- `surface_pages_test.m`: scattered file alias, foreign session, malformed
  manifest, permissions, duplicate/out-of-range pages and retirement replay.
- `test_imported_surface_frontend.m`: synthetic 64×64 RGBA16Float registration,
  two aliases, eight forwarded clear passes, all 4096 signed/HDR final pixels
  correct, no readback RPC, one 32768-byte backing allocation, host last-alias
  acknowledgement before provider retirement and replay rejection. This is
  a host rehearsal and does not execute guest QuartzCore or XNU.
- The native backend branch of that test passes with Metal validation enabled.
  The frontend branch cannot use that layer: Apple's descriptor setter aborts
  with `texture is not a MTLTextureImplementation` before our forwarding code.
  The initial failed run is retained, not counted as GPU incompatibility.
- 81 project unit tests and 26 existing host driver tests pass. The alignment
  regression adds a seven-format aggregate test, caching and a host-derived
  maximum; all four sampled-only 1D formats remain excluded.

These eight-frame runs are correctness/ownership checks, not sustained pacing
measurements. Native GPU durations alone exclude transport and presentation.

## Exact backboardd experiment CA_SURFACE_IMPORT_GUEST21

The disposable disk boot stopped at its first failed contract after 90.281 s.
Actual backboardd registered the arm64e driver; SPTM/TXM remained enabled. Its
audited registration was:

```
GPU_LOAD_SURFACE_REGISTER surface=2 kr=0 count=4 id=1 bytes=24211456 span=24215552 ok=1
```

QEMU independently recorded 1478 pages. Host request 55 imported the measured
1179×2556 RGhA surface, row 9472, format 115, usage 5. Host Metal returned texture
handle 28, `mappedBytes=24215552`, `allocatedSize=24215552`, after 2838.917 us
of host service time. That is first-use host import cost, **not frame latency**.
Registration/session/page evidence and raw guest audit CRCs were checked.

There were **zero render/blit submissions and no verified compositor pixels**.
Request 60 failed exactly:

```
request: {"op":"linearLayout","format":23,"seq":60}
reply: {"ok":false,"code":45,"description":"unsupported linear format/alignment"}
```

The aggregate `deviceLinearReadOnlyTextureAlignmentBytes` getter incorrectly
iterated every texture format, including V20's sampled-only R16Uint 1D LUT.
The host correctly rejected that unsupported linear-view query. The fix filters
the aggregate through the existing linear format contract; it does not enable
linear LUT allocation. The getter's transport wait was also moved outside the
owner monitor so a preceding completion can retire its queue slot.

Dynamic backboardd loading/restart remains deferred. Boot registration, surface
registration and host texture creation pass within the above scope. Actual
compositor GPU execution, correct output, safe DCP presentation/reuse,
display/input recovery and sustained pacing/memory acceptance remain open.

## Exact follow-ups: GUEST22 and GUEST23

GUEST22 passed the corrected alignment batch and native use-count check,
created 140 host objects through 223 successful RPCs, then stopped at 105.118 s
on `-[DVMCommand commitAndWaitUntilSubmitted]: unrecognized selector`.
There was no host submission in that run.

Targeted exact Metal disassembly resolved the new contract:
`-[_MTLCommandBuffer commitAndWaitUntilSubmitted]` at `0x1a54fcb64` clears
`_wakeOnCommit`, calls `commit` at `0x1a54fcb88`, then tail-calls queue
`submitCommandBuffer:` at `0x1a54fcbb8`. The queue implementation at
`0x1a54d6bf8` synchronously dispatches its submission block and returns the byte
result of `_submitAvailableCommandBuffers` (block `0x1a54cd880`). The driver
commits and waits for its existing host completion reply, returning true only
on success. This is conservatively later than a separate submission ack; it is
not a new asynchronous scheduling or latency claim. Focused tests cover actual
dispatch, failure, duplicate commit and existing FIFO/callback routing.

The related deadline variant at `0x1a568af90` calls `commitWithDeadline:` then
the same queue method. The base class's `commitWithDeadline:` implementation at
`0x1a568af88` raises `doesNotRecognizeSelector:`; a concrete driver subclass may
override it. Its parameter type and deadline semantics remain unresolved and
unsupported here, not declared impossible.

**GUEST23 executed actual compositor work on the host GPU.** Request 255
(`renderStageCommit`) reconstructs a 128560-byte guest `renderSubmit`, SHA-256
`0f9fb6328cb0934e387246da58edf93e8c2ed1006c701813840d15dfd99c0d1c`.
All generated buffer uploads, pipeline descriptors, constants and 21 ordered
render passes / 58 draws are captured. Seven passes target handle 28, the actual
1179×2556 guest compositor IOSurface. Native Metal returned status 4, zero
written-buffer entries, GPU time 3348.500 us. Host service time was 15933.833 us;
these are first-use batch measurements, not steady frame pacing.

The next exact failure is `DVM Metal: purgeable allocations unsupported` after
that completion. The trial stopped at 98.149 s, 255 host RPCs, no host errors.
The failing resource and requested purgeability state are not logged yet;
inspect the exact call and implement ordinary-resource discard/reacquire
semantics separately from pinned IOSurfaces. Do not turn volatility into fake
nonvolatile success. The SDK notes both CPU/shared and device cached copies
must participate and that IOSurface-backed resources use IOSurface purgeability.

The final imported byte range is preserved independently of host Metal. It has
finite pixels, alpha 1, RGB maximum 0.00390625 and 254660 nonzero RGB components.
The diagnostic preview contains the real lock-screen date, clock and controls
but is very dark. It uses clamped linear-to-sRGB decoding with no exposure
normalization. This is **not a verified display image or pixel oracle**; first-
frame animation/brightness and HDR/color interpretation are still unresolved.
No native display presentation or sustained reuse is established by this run.
The current QEMU scanout parser accepts only packed BGRA (`darwin_iomfb_swap.c:77`);
whether the accelerated display submits RGhA directly is still unobserved. Its
format/color and surface lifetime contract must be resolved before acceptance.

The lost-host-retirement-reply test now also proves that the frontend does not
call provider retirement and refuses subsequent GPU reuse with an empty queue.
The first fault-injection test exposed a test-fixture NSError lifetime error
(`NSError retain` on a freed object); it was fixed by returning errors outside
the native-resource autorelease pool, and the corrected test passes.

Snapshot capture copies only registered, unretired logical byte ranges from a
stopped owned VM. GUEST22 snapshots precede any host submission; GUEST23 bytes
are explicitly post-run output and cannot masquerade as replay inputs. The
existing replay tool needs registered-resource setup for these new captures;
full imported-input/concurrent-producer replay is not yet established.

Durable diagnostic records (971 files, 118835763 bytes) live at
`/Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry`.
The sibling `gpu-compositor-import-20260907-registry-inputs` contains the tested
QEMU, guarded BootKC, all three arm64e revisions, host worker, trust caches,
exact shader cache and bounded surface snapshots. Its execution index records
hashes. The migrated baseline and unrelated interactive VM were untouched;
all three test VMs stopped explicitly at their bounded contract failures.

`snapshot_registered_surfaces.py TRIAL NEW_OUT` requires the owned VM to be
stopped and excludes retired pages. `preview_registered_surface.py SNAPSHOT 1
NEW.png` creates the explicitly labelled diagnostic view with numpy/Pillow.
The complete guest audit in each trial passes independent sequence/session/CRC
verification against the retained 16 MiB transport snapshot.

## Reproduction

From the isolated worktree, use fresh output tags. The base manifest pins the
migrated disk lineage and native SMC. Installation creates a disposable child;
`run_system_boot.py` creates another child and stops on the first failure or
registered rendering plus native presentation. Its regression deadline is
separate from interactive runner lifetime.

```sh
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
  bash tools/gpu/build_host_driver_tests.sh /tmp/dvm/IMPORT_HOST
cp /Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry-inputs/transport-mode.txt /tmp/dvm/IMPORT_HOST/
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
  python3 tools/gpu/build_surface_pin.py \
  /Users/jdolbe1/dvm-artifacts/gpu-runtime-loader-ios27/bootkc \
  /tmp/dvm/IMPORT_KERNEL --registry
# Rebuild this worktree's QEMU before deriving/pinning the new manifest.
python3 tools/gpu/derive_gpu_manifest.py \
  --source /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/control.json \
  --bootkc /tmp/dvm/IMPORT_KERNEL/bootkc --qemu "$PWD/qemu-sptm/build/qemu-system-aarch64" \
  --output /tmp/dvm/IMPORT_KERNEL/control.json
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
  python3 tools/gpu/build_system_bootstrap.py \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-render-ready-ios27/driver-build \
  /Users/jdolbe1/dvm-artifacts/extract/bin/backboardd /tmp/dvm/IMPORT_BUILD --surface-import
python3 tools/gpu/prepare_system_bootstrap.py /tmp/dvm/IMPORT_BUILD \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-render-ready-ios27/launchd.plist \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-handoff-ios27/tc /tmp/dvm/IMPORT_STAGE
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/IMPORT_KERNEL/control.json \
  --stage /tmp/dvm/IMPORT_STAGE --tag IMPORT_INSTALL --mmio-restore
python3 tools/gpu/run_system_boot.py /tmp/dvm/IMPORT_INSTALL/warm-manifest.json \
  --worker /tmp/dvm/IMPORT_HOST/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry-inputs/library-cache \
  --tag IMPORT_GUEST --seconds 180
```
