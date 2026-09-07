# Dynamic backboardd driver revision reload — 24A5430a

Guest: iOS 27 build 24A5430a, iPhone17,3/T8140. This is a development
iteration mechanism for the GPU work, not a production loading policy and not
system-wide Metal discovery. It replaces a **process**, never a live driver.

Status: plan and implementation record. Every runtime claim in the result
sections carries a tag, a PID and a log line.

## 1. The mechanism that already exists

Nothing below was invented for this task; it is what the tree already has.

### 1.1 Boot-time driver installation (revision A carrier)

`tools/gpu/build_system_bootstrap.py` compiles `system_bootstrap.m` together
with the driver frontend (`driver_guest.m`), the MMIO transport
(`driver_mmio_transport.inc`) and the owned/imported mapping providers into
`DVMMetal.bundle/DVMMetal`, then appends one `LC_LOAD_DYLIB` command for
`/System/Library/Extensions/DVMMetal.bundle/DVMMetal` into **verified zero
header padding** of an isolated copy of backboardd. It re-checks that every
instruction section keeps its original SHA-256, ad-hoc signs both images and
emits `helper.tc`. `prepare_system_bootstrap.py` merges that into the boot
trust cache and builds a restore-ramdisk installer;
`run_guest_install.py` runs it against a disposable qcow2 child.

The constructor refuses any process that is not named `backboardd`, opens the
transport, obtains the real registry ID, adds `MTLDeviceSPI` protocol identity
and calls the guest's own exported `MTLAddDevice`, then requires pointer
identity from `MTLCreateSystemDefaultDevice()`.

### 1.2 Transport (`darwin_gpu_transport.c` + `driver_mmio_transport.inc`)

16 MiB of owned shared RAM plus a doorbell register; one outstanding request;
completion is polled, not an interrupt. Three facts decide the restart design:

* The QEMU session id (`s->session[16]`) is generated **once per VM** and the
  `submitted`/`done` counters are monotonic for the VM's life. A guest that
  restarted its sequence at 1 would be rejected by `failed(s, 2)`.
* The guest side already supports a **successor process**. Under
  `DVM_TEST_RUNNER` — which `system_bootstrap.m` defines — `openMMIO()` does
  `ns.sequence = REG_DONE` and `mmioReportSequence = ram[0x180]` instead of
  the `mmio-used-session` check, and the audit ring is modulo-indexed with a
  host-acknowledged tail at `ram[0x188]`.
* Host GPU work is **synchronous inside the RPC**: `driver_host.m:599-600` is
  `[cb commit]; [cb waitUntilCompleted];`. Therefore *transport idle implies
  no GPU command buffer in flight*, which is what makes quiescence checkable.

### 1.3 Package staging wire (`driver_runner_peer.py`, `runner_control.py`)

`RunnerPeer.control_reply` answers `runnerNext` / `runnerFetch` /
`runnerResult` from the host without involving the Metal worker:
base64 chunks of at most 32 KiB, SHA-256 checked end to end, plus `Info.plist`
and `_CodeSignature/CodeResources`. `runner_control.py` queues a job by
writing `<inbox>/<job>.json` and refuses to talk to a PID that is not the
owned QEMU. **Per job it also replaces the host Metal worker process**, which
is an existing host-side generation boundary.

### 1.4 Signing and trust for a new revision

`sign_linked_revision.py` rewrites the ad-hoc CodeDirectory to version
`0x20600` with the linkage fields (application type 2, subtype 1) bound to the
parent executable's complete CodeDirectory hash, fixes affected load commands
and page hashes, and requires host `codesign --verify --strict`. Guest
acceptance additionally needs all of:

| Requirement | Where it comes from |
|---|---|
| parent carries `com.apple.private.oop-jit.loader = previews` | TXM `0xfffffff017047220` |
| TXM class-2 completion accepted | needs SEP xART SCRD magic with bit 16 clear → `DARWIN_SEP_TXM_XART_TEST=1` |
| `org.darwin-vm.development-loader`, `get-task-allow` | opt-in BootKC wrapper, dev open type `0x44564d4c` |
| sandbox exception `/private/var/tmp/dvm-gpu-runner/` | `build_system_bootstrap.py --runtime-probe` entitlements |

The opt-in BootKC with that wrapper is `934efdcc…`; the registry BootKC used by
the `control-1.json` package is `b2cc67ba…`, whose `kernel-ledger.json` records
`source_sha256 = 934efdcc…`, so **the control package's kernel already carries
the development-loader route**.

This route is proven in *test-runner* processes (`gpu-linked-runtime-loading-ios27.md`,
`CA_LOAD_XART_GUEST2`: two code-distinct bundles, one built after VM start,
verified renders, no debugger). It is **not** proven inside backboardd.

### 1.5 The one prior in-backboardd attempt

`build_system_bootstrap.py --runtime-probe` compiles `system_revision_probe.inc`
into the constructor. `CA_SYSTEM_LOAD_GUEST1` is its only run. In actual
backboardd PID 73: development `IOServiceOpen` type `0x44564d4c` returned 0,
MMIO mapped, and the host delivered the entire package (11 transport
requests). It then failed at

```
GPU_LOAD_SYSTEM_EXCEPTION name=NSInternalInconsistencyException reason=system-revision directory errno=2
```

i.e. `mkdir("/private/var/tmp/dvm-gpu-runner/<job>")` returned ENOENT before
NSBundle or `dlopen` was reached. Nothing about signing, arm64e loading or the
sandbox was established. The existing probe also (a) blocks the constructor for
up to 60 s waiting for a job and hard-fails when none arrives, and (b) never
registers the loaded image as the process's Metal device.

## 2. Ownership contracts inspected before writing new infrastructure

| Contract | Current implementation | Consequence for restart |
|---|---|---|
| Outstanding GPU commands at exit | `[cb commit]; [cb waitUntilCompleted]` per RPC | none in flight while the transport is idle; quiesce = take the transport lock |
| Transport queue generation | VM-lifetime session, monotonic seq; guest resumes from `REG_DONE` | successor process is already representable; needs an explicit generation record for evidence |
| Host object table | `host.entries`, keyed by **guest-assigned** handles | a fresh process restarts handles at 1 and would collide; retire by replacing the worker process (existing per-job pattern) |
| Managed pool | `managed_write` registers 759 pages once, `lifetime=vm`, and `failed(s,20)` on any later write; userspace only `IOConnectMapMemory64(type+2)` | successor must map, never re-register |
| Imported IOSurface pins | `dvm_surface_registry.h`: "Client death/host loss without this handshake leaks pinned pages; no recovery or checkpoint support is implied." Retirement needs a host tombstone `<id>.retired` **then** guest selector `0x44565302` | a dead generation's surfaces stay pinned; 64 slots / 256 MiB total, ~24.2 MB per fullscreen surface |
| Host page aliases | `DVMImportedPages.dealloc` deliberately does nothing; only `retire` writes a tombstone, and only after every texture alias is gone | worker replacement destroys aliases **without** tombstones → quarantine, not reuse |
| Native presentation | QEMU IOMFB holds the last presented surface (A408/D594) | the last presented surface must stay pinned until the successor presents its own |
| Input | separate helper, `input-status.json` `guest_pid`/`guest_epoch`/counters | restart must not change the helper PID/epoch and must not increase error counters |

The registry's per-page owner map rejects re-registration of a page that a
stale surface still owns, so an unquarantined leak surfaces as a *failed
import in the successor*, not as silent double ownership. That is the correct
failure direction and is the reason quarantine is the default answer here.

## 3. Proposed lifecycle

One VM, kept alive across the whole cycle.

1. **readyA** — boot the installed revision-A image. Require
   `GPU_LOAD_SYSTEM_REGISTERED pid=`, at least eight native RGhA presentations
   with D594 completions, a native Home press whose two edges dispatch and ack
   with a later presentation, and an exact final source→conversion→console match.
2. **stage B** — host builds B (identical sources, revision-renamed classes,
   only `DVMRevisionNumber` differs), signs it with the linked v0x20600
   CodeDirectory against the *installed* backboardd, records the `DVMProxy`
   SHA-256, the candidate CDHash and its absence from the boot trust cache,
   and queues it in the runner inbox.
3. **quiesce** — the host publishes a control word in shared RAM at `0x300`
   (`magic, generation, command, deadline`, outside every existing region).
   A dedicated thread in the bootstrap observes it, takes the transport lock —
   which by 1.2/2 means no request and no GPU command buffer is outstanding —
   retires what it can prove is retirable, sends `sessionRetire`, marks the
   namespace retired so any later call fails fast, and exits 0.
4. **restart** — launchd (`KeepAlive: true`, `UserName: mobile`) respawns
   backboardd. `exit(0)` is not a crash, so `_PanicOnCrash` does not apply.
   The host bumps the generation, quarantines the retiring generation's
   surviving surface registrations and replaces the Metal worker process.
5. **load B** — the successor constructor asks the host for a staged revision.
   On `idle` it registers the built-in driver (so an ordinary boot still
   works). On `stage` it fetches, hashes, writes, `dlopen`s, resolves
   `DVMRevisionNumber` and `DVMCreateSharedMetalDevice`, builds the device from
   **B's** code, and registers that with `MTLAddDevice`.
6. **verify B** — new PID, `DVMRevisionNumber() == B`, class names carrying
   B's prefix, and the staged SHA-256 matching what the host sent; then real
   compositor submissions from that PID, host Metal completion, native
   presentations with D594, verified console delivery and a Home press
   recovered.
7. **repeat** — a second cycle, recording registry slots/pages/bytes, worker
   RSS and QEMU RSS per generation, to expose stale ownership and accumulation.

`dlopen` success, a new PID, or a revision log line alone do **not** pass;
step 6 is the acceptance.

## 4. Expected evidence

* `GPU_LOAD_SYSTEM_BEGIN/ADD/REGISTERED pid=` once per generation.
* `GPU_LOAD_SYSTEM_REVISION_STAGED/LOADED pid= job= revision= class=`.
* `GPU_LOAD_SESSION_RETIRE generation= requests= imports= retired= quarantined=`.
* Host `driver-host.jsonl` and `driver-audit.jsonl` split by generation.
* `input-status.json` snapshots before and after each restart.
* Native presentation/completion counts from the QEMU display log, plus
  `verify_rgha_scanout.py` on the final delivered console bytes.
* Separate timings: build, stage, quiesce, exit→new PID, readiness, first frame.

## 5. Stop conditions

* Any `dvm-gpu-shm: failed code=`, `mmio-*` guest failure, `panic(cpu`, or a
  post-restart `GPU_LOAD_SYSTEM_EXCEPTION`: stop, capture, quarantine, do not
  reuse the session.
* A surface whose retirement or completion cannot be established is
  quarantined and the session is marked `reuse=false`.
* The same contract failing twice: stop and improve instrumentation or read
  the implementation; do not run the same experiment a third time.
* Automated regression runs keep the 600 s cap. The interactive session has
  separate boot/readiness and per-test deadlines and is kept alive between
  tests.

## 6. The failed contract that required a kernel addition

`BBRELOAD_SESSION2` replaced generation 1 (pid 73) with generation 2 (pid 344)
after an unrelated driver exception, and the successor's very first compositor
import failed:

```
GPU_LOAD_SURFACE_REGISTER surface=2 kr=e00002e2 count=4 id=0 bytes=0 span=0 ok=0
GPU_LOAD_TEXTURE_REJECT reason=import-contract type=2 width=1179 height=2556 ...
```

`0xe00002e2` is `kIOReturnNotPermitted`, returned by
`surface_registry_kernel.inc:18-19`: the provider keeps the **dead
IOUserClient** in `DVMSurfaceOwner`, and `boot_transport_shim.cpp:80-85` keeps
it in `DVMManagedOwner`. The payload said so itself — *"A failed owner never
hands these pages to a replacement process; restart is unsupported."* No
replacement backboardd can ever register a page while the previous client
object is retained as a property.

The narrowly scoped opt-in addition is selector `0x44565303`. It gives up the
**right to register** and nothing else:

* it never unpins a page and never retires a record, so a surface the owner
  failed to retire stays owned in the registry's per-page map and a
  replacement that lands on it is still refused;
* it never runs on client death — the owner has to ask while it is alive;
* it touches no register aperture and no monitor state.

Built by `build_surface_pin.py --registry` from the same pinned
`934efdcc…` runtime-loader BootKC. Output `6094766a4be38b2616d422041d75634f51d8effd9253dbe0deff14c66957a011`,
payload 3,872 bytes at `0xfffffff00aa5e000`, identical patch addresses to the
previously validated registry BootKC. `sptm_modified` and `txm_modified` are
both false; the ledger is in `kernel/ledger.json`.

## 7. Boot-order finding: the staging root is not a sandbox denial

`CA_SYSTEM_LOAD_GUEST1` died on `mkdir` ENOENT with no further evidence. The
instrumented probe localizes it in one boot. In backboardd pid 73:

```
GPU_LOAD_SESSION_PATH path=/private/var          rc=0  mode=40755 uid=0
GPU_LOAD_SESSION_PATH path=/private/var/tmp      rc=0  mode=41777 uid=0
GPU_LOAD_SESSION_PATH path=/private/var/tmp/dvm-gpu-runner rc=-1 errno=2
GPU_LOAD_SESSION_LIST path=/private/var/tmp count=2
```

and in the replacement pid 356 of the same VM:

```
GPU_LOAD_SESSION_PATH path=/private/var/tmp/dvm-gpu-runner rc=0 mode=40700 uid=501
GPU_LOAD_SESSION_LIST path=/private/var/tmp count=12
GPU_LOAD_SESSION_ENTRY index=0 name=dvm-gpu-runner
GPU_LOAD_SESSION_MKDIR path=/private/var/tmp/dvm-gpu-runner errno=17 directory=1
```

The directory the installer creates is simply not visible yet to the very
early backboardd, and is visible by the time a **successor** starts — which is
the only case that matters here. `TMPDIR` is `/var/mobile/tmp` and `mkdir`
there returns EPERM, so it is not an alternative.

## 8. Result: one verified A -> B cycle without a reboot

`BBRELOAD_SESSION6`, one continuously running VM, no reboot, no debugger.

| | generation 1 | generation 2 |
|---|---|---|
| backboardd PID | 73 | 356 |
| driver | built into the boot-trusted bootstrap | staged at runtime from Data |
| revision | 1 | 2 |
| registered class | `DVMDevice` | `DVMRevision2Device` |
| package SHA-256 | — | `6afa0ade55303eae20c5ccb31ecdd6379b8c026ab43465c998f7e966bfd2f1fc` |
| CDHash in boot trust cache | yes | **no** (`af11db64…` absent from all 3,968 entries) |

The handshake, from the audit ring:

```
32 GPU_LOAD_SESSION_QUIESCE   generation=1 outstanding=2 tombstones=2
33 GPU_LOAD_SURFACE_RETIRE    id=1 kr=0 status=0 complete=0 ok=1
34 GPU_LOAD_SURFACE_RETIRE    id=2 kr=0 status=0 complete=0 ok=1
35 GPU_LOAD_SESSION_RELINQUISH kr=0 ok=1 owned=1
36 GPU_LOAD_SESSION_RETIRE    generation=1 pid=73 revision=1 requests=1438
                              imports=2 retired=2 quarantined=0 relinquished=1
37 GPU_LOAD_SESSION_BEGIN     generation=2 pid=356 resumed_sequence=1439
57 GPU_LOAD_SYSTEM_REVISION_STAGED pid=356 bytes=361216 sha256=6afa0ade…
58 GPU_LOAD_SESSION_ADOPT     class=DVMRevision2Device registry=4294968305
61 GPU_LOAD_SYSTEM_REVISION_LOADED pid=356 generation=2 revision=2 staged=1
```

Acceptance after the replacement, required by the host, not inferred:

* 18 native presentations, 18 D594 completions and 19 accepted render
  submissions from PID 356 within the test window;
* a native Home press whose two edges dispatched and acked, with the input
  helper PID and epoch unchanged, zero timeouts and a later presentation;
* delivered pixels verified at the instant the test passed:
  `display_errors 0`, `conversion_errors 0`,
  `source_to_console_verified true`, 1179x2556 RGhA, 837,544 non-zero RGB
  components.

`BBRELOAD_SESSION4` is an independent repetition of the same cycle
(pid 73 -> 403, revision 2, 14 presentations/completions/submissions, Home
recovered). Its pixel check is **not** valid evidence: the screendump was taken
after the session had already stopped, so the console and the retained A408
source are different frames. `capture` now runs inside the passing test.

### Measured phases

| Phase | SESSION4 | SESSION5 | SESSION6 |
|---|---|---|---|
| boot to display/input readiness | 97.974 s | 96.526 s | 96.567 s |
| stage revision B (host) | 0.019 s | — | 0.020 s |
| quiesce: retire, tombstone, relinquish | 0.141 s | — | 0.081 s |
| clean exit to successor PID | 3.980 s | — | 35.064 s |
| restart total, to a verified revision | 4.235 s | 6.327 s | 35.255 s |
| post-restart rendering + input test | 62.912 s | 62.786 s | 70.231 s |

Host build phases: pinned tree 0.142 s, bootstrap 6.851 s, revision package
3.635 s, ramdisk stage 8.828 s, guarded guest install 33.4 s.

**Iteration cost.** Changing a driver revision inside a live VM costs the
package build plus stage plus restart: 3.7 s to 38.9 s. Reaching the same
state by rebuilding and reinstalling costs 6.9 + 8.8 + 33.4 + 96.5 s, about
146 s, and discards the guest state. The saving is roughly 107-142 s per
revision, 4x to 18x. The spread in the restart figure is launchd respawn
timing, not our work: quiescence itself is 0.081-0.141 s.

## 9. V28: two cycles in one VM, both replacements verified

After merging the tested GPU development limits (root `4a1f25e`, QEMU
`5282e4e`), the compositor no longer walks into the allocation wall that
truncated every earlier session, and the second cycle completes. The guest
snapshot is the V28 package's own `guest/` build directory and the backend is
its matched `host/driver_host`, so no V28 guest ever runs against an older
backend. The pinned QEMU is that package's binary; its new transition and
input timing diagnostics are gated on `DARWIN_DCP_TRANSITION_TRACE_DIR` and
`DARWIN_INPUT_TIMING`, neither of which these runs set.

`BBRELOAD_SESSION9`, one VM, no reboot:

| | generation 1 | generation 2 | generation 3 |
|---|---|---|---|
| backboardd PID | 73 | 340 | 424 |
| revision / class | 1 / `DVMDevice` | 2 / `DVMRevision2Device` | 3 / `DVMRevision3Device` |
| package digest | built in | `16d2480f…` | `ea596f32…` |
| restart total | — | 13.601 s | **0.526 s** |
| quiesce | — | 0.179 s | 0.062 s |
| imports / retired / quarantined | — | 2 / 2 / **0** | 2 / 2 / **0** |
| relinquished | — | yes | yes |
| submissions / presentations / completions | — | 10 / 9 / 9 | 15 / 15 / 15 |
| Home dispatch, ack, later frame | — | passed, 0 timeouts | passed, 0 timeouts |

Delivered pixels after the **second** replacement: `display_errors 0`,
`conversion_errors 0`, `source_to_console_verified true`, 1179x2556 RGhA,
820,243 non-zero RGB components. `BBRELOAD_SESSION8` is an independent
repetition (73 -> 338 -> 486, restarts 25.569 s and 0.639 s, same ownership
result, 1213 presentations, VM healthy at 351 s).

**No stale-handle or pinned-page accumulation across cycles.** The registry
directory after two cycles holds `.pages` **and** `.retired` for surfaces 1-4
and only `.pages` for generation 3's live 5 and 6. `quarantine` is empty,
`ownership_failure` is null, `reuse` stays true, and exactly one worker
replacement occurred per boundary.

### Historical one-capture limitation (fixed after merge)

The limitation below describes the QEMU used for sessions8/9. The merged
capture fix exports a fresh snapshot on every stop and publishes a versioned
`last-scanout.json` manifest last. `session_cli capture` copies the console,
source and manifest while paused, checks a fresh successful snapshot ID and
then resumes. Identical pixels are allowed; a repeated content hash is not
evidence of a stale snapshot. Old QEMU retains the conservative hash fallback.
A withheld/missing pixel verdict now fails `test --capture` acceptance.

`REPEAT_CAPTURE1` validates three captures in one exact-guest VM using the
unchanged V28 reload boot package and a rebuilt, separately pinned QEMU:
snapshot1/presentation311, snapshot2/presentation324 and snapshot3/presentation726
all report zero conversion/display differences and verified source-to-console
delivery. Home dispatch and display recovery pass between the first two.
These are repeated captures in generation1, not new reload-cycle evidence.
30 session/capture unit tests and 81 project regressions pass. Unit coverage
also verifies two fresh identical-pixel snapshots, copying before resume, and
withholding a repeated snapshot ID. Evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-repeat-capture-20260907`.

Historical evidence:

`darwin_iomfb.c` `gpu_present_stopped` exports the retained RGhA witness once
per VM: `rgha_witness.exported` latches and `iomfb_export` opens with `"wx"`.
A second capture therefore pairs the **first** capture's frozen source with a
newer console. `BBRELOAD_SESSION8` recorded 1,722,834 differing pixels that
way, with `nonzero_rgb_components` byte-identical to its earlier capture --
a measurement artifact, not a rendering failure. `capture` now hashes the
retained source, reports `stale_retained_source` and withholds the verdict
instead of failing. Verify pixels in exactly one capture per VM, positioned
after the replacement that matters.

## 10. Limitations, and what is still a hypothesis

* **Two cycles, not sustained use.** Two replacements in one VM are verified;
  nothing here establishes behaviour over many cycles, long uptime or
  concurrent producers. Before V28 every session ended at 220-290 s on the
  driver's `texture memory cap exceeded` -> `GPU_LOAD_SYSTEM_UNCAUGHT ...
  render operation budget`; that wall was the compositor driver's, not the
  restart mechanism's, and the merged limits removed it.
* **Historical reload pixel evidence spans sessions.** Replacement #1 was
  verified in `BBRELOAD_SESSION6` and #2 in `BBRELOAD_SESSION9`. The later
  repeated-capture fix removes that tool restriction, but does not retroactively
  turn those runs into pixel checks of both replacements in one VM.
* **The slow Home transition is unaddressed here.** The merged evidence records
  a first unmistakable transition 1.405 s after input with a 648 ms frame gap
  during icon movement. Nothing in this work measures or improves it, and the
  Home checks above prove dispatch, ack and a later frame -- not responsiveness.
* **Retirement has only ever been observed succeeding.** `quarantined=0` in
  every cycle. The quarantine path, the reuse stop and the ownership failures
  are covered by host regressions, not by a guest run that actually failed to
  retire.
* **Display ownership across process death is not established.** The tombstone
  is written after the aliasing worker process is gone, which proves the host
  side. Nothing here proves the native display had finished with a surface at
  that instant; the successor simply presented its own frames afterwards.
* **launchd clean-exit recovery is observed, not characterised.** Seven clean
  `exit(0)` restarts were recovered, with successor spawn between 0.338 s and
  35.064 s. No throttling model is claimed and no failure mode was explored.
* This is a development loading route: a privileged test loader, an immutable
  xART fixture and an opt-in kernel selector. It is not global Metal discovery,
  not a production signing policy, and not in-place replacement inside a live
  process.
