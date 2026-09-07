# Descriptor compute in the compositor — 24A5430a

This advances the actual backboardd compositor, preserving its imported
IOSurfaces and native DCP path. It is not universal Metal conformance.
Dynamic backboardd revision loading remains deferred.

## Contract and evidence

V23–V25 add a descriptor-based compute pipeline using the **owned native function
handle**, including its specialization constants. Unlike the legacy luma/blur
control path, the new path has no shader-name allowlist. Native reflection
supplies active binding indices, access, minimum sizes, alignment and texture
types. Unsupported reflection types/arrays fail pipeline creation. Reflection
cannot prove data-dependent shader array bounds; those remain the application's
Metal responsibility.

The supported descriptor admits empty stage-input/linking defaults, direct
threadgroups, a maximum-thread limit, an execution-width promise and required
threadgroup shape. It rejects active stage input, linking, archives, preloaded
libraries, nondefault buffer mutability, indirect commands and guest reflection
requests. Native reflection is retained for backend validation only. Host probing
found that reading stageInputDescriptor and linkedFunctions lazily returns
non-null **empty** objects; treating non-null as active incorrectly rejected the
first host rehearsal. Their default fields are recorded, not guessed.

The ordered submission path admits render/descriptor-compute/blit combinations,
with preflight before uploads or GPU commit. Each compute dispatch ends a native
encoder; tracked native resources supply inter-encoder dependencies. Sparse
texture/buffer/sampler bindings and dynamic threadgroup memory are captured.
Writable buffer results are delivered before guest completion callbacks, and
objects remain retained through completion. This does not implement arbitrary
barriers, concurrent dispatch, events or multiple execution queues. Legacy
function-based luma/blur command packets remain separate; mixing their packet
format into the new ordered path is unsupported.

Bounds are transport policy: eight binding slots per class, 4 KiB inline values,
1 MiB aggregate writable-buffer delivery, up to 1,024 threads/group (further
limited by the actual pipeline), 16,777,216 invocations/dispatch and 32 KiB total
static/dynamic threadgroup memory. The frontend's existing per-slot dynamic
allocation limit remains 16 KiB. V24 expands the ordinary allocation budgets below; imported limits are
unchanged. No GPU family predicate was enabled to enter this path.

## Host validation, separate from guest execution

`test_compute_render_forwarding.m` compares native and forwarded host work over
six reused frames: render clear → specialized compute with sparse sampled and
writable textures, sampler and buffer bindings → render from compute output →
blit the computed buffer. All 256 pixels and both 256-element buffers match
independent expected values and native Metal. Final resource retirement reaches
zero objects/bytes. Six malformed later compute variants reject before any
native command-buffer submission. Active stage input, mutability, linking and
guest reflection requests also reject. The test substitutes its fixture library
only in its host RPC adapter; guest file loading remains unchanged.

`test_descriptor_compute_exact_air_and_atomic_rejection` loads the exact guest
QuartzCore AIR (SHA256
8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364)
on the host, creates descriptor pipelines for compute_average_luma and
compute_sum_luma, then runs three render→average→sum batches. Partial and final
float results match exactly. Malformed ownership, geometry, bindings and scratch
reject without executing the earlier clear or applying a staged poison upload.
This test passes with native Metal API validation enabled. All 30 backend tests
and 81 project regressions pass without the validation wrapper. The full backend
suite with that wrapper hits the previously documented proxy-object assertion
in test_render_writeback; this is not a new compute execution failure.

## Targeted exact-cache investigation

The descriptor builder at `0x1845abce0` sets function/label and conditionally
sets max threads 1024 or binary archives. Its direct private creation call is
`0x1844f3828`, with retry at `0x1844f3918`. These static branch values were not
observations of the compositor's descriptor.

The broader call decoder now identifies the related reduction path at
`0x1844eb6f8`–`0x1844ebae4`: pipeline thread-limit queries, texture view creation
at `0x1844eb8cc`, sparse buffer bindings, scratch and two dispatches at
`0x1844eb964/eba2c`, followed by a completion handler. The view preserves format
and texture type, selects one mip starting at x20, and slice {0,1}; the selected
level and runtime resource remain to be captured. This is the next resource
contract to inspect if the compositor reaches it, not proof that every call is
executed in the current scene.

Separate direct sites reference nonuniform dispatch (`0x1845a2a10`), tile
dispatch, and computeCommandEncoderWithDispatchType: (`0x1844f44b0`). A targeted window confirms the latter receiver comes from owner+0xf00 and
`0x1844f44ac` sets x2 to zero (serial); V24 implements that explicit serial
entry point and rejects concurrent type. The mixed host test alternates the
implicit and explicit serial methods. Runtime reachability remains separate. Tile/imageblock and nonuniform paths
remain unsupported. Unknown indirect calls and capability-gated branches remain
coverage gaps; absence from this boot does not settle them.

## Reproduction

```sh
bash tools/gpu/build_host_driver_tests.sh /tmp/dvm/CA_COMPUTE_DESCRIPTOR_HOST2
/tmp/dvm/CA_COMPUTE_DESCRIPTOR_HOST2/test_compute_render_forwarding
DVM_DRIVER_BUILD=/tmp/dvm/CA_COMPUTE_DESCRIPTOR_HOST2 \
  python3 -m unittest discover -s tools/gpu -p test_driver_host.py -v
DVM_DRIVER_BUILD=/tmp/dvm/CA_COMPUTE_DESCRIPTOR_HOST2 MTL_DEBUG_LAYER=1 \
  python3 -m unittest discover -s tools/gpu -p test_driver_host.py \
  -k descriptor_compute -v
```

The first test run in HOST2 contained a test-fixture indexing error: it assumed
dictionary serialization ordered sparse textures by slot. It accidentally bound
the writable intermediate as both textures, a valid contract. The corrected
negative test locates slot 2 explicitly. Original failures are retained.

## First exact boots and the allocation branch

CA_COMPUTE_DESCRIPTOR_GUEST1 (BUILD1/HOST2, V23) finishes in 94.145 s:
eight actual compositor submissions, eight native RGhA presentations and eight
D594 completions, 352 RPCs, zero host errors. Native Home down/up dispatch
increases by two, with no new input errors, stable helper PID165/epoch3,
empty queues and subsequent display. An earlier helper restart and rejected
ack already existed before the Home test; this does not prove a pristine input
boot. Final source→conversion→console has zero differing components. This
proves display delivery, not independent scene semantics or native Liquid Glass.
No descriptor compute call was reached before the eight-frame stop.

CA_COMPUTE_DESCRIPTOR_GUEST2 (same revision, no Home, 64-frame target) stops
at 94.713 s with 125 successful RPCs, zero render submissions/presentations:

```
GPU_LOAD_TEXTURE_REJECT reason=descriptor type=2 width=1216 height=2560 depth=1 format=115 storage=2 usage=65541 options=32 levels=1 samples=1 array=1 compression=0 surface=0 plane=0
```

This private image requires 24,903,680 logical bytes, exceeding the 16 MiB cap.
V24 permits 32 MiB per private image and 64 MiB total ordinary allocation bytes,
leaving copied images, per-transfer sizes, imported-page accounting and object
limits unchanged. These are bounded supported allocation budgets, not copied
host hardware limits. They do not count all native compiler/driver overhead.
The extended private-block test renders a 1216×2560 RGBA16F chain at four mip
levels across eight frames and samples changing edges into a small output;
all sampled pixels and retirement match. Full native-allocation residency and
sustained growth are measured separately.

V24 also quarantines shared leases and imported mappings after any partially
executed split batch fails, covering targets absent from render target fields.
The existing GPU-generated invalid-index/lease rejection test still passes.

`sample_boot_memory.py RUN --seconds 180` records only the owned QEMU and its
sibling Metal worker once per second. Sampling begins at invocation and may
miss short peaks. It does not perform guest RPCs or claim Metal allocation
counts from RSS; it stops when that VM exits or reaches its own bounded deadline.

## Current exact result: descriptor passes, texture view blocks dispatch

CA_COMPUTE_DESCRIPTOR_GUEST3 (BUILD2/HOST4, V24) completes six native displayed
batches (88 render passes, 242 draws), then rejects RPC 449 with
`render batch extent`: **35 render passes**, 575 encoded operations and two
uploads. Preflight rejects before executing that new batch. Final prior
source→conversion→console remains exact. The earlier 1216×2560 allocation
branch did not occur in this boot, so this run is not guest verification of that
new allocation limit.

V25 uses a negotiated 64-command limit across ordered render/compute/blit
recording and host preflight; the existing 2 MiB request byte bound is retained.
The paired native/forwarded test now includes one 64-command mixed batch and
rejects a 65-command variant before execution. This verifies the larger count
on the host; it does not claim a 64-pass exact compositor trace.

CA_COMPUTE_DESCRIPTOR_GUEST4 (BUILD3/HOST5, V25) reaches **actual guest compute
pipeline creation** at RPC 434, function handle 185 (compute_average_luma),
returning pipeline 186. Actual descriptor audit:

```
GPU_LOAD_COMPUTE_DESCRIPTOR function=compute_average_luma max=0 multiple=0 required=0,0,0 archives=0 options=0 stageInput=1 indirect=0 stack=1 validation=0
```

Host reflection reports texture0 (2D read), buffer0 (20 bytes/alignment4 read),
buffer2 (16 bytes/alignment16 read-write) and threadgroup0 (16 bytes/alignment16
read-write), execution width 32, maximum threads 1024, static scratch 0. The guest
then fails with:

```
GPU_LOAD_SYSTEM_UNCAUGHT name=NSInvalidArgumentException reason=-[DVMTexture newTextureViewWithPixelFormat:textureType:levels:slices:]: unrecognized selector sent to instance 0x7be2cb4ee0
```

**Zero descriptor-compute dispatches occurred.** It completes four earlier
native displayed batches (82 render passes, 225 draws), 434 successful RPCs,
and final source→conversion→console matches exactly. Elapsed boot 89.660 s.
Its first batch GPU time is 4.550 ms; later batches 0.428–1.194 ms. Host batch
service 1.821–20.627 ms excludes earlier uploads, guest scheduling and DCP;
these are startup samples, not end-to-end frame latency or sustained pacing.
The 64-frame target is unmet.

The trace accounts for a peak 145 live handles, 12,615,296 bytes of reported
ordinary native allocations (buffer-backed aliases excluded), and 48,431,104
bytes of unique imported spans. Seventeen late one-second RSS samples peak at
30,384 KiB for the host worker and 5,599,856 KiB for QEMU. Those are separate
measurements; GPU allocations do not necessarily appear in process RSS, and
startup samples cannot establish absence of a leak.

The next coherent resource batch is same-format 2D single-mip views: native
subresource mapping, parent/view retention and retirement, shared purgeability
state, binding validation and transfer semantics. Runtime source handle/level
must be captured; static x20 alone does not establish the level. Do not return
the parent blindly or claim general texture reinterpretation. Capability-gated
events and unresolved indirect receiver calls remain on the coverage checklist.

All generated buffer uploads, descriptors, function constants and command order
remain captured. Standalone replay of the entire compositor stream still needs
an explicit imported-memory initial-state fixture; the ordinary-resource replay
tool does not recreate live registered guest DRAM. This is a replay limitation,
not evidence against the guest import/compute architecture.

Final validation: HOST5's 30 backend tests and six-frame mixed comparison pass;
HOST4's 24 private-target frames include the full-size RGBA16F case. Project
regressions 81 pass. QEMU remains commit 83cc50a, SHA256
`df3efb8588fda07ce83fdcee929dd0174e677055d5d3ceb3004ac4aae7d09ab6`;
no QEMU code changed or rebuild was needed in this batch. Each guest boots a
fresh child of the guarded installed parent; parent-chain verification runs
before and after. No dynamic restart, debugger or unrelated VM was used.

## Durable artifacts and isolated reproduction

Evidence: `/Users/jdolbe1/dvm-artifacts/research/gpu-compute-descriptor-20260907`
(1,552 records, 287,586,394 bytes). Executables, trust caches and two thin
installed children: sibling `gpu-compute-descriptor-20260907-inputs` (indexed
SHA256 identities). `control-1.json` reproduces the eight-frame V23 milestone;
`control-3.json` reproduces the V25 texture-view blocker. Both point to the
preserved exact registry kernel, SPTM/TXM, native SMC configuration and the
unchanged durable migrated backing chain. Their pinned input hashes and disk
chains were reverified after copying. These are experimental packages, not a
replacement for the migrated baseline.

```sh
python3 tools/gpu/run_system_boot.py \
  /Users/jdolbe1/dvm-artifacts/research/gpu-compute-descriptor-20260907-inputs/control-3.json \
  --worker /Users/jdolbe1/dvm-artifacts/research/gpu-compute-descriptor-20260907-inputs/HOST5/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --library-cache /Users/jdolbe1/dvm-artifacts/research/gpu-compositor-import-20260907-registry-inputs/library-cache \
  --tag UNIQUE_COMPUTE_REPRO --seconds 180 --min-presentations 64
```

For the completed eight-frame/input control use `control-1.json`, `HOST2`,
`--min-presentations 8 --home-after-presentations 4` and a new unique tag.
`verify_rgha_scanout.py RUN` checks final display delivery independently of the
runner's completion verdict; `summarize_compositor_trial.py RUN` records timing,
allocation accounting and sampled RSS without labeling startup as steady state.
