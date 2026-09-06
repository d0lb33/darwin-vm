# Exact-guest GPU forwarding experiments

Follow-up: [the command round-trip experiment](gpu-roundtrip-ios27.md) now
proves nine guest-issued host GPU operations with returned guest IOSurface
verification and original input-helper startup. The upload failure below is
retained as historical evidence. Global plugin discovery, useful UI
acceleration, display adoption and live GPU checkpoint state remain untested.

2026-09-05 follow-up to [the feasibility assessment](gpu-feasibility-ios27.md).
Same `codex/gpu-feasibility-ios27` worktree, off `main` at `99e012c`; no QEMU
changes or rebuild. This is a narrow diagnostic, not a globally usable Metal
device or useful compositor acceleration.

## Signed bundle and concrete base initialization

**Proven in normal System userspace on iOS 27.0/24A5430a.**
`GPU_LOAD_COLD3/result.json` and `wire.log` contain these runtime witnesses:

| Elapsed host seconds | Guest marker |
| ---: | --- |
| 415.682 | `GPU_LOAD_START version=2 pid=66` |
| 418.627 | Foundation `dlopen` succeeded |
| 420.207 | Metal `dlopen` succeeded |
| 425.367 | `GPU_LOAD_BUNDLE loaded=1 error=none` |
| 425.437 | `GPU_BUNDLE_CLASS_REGISTERED class=0x79fec28060 scope=process-local` |
| 425.438 | `GPU_BUNDLE_INIT_ENTER port=0` |
| 426.065 | `GPU_BUNDLE_INIT_RETURN device=0x1031841b0` |
| 426.066 | `GPU_LOAD_NAME value=DVM process-local feasibility device` |
| 426.630 | `GPU_LOAD_DEVICE_RELEASED` |
| 426.684 | `GPU_LOAD_COMPLETE result=pass scope=bundle-and-local-device-only` |

The C helper dynamically loads Foundation/Metal, uses NSBundle's
`loadAndReturnError:`, resolves an exported factory, creates the object,
calls `name`, and releases it. The bundle dynamically subclasses `_MTLDevice`,
overrides `initWithAcceleratorPort:`, and super-sends **`init`**, not the
abstract port initializer. This initializer returned a live object without an
IOAccelerator service or full IOGPU implementation. It does not prove all
inherited methods can operate without their ordinary dependencies.

There was no `MTLAddDevice`, system enumeration, custom kernel service,
IOGPUFamily replacement, AGX driver change, or default-device override.
Software rendering remains selected by the existing global configuration.
Loading in this root launchd helper does not prove injection/library validation
in backboardd, SpringBoard or a sandboxed application.

The helper and bundle are independently ad-hoc signed. Only the helper has
`platform-application=true`. Their SHA-256 CDHashes are respectively
`e4541af8e38111121868ba655bc288127a66f0a8` and
`e7f5cae347c43f5e4c93e3ab0b8a818a6e55246b`, merged into a derived trust cache.
This is observed acceptance with the VM's existing boot/trust policy, not
a claim about stock-device signing permissions.

### Isolation and commands

The source is `/tmp/dvm/warm-input-v5/warm-manifest.json`, disk-only migrated
lineage, disk SHA-256 `d95356ee3ed9344ead971120c610ee48482aaf098840d3f4b9da40a694a099ae`.
The full pinned backing chain, exact QEMU/bootkc/DT/SPTM/TXM, and original
input service/binary are preserved. Installations use fresh qcow2 children;
only a **copied small restore ramdisk** is attached to the host through
`safe_attach.sh`. System/Data are mounted only inside the disposable restore
guest. The installed child is stopped and made read-only before normal boot
creates another writable child. No RAM checkpoint is restored.

```sh
tools/gpu/build_guest_load.sh /tmp/dvm/GPU_LOAD_BUILD2
python3 tools/gpu/prepare_guest_load.py \
  --build /tmp/dvm/GPU_LOAD_BUILD2 \
  --cache /tmp/dvm/WARM_RUNTIME_STAGE2/launchd-input.plist \
  --system-tc /tmp/dvm/warm-input-v5/system.tc \
  --output /tmp/dvm/GPU_LOAD_STAGE2
python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/warm-input-v5/warm-manifest.json \
  --stage /tmp/dvm/GPU_LOAD_STAGE2 --tag GPU_LOAD_INSTALL4
python3 /Users/jdolbe1/Downloads/darwin-vm/tools/derive_warm_manifest.py \
  /tmp/dvm/warm-input-v5/warm-manifest.json \
  /tmp/dvm/GPU_LOAD_INSTALL4/disk.qcow2 \
  /tmp/dvm/GPU_LOAD_STAGE2/warm-manifest.json \
  --tc /tmp/dvm/GPU_LOAD_STAGE2/system.tc
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_LOAD_STAGE2/warm-manifest.json \
  --tag GPU_LOAD_COLD3 --seconds 900 --keep-paused
python3 tools/hmp.py /tmp/dvm/GPU_LOAD_COLD3/monitor.sock quit
```

The read-only `derive_warm_manifest.py` was existing uncommitted main-checkout
work; its source is retained in the evidence bundle. The load runner stops on
success/failure markers, continuously drains its private UART, and collects
status/registers/frame. The 900-second value is a maximum, not a blind sleep.
The load guest was terminated after collection. Restore `probe.sh` verdict:
**506 serial lines, 0 panics, reached shell: yes**, guarded installer marker
`GPU_LOAD_INSTALLED`. A System boot is not expected to reach a restore shell.

### Failed trials and corrected interpretations

* `GPU_LOAD_INSTALL2`: first installer runner omitted the manifest's graphics
  geometry and proper display bootkc. Exact panic:
  `IOMFB: carveout memory region "PurpleGfxMem" not found @IOMobileFramebufferAP.cpp:3290`.
  No install occurred. Corrected manifest-derived arguments produced successful
  `GPU_LOAD_INSTALL3` and `GPU_LOAD_INSTALL4`; this was a runner error.
* `GPU_LOAD_COLD1`: at 420 seconds no helper serial marker was visible. RAM
  inspection nevertheless showed the helper had reached main, inside Foundation
  loading and Objective-C category registration. `_load_categories_nolock` at
  static `0x18042258c` recurred at sampled PCs, but its category index advanced
  from 0 to 6 and its pointer changed over 60 seconds. This disproves a claim
  that the repeated PC alone established a fixed pre-main hang.
* Attaching a UART peer without draining it temporarily blocked monitor
  responsiveness. Closing it restored the monitor; this was not a QEMU crash.
  Continuously draining UART exposed successful Foundation and Metal loads.
  `GPU_LOAD_COLD2` independently observed normal loads at 341.113/341.190
  seconds without debugger inspection.
* Version 1 queried `MTLCreateSystemDefaultDevice` **before** testing the custom
  bundle. That separate discovery path was still initializing when the trial
  was deliberately stopped. Version 2 isolates bundle loading and passes.
  No incompatibility or permanent default-device hang was established.
* A postmortem helper temporarily returned no threads after a thread signature
  field changed. Reading the already-known thread's saved state still worked.
  Empty scanner output was not evidence that the process died. The sampled
  exit-reason records contained unrelated service deaths, not this helper.

All sampling of the load trial was read-only guest-memory inspection plus
pause/resume. The successful COLD3 trial used no debugger or memory writes.
Cold display/input behavior of the unpatched source disk is a separate known
baseline limitation; the existing working rendered-Home VM was not modified.

## Host worker and narrow forwarding object checks

The host worker receives `LIB`, `PIPE`, and `RUN` plus binary payloads. It
executes the unchanged guest QuartzCore `read_write_surf_compute` shader on
host Metal, poisons the host destination before dispatch, verifies both GPU
texture readback and locked host IOSurface bytes, then returns actual pixels.
No host-authored shader or CPU result substitution is present.

The process-local forwarding objects deliberately do not claim complete
`MTLDevice`/`MTLDeviceSPI` conformance. They implement the selectors exercised
by this one workload. There is one serial caller, one live device/borrowed-FD
session, one active command, BGRA8 64×48, 9×7 groups of 8×8 threads, and a
fixed function. The caller keeps device/FDs alive. Transport errors require
discarding the session; reconnect/replay and thread safety are not implemented.

Parent review added same-device ownership checks, descriptor/surface layout
checks, early-read rejection verification, direct surface-byte comparison,
and source mutation after commit. Host checks exercise three patterns per
surface across three generations. The first host-only commit returns before
the worker's intentional 200-ms reply delay; destination reads fail before
wait, then succeed with all bytes equal. This proves one outstanding copied
RPC, not general asynchronous Metal scheduling.

`GPU_FORWARD_HOST_FINAL1/commands.json` records reproducible builds and tests.
`GPU_PROXY_HOST_PARENT1/test-evidence.json` additionally records malformed
protocol tests, including `ERR 1 MTLLibraryErrorDomain 1` for invalid library
data (`Invalid library file (unexpected end of file)`). Host-simulated UART
framing also completes all nine operations. These host tests are not evidence
that guest loading, guest IOSurface allocation or physical UART transport pass.

## Forwarding guest trial

The diagnostic temporarily substitutes a console supervisor for the copied
input launch-service entry. It retains the original `dvm-input` executable
and backs up the original cache/service. The supervisor has a singleton lock,
a one-shot sentinel, child stdin/stdout pipes, bounded offset/hex framing,
and a 900-second worker deadline. It execs the original input helper after
the worker exits or fails. GPU/input concurrency is **not** tested by this
sequential ownership arrangement; it is unsuitable as the final transport.

The host bridge receives shader bytes from the guest's original
`QuartzCore.framework/default.metallib`; the guest selects and hashes the
unmodified AIR slice itself. The host receives no preloaded local library
argument. Request/response byte offsets prevent silent replay or omission.
The host worker output is copied into a real guest-created IOSurface only
after a complete matching response. This tests copying, not shared backing.

```sh
bash tools/gpu/build_guest_forward.sh /tmp/dvm/GPU_FORWARD_BUILD1
python3 tools/gpu/prepare_guest_load.py --mode forward \
  --build /tmp/dvm/GPU_FORWARD_BUILD1 \
  --cache /tmp/dvm/WARM_RUNTIME_STAGE2/launchd-input.plist \
  --system-tc /tmp/dvm/warm-input-v5/system.tc \
  --output /tmp/dvm/GPU_FORWARD_STAGE1
python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/warm-input-v5/warm-manifest.json \
  --stage /tmp/dvm/GPU_FORWARD_STAGE1 --tag GPU_FORWARD_INSTALL1
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_FORWARD_INSTALL1/warm-manifest.json \
  --tag GPU_FORWARD_COLD1 --seconds 1200 \
  --worker /tmp/dvm/GPU_FORWARD_HOST_FINAL1/metal_proxy_server
```

`GPU_FORWARD_INSTALL1/probe.txt`: **515 serial lines, 0 panics, reached
shell: yes**, `GPU_LOAD_INSTALLED`, stopped child pinned read-only. The
new install runner emits its own derived normal-boot manifest.

The ARC helper/bundle use iOS platform 2, deployment target 27.0. No iPhoneOS
SDK is installed locally. The public macOS headers compile the objects;
minimal iOS link declarations name verified exact-guest exports/reexports.
The final import inventory includes the supervisor's C imports. This does
not substitute macOS framework implementations into the guest. An initial
iOS7 ARC attempt failed on missing `libarclite_iphoneos.a`; direct iOS27
linking with macOS Foundation.tbd failed its platform check. Both build
failures are preserved in `GPU_PROXY_OBJECTS1`, separate from runtime results.
Compiler-generated selector import aliases were removed from declarations;
`-fno-objc-msgsend-selector-stubs` leaves real generic libobjc imports.

The supervisor uses the established C-only iOS7-target helper build and emits
the known macOS libSystem stub warning. Its actual imports are also checked
against guest exports. The derived TC contains 3936 entries including bundle,
worker and supervisor CDHashes `ffb7e4f39a982a94eb7d37ae905402c0b23d2329`,
`727dd36ebdd4913d1fd5c7eb11cae4f329f784c6`, and
`866f231c126b8a56d0a38a46af97212e9e8b1813`.

**Observed:** `GPU_FORWARD_COLD1` reached `DVMGPU_READY` at 13.069 seconds,
`HARNESS_START` at 13.184, and the exact 2,705,796-byte AIR hash marker at
13.397. This is a normal cold boot from the installed migrated child. The
signed ARC executable loaded the signed forwarding bundle and found its
factory before reading/hashing the actual System library.

**Disproven within the tested transport scope:** newline-delimited UART
hex frames are not isolated from kernel console output. At request byte
offset 25,104, `wire.log` records:

```text
DVMGPU_OUT 25104 706c655f667261675f6c70680054595045010001484153482000fc83a0a8a72"AppleSEPKeyStore":pid:154,:13996: operation failed (sel: 31 ret: e00002bc)
```

The parser detected a malformed frame; `result.json` records the exact
exception and termination at 44.755 seconds. `guest-requests.bin` contains
25,104 validated bytes (14-byte header plus 25,090 library bytes); an offline
comparison confirms the received library prefix is byte-identical to the
exact AIR reference (`stream-summary.json`). `host-responses.bin` is empty.
On teardown the worker records `detail=short library payload read` after
stdin closes. The library upload did **not** finish, so the host had no library,
pipeline, command execution or returned pixels from this guest trial.
No CRC-free heuristic joined fragments or treated an incomplete packet as
successful work. The owned VM and host worker were terminated automatically.

This does not disprove Metal forwarding. It falsifies this particular console
framing contract under real kernel traffic. It also shows that dumping the
whole library through many tiny console writes is unsuitable for a useful
transport; this partial trace is not a general bandwidth benchmark. The
minimum next forwarding experiment needs reliable framing with integrity and
unambiguous byte acknowledgement, or an isolated transport channel. A host
library cache keyed by the exact guest-computed hash could avoid repeated
library uploads, but does not fix pixel/command transport integrity.

## Independent guest IOSurface check

`GPU_SURFACE_COLD1` uses BUILD2 and `prepare_guest_load.py --mode forward
--surface-only`, installed into a separate fresh child by
`GPU_SURFACE_INSTALL1`. Restore verdict: **514 serial lines, 0 panics,
reached shell: yes**. The worker loads the bundle then attempts three local
IOSurface generations with CPU writes/lock/unlock/readback, without uploading
a shader or sending any host GPU request.

**Observed failure, scoped to the minimally entitled helper:**
`wire.log:648` records
`IOUC IOSurfaceRootUserClient failed MACF in process pid 111, dvm-gpu-work`.
At 34.149 seconds the worker reports
`HARNESS_FAIL local IOSurfaceCreate returned NULL`; it exits 1. At 35.372
seconds the supervisor reports `wait_status=256` and hands off to the
original input executable. At 47.507 seconds
`DVM_INPUT_START version=5 pid=84` is observed. This proves original-helper
exec after a failed diagnostic, not completion of native input registration
or an input ACK. The owned VM was then terminated.

The same local-surface test passes on the host; that does not answer guest
permissions. The guest's MACF denial precedes allocation and identifies the
next policy contract to resolve. No claim that a new IOSurface implementation
or IOGPU inheritance is necessary follows from this denial. Access in an
appropriately entitled/sandboxed guest process remains untested at this point.

### One-entitlement retry: passed

`GPU_SURFACE_COLD2` repeats the same code/workload on another original-parent
child, adding only this worker entitlement:

```xml
<key>com.apple.security.exception.iokit-user-client-class</key>
<array><string>IOSurfaceRootUserClient</string></array>
```

This was a hypothesis based on the exact guest backboardd using that exception
mechanism for another client. An independent audit subsequently found Setup
and SpringBoard list IOSurfaceRootUserClient under the **direct**
`com.apple.security.iokit-user-client-class` key. That alternative remains
untested by us; runtime, rather than the entitlement inventory alone,
establishes the tested exception's effectiveness here. No broad sandbox
profile, protected-surface entitlement, kernel patch or IOGPU service was added.

The worker's code is unchanged from BUILD2; its new signature CDHash is
`9274b9db1ac64c38abb960204bbe478ae39b73e7`. Bundle and supervisor CDHashes
remain identical. BUILD3 and STAGE2 preserve the signed entitlements and TC.
Commands differ from the prior surface trial only in new output names and
`build_guest_forward.sh NEW_OUTPUT --iosurface-client`. Both surface staging
commands use `--mode forward --surface-only`.

`GPU_SURFACE_INSTALL2/probe.txt`: **514 serial lines, 0 panics, reached
shell: yes**. Normal System trial command:

```sh
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_SURFACE_INSTALL2/warm-manifest.json \
  --tag GPU_SURFACE_COLD2 --seconds 180 \
  --worker /tmp/dvm/GPU_FORWARD_HOST_FINAL1/metal_proxy_server
```

`GPU_SURFACE_COLD2/result.json` records three successful generations at
12.770, 12.776 and 12.779 seconds, each **12,288 bytes × three patterns**,
followed by `HARNESS_SURFACE_COMPLETE result=pass
scope=guest-local-cpu-access-only` at 12.780. The worker exits 0 at 12.908;
`DVM_INPUT_START version=5 pid=84` follows at 12.989. The runner terminates
the owned VM at 13.059 seconds. The host worker receives no GPU request.

**Proven:** ordinary guest IOSurface allocation, CPU address, declared
geometry/allocation size, lock/write/unlock/read-lock/readback/unlock, and
release work for this properly entitled helper. **Untested:** protected
surfaces, GPU cache coherency, cross-process adoption, guest fences or display
submission. All three released/recreated surfaces returned ID **2**: an
IOSurface ID alone cannot serve as a persistent generation or checkpoint
identity. The forwarding protocol's generation counter is deliberately separate.

## Remaining dependencies

Automatic discovery/registration, a full compositor's private Metal behavior,
all broader shaders/formats, concurrent input/GPU transport, throughput,
display adoption of the returned guest surface, and live-GPU checkpointing
remain separate experiments. The earlier copied-pixel display and checkpoint
proofs do not cover live queues, fences, retained resources or host object
identity. Windows lacks this tested host Metal execution path; translation
of this AIR/shader workload to a Windows backend remains untested.

## Updated route assessment and next implementation

| Route / contract | Status and scope |
| --- | --- |
| Exact guest UI AIR → this Mac's Metal pipeline and verified pixels | **Proven**, one copy shader; broader compositor shaders and performance untested. |
| Signed guest bundle + process-local `_MTLDevice` concrete initializer | **Proven**, no automatic discovery/global registration. |
| Guest IOSurface CPU access in the minimally entitled control | **Disproven within that entitlement scope** by MACF denial. |
| Guest IOSurface CPU access with one class exception | **Proven**, three generations/nine patterns, no display submission. |
| Custom guest forwarding → host Metal → guest output | **Untested end to end**; trial stopped during upload. Console framing without integrity/isolation is **disproven** under observed kernel output. |
| Adapted Apple PV guest stack | **Untested**; prior host PV primitives do not establish the guest ABI/loading/command or live-state contracts. |
| Interception/reuse of existing guest components | **Untested** beyond shader reuse and base-object construction; compositor call coverage and controlled opt-in remain dependencies. |
| Live GPU checkpoint/resume | **Untested**; earlier copied-pixel snapshot proof is narrower. |

The smallest next implementation is a **reliable one-operation transport**
for these existing diagnostic objects. First test integrity, ordered byte
delivery and duplicate suppression under deliberate console noise, disconnect
and delayed acknowledgements. If using UART, framed blocks need explicit
length, integrity, bounded resynchronization and idempotent byte offsets;
only acknowledged bytes may reach the worker. Repeating a frame must never
repeat an already-submitted GPU command. A dedicated channel is preferable
for subsequent performance work, but its guest discovery/access is another
dependency. Preserve one console reader and the original input path.

Then repeat the exact shader operation and verify actual returned host bytes
through the now-proven guest IOSurface lock/readback path. This should precede
automatic GPU publication or a compositor hook. Library caching by checked
guest content hash can reduce diagnostic traffic, but an explicit first-load
byte/provenance check must prevent accidental substitution. Only after this
round trip passes should we measure a real compositor operation and choose
between a small opt-in interception point and a discoverable plugin.

Even a successful round trip will leave major dependencies: useful transport
bandwidth/latency, broader shader/pipeline/format behavior, complete resource
ownership/error semantics, fence ordering, surface adoption into the existing
display, concurrent input, and draining/recreating host state across a guest
checkpoint. No route is declared impossible from missing symbols, method
lists, the two build-tool failures, or the scoped MACF/console failures.

## Review, validation and evidence

Terra/high subagents performed bounded provisioning, ABI, linkage and negative
test audits. Parent review corrected overstrong initializer claims, removed
unverified selector aliases, strengthened ownership/surface checks, reran
host tests, and fixed a negative-test cleanup race. The later runtime
entitlement result supersedes an agent's untested choice of the direct key.
No agent launched a VM or edited the main checkout.

The 30 existing host regressions pass. C/Objective-C builds use warnings as
errors, apart from the explicit incompatible-sysroot compatibility setting.
Host worker tests, nine forwarding operations, nine simulated-UART operations,
13 ownership/shape rejection cases and transport parser checks pass. Shell
syntax, Python compilation and `git diff --check` pass. Five successful
restore-install verdicts above are actual `tools/probe.sh` runs. The earlier
misconfigured restore trial is retained as a failure, not omitted.

Small records, source versions, raw errors, exact commands, signatures,
manifests and hashes are retained under
`/Users/jdolbe1/dvm-artifacts/research/gpu-guest-forwarding-ios27-20260905/`.
`index.json` hashes every preserved file. Original large RAM snapshots,
disposable disks and extracted libraries remain outside git. All VMs and
host workers created for this follow-up were terminated; unrelated VMs,
the migrated baseline, SPTM/TXM, and working rendered-Home lineage were not
modified. No useful UI speedup has yet been demonstrated.
