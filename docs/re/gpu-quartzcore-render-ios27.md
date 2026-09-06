# Narrow CARenderer forwarding — exact iOS 27

This continues `gpu-quartzcore-consumer-ios27.md` on the isolated
`codex/metal-driver-ios27` worktree. **The narrow acceptance passed in
`CA_RENDER_GUEST7`: actual iOS CARenderer rendered a 64×64 red CALayer through
our process-local Metal driver, host Metal executed one indexed draw, and both
guest and host verified all 4,096 pixels.** All host resources retired. This is
an offscreen render result, not system-wide acceleration or display adoption.

## Inventory reconciliation

The read-only input checklist is
`~/dvm-artifacts/research/quartzcore-api-inventory-20260906-173144/inventory.md`.
It is useful coverage guidance, not an authoritative runtime sequence:

* `CA_CAPS_GUEST14` already verified all 20 scalar capabilities, using one
  negotiated profile. Its actual failure was `DVMTexture protectionOptions`.
* `newLibraryWithURL:error:` reads the consumer-requested guest file and forwards
  its verified AIR slice. Only the compile-time host rehearsal substitutes an
  explicitly supplied AIR path. The iOS build rejects that rehearsal flag.
* Generic IOSurface imports, global discovery, multiple queues and checkpoint
  support remain deferred. The existing managed blur/display extension and
  software compositor remain separate, preserved paths.

## Implemented batches

The complete profile is still negotiated once and compared with the compiled
guest contract. Version 2 adds these bounded operations; it does not mirror the
host's GPU-family feature table.

| Batch | Implemented contract |
| --- | --- |
| Allocation metadata | Accepted logical storage/cache options, tracked hazards, unprotected allocations, real host allocated size, no heap or purgeable allocation, texture dimensions/usage and backing relationships |
| Texture allocations | Single-sample 2D, one mip/slice, ≤512 per dimension and ≤1 MiB; R8Unorm, RG8Unorm, RGBA8Unorm, BGRA8Unorm and RGBA16Float |
| Linear textures | Read-only GPU views of owned buffers; alignment negotiated for the implemented host allocation, checked on both sides; parent cannot retire while a view exists |
| Render state | One color attachment, eight vertex/fragment buffer indices and fragment texture/sampler indices, single-sample direct render pipelines and sampler/depth state |
| Shader specialization | Exact supplied named/indexed scalar and selected vector constant bytes; real host `newFunctionWithDescriptor:error:`; no source compiler or replacement shader |
| Commands | One queue and one in-flight buffer; enqueue, bounded render passes, viewport/scissor, state/bindings, indexed and nonindexed draws, completion callbacks and status |
| Transfers | Owned buffer uploads split into ≤32 KiB chunks; render buffers must be read-only shader inputs, so no per-submission readback of those buffers; final texture read is explicit |

The frontend keeps CPU shadows for these ordinary Metal resources. This is not
the no-copy managed IOSurface extension. Managed logical shadows are uploaded
at submission. Linear views cannot be GPU outputs. Protected resources,
write-combined CPU shadows, heaps, memoryless/MSAA targets and general IOSurface
imports are rejected. Full GPU-family predicates remain false.

Host pipeline reflection rejects unsupported/writable shader bindings. Draw
validation checks binding minimum sizes, vertex footprints, index ranges,
target format/usage and single-color attachment ownership before committing.
An invalid later pass cannot execute an earlier pass. Immutable state and
resources remain strongly retained through completion.

The private constant accessors are checked against the receiver's method
signature before copying bytes. The exact guest Metal metadata also declares
`newNamedConstantArray`, `newIndexedConstantArray`, and the constant elements'
`name`/`index`, `dataType` and `data` properties. Host metadata alone was not
used as proof of the iOS ABI.

## Exact guest observations

### Successful consumer

`CA_RENDER_GUEST7/result.json` records `passed: true` and the scope
`exact-guest-CARenderer-64x64-red-CALayer`. The CRC/sequence-checked shared-RAM
audit ends with:

```
GPU_LOAD_CA_VERIFIED width=64 height=64 passes=1 draws=1 bad_pixels=0
GPU_LOAD_COMPLETE result=pass scope=quartzcore-render resources=0
```

`driver-host.jsonl` records the consumer's actual function specializations:

| Sequence | Function | Specialized name | Named constants |
| --- | --- | --- | --- |
| 5 | `fixed_frag_lph_cpf` | `Xfc` | 60 |
| 6 | `fixed_vert_lph_spc` | `Vfx` | 5 |

The unchanged guest AIR SHA-256 is
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
The host creates the real specialized vertex/fragment functions and pipeline.
Sequence 17 uses `loadAction=1` (load), binds pipeline 6 and buffer 7,
and issues `drawIndexedPrimitives` with triangle topology, six uint16 indices
at offset 528. Its encoded clear color is transparent black and is unused by
that load action; no red attachment clear is requested. Buffer 7 is 256 KiB;
eight 32 KiB uploads stay below the
64 KiB transport message limit after JSON/base64 encoding.

The host reply reports status 4 (completed), one pass and one draw. One explicit
final target read returns 16,384 bytes of BGRA `00 00 ff ff`. Its SHA-256 is
`c34fb4331b2d031d7c644860b54a678424c66ef12352fc165a91dc09840d98fd`.
The final stats reply has zero live objects. There are no rejected host RPCs or
optional pipeline failures in this exact guest run.

The run started from disk on a disposable child, with no debugger or saved RAM.
It stopped after 33.086 seconds; the workload was released at boot rather than
waiting for display readiness. The render request's host service time was
6.271 ms, with 30.792 µs reported GPU execution. Neither number includes the
preceding uploads, and neither is a sustained or guest end-to-end benchmark.

`CA_RENDER_GUEST8` repeats the acceptance with the final BUILD8 after the
existing readiness gate. `driver-readiness.json` records 51 native presentations,
ten seconds of stable input-helper identity and a fresh acknowledgement, with
59 acknowledgements and zero timeouts/restarts. Readiness took 136.911 seconds;
the run stopped successfully at 140.405 seconds. This gate proves native
presentation and helper readiness, not a visually confirmed home screen or a
gesture test. The CARenderer target remains offscreen.

GUEST8 again records one pass, one indexed draw, the same exact pixel/AIR hashes,
zero live resources and no rejected RPCs. Its render request reports 6.156 ms
host service and 30.875 µs GPU execution, subject to the same timing limitations.

### Previous failed contract and correction

`CA_RENDER_GUEST6` used the full resource/render batch, entered CARenderer,
completed initialization and `beginFrame`, then stopped during `render`:

```
GPU_LOAD_ERROR consumer_stage=render exception=NSInvalidArgumentException reason=-[DVMCommand setResponsibleTaskIDs:count:]: unrecognized selector sent to instance 0x7ced0dda80
```

This was a fresh disk boot on a disposable child, without debugger assistance
or saved RAM. It took 40.375 seconds to the recorded stop; this is not a latency
measurement. Both immutable ancestors and seven pinned boot inputs were checked
again afterward.

Static inspection then established the next command contract:

* `CA::OGL::MetalContext::start_command_buffer` at `0x1844a1f98` calls
  `protectionOptions`, `commandBuffer`, `setLabel:`, `setProtectionOptions:`,
  `setResponsibleTaskIDs:count:`, `enqueue`, and `addCompletedHandler:`.
* `MTLIOAccelCommandBuffer setResponsibleTaskIDs:count:` at `0x1a55a1334`
  copies count × four bytes: the load/store at `0x1a55a13b4`/`0x1a55a13b8`
  uses 32-bit words with a four-byte stride. The implementation now copies a
  bounded list into command metadata. These remain guest IDs; they are never
  passed to host Metal as host task identities.

## Host rehearsal, kept separate

`CA_RENDER_HOST10` uses macOS QuartzCore and explicitly substitutes the exact
guest AIR library for its requested Mac library. It completed one render pass
and one indexed draw, verified all 4,096 pixels, and retired its host objects.
It also attempted an unused pipeline that was rejected with ENOTSUP because
its bindings exceeded the read-only render contract. That rejection is retained
in the evidence rather than hidden or treated as a failed shader compiler.

Earlier `CA_RENDER_HOST7` produced a green component in the nominal red output.
`CGColorCreateGenericRGB` plus an unspecified destination color space was not a
valid pure-sRGB pixel oracle. Both source color and `kCARendererColorSpace` now
explicitly use sRGB. The same AIR and draw path then produced exact BGRA
`00 00 ff ff`; no shader tuning or tolerance relaxation was used.

The probe retains the renderer through its exception handler. Otherwise an
exception during renderer destruction can mask the first missing selector with
a second exception from `endFrame`.

## Timing and remaining scope

GPU and kernel timestamp pairs currently retain the host Metal clock domain,
explicitly named in the negotiated profile. Their differences are usable host
durations; their absolute values must not be compared with the guest clock.
Guest clock translation is not implemented, and this prototype must not be
published globally with an implied system-wide Metal timing contract.

This workload uploads the consumer's buffer shadows; it does not yet retain
dirty ranges or eliminate those copies across frames. No native scrolling,
Liquid Glass, actual CARenderer-to-DCP presentation, multiple in-flight work,
or live checkpoint claim follows from the narrow offscreen test.

All experiments retain iOS 27 build 24A5430a, iPhone17,3/T8140, SPTM/TXM, native
SMC and the migrated disk lineage. The kernel/DT transport and QEMU binary are
unchanged from the preceding tested checkpoint; only isolated driver artifacts
and child disks are updated.

## Reproduction and preservation

Run these from the isolated worktree. The `--driver-present` flag selects the
existing mode-3 transport; `--driver-consumer` selects a separate offscreen
verifier and does **not** claim DCP presentation of the CARenderer output.

```bash
DVM_CA_PROBE=1 bash tools/gpu/build_driver.sh /tmp/dvm/CA_RENDER_BUILD8 --mmio-present-pool
DVM_DRIVER_BUILD=/tmp/dvm/CA_RENDER_BUILD8 python3 -m unittest discover -s tools/gpu -p 'test_*.py'
python3 tools/gpu/prepare_driver_update.py \
  --before-build ~/dvm-artifacts/gpu-managed-pool-ios27/driver-build \
  --build /tmp/dvm/CA_RENDER_BUILD8 \
  --cache ~/dvm-artifacts/gpu-managed-pool-ios27/launchd.plist \
  --system-tc ~/dvm-artifacts/gpu-managed-pool-ios27/tc \
  --out /tmp/dvm/CA_RENDER_STAGE8
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/CA_CONSUMER_RESTORE1.json \
  --stage /tmp/dvm/CA_RENDER_STAGE8 --tag CA_RENDER_INSTALL8
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/prepare_display_state_trial.py \
  --boot-build /tmp/dvm/POOL_EXPORT_BOOT4 \
  --qemu "$PWD/qemu-sptm/build/qemu-system-aarch64" \
  --installed /tmp/dvm/CA_RENDER_INSTALL8/warm-manifest.json \
  --out /tmp/dvm/CA_RENDER_STATE8
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/CA_RENDER_STATE8/state.json --tag CA_RENDER_GUEST8 --seconds 360 \
  --driver-mmio --driver-present --driver-consumer --driver-wait-display \
  --driver-worker /tmp/dvm/CA_RENDER_BUILD8/driver_host \
  --library-cache ~/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib
```

The first successful run used BUILD7/STATE7 and omitted `--driver-wait-display`.
BUILD8 changes only `rootResource` for a buffer-backed texture to identify its
parent buffer. The consumer target is an ordinary texture. The final verifier
also requires matching positive guest/host draw counts; a clear-only pass cannot
satisfy acceptance. Its negative tests cover zero/mismatched draws, an incorrect
pixel and a read preceding the render completion.

The successful BUILD7 image is preserved as
`~/dvm-artifacts/gpu-quartzcore-render-ios27/control.json`, with its matching
`driver-build`, exact AIR, immutable flattened disk and pinned boot inputs.
`qemu-img compare` reported `Images are identical.` Rechecking the trial inputs
verified two immutable ancestors and seven boot inputs unchanged. Preservation
does not imply a checkpoint or an additional boot of the flattened image.
The final BUILD8/readiness-gated result is separately preserved at
`~/dvm-artifacts/gpu-quartzcore-render-ready-ios27/`, with the same package
layout. Each package pairs its own installed image with its matching driver;
do not mix their binaries and disks.

After `/tmp` is cleared, the preserved installed image can be tested directly
with a fresh child and a unique tag, without the intermediate install files:

```bash
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py \
  ~/dvm-artifacts/gpu-quartzcore-render-ready-ios27/control.json \
  --tag CA_RENDER_PRESERVED_RETEST --seconds 360 \
  --driver-mmio --driver-present --driver-consumer --driver-wait-display \
  --driver-worker ~/dvm-artifacts/gpu-quartzcore-render-ready-ios27/driver-build/driver_host \
  --library-cache ~/dvm-artifacts/gpu-quartzcore-render-ready-ios27/QuartzCore.metallib
```

Small evidence is archived under
`~/dvm-artifacts/research/gpu-quartzcore-render-ios27/`, indexed with per-file
SHA-256. Its `CA_RENDER_GUEST6`, `CA_RENDER_GUEST7` and `CA_RENDER_GUEST8`
directories preserve exact runtime verdicts, audits, host request/reply ledgers,
owned 16 MiB transport RAM and final pixels. `CA_RENDER_HOST*` are explicitly
macOS rehearsals. `CA_EXACT_*` are static exact-guest disassembly/metadata;
`CA_FUNCTION_CONSTANT_METADATA2.txt` is host metadata only. Upload packet hashes
are preserved; the render buffer chunk bytes are not individually captured.
This supports the execution proof, not offline replay of every original packet.

Exact metadata/disassembly collection used the guest dyld cache, for example:

```bash
ipsw dyld macho ~/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e Metal --objc
```

The final source verifier was rerun against GUEST7 and used live in GUEST8.
Build directories preserve the verifier copy present at build time; the
repository verifier additionally checks matching positive draw counts. The
runtime native binaries are unaffected by this Python verifier tightening.

Host regressions: 84 GPU tests, 83 passed and one optional test skipped
(`CA_RENDER_TESTS_FINAL.log`); 79 project tests passed
(`CA_RENDER_PROJECT_TESTS.log`). QEMU remains pinned at `59e34b3`; no QEMU source
or executable was rebuilt or changed in this consumer batch.

The smallest next step is a repeated, changing CALayer on the same queue and
resources, followed by an explicit bridge to the already verified managed
IOSurface presentation path. Its unresolved dependencies are dirty-buffer
tracking, render-target alias/import coherence, display retirement and clock
translation for pacing. These are separate from the now-proven guest shader
specialization and indexed-render submission. Global discovery, multiple queues
and checkpoint support remain deferred.
