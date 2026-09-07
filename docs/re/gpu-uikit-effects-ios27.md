# UIKit material effects and bounded command staging — 2026-09-07

This continues `f02bc08` in `codex/metal-driver-ios27`. The exact guest remains
24A5430a, iPhone17,3/T8140, with SPTM/TXM, native SMC and the migrated disk
ancestry. No QEMU/kernel/tree/baseline changes were needed. All new jobs use
fresh processes in the disposable `CA_UIKIT_EFFECT_GUEST1` interactive VM.
The previous displayed-UIKit and 1,024-frame result is a separate workload;
these effect probes are **320×480 offscreen CARenderer**.

## Findings and acceptance boundaries

| Question | Evidence | Status / limit |
| --- | --- | --- |
| Why did material blur disappear? | Guest V11 audit rejects BGRA/private usage 65541; native Metal names bit 0x10000 `MTLTextureUsageBlockWritesOnly` | Concrete descriptor-contract failure identified and fixed |
| Does forwarding the bit actually render? | Host native-versus-forwarded UIKit blur: all 3 frames byte-identical; arithmetic private-render/read/reuse test covers BGRA/RGBA | Proven in those host workloads |
| Does exact guest UIKit submit the effect? | Job 1788779250840552: 9 passes, 23 draws, visible material, successful child exit, zero live objects/bytes | Execution and retirement proven; independent effect pixel oracle still missing |
| Can the guest transmit its complete command list? | 67,680-byte JSON request staged in 3 chunks, then validated/executed at seq 7954 | Proven on the existing MMIO service, no debugger/NVMe |
| Is guest backend replay stable under validation? | 182 requests match original replies and pixels with API and shader validation enabled | Proven replay; not an independent rendering reference or another guest execution |
| Does `UIGlassEffect` render here? | Guest job 1788779399653124 equals the no-effect image; host no-window control also equals its no-effect image | No visible effect in the tested no-window configurations; normal window behavior unknown |
| Is fallback display/input preserved? | Fresh red package job 1788779643691816 verifies exact pixels; Home ACKs and post-job D594/A408 completion pass; software lock-screen screenshot | Proven recovery observation, not system GPU-compositor adoption |

The generic red/CPU-layer oracle does **not** pass the blur job. Its
`result.json` and `uikit-analysis.json` remain `verified:false`; neither was
promoted because the process exited successfully. The guest CPU layer renderer
cannot itself supply a correct material-effect reference. Host UIKit and guest
UIKit create different backdrop filters, so host UIKit pixels cannot simply be
used as the exact guest's oracle.

## Texture and function contracts

V13 retains V11's framebuffer-read support and adds the demonstrated private
color usage **65541 = shader read | render target | block writes only**, only
for owned private 2D RGBA8/BGRA8 textures. Other combinations, storage modes and
formats are rejected. The native usage flag is passed unchanged; native texture
usage must echo it. Capability negotiation probes both native allocations.
Neither a general private-bit mask nor compute-write semantics is advertised.

`inspect_texture_usage.m` observes native descriptors and allocations with
Metal API validation. `test_private_block_texture.m` renders an independent
integer pattern, reads the private texture in a later GPU pass, verifies every
output byte, and reuses both formats for eight frames each: 32 passes/draws,
zero retained resources. Two first-frame requests intentionally exceed the
MMIO envelope and exercise the frontend staging path.

This test also exposed a real metadata omission: unspecialized
`newFunctionWithName:` returned a name-only object with `functionType=0`.
It now obtains the owned native function through the same function RPC used
for specialized functions, returning the actual stage. Compute execution and
frontend ownership/cancellation tests still pass. The fake transport in the
ownership unit test now explicitly models a kernel-function reply.

Applying the host's API validation layer to the *custom frontend* still fails
its private-class check (`texture is not a MTLTextureImplementation`). This is
preserved separately from validated **native backend** execution; passing the
latter does not claim compatibility with the host validation wrapper.

## Command transaction

The V12 guest blur attempt (job 1788778853429815) passes descriptor allocation
but exits with `GPU_LOAD_ERROR driver=mmio-request-size`, before GPU rendering.
The installed MMIO helper has a fixed 65,536-byte request slot. Its size check
remains intact.

V13 introduces one bounded render transaction per backend session:

1. `renderStageBegin`: reserve at most 2 MiB, with expected SHA-256 and a new
   monotonically increasing token.
2. `renderStageChunk`: owned token, exact next offset, at most 32 KiB decoded
   bytes. Frontend also bounds the serialized envelope, including possible
   JSON escaping, with room for the transport sequence.
3. `renderStageCommit`: require all bytes and the expected digest; accept only
   an unsequenced `renderSubmit`; pass it through the existing complete render
   validator before executing anything.
4. `renderStageAbort`: discard pending bytes. Stale tokens fail. Resource
   mutation/release and other submissions are refused while staging is active.

Small requests stay direct. GPU completion is returned only by commit, with
the staged byte count/digest. FIFO resource retention and error cancellation
remain active. Memory/transaction counts return to zero after completion.
Host unit tests cover incomplete/oversized uploads, bad offsets/tokens/hash,
abort, resource exclusion, and an invalid final command that must not execute
an earlier clear. This does not add parallel queues or checkpoint support.

The bytes are ordinary captured JSON/base64, not native pointers or Metal
objects. The transport envelope is suitable for another backend; the render
commands, validation and shaders still require an actual alternative backend.
The private texture flag is specifically a native Metal dependency.

`render_staging_capture.py` reconstructs logical submissions for pixel
verifiers only after checking chunk order, token, byte count, digest and GPU
completion. Raw wire records remain unchanged and replayable. Corrupted,
incomplete and aborted captures cannot become successful render submissions.

## Exact guest observations

The final initial blur capture has GPU SHA-256
`b1c96c1505bff64023d9ef41840c09c9d03a9e64754733be15cb7b748e36405a`.
Its one staged request is 67,680 bytes, digest
`fd9914971dd969eb25f1ce717fa62e343dbd69b39d0924abafc958dd08e81b5a`.
Chunk acknowledgements advance to 32,768, 65,536 and 67,680. Commit reports
9 passes, 23 draws, completed status 4 and GPU duration 133.625 µs.
The process duration is 1.124 seconds including setup/inspection/capture.
**Neither number is sustained frame latency or throughput.** Setup and final
readback are not removed from the process duration.

The guest filter observation is `UICABackdropLayer` with
`luminanceCurveMap`, `colorSaturate`, `colorBrightness`, `gaussianBlur`.
The host native control instead observes `CABackdropLayer` with
`sdrNormalize`, `gaussianBlur`, `colorSaturate`. Full parameters and normal
window context must be established before an independent exact-guest effect
reference can be claimed.

The glass job exits cleanly with 3 passes/10 draws, but its GPU hash is
`394fc03ad61980e02e3aed45e13e86c9a640f386fea88ee4321f5c5dd244b385`,
identical to the already verified no-effect scene. UIKit's SDK documentation
notes window-context limitations for visual effects. That motivates a window
experiment; it does not establish the cause in this guest.

## Failures retained

- BUILD1's multiline filter description violated the audit single-line record
  contract and caused the host runner to stop the prior VM. Pending audit slot
  1368 contains the multiline NSArray; job 1788778073403276 has no completed
  result. This was our diagnostic bug. `UIKitOneLine` now strips CR/LF before
  writing bounded descriptions; BUILD2 and later guest audits validate.
- BUILD2 V11: exact descriptor rejection above; output equals no-effect.
- BUILD3 V12: MMIO request-size failure above.
- Host unsplit-command and three-frame controls did not recover the V11 blur.
  They rule out those particular splitting/first-frame explanations.
- Builtin control job 1788779559841462 uses the pinned helper's **512**-pixel
  capability expectation against the newer 4096-pixel profile; it fails
  `capability-maxTextureWidth2D`. Rebuilt package control 1788779643691816 uses
  the current profile and verifies exact red pixels. The old helper was not
  changed to conceal that mismatch.

## Reproduction and durable evidence

Small records, captured uploads, completed-job audit RAM, images, validation
replay and failure logs:
`/Users/jdolbe1/dvm-artifacts/research/gpu-uikit-effects-20260907-part1`.
The index records source paths, lengths and SHA-256 values. Full isolated
packages/build manifests live in
`/Users/jdolbe1/dvm-artifacts/gpu-uikit-effects-v13-ios27`.
Use `CA_UIKIT_EFFECT_GUEST_BUILD4` as the initial blur build, BUILD5 for the
negative glass probe, BUILD6 for the current-contract red control, and BUILD7
for the final envelope-bounded implementation. Final fresh-process job
1788779916389756 repeats 9 passes/23 draws, clean retirement and the identical
`b1c96c…405a` GPU output. Its captures and final recovery observer are in the
adjacent `gpu-uikit-effects-20260907-part2` evidence directory.

```sh
# Host-only controls; use new output directories.
export DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer
bash tools/gpu/build_host_driver_tests.sh /tmp/dvm/NEW_EFFECT_TESTS
DVM_DRIVER_BUILD=/tmp/dvm/NEW_EFFECT_TESTS PYTHONPATH=tools/gpu \
  python3 -m unittest test_driver_host test_driver_binary \
  test_consumer_verify test_shared_consumer_verify test_render_staging_capture -v
/tmp/dvm/NEW_EFFECT_TESTS/test_private_block_texture

# The running isolated VM can accept the next fresh-process revision.
python3 tools/gpu/build_consumer_package.py BASE NEW_BUILD --frames 1 \
  --uikit --uikit-external-reference --uikit-effect blur --renderer-flags 2
python3 tools/gpu/sign_linked_revision.py NEW_BUILD/DVMProxy.bundle NEW_LINKED \
  --parent /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/installed-build/dvm-gpu-load
python3 tools/gpu/runner_control.py /tmp/dvm/CA_UIKIT_EFFECT_GUEST1 \
  --bundle NEW_LINKED/DVMProxy.bundle --mode data --development --test package \
  --expected observe --worker NEW_BUILD/driver_host
python3 tools/gpu/analyze_uikit_capture.py JOB_DIRECTORY

# Replay original commands, including staging, without another guest process.
MTL_DEBUG_LAYER=1 MTL_SHADER_VALIDATION=1 python3 tools/gpu/replay_driver.py \
  JOB_DIRECTORY --worker MATCHING_BUILD/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --out NEW_REPLAY_DIRECTORY
```

Current focused regression run: 74 tests pass, 10 fixture-dependent tests skip;
the skips are explicitly recorded, not passes. Native framebuffer-read control
and private block-write/reuse test also pass. Exact red control and native
input/display recovery are separate runtime acceptance checks.

The following window experiment is now recorded in
[half-float, mip and glass evidence](gpu-uikit-glass-ios27.md): the original
guest glass shaders execute, but the glass region is black in both the guest
and native host offscreen controls. This supersedes the no-window unknown
above without claiming correct glass appearance.

Original next experiments: attach the actual effect to a real UIKit window, capture
its resulting layer/filter inputs, and compare the guest blur against a native
QuartzCore reference built from those inputs. Bound this to one host control
and one exact-guest configuration; if glass still produces no effect, inspect
its exact guest implementation rather than repeating boots. Only then extend
the displayed effect and pacing suite. Global discovery, system-compositor
adoption, normal window interaction, original lock-screen Liquid Glass and
broad Metal compatibility remain incomplete.
