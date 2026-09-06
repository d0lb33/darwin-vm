# Exact-guest GPU command round trip

2026-09-05. Follow-up to [shader/display feasibility](gpu-feasibility-ios27.md)
and [guest loading and forwarding](gpu-guest-forwarding-ios27.md).
Work remains in `codex/gpu-feasibility-ios27`, created from main `99e012c`,
following commits `586fc56` and `b147f0d`. No QEMU change or rebuild.

**Proven within this narrow workload:** an exact-guest process issues nine
commands through its signed forwarding bundle, the host executes unchanged
guest QuartzCore AIR on Metal, and the guest verifies the returned pixels in
its own IOSurfaces. This is now an actual guest/host round trip. It is not yet
a globally usable GPU or accelerated iOS UI.

`GPU_ROUNDTRIP_COLD3` also passes the full runner gate, including reader
shutdown and original input-helper startup. Earlier COLD2 proved the GPU
part but failed that handoff. Both results and the intervening fix are retained.

## Contract being tested

The earlier tests already established unchanged guest AIR execution on this
Mac and debugger-assisted pixel transfer into an existing display surface.
This experiment instead tests a guest-issued command, host GPU execution,
returned bytes, guest completion, and guest IOSurface verification without
host debugger pixel writes. It does not repeat the display or copied-pixel
checkpoint test and does not publish a global Metal device.

Guest: iOS 27.0 / 24A5430a / iPhone17,3 / T8140. Host: Apple M5 Max,
macOS 27.0 / 26A5421a. The source is the original disk-only migrated manifest
`/tmp/dvm/warm-input-v5/warm-manifest.json`, disk SHA-256
`d95356ee3ed9344ead971120c610ee48482aaf098840d3f4b9da40a694a099ae`.
Runners verify its backing chain and pinned QEMU, bootkc, DT, SPTM and TXM.
Each install and System boot uses a fresh child. Only copied small restore
ramdisks are attached to the host, through `safe_attach.sh`; migrated
System/Data are mounted only inside the disposable installer guest.

## Minimal implementation

The existing process-local forwarding objects retain their narrow selector
contract. A signed helper loads the bundle, reads the actual guest System
QuartzCore library, selects its unchanged 2,705,796-byte AIR slice, and hashes
it. `LIBREF` asks the host to load a cached copy with exactly that size and
SHA-256:

```text
8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364
```

The host re-hashes the cache before loading it into Metal. Missing cache,
wrong length and wrong digest are explicit failures. This experiment avoids
uploading 2.7 MB through the diagnostic console; it does not substitute a
host-authored shader or claim uncached shader upload. The earlier extraction
record establishes provenance of this byte-identical slice.

The workload is `read_write_surf_compute`, BGRA8 64×48, three input patterns
on each of three surface generations. Each operation sends 12,288 actual
input bytes and receives 12,288 GPU output bytes. The host poisons the
destination before dispatch, requires a differing pre-dispatch image,
waits for real Metal status 4, and compares both texture readback and its
locked host IOSurface. The guest mutates its source after commit, requires
an early output read to fail, waits, and compares returned texture bytes and
the locked guest IOSurface with the original expected pattern.

The v2 diagnostic link adds session IDs, fixed lengths, CRC32, byte offsets,
bounded resynchronization, stop-and-wait acknowledgements and retries. A
duplicate frame is acknowledged without appending its payload a second time.
There is one outstanding frame per direction; it is not a general scheduler
or a reconnect/replay protocol. CRC protects against console noise, not a
malicious peer. Limits are 128 payload bytes/frame, 80 attempts/frame at
0.5-second intervals, a 900-second supervisor deadline, and a shorter host
runner deadline in the commands below.

The guest sends its nine-operation verification report through the protected
stream. A protected close carries the raw child wait status and consumed
response offset; this also retires a lost final response ACK. Success requires
that close with status zero, nine independent host GPU witnesses, byte-exact
request/response verification, the guest report, and the original input
helper's start marker. Unprotected success text alone cannot pass the test.

The supervisor temporarily owns the console and then execs the unchanged
original input helper. Concurrent input/GPU traffic is not tested. Global
GPU discovery and software rendering configuration are unchanged. The helper
retains the previously verified IOSurfaceRootUserClient class exception and
platform-application entitlement; the bundle has no new broad entitlement.

## Failures retained

`GPU_ROUNDTRIP_BUILD1` failed to link `___snprintf_chk`, emitted by the new
report formatting code. The minimal link declarations lacked that symbol;
the exact guest export inventory contains it. Adding the declaration made
BUILD2 link, and the complete final import list was checked against guest
exports. No guest framework implementation was replaced.

`GPU_ROUNDTRIP_COLD1` reached the protected session handshake, then at
28.248 host seconds reported:

```text
DVMGPU_DONE reason=console-disconnected wait_status=9 fallback=original-input
```

No library request or GPU execution completed. The original input executable
started at 28.403 seconds and subsequently reached `DVM_INPUT_READY` at
234.284 seconds. This trial disproves that version's console-poll handling,
not shader compatibility. It returned immediately on any HUP/ERR/NVAL before
reading; the precise `revents` bits were not recorded, so their identity and
the guest kernel's underlying condition are unknown. The owned VM was
terminated after observing input readiness. A teardown monitor-path race is
also retained in the runner traceback; `result.json` still records the
failed success gate, and the VM is gone.

BUILD3 replaces console polling with a blocking console reader thread feeding
a pipe. The main loop polls that pipe, drains data before EOF, and retains
the same frame and deadline logic. This is supported by the existing input
helper's blocking-read design. An independent Terra/high review also found
that Apple's reference XNU can report POLLIN with POLLHUP: see
[poll event mapping](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/sys_generic.c#L1859-L1876)
and [console select delegation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/dev/arm/cons.c#L187-L200).
This reference analysis does not identify the exact failed guest event.

## Observed round-trip result

**COLD2 passed the GPU byte/IOSurface contract but failed the full runner's
input-handoff gate.** Its protected close succeeded, but no original input
start marker appeared before the 600-second deadline. `result.json` correctly
records failure; `independent-verification.json` separately proves the nine
GPU round trips. The owned guest was terminated. Do not label the entire
COLD2 runner as passing.

The detached reader could enter another blocking console read before the
main thread called exec. Apple's reference XNU explicitly waits for other
threads during exec in
[mach_loader.c](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/mach_loader.c#L913-L942).
That makes a blocked reader a supported explanation, although no exact guest
thread stack was captured. BUILD4 explicitly stops the reader after it
forwards and validates the final session/offset-matching close ACK, then
joins it before exec. An atomic release/acquire flag publishes the expected
ACK identity. Host corruption/disconnect tests pass for that revision.

The error paths that never receive a final ACK still detach the reader;
immediate input handoff after such a guest-console failure is **not proven**.
The host's bounded runner tears down that disposable VM. Host socket EOF
tests alone cannot establish wakeup behavior of the guest console driver.

`GPU_ROUNDTRIP_COLD2` uses BUILD3/STAGE2/INSTALL2. No RAM restore, debugger
attachment or guest memory/register writes occurred. Witnesses in `wire.log`
and the runner events:

| Host seconds from launch | Observation |
| ---: | --- |
| 45.349 | Guest `HARNESS_START` |
| 46.018 | Exact guest AIR hash and length |
| 47.221 | Host-created pipeline acknowledged to guest |
| 110.724 | First returned result verified in guest IOSurface |
| 276.799 | First result on the second surface generation verified |
| 359.961 | First result on the third surface generation verified |
| 386.169 | Ninth returned result verified |
| 386.542 | Guest verification report acknowledged by host |
| 386.658 | Supervisor `reason=complete wait_status=0` |

`bridge.jsonl` records all three deliberate faults and a CRC-protected close
for **112,636 request bytes and 110,727 response bytes**, child wait status
zero. The guest link reports 134 retransmissions, 28 duplicate response
frames, and four rejected frame candidates. The independent verifier passes
on the captured binary streams and host GPU witnesses. Its complete hashes
and per-operation pixel digests are in `independent-verification.json`.

Guest elapsed time per operation ranged from 12.25 to 103.84 seconds.
Measured host GPU times are recorded separately in `host-worker.stderr`;
do not equate these diagnostic round-trip times with GPU execution time or
claim UI performance from them. Unprotected `HARNESS_RESULT` text is a useful
log but is not the evidence gate: the protected REPORT and close are.

BUILD3 CDHashes are bundle `d65acfca64f396968672a255ab9040fdb832eae5`, worker
`ea63408f112b4b4f2b9cb643cb452470b63e5cae`, supervisor
`a593c7f7fe6bda03551f1f400289170b1458b25c`. The merged 3936-entry trust cache
has SHA-256 `19a3ee2ef33b29a7e0ae093a992f32515c190d20494a734740078b3fa3bf4e91`.
`BUILD3/artifact-sha256.txt` also pins the signed executable bytes.

## Final retry and reproduction

### Final retry: full gate passed

`GPU_ROUNDTRIP_COLD3/result.json` records `passed: true`, no debugger or RAM
restore, and termination of the owned guest at 537.437 host seconds. BUILD4
changes the supervisor shutdown only; signed bundle and worker SHA-256/CDHashes
are byte-identical to BUILD3. The supervisor CDHash is
`90721b09d3a88d9093fcc0660b02372e7bfeba2a`; STAGE3's trust cache SHA-256 is
`dddc057314bbb82502c9b8af60632b03d558fd49f89461b4c6e2771d8d41408a`.

| Host seconds | Final retry witness |
| ---: | --- |
| 122.372 | Exact guest AIR hash/length verified |
| 128.315 | Pipeline created |
| 183.472 | First guest output verified |
| 530.005 | Ninth guest output verified |
| 530.574 | Guest verification report acknowledged |
| 532.260 | `DVMGPU_READER_STOPPED final_ack=1` after join |
| 532.261 | `DVMGPU_DONE reason=complete wait_status=0` |
| 537.199 | `DVM_INPUT_START version=5 pid=84` |

The protected close covers 112,635 request bytes and 110,727 response bytes.
The one-byte request-length difference from COLD2 is serialized diagnostic
timing text; the pixel operations and returned byte stream are identical.
`independent-verification.json` rechecks the final captures. All three deliberate
faults occurred. Host bridge counters are 20 retries, 27 duplicates and 230
rejected frame candidates; none became a second GPU submission. Exactly nine
host `DIAG event=run` witnesses have completed status and the poison check.
The guest's separate link counters are 257 retries, 18 duplicates and two
rejected candidates. These must not be confused with the host bridge counters.

This establishes startup of the original input executable after normal
diagnostic completion. It does not assert native input registration, touch
ACKs, or concurrent GPU/input operation in COLD3. All owned installer/System
VMs and host workers are stopped. Unrelated VMs, the migrated parent disk,
existing SPTM/TXM files and working rendered-Home lineage were not modified.

### Commands

Run from this worktree; all output names must be new. Exact executed host
compiler/test argv are retained in `GPU_ROUNDTRIP_HOST_FINAL4/commands.json`.

```sh
python3 tools/gpu/run_forward_host_checks.py \
  --library /tmp/dvm/GPU_FEAS_SHADER1/libraries/QuartzCore.framework.default.metallib \
  --output /tmp/dvm/GPU_ROUNDTRIP_HOST_FINAL4
bash tools/gpu/build_guest_forward.sh \
  /tmp/dvm/GPU_ROUNDTRIP_BUILD4 --iosurface-client --reliable
python3 tools/gpu/prepare_guest_load.py --mode forward \
  --build /tmp/dvm/GPU_ROUNDTRIP_BUILD4 \
  --cache /tmp/dvm/WARM_RUNTIME_STAGE2/launchd-input.plist \
  --system-tc /tmp/dvm/warm-input-v5/system.tc \
  --output /tmp/dvm/GPU_ROUNDTRIP_STAGE3
python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/warm-input-v5/warm-manifest.json \
  --stage /tmp/dvm/GPU_ROUNDTRIP_STAGE3 --tag GPU_ROUNDTRIP_INSTALL3
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_ROUNDTRIP_INSTALL3/warm-manifest.json \
  --tag GPU_ROUNDTRIP_COLD3 --seconds 900 \
  --worker /tmp/dvm/GPU_ROUNDTRIP_HOST_FINAL4/metal_proxy_server \
  --reliable --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib \
  --faults
```

`GPU_ROUNDTRIP_INSTALL1/probe.txt`: 514 serial lines, 0 panics, reached shell:
yes. `GPU_ROUNDTRIP_INSTALL2/probe.txt` and `GPU_ROUNDTRIP_INSTALL3/probe.txt`:
each 515 serial lines, 0 panics, reached shell: yes. All observed the guarded
`GPU_LOAD_INSTALLED` marker, stopped
the installer and pinned the installed child read-only. These are actual
restore `probe.sh` verdicts, separate from normal System workload acceptance.

COLD2 used the same commands with BUILD3, STAGE2, INSTALL2, HOST_FINAL2 and
the COLD2 tag, with `--seconds 600`. COLD1 used BUILD2, STAGE1, INSTALL1,
HOST_FINAL1 and the COLD1 tag, also 600 seconds. Sources for each signed
guest build and its raw errors are retained separately.

## Validation and review

The actual C transport loop runs in the host socket simulation, with the
actual forwarding wrapper and Metal worker. Both versions passed deliberate
host-frame corruption, one dropped guest-data ACK, and one dropped host-data
ACK. Nine GPU operations complete once each, with byte-exact streams. A
separate midstream disconnect produces a nonzero supervisor exit and no
protected success. These are host tests, not substitutes for the guest run.

The codec checks maximum payload, corruption rejection and following-frame
resynchronization. Existing wrapper ownership/shape rejection tests, legacy
UART tests, all 30 repository regressions and shell syntax checks pass.
Evidence negative tests run under `python3 -O` and reject changed request or
response bytes, absent host poison witnesses, and absent guest reports.
Production evidence checks use explicit exceptions, not Python assertions.
The cache-negative test passes for missing cache, wrong length and wrong SHA;
its command was run separately for FINAL2 and is now in the standard suite.

Terra/high provided bounded protocol/evidence and console-contract reviews.
Parent review implemented the corrected pass gate, independent input-pattern
oracle, non-assert evidence checks, parser rejection accounting and broader
standard host checks, and reviewed the reader-thread lifecycle. Agents did
not operate VMs or edit the main checkout.

## Route status

| Route / contract | Status and exact scope |
| --- | --- |
| Unchanged exact-guest UI AIR on this Mac | **Proven**, the selected copy shader, including this guest-initiated pipeline. |
| Custom process-local forwarding bundle → host GPU → guest IOSurface | **Proven**, nine operations, three surface generations, sequential copied RPC. |
| Full discoverable custom Metal plugin / useful UI acceleration | **Untested**; consumer loading, call coverage, transport and presentation remain. |
| Adapted Apple PV guest stack | **Untested** end to end; earlier host primitives are narrower. |
| Interception of an existing compositor's Metal work | **Untested**; shader reuse alone does not establish interception. |
| Legacy integrity-free console frames | **Disproven** under the earlier observed kernel-output interleaving. |
| Direct console-poll supervisor in COLD1 | **Disproven within that implementation**; handshake succeeded, then premature failure. Exact event bits unknown. |
| Host pixels → already-adopted guest surface → native display | **Proven earlier**, with diagnostic memory injection; not repeated here. |
| Newly produced offscreen guest surface → native display adoption | **Untested** by this round trip. |
| Copied pixel memory across checkpoint restore | **Proven earlier** in two fresh QEMU processes. |
| Live GPU queues/resources/fences across checkpoint | **Untested**. |
| Windows execution of the guest shader workload | **Untested**. |

## Scope and next gate

This explicit process-local object path uses no custom IOService or
IOGPUFamily subclass. That is evidence against requiring full IOGPUFamily
for every possible forwarding design. It does not establish automatic Metal
plugin discovery, `IOAcceleratorES` matching, system registration, or loading
inside a compositor's process and sandbox. The earlier static discovery
findings remain hypotheses to test at those boundaries.

The next useful gate is one **real, opt-in QuartzCore operation** rather than
another host triangle or another demonstration of this copy kernel. Before
extending the adapter, capture that operation's actual shader function,
pipeline descriptor, selectors, resource layout and completion behavior in
a disposable guest helper. Test the unchanged function on host Metal, then
implement only that observed call sequence. Keep software fallback available
when the adapter cannot handle an operation.

Its major unresolved dependencies are:

* A transport separate from the input console, with measured latency and
  bandwidth. Guest access/discovery for that channel is still unproven.
* The real consumer's loading policy and private Metal call coverage; a
  helper loading our bundle is narrower than a system compositor doing so.
* Adoption of a newly returned guest IOSurface by the existing display path,
  including producer/consumer fences and cross-process ownership. The prior
  debugger-assisted write to an already adopted surface did not test this.
* Resource identity and cancellation across failure and checkpoint. The
  current session ends on disconnect; it does not reconnect or restore host
  objects. Draining commands, saving logical resources, discarding host
  handles, recreating them and verifying resumed output require an actual
  state experiment. API names alone establish none of these properties.

The current copy workload cannot establish a speedup: it is small and dominated
by diagnostic transport and guest scheduling. Shader reuse removes one
demonstrated compatibility concern on this particular Mac; it does not prove
all QuartzCore shaders/formats or make a full driver a small implementation.
Adapted Apple PV and interception remain untested end to end. Windows backend
shader execution/translation remains untested; this experiment uses host Metal.

## Evidence retention

Small records and versioned sources are preserved under
`/Users/jdolbe1/dvm-artifacts/research/gpu-roundtrip-ios27-20260905/`.
`index.json` records original paths, lengths and SHA-256 for every retained
file. The archive includes the reference-only command/pixel captures, unlike
the older full-library UART captures; Apple shader libraries, executable
binaries, RAM and disk images remain outside git and this small-record archive.

The byte/GPU/IOSurface proof can be rechecked independently of the VM runner:

```sh
python3 tools/gpu/verify_roundtrip.py \
  /Users/jdolbe1/dvm-artifacts/research/gpu-roundtrip-ios27-20260905/GPU_ROUNDTRIP_COLD2
```

For COLD2 this intentionally passes even though the **separate** input-handoff
gate failed. Consult that trial's `result.json` and `bridge.jsonl` for the full
trial outcome. `verify_roundtrip.py` does not claim display adoption, input
readiness, global device publication or live GPU checkpoint support.
