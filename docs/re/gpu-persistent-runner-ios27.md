# Persistent exact-guest GPU iteration

Worktree: `codex/metal-driver-ios27`. Guest: iOS 27 24A5430a,
iPhone17,3/T8140. SPTM/TXM, native SMC and migrated disk lineage are retained.
Every runtime experiment uses a disposable child. The driver remains explicitly
selected in the test process; software system composition remains available.

The newer main/DCP QEMU and vertex-writeback driver are preserved separately in
`~/dvm-artifacts/gpu-runtime-loader-main-ios27/`. Its manifest still uses the
original installed control parent; select the matching backend per job. See
[main integration and buffer coherence](gpu-main-sync-buffer-coherence-ios27.md)
for the eight independently verified jobs and the remaining multi-frame failure.

## Contracts and stop conditions

The supervisor boots once, waits for native display/input readiness, and accepts
only a signed bundle package, a numeric job ID and a fixed test/location choice.
It fetches bounded chunks through owned MMIO RAM, checks the full executable
SHA-256, stages a unique directory and execs its own pinned helper. No command
line or arbitrary executable path is accepted. The child alone owns submission
until it exits; the parent resumes the completion and audit sequence afterward.
Each job uses a fresh host backend, preventing failed-child handles leaking into
the next test. Child execution is bounded to 90 seconds; System remount to 15
seconds; the session to its explicit deadline or a queued stop.

A digest proves byte identity, not signing trust. Host `codesign --verify
--strict` precedes staging, and guest dyld/kernel loading remains independently
observed. Successful jobs require actual guest CARenderer work, host GPU
completion and verified pixels. Loading alone never passes. Native display/input
readiness and post-session recovery are separate from offscreen output.

## Exact guest loading failures

Small evidence is preserved under
`~/dvm-artifacts/research/gpu-persistent-runner-loading-failures-ios27/`.
The index records hashes and original paths. Full guest RAM and disks are excluded.

| Trial/job | Observed result | Scope of conclusion |
| --- | --- | --- |
| `CA_RUNNER_GUEST2`, `1788739280762085`, PID 280 | Trusted BUILD2 bundle staged on Data; child exited 121 | Diagnostic overflow obscured dyld error. Owned RAM residue at file offsets `0xea92b1c4`/`0xea92b212` suggested an executable mapping denial; this alone was not a clean runtime trace. |
| `CA_RUNNER_GUEST3`, `1788740001034490`, PID 284 | Trusted BUILD3 bundle: `NSCocoaErrorDomain Code=3588`, `file system sandbox blocked mmap()` | Direct chunked audit confirms denial of the staged Data path. Helper-only `com.apple.private.security.no-sandbox` did not resolve this tested contract. |
| Same VM, `1788740001095030`, PID 303 | New revision: `code signature invalid`, `errno=1`, `sliceOffset=0`, `codeBlobOffset=0x3A820`, `codeBlobSize=0x300` | This revision's CDHash `6d3b6862e75abdeb3f247f76ccfa5ab1e1a7b07a` was absent from the boot trust cache; signature rejection is separate from the trusted Data mapping failure. |

Exact error parts and exits are in each trial's `runner-results.json` and job
`driver-audit.jsonl`. These are scoped loading failures, not evidence that a
custom service or development loader is impossible. Neither job submitted GPU
work. BUILD5 removes the ineffective helper entitlement. In `CA_RUNNER_GUEST5`,
System remount fails before staging: serial lines 23086–23089 record
`mount_apfs: volume could not be mounted: Operation not permitted`, exit 77,
and `GPU_LOAD_ERROR driver=runner-system-remount`. The known System-installed
controls had already passed. The supervisor exited on that setup failure; the
host subsequently stopped/reaped its VM on the 60-second no-progress bound.
The session as a whole is **not passed**. Later supervisor source emits a fatal
marker so the host can stop immediately; compiled in BOOTSTRAP6, not guest-tested.

Upstream Apple's [dyld Loader implementation](https://raw.githubusercontent.com/apple-oss-distributions/dyld/main/dyld/Loader.cpp)
classifies a failed `mmap` with EPERM using a sandbox check. That reference helps
interpret the error wording; it is not a disassembly of this exact guest dyld.

## Verified fresh-process controls and replay

`CA_RUNNER_GUEST5` established readiness once at **113.154 seconds**: 92 native
presentations, helper PID 87 stable for 10 seconds and a fresh ACK. Jobs
`1788740779104943` / `1788740779224631` ran in fresh guest PIDs **283 / 311**.
Both executed real CARenderer → driver → host Metal, one pass and indexed draw,
4096 exact red pixels, final SHA-256
`c34fb4331b2d031d7c644860b54a678424c66ef12352fc165a91dc09840d98fd`,
and zero live host resources. Each independent job verifier passed audit slot
CRC/sequence, package/upload hashes and GPU/pixel evidence.

The installed executable SHA-256 is
`5fad49f8d02c6b1d24a1ec292b55e2cfc4e6e325d367ebd83b569c868a4582c4`.
Host bootstrap reports Apple M5 Max. These controls use the installed driver;
they **do not prove loading a revised driver**.

| Measurement | First job | Second fresh process |
| --- | ---: | ---: |
| Guest package fetch/hash/preimage phase | 41.956 ms | 32.175 ms |
| Spawn through exit (guest `runnerResult`) | 22.648 s | 6.395 s |

These are setup plus execution durations, not rendering frame latency. The
second job's render RPC alone took 5.773 ms of host service time. Sustained
CARenderer pacing is still untested. At final failure teardown the input helper
was still PID 87 in ready state: 1,369 native presentations, 109 ACKs, zero
timeouts/restarts. This is display/input continuity evidence, not presentation
of the offscreen CALayer or a gesture test.

`CA_RUNNER_REPLAY1` replayed all **27** captured first-job JSON requests in a
fresh host worker, matching every response except declared variable timestamps,
including final pixels, completion and object counts. Worker exit 0; **0.102 s**.
This is replay of real guest-generated data, not a second guest execution.
Durable small evidence: `~/dvm-artifacts/research/gpu-persistent-runner-controls-ios27/`.

## Host-only iteration evidence

`build_driver_revision.py` compares compiler-discovered project dependencies to a
frozen build. A driver/backend update took **4.746 seconds** (`CA_RUNNER_REVISION3`);
a subsequent no-op took **0.207 seconds** (`CA_RUNNER_NOOP3`). The helper and test
client are inherited unchanged; an explicit `--bootstrap` option now rebuilds affected supervisor components for reinstall/reboot. `CA_RUNNER_BOOTSTRAP6` rebuilt only `driver_probe`, linked/signed/import-checked the helper, and reused the guest driver/backend/workload in **4.718 seconds**. This build is not yet boot-validated.

The related render binding batch implements vertex/fragment offset updates,
bulk bindings and unbinding with ownership, range and current-binding checks.
`CA_SEQUENCE_HOST3/4` renders and verifies the first three-draw geometry scene.
Subsequent QuartzCore prewarming aborts at a pipeline whose host reflection says:

```
stage=0 name=vertex_buffer index=1 type=0 access=1 array=1 textureType=0
```

The current render contract accepts read-only shader buffers. Accepting this
pipeline requires general write coherence, including CPU-visible completion;
removing the reflection rejection alone would be incorrect. This is a macOS
QuartzCore rehearsal with explicit AIR substitution, not an exact guest trace.
Native-versus-forwarded comparison and sustained sequence completion remain open.

## Reproduction tools

- `build_driver.sh`: opt-in `DVM_TEST_RUNNER=1 DVM_CA_PROBE=1` bootstrap.
- `runner_control.py TRIAL --bundle BUNDLE --mode installed|data|system`: queue
  one job; `--expected observe` preserves an anticipated loading failure.
- `runner_control.py TRIAL --stop`: finish queued jobs and stop the owned trial.
- `verify_runner_job.py JOB`: independently check captured audit CRC/sequence,
  package identity, generated upload hashes, completion and pixels.
- `replay_driver.py JOB --worker WORKER --library AIR --out NEW_DIR`: replay JSON
  guest submissions without guest provisioning or execution. Shader bytes are a
  separately hash-checked input. Generated buffer data, constants, descriptors,
  command order and output replies remain in the capture.

A package test export is implemented but remains unverified until controlled
revision loading succeeds. Global discovery, multiple queues and checkpointing
are not supplied by the runner. See [coverage](gpu-driver-coverage.md) for the
larger objective and separate acceptance categories.

## Next loading experiment: scoped kernel development support

**Update:** the [kernel development-loader experiments](gpu-development-loader-ios27.md)
prove stock TXM approval with the scoped service and helper `get-task-allow`.
The subsequent user-bounded two-fix experiment now proves actual CARenderer
execution and verified pixels from an already boot-trusted bundle staged on
Data, using a scoped RX mapping exception. Newly signed revisions still fail
signature validation after the scoped ad-hoc CT gate. Five additional positive
jobs passed independent verification; normal controls pass before and after.
That two-fix stop was honored. The user then authorized three more fixes:
scoped AMFI completion, stock TXM compilation-hash authorization, and retaining
the kernel compilation capability. AMFI now returns success and TXM matches the
real candidate hash, but selector 24 still rejects its signature. All three
failed revised-driver acceptance; six further installed controls verified pixels.
That stop was honored, then the user removed the limit. **Runtime revision
loading now works in the scoped development runner:** linked OOP-JIT signatures
and an opt-in SEP xART record resolve the exact TXM gates. Two code-distinct
Data revisions execute actual guest CARenderer with verified host pixels in
one uninstrumented boot; one was built after boot. Wrong subtype still fails
signature validation and omitting development opening still fails mmap.
See [current runtime-loading evidence](gpu-linked-runtime-loading-ios27.md)
for commands, iteration timings and the durable runner package.

The user authorized an opt-in kernel addition for testing on isolated boot
artifacts. Stop searching for a remount/entitlement-only shortcut. The required
contract is executable mapping of one staged, host-validated driver revision in
the development worker, while ordinary guest processes retain their policy.

Exact static inspection identifies AMFI's trust-cache user-client handler at
`0xfffffff00919ec88`, with a reference to its full method-name string at
`0xfffffff00919ecb0`, entitlement check at `0xfffffff00919ecf4`, and selector
2/7 dispatch at `0xfffffff00919ed80`. This is static evidence, not a successful
runtime load. Kernel symbolsets list trust-cache load/query interfaces but have
no corresponding symbol addresses; names alone do not supply a callable ABI.

Apple's [upstream trust-cache implementation](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/kern_trustcache.c)
uses TXM on SPTM systems and explicitly rejects legacy loading. Its
[per-process code-signing routine](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/kern_cs.c)
and [TXM integration](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/code_signing/txm.c)
show that a process flag change alone is insufficient evidence: monitor approval
and executable mapping must also succeed. These upstream references are not the
exact guest implementation. Resolve and guard that implementation before adding
a scoped service call; do not invoke the known legacy panic path or modify
SPTM/TXM as a shortcut. Acceptance is a previously untrusted revision executing
and verifying the GPU workload, followed by an unchanged normal-process control.
