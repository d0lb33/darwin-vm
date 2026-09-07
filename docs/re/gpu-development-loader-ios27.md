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

Next bounded probe: instrument the original return and relevant inputs of the
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
