# Opt-in kernel development loading: exact iOS 27

Guest: 24A5430a, iPhone17,3/T8140. This is a development iteration
experiment, not a production signing policy or system Metal registration.
Only disposable disk children and separately generated BootKCs are used.
SPTM, TXM, the device tree and the existing managed MMIO service are unchanged.

## Contract and bounds

Question: can a fresh, explicitly authorized test process map a newly staged
driver from Data and execute actual guest CARenderer through it?
Acceptance requires package identity, executable loading, real host GPU work,
verified pixels, resource release, and a subsequent ordinary control. Opening
a user client or changing code-signing flags alone is insufficient.

The opt-in BootKC adds a wrapper around the existing service entry point.
Development open type `0x44564d4c` requires service name `dvm-transport`, the
current process's AMFI-resolved Boolean entitlement
`org.darwin-vm.development-loader`, and its own embedded task. Normal transport
open `0x44564d54` delegates to the unchanged service. The test child requests
development opening only when its fixed runner job explicitly enables it.

The wrapper invokes stock `cs_allow_invalid`; a narrowly scoped AMFI policy
wrapper permits this call for the entitled current process. All other callers
use the original policy. The TXM wrapper preserves and logs the original
return, including failure. It does not resolve entitlements while the caller
holds `p_mlock`. No monitor code or result is patched to claim approval.
The stock code-signing routine can change process flags even when TXM rejects
the request. Those changes end with the fresh test process; they do not prove
the staged executable is usable.

Each child has a 90-second deadline. The session uses the existing readiness
and no-progress bounds and an explicit total deadline. Stop after observing
the failing contract; do not repeat the same kernel/entitlement combination.

## Static evidence

`inspect_development_loader.py` verifies both input hashes, records bounded
disassemblies, and decodes TXM's selector table. Addresses below are link VAs.

| Contract | Exact evidence |
| --- | --- |
| AMFI invalid-code policy | `0xfffffff0091b009c`; registered at `0xfffffff0091acb18` in policy slot `0x998`; kernel dispatch at `0xfffffff00b304534` |
| Entitlement resolver | `0xfffffff0091ac43c`, taking process, key and Boolean output |
| Stock process transition | `0xfffffff00afc937c`; guarded inline `proc_task` layout at `+0x60`: process byte `+0x448`, embedded task `+0x768` |
| TXM address-space request | `0xfffffff00b045b40`; selector 41 at `0xfffffff00b045b6c`; original call preserves pmap lock/reference handling |
| TXM selector 41 | Table entry file offset `0x37724` resolves to `0xfffffff01703b33c`; call at `0xfffffff01703b348` reaches `0xfffffff017033590` |
| Monitor prerequisites | Developer Mode check at `0xfffffff0170335b0`; target `get-task-allow` check at `0xfffffff017033618` |
| Kernel Developer Mode getter | `0xfffffff00b0429b8`; also used by the AMFI policy |

The builder pins the managed-export input BootKC SHA-256
`41abd56f8295dbc83d9131857357909c035ca0a33b244f5614f16e03185947c9`.
It also guards individual ABI bytes and proves the injected range is zero
padding inside the outer executable mapping without overlapping another
fileset segment. It extends only the immediately preceding tmpfs RX extent
and places the payload at `0xfffffff00aa5c180`. The prior transport payload
at `0xfffffff00b34b480` remains byte-identical. The ledger records every patch,
the payload and all input/output identities.

TXM SHA-256 remains
`b8617cfca055a03711247ad9652f3cfdb026889dcca2eb1436496df4cb61a398`;
SPTM remains
`b0fd274d3009ccfbc9902e99ef441a2231fb852931f8b31233e9bc0f55207048`.

## First runtime experiment

`CA_DEV_LOADER_GUEST1` uses BUILD1 and BOOT3. Its kernel payload is 1,029
bytes; output BootKC SHA-256
`1c7bab8245be1b65d913681b58ee836d290e00a745e8e5c8d94c65cbdab09f58`.
The helper has the dedicated entitlement but lacks `get-task-allow`.

| Job / fresh PID | Request | Observed outcome |
| --- | --- | --- |
| `1788742723242885` / 350 | Installed driver, normal open | Actual CARenderer red64 passes; 22.739 s spawn-to-exit |
| `1788742755984510` / 366 | New revision on Data, development open | Kernel TXM wrapper returns `0x5`; flags `0x22002301` → `0x32002001`; dyld rejects signature, exit 1 |
| `1788742776007234` / 375 | Boot-trusted driver on Data, development open | Same monitor/flag outcome; dyld reports `file system sandbox blocked mmap()`, exit 1 |
| `1788742804579399` / 397 | Installed driver, normal open | Actual CARenderer red64 passes again; 7.201 s spawn-to-exit |

The candidate CDHash `6d3b6862e75abdeb3f247f76ccfa5ab1e1a7b07a`
is absent from the boot trust cache. The exact signature failure is
NSCocoaErrorDomain 3588, errno 1, slice offset 0, code blob offset `0x3a820`,
size `0x300`. Serial lines 32638–32639 and 33927–33928 record the monitor and
flag results. Per-job `guest-loading.log` preserves those UART witnesses;
`driver-audit.jsonl` preserves the chunked dyld failures independently.

Both ordinary controls independently pass audit CRC/sequence verification,
one render pass/one indexed draw, 4,096 exact red pixels and zero live host
resources. Pixel SHA-256:
`c34fb4331b2d031d7c644860b54a678424c66ef12352fc165a91dc09840d98fd`.
They prove that the experimental kernel preserves this existing workload;
they do not prove a driver revision loaded.

Readiness was established once at 123.908 s, with 49 native presentations,
input PID 163 stable for 10 seconds and a fresh ACK. At teardown: same PID,
1,895 presentations, zero timeouts. There was one helper restart and one
rejected ACK **before** readiness; neither increased during the jobs. This is
native display/input continuity, not a gesture test or presentation of the
offscreen CALayer. The owned VM exited after the queued stop. The runner
session passed its explicitly mixed positive/negative controls; the
**development-loading milestone failed**.

The result disproves this particular custom-entitlement-plus-process-flags
route. It does not disprove a development service. Static inspection identifies
the missing `get-task-allow` prerequisite and an independently necessary
Developer Mode gate. BUILD2 adds the former; BOOT4 logs both before invoking
the unchanged monitor path. The second experiment confirms them at runtime.

## Second runtime experiment: monitor approval

`CA_DEV_LOADER_GUEST2` uses BUILD2 and BOOT4: a 1,196-byte payload, BootKC
SHA-256 `2845d7d0147448ca8f2bcfacd5d36c5507340ebcb00042418042e7b00034392d`.
The helper-only link/sign rebuild took 4.387 seconds; driver/backend objects
and QEMU were reused. Normal controls ran in PIDs 265 / 347, jobs
`1788743175370408` / `1788743447070457`. Both independently verified the same
actual GPU workload, pixels and zero live resources; spawn-to-exit durations
were 32.249 / 2.739 seconds. Host CPU activity was not controlled, so these
setup-inclusive timings are not comparative performance benchmarks.

Job `1788743366593384`, PID 298, new revision on Data:

```
DVM_DEV_LOADER prerequisites pid=298 developer_mode=1 task_allow_result=0x0 task_allow=1
DVM_DEV_LOADER monitor pid=298 result=0x0
DVM_DEV_LOADER flags pid=298 before=0x22002305 transition=1 after=0x32002005
```

These are serial lines 27516–27518. The unchanged TXM path now succeeds.
Nevertheless, dyld rejects the signature with the same errno/blob evidence;
child exit 1, no GPU submission. Job `1788743394639219`, PID 312, uses the
boot-trusted bundle on Data. It also obtains monitor approval but still fails
with `file system sandbox blocked mmap()`, exit 1. This isolates two loading
failures that remain after the development address-space contract is satisfied.
Do not summarize either job as successful revision loading.

Readiness: 152.724 seconds, 41 presentations, input PID 159. Final: same PID,
1,420 presentations, zero timeouts. The one pre-readiness restart/rejected ACK
did not increase. Both sessions stopped and reaped only their owned VM.

**Proven:** opt-in, entitled kernel entry and stock TXM development permission;
normal exact-guest CARenderer controls before and after it. **Disproven within
this scope:** adding that permission and `get-task-allow` alone makes the
staged bundle load. **Untested:** a scoped correction to the remaining
signature and executable-mapping contracts, unrestricted revision loading,
normal system device discovery and system-wide QuartzCore acceleration.

The proposed follow-up was to instrument the original return and relevant inputs of the
exact file-signature and executable-mapping policy callbacks for this test
process and its staged bundle. AMFI's `vnode_check_signature` implementation
starts at `0xfffffff0091ad774`, identified by its diagnostic at
`0xfffffff0091ad7d4`. A generic signature-failure diagnostic is referenced at
`0xfffffff0091ae190`. These are static landmarks, **not** observed branch
coverage. Establish the complete callback ABI and the actual failing branch
before adding a permission exception. Keep the now-proven TXM prerequisite;
do not repeat the old flag-only trial or patch TXM's result.

Small evidence and signed test packages are preserved under
`~/dvm-artifacts/research/gpu-development-loader-ios27/` with hash indexes.
The durable source BootKC and device tree are already in
`~/dvm-artifacts/gpu-managed-pool-ios27/`; no new flattened image is promoted
while staged loading remains unsuccessful. Host regressions: 89 GPU tests
passed, one skipped, 79 project tests passed before the initial boot; six
runner tests passed after the instrumentation changes. All four positive
guest jobs passed independent audit/pixel verification. Both generated
BootKCs were reconstructed byte-for-byte from the preimages, listed patches
and payload, with unchanged device-tree bytes. Post-experiment hashes of all pinned boot
inputs and the complete disk ancestry also matched both manifests.

## User-bounded follow-up: two fixes

The user limited further runtime-loading work to **two fixes**, with a stop
if a new revision still could not execute. These are separate from the two
earlier experiments above. Both reuse the already-installed BUILD2 helper,
driver/backend and unchanged QEMU; only an isolated BootKC changes. Each trial
uses a fresh disposable child of `CA_DEV_LOADER_INSTALL2`, establishes readiness
once, and runs successive tests in fresh processes. No guest reinstall,
debugger or trust-cache insertion is used in these trials.

### Fix 1: scoped ad-hoc CT gate

Review of the earlier **exact guest** UART, not merely static inspection,
found `unsuitable CT policy 0 for this platform/device, rejecting signature`
for the staged revision. The guarded branch at `0xfffffff0091ae5c8` leads to
that diagnostic; the original accepted continuation is `0xfffffff0091ae5f0`.
`--file-policy 1` allows that one branch only for a parsed ad-hoc signature,
real CT policy zero, Developer Mode, the entitled current process after its
explicit development opening, and this exact path shape:

```
/private/var/tmp/dvm-gpu-runner/<1..20 decimal digits>/DVMProxy.bundle/DVMProxy
```

The exception retains real policy bits and subsequent AMFI checks. The
mid-function trampoline preserves caller-clobbered integer/SIMD registers and
NZCV; ordinary accepted branches keep their native continuation. A separate
observer calls the original `mac_file_check_mmap` at `0xfffffff00b31e2dc`, logs
its result for the entitled/debugged test child, and returns it unchanged.
Its six-argument ABI and in/out maximum-protection pointer are established by
the exact dispatcher's prologue/call sites. Apple's public
[MAC policy interface](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/security/mac_policy.h)
corroborates the callback semantics, but is not exact-guest runtime evidence.

`CA_LOAD_FIX1_BOOT`: 2,291-byte payload, BootKC SHA-256
`8dc2564b4b5961f8bec66cecad28793dfd3ae6e060dfbd9f6322028a05380b48`.
In `CA_LOAD_FIX1_GUEST`:

| Job / PID | Outcome |
| --- | --- |
| `1788744384804857` / 267 | Installed normal GPU control passes; 15.889 s spawn-to-exit |
| `1788744384880102` / 278 | New revision: TXM 0, CT exception `allow=1` twice, then AMFI signature validation fails; exit 1; no GPU work |
| `1788744656633763` / 423 | Trusted bundle on Data: TXM 0, original mmap returns EPERM (1), prot 5, flags `0x40012`, offset 0, maximum 7 unchanged; exit 1 |
| `1788744719357846` / 475 | Installed normal GPU control passes again; 1.890 s spawn-to-exit |

The new-revision failure remains NSCocoaErrorDomain 3588 / errno 1, code blob
offset `0x3a820`, size `0x300`. Its per-job UART contains both `allow=1` and
`AMFI: code signature validation failed.` No executable-mapping observer ran
for this failed revision. **Observed:** the CT exception alone is insufficient.
**Not established:** which subsequent AMFI validation branch failed; the
generic diagnostic does not identify it. The trusted Data control independently
identifies an actual MAC mmap denial, rather than relying on dyld's wording.

Both normal controls independently pass audit CRC/sequence, package/upload
identity, actual render/draw, exact red pixels and zero live resources. Native
readiness occurs at 145.700 s, 70 presentations, input PID 162; teardown retains
that PID, 2,576 presentations and zero timeouts. One restart/rejected ACK occurs
before readiness and does not increase afterward. The owned VM is stopped and
reaped. Session acceptance of expected negative jobs is **not** acceptance of
runtime revision loading.

### Fix 2: scoped observed RX mapping

`--file-policy 2` adds one exception to the original mmap result: EPERM becomes
success only for the opted-in entitled/debugged current process in Developer
Mode, requested protection 5 (read/execute), flags `0x40012`, file offset zero,
and unchanged original/output maximum protection 7. It does not widen maximum
protections or permit a requested writable/executable mapping. This exception
is **process-scoped, not pathname-scoped**. Other requests retain the original
result; upstream signature validation and SPTM/TXM remain unchanged. Both the
original and returned mmap results are logged.

This final fix tests the independently observed mapping contract. It does not
claim to repair the new revision's remaining signature failure. Four jobs are
defined in advance: normal installed control, new revision on Data, trusted
bundle on Data, normal installed control, then stop. No third loading fix is
authorized by this experiment's limit.

`CA_LOAD_FIX2_BOOT`: 2,395-byte payload, BootKC SHA-256
`e4b6a2d02e97f3f058fa5ec1993c0423915eda8926dca0f7dac21c343e5f0741`.

Observed in `CA_LOAD_FIX2_GUEST`:

| Job / PID | Outcome |
| --- | --- |
| `1788744850776462` / 271 | Installed normal GPU control passes; 9.539 s spawn-to-exit |
| `1788744850854056` / 279 | New revision: TXM 0, CT `allow=1` twice, same signature rejection; exit 1; no GPU submission |
| `1788744850930345` / 280 | Trusted bundle on Data: mmap original 1, returned 0; actual CARenderer → host Metal, all 4,096 red pixels verified; 2.053 s spawn-to-exit |
| `1788744851034604` / 282 | Installed normal GPU control passes afterward; 2.141 s spawn-to-exit |

The Data success independently verifies guest staging SHA-256, audit CRC and
sequence, generated uploads, one render pass/indexed draw, exact final pixel
hash and zero live host resources. It uses the **unchanged boot-trusted driver**,
not REVISION3. The new revision still fails before mmap; the recorded generic
AMFI failure does not prove TXM rejected file registration or identify a more
specific subsequent validation branch.

Readiness: 151.674 s, 174 native presentations, input PID 230, ten stable seconds
and a fresh ACK. Final: same PID, 363 presentations. Five timeouts, five rejected
ACKs and one helper restart happened **before** readiness; those counters did
not increase during the tests. This confirms continuity during this short batch,
not a clean boot, a gesture test, offscreen-CALayer presentation, or sustained
pacing. Both owned VMs exited and were reaped after queued stops.

**Proven in this test image:** controlled staging and execution of an already
boot-trusted driver from Data through the opt-in RX mapping exception, followed
by normal GPU control. **Disproven within the two tested fixes:** CT-gate plus
RX-mapping exceptions are sufficient for this absent-from-boot-cache revision.
**Unresolved:** remaining file-signature validation/label acceptance; its exact
later failing branch is not yet observed. A future authorized experiment could
trace that branch and its original result without altering monitor approval.

Implementation stopped at the user's two-fix limit. No third fix or broader GPU
change was made. Continuing GPU development can use the already proven isolated
install/reboot path; dynamic revision loading remains an iteration improvement,
not proof against the existing accelerated CARenderer implementation.

Validation: 79 project tests passed before Fix 1. Both experimental BootKCs were
reconstructed exactly from recorded patch preimages/payloads. The final builder's
default `--file-policy 0` reproduces prior BOOT4 byte-for-byte. Five positive jobs
across this follow-up passed independent verification. These checks do not
substitute for the failed revised-driver acceptance test. Small evidence and
signed job packages are in
`~/dvm-artifacts/research/gpu-development-loader-two-fix-limit-ios27/`.
All pinned boot inputs and the complete disk ancestry matched their manifests
after both trials. The preserved v2 trust cache has 3,961 entries; the installed
control's CDHash is present and REVISION3's is absent. Complete preserved signed
bundles allow all five successful jobs to pass the independent verifier again
from durable evidence. Build source snapshots retain each tested variant,
including Fix 1's original observational logging format.

Reproduce the two variants with `build_development_loader.py --file-policy 1`
or `2`, using the durable managed-pool `bootkc` / `dtree`, then
`prepare_display_state_trial.py --installed
/tmp/dvm/CA_DEV_LOADER_INSTALL2/warm-manifest.json` with an isolated output.
Run `run_guest_load.py` with `--driver-mmio --driver-present --driver-consumer
--driver-runner --driver-wait-display`, the pinned BUILD2 `driver_host`, and the
exact QuartzCore library. Queue the four recorded jobs with `runner_control.py`
(`--mode installed` controls; `--mode data --development --expected observe`
for staged candidates), then `--stop`. Preserved ledgers, source snapshots,
manifests, launch commands and per-job packages supply exact inputs; input disk
overlays themselves are disposable and are not promoted as a new baseline.

## Reproduction

```sh
python3 tools/gpu/inspect_development_loader.py \
  --bootkc /tmp/dvm/POOL_EXPORT_BOOT4/bootkc --txm firmware/txm \
  --out /tmp/dvm/CA_DEV_LOADER_STATIC1
python3 tools/gpu/build_development_loader.py \
  --bootkc /tmp/dvm/POOL_EXPORT_BOOT4/bootkc \
  --dtree /tmp/dvm/POOL_EXPORT_BOOT4/system.dtree \
  --out /tmp/dvm/CA_DEV_LOADER_BOOT4
python3 tools/gpu/build_driver_revision.py /tmp/dvm/CA_DEV_LOADER_BUILD1 \
  /tmp/dvm/CA_DEV_LOADER_BUILD2 --bootstrap --development-loader
```

Stage/install with `prepare_driver_update.py` and `run_guest_install.py` on
the pinned managed-pool restore manifest; then freeze with
`prepare_display_state_trial.py`. These are the same guarded disposable-child
steps used by the [persistent runner](gpu-persistent-runner-ios27.md).
Enable `--driver-runner --driver-wait-display` in `run_guest_load.py` and queue
explicit jobs using `runner_control.py --mode data --development`.
Use `--expected observe` for anticipated loading failures. Keep at least one
required positive GPU control, then queue `--stop`. Full staging, manifest,
launch, package and timing records accompany each experiment.
