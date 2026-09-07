# Transport-owned render target: backend prerequisite

2026-09-07, continuing `gpu-client-storage-scenes-ios27.md`.
This milestone adds the host resource and synchronization contract for ordinary
render commands over the existing registered page pool. It does **not** yet
connect guest CARenderer to that shared target or establish DCP retirement.
The v5 guest interface and capabilities are unchanged. No VM was booted for
these host-only changes; the prior exact-guest and display evidence remains
separate.

## Observed results

`tools/gpu/test_shared_render.py` drives the actual backend over its framed
protocol, using a disposable sparse RAM file and the existing page manifest.
The 759 pages are deliberately registered in reverse physical order. The
host maps them into one CPU/Metal virtual allocation, then constructs a BGRA8
render target: **1179×2556, row 4864, allocation 12,435,456 bytes**.

Five new tests passed:

* Three render/reuse cycles and two native command buffers per cycle. Final
  verification reads the original file pages and checks all **3,013,524 pixels**,
  row padding and allocation tail. No pixel readback RPC is used.
* Initial CPU contents survive allocation. Subsequent CPU writes remain visible
  through a native load/store pass without uploading those pixels.
* Invalid later commands execute no earlier GPU pass and leave shared pixels
  unchanged. A valid retry remains possible.
* Foreign sessions, duplicate/unaligned/out-of-range pages and incorrect or
  incorrectly typed layouts are rejected without allocating a live resource.
* A 150 ms native GPU-event hold prevents an early response and pixel write.
  The reply arrives after completion with native status 4.

The 19 existing driver/resident tests also passed, including copied compute,
client-buffer writeback, generated indices and the previous managed blur path:
**24 tests in 2.287 s**. A subsequent targeted writeback test adds a state-only
shared lease over its real GPU target: invalid GPU-generated indices execute
the first draw, reject the dependent draw, poison the lease and prevent seal.
That test passed; it does not substitute for the separate scattered-page test.

## Resource and synchronization contract

The optional backend operations are `sharedRenderCreate`, `sharedRenderAcquire`,
`sharedRenderSeal` and `sharedRenderRetire`. These accept handles, monotonic
epochs, layout and completion metadata; they never use guest-provided host
addresses, GPAs or file paths. Allocation uses `DVMMapManaged` with the session
read from the host-controlled transport file. Only one owner of the existing
pool is admitted, and it cannot overlap the resident blur owner.

| State | Permitted transition | Rejected behavior |
|---|---|---|
| Idle | Acquire the next epoch; release allocation | Render, seal, stale/skipped acquisition |
| Acquired | Multiple synchronous render submissions, then seal | Release; seal before any completed GPU write |
| Sealed | Retire matching epoch with native mode-1 wait-success report | Further GPU writes, reuse, early release |
| Failed after partial GPU execution | Worker teardown | Seal, reuse, ordinary release |

The ordinary render encoder accepts the shared texture only while acquired.
Its native buffer directly aliases the registered pages. GPU completion is
waited before recording a completed write. A failure after partial execution
invalidates the frame; failed preflight with no execution permits retry.
Copied texture upload/readback APIs reject this allocation. Resource accounting
includes its full backing length. Texture and buffer references are dropped
before the resource unmaps its host address; physical guest pages retain the
existing VM lifetime.

`sharedRenderRetire` validates the caller's reported swap ID, epoch, wait mode
and result. **A report is not independent proof that DCP retired the surface.**
The host tests use synthetic wait reports. The exact-guest integration must
correlate native swap completion separately, as the existing display acceptance
does. Multi-process ownership, arbitrary IOSurfaces, checkpoint recovery and
malicious-caller enforcement of CPU access remain unproven.

## Next guest integration and stop condition

The fixed helper's `Namespace` owns the service connection and page mapping;
the current Metal factory receives only an RPC function. Add an explicit,
versioned process-local mapping provider that retains that connection. The
driver can then validate a matching owned IOSurface and descriptor, retain it
with the texture, and return that original IOSurface from resource metadata.
Do not extract the service from block internals or use an unrelated second
connection: the kernel pool deliberately rejects a different owner.

This needs one helper/bootstrap revision, then subsequent test/driver revisions
can use the persistent runner. Group and validate the interfaces before that
boot. Keep the generic IOSurface-import capability false until its broader
contract exists. The fixed display-sized allocation is an explicit extension
to the ordinary 512-dimension / 1 MiB copied-resource profile.

Next acceptance: actual guest CARenderer renders into the owned IOSurface via
this ordinary driver path, native Metal completion precedes display submission,
native retirement precedes reuse, and final pixels match without per-frame
output copies/readback. Stop at the first failed mapping, allocation, completion
or presentation contract and retain its evidence. Host allocation tests alone
cannot pass that acceptance.

## Reproduction and evidence

```sh
bash tools/gpu/build_host_driver_tests.sh NEW_BUILD
DVM_DRIVER_BUILD=NEW_BUILD PYTHONPATH=tools/gpu python3 -m unittest tools.gpu.test_driver_host tools.gpu.test_managed_driver tools.gpu.test_shared_render -v
```

Durable evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-shared-render-backend-20260907`.
It contains build sources, backend/test binaries, test logs, and a replay of
the previously captured four-frame exact-guest image submission. Replay uses
the unchanged guest AIR by verified hash and checks outputs and replies; it
is host replay, not a new guest run.
