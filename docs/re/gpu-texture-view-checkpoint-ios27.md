# Texture-view development checkpoint — 2026-09-07

This is an implementation checkpoint, not a new exact-guest milestone. The
last guest failure remains `DVMTexture
newTextureViewWithPixelFormat:textureType:levels:slices:` in
`CA_COMPUTE_DESCRIPTOR_GUEST4`; see `gpu-compute-descriptor-ios27.md`.
That run created `compute_average_luma`'s pipeline but dispatched no compute.

Contract V26 adds owned native same-format 2D texture views with contiguous
mip ranges and slice 0. Nested views retain their parents, preserve relative
level metadata, and share allocation ownership. Views do not add allocation
bytes to accounting. Render, compute and blit validation now follows view
ancestry when checking overlapping resources. General reinterpretation,
arrays, planes and swizzles remain unsupported; general `textureViews` remains
false, with the narrower contract described separately.

## Host evidence

Durable build, source snapshot and logs:
`/Users/jdolbe1/dvm-artifacts/research/gpu-texture-views-20260907-commit-host`.
Prior experiments, including failed expectations, are in `prior-experiments/`.

`test_texture_views.log` verifies 24 changing render/sample frames across
RGBA8, BGRA8 and RGBA16F, including a 1216×2560 allocation, four mip levels,
nested views, edge samples, exact expected output, completion and zero final
live resources. Its additional shared-view test checks CPU alias writes and
a partial update preserving preceding GPU writes. These use harness-authored
shaders through the frontend/backend on macOS; they are not guest AIR tests
or DCP presentation evidence.

`native-probe.log`, collected with Metal API validation, observes immediate
parent identity, relative mip levels and equal allocation sizes for nested
views. It also disproves an earlier residency assumption: private roots report
Volatile (3) while their views report NonVolatile (2). The implementation
therefore returns separate native view and allocation-root states. A view's
metadata alone cannot establish safe allocation reuse.

Reproduction (choose a new output directory):

```sh
bash tools/gpu/build_host_driver_tests.sh "$out"
DVM_DRIVER_BUILD="$out" python3 -m unittest discover -s tools/gpu -p test_driver_host.py -v
DVM_DRIVER_BUILD="$out" python3 -m unittest discover -s tools/gpu -p test_shared_render.py -v
"$out/test_texture_views"
python3 -m unittest discover -s tools/tests -v
xcrun clang -fobjc-arc -O1 -Wall -Wextra -Werror tools/gpu/probe_native_texture_views.m -framework Metal -framework Foundation -o "$out/probe_native_texture_views"
MTL_DEBUG_LAYER=1 "$out/probe_native_texture_views"
```

The checkpoint rebuild passed 30 backend tests, 6 shared-render tests (including
the owned-surface frontend with its fixture), and 81 project tests. The
focused texture-view, purgeability, imported-surface, ordered compute/render,
blit, private-texture and submission-queue executables passed. The initial
direct invocation of `test_owned_surface_frontend` lacked its required RAM
session fixture and failed `test session`; use its `test_shared_render.py`
harness, not the executable alone. Full outputs are retained.

## Remaining work before exact-guest acceptance

- Finish view-purgeability failure handling: quarantine the allocation if a
  transport exception occurs after sending a state change, matching the
  existing non-view implementation.
- Verify native per-view initial residency metadata and frontend reuse checks
  independently from the root's state. Add private-root Volatile/view
  NonVolatile tests and reacquisition checks.
- Add direct overlapping-view rejection and imported-view lifetime tests;
  current passing imported-surface tests cover the existing non-view path.
- Run V26 inside the isolated exact guest, capture the actual view request,
  then require compute dispatch, verified pixels, completion and display/input
  preservation. No V26 guest boot, sustained pacing or checkpoint claim is
  made here. The previously validated V25 boot package remains unchanged.

Do not mistake this checkpoint or its implemented selectors for completed
system-wide acceleration or universal Metal compatibility.
