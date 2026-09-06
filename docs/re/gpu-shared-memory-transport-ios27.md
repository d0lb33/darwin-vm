# Shared-memory transport, exact iOS 27 guest

## Bounded service-loading experiment (2026-09-06)

This experiment transferred **zero commands or buffers to a host transport**.
It tests loading/mapping prerequisites; it does not establish MMIO latency or
GPU acceleration. Existing Metal results remain in
[the driver ledger](gpu-metal-driver-ios27.md).

Baseline: iOS 27 24A5430a, iPhone17,3/T8140, native SMC, original powerd,
SPTM/TXM and migrated Data lineage. Each run used a fresh qcow2 child, no RAM
restore or debugger. The existing DVMProxy bundle was preserved byte for byte.
Pinned QEMU SHA-256:
`577dc57e37246af6bc2bfdd0abafcf78a9000e9c3eb85c9a41c08bf9f4f6f4f5`.
No QEMU implementation changed in this experiment.

Before starting, project `8345192` and QEMU `eb8b65b` were pushed to their
respective `origin/codex/metal-driver-ios27` branches. Neither was merged to main.

### Observed results

| Contract | Result | Scope |
|---|---|---|
| `kext_request` MIG delivery | `kr=0` in every recorded call | Request reached the operation handler; this is distinct from operation success. |
| Recognizable 48-byte mkext envelope | `op=0x2e`, `KERN_NOT_SUPPORTED`, with/without management entitlement | Disabled legacy runtime mkext route. No executable kext or valid complete archive was submitted. |
| XML dictionary without predicate | `op=0xdc008005` | Parser/argument control. |
| `DaemonReady`, original entitlements | `op=0xdc008004` | Not privileged. |
| `DaemonReady`, management entitlement | `op=0xe00002d8` | Not ready; management gate passed, daemon activation not established. |
| Own inert `LoadCodelessKext`, original entitlements | `op=0xdc008004` | Rejected at management gate. |
| Same codeless request, management entitlement | `op=0` | Codeless personality accepted; **not proof of custom executable loading**. |
| Immediate property-matched service query | count 0 in both variants | No usable published service observed at that point; asynchronous matching/registration was not resolved. |
| `IOPlatformExpertDevice` open type 0 | `0xe00002c2` (`kIOReturnBadArgument`) | This type is not an available generic mapping client. Other types not tested. |
| Owned NS6 user client open type 0 | success | Existing storage client opens; no block I/O performed. |
| NS6 `IOConnectMapMemory64`, memory type 0, read-only | `0xe00002c2`, size 0 | This specific mapping contract failed. Other indices not tested. |

The two management builds have identical hashes for every `__TEXT` section.
The sole entitlement addition is
`com.apple.private.security.kext-collection-management=true`.
They run at ordinary RunAtLoad on siblings of the same installed parent.
The codeless personality uses the existing `IOService` class, a unique
`IOMatchCategory`, and no driver executable. It does not select a GPU or replace
a hardware driver.

The guest has `/usr/libexec/driverkitd`, a DriverExtensions directory with twelve
observed bundles, and DriverKit 509.2.1 in its exact dyld cache. `/dev/mem` and
`/dev/kmem` returned ENOENT. No active `IOUserServer` matched during the probe.
These facts justify investigating DriverKit; they do not prove custom-driver
startup, provider matching, mapping, DMA preparation, or interrupt delivery.

`MMIO_CONTRACT_ENTITLED2` completed its native display/input observation at
109.508 seconds: 83 presentations, input ready, stable PID/epoch for ten seconds,
and a fresh ACK. This proves continued native presentation/helper readiness,
not a gesture or accelerated presentation. The first entitled run completed its
contract calls and displayed the lockscreen, but its new observer omitted
`DARWIN_INPUT_STATUS`; it was stopped and retained as an orchestration failure.
The corrected run is the display/input pass. Both outcomes are archived.

### Static evidence and limits

Exact BootKC `_Xkext_request` at `0xfffffff00ab75bb8` calls the operation at
`0xfffffff00b1bac9c`. At `0xfffffff00b1badd0`–`0xfffffff00b1badfc`, length and
magic/signature checks lead directly to writing `0x2e` to the operation result.
The branch precedes archive validation or executable loading. This corroborates
the runtime envelope result; it does **not** rule out boot-integrated services.

Apple's [OSKextLib.cpp](https://github.com/apple-oss-distributions/xnu/blob/main/libkern/OSKextLib.cpp)
provides the corresponding secure-kernel mkext rejection, while
[OSKext.cpp](https://github.com/apple-oss-distributions/xnu/blob/main/libkern/c%2B%2B/OSKext.cpp)
describes entitlement-gated codeless requests. These are architectural references,
not identical-build source. Runtime return values and exact BootKC instructions
are the evidence for this guest.

### Reproduction and artifacts

From this worktree:

```sh
bash tools/gpu/build_transport_contract.sh /tmp/dvm/METAL_DRIVER_BUILD19 /tmp/dvm/MMIO_CONTRACT_BUILD2 --manage-control
bash tools/gpu/build_transport_contract.sh /tmp/dvm/METAL_DRIVER_BUILD19 /tmp/dvm/MMIO_CONTRACT_BUILD3 --manage-entitled
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/MMIO_CONTRACT_BUILD1 --build /tmp/dvm/MMIO_CONTRACT_BUILD3 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/MMIO_CONTRACT_STAGE1/system.tc --out /tmp/dvm/MMIO_CONTRACT_STAGE4
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/MMIO_CONTRACT_INSTALL1/warm-manifest.json --stage /tmp/dvm/MMIO_CONTRACT_STAGE4 --tag MMIO_CONTRACT_INSTALL3
python3 tools/gpu/run_guest_load.py /tmp/dvm/MMIO_CONTRACT_INSTALL3/warm-manifest.json --tag MMIO_CONTRACT_ENTITLED2 --seconds 150 --aux-namespace --probe-observe-display
python3 tools/gpu/verify_transport_contract.py /tmp/dvm/MMIO_CONTRACT_ENTITLED2 --management accepted --require-display
```

Output paths must be fresh. Initial BUILD1 uses the same build command without
a management flag, installed as a child of `METAL_DRIVER_INSTALL10` using
BUILD19 as the preimage and `METAL_DRIVER_STAGE12/system.tc`. INSTALL2 installs
BUILD2 on INSTALL1. Archived orchestration/provenance files record full commands
and all preimages. STAGE3 was prepared with delayed launch but **never booted**;
the paired management trials use STAGE2 and STAGE4 with RunAtLoad.

Durable evidence:
`/Users/jdolbe1/dvm-artifacts/research/gpu-mmio-contract-ios27-20260906/`.
Its manifest hashes sources, import provenance, entitlement comparison,
disassembly, result/launch records, logs, and screenshots. Disk/RAM images,
firmware, shader libraries and executable binaries are excluded. Disposable
parents and exact binaries remain under `/tmp/dvm/MMIO_CONTRACT_*`.

Validation: 79 project regressions, 13 existing driver tests, shell syntax and
exact-cache guest import validation passed. The contract verifier distinguishes
operation results from MIG success and explicitly reports
`transport_proven=false`.

### Decision after this bounded experiment

Legacy mkext loading is **disproven within the tested runtime-envelope scope**.
Entitled codeless request acceptance is **proven**. A usable custom DriverKit
service, boot-integrated mapping service, shared-memory data path, notifications,
and transport latency remain **untested**. No result here establishes that
IOGPUFamily inheritance is required.

Next evaluate a dedicated DT node and a narrowly gated boot-kernel mapping path
using existing IOKit machinery. This avoids making custom DriverKit executable
loading a prerequisite. Required evidence: the guest opens that owned service,
maps only its RAM/register ranges, publishes a command and rings a doorbell,
then verifies a host response without NVMe or debugger access. Keep the existing
NVMe proof unchanged until this path passes.

For that milestone, stop on any out-of-range mapping, stale session/sequence,
CRC mismatch, timeout, guest panic, or loss of native display/input. Start with
one in-flight command and fixed owned pages; prove close/reopen and stale-reply
rejection before reusing GPU resources. No checkpoint success is assumed:
initially block migration while the endpoint is active, then design drain/reset
and session-generation behavior explicitly.

Only after verified transport should the existing exact-AIR Metal workload move
onto it. Measure guest round-trip p50/p95/p99/max separately from host GPU time,
bytes copied, doorbells, guest CPU busy-wait time, and CPU reference time. A useful
first transport target is warmed empty-command p50 below 0.25 ms and p99 below
1 ms; these are proposed acceptance targets, **not measured results**. Retain
software execution for workloads whose total offload time exceeds their CPU
time, including the current tiny luma test if it remains below the crossover.

## Boot-integrated endpoint and host Metal (2026-09-06)

The boot route has now passed the requested mapping/doorbell milestone and the
existing exact-AIR Metal workload. The earlier service-loading section is the
record of the bounded experiment before this implementation, not the current
transport status. Custom executable DriverKit startup remains **untested**; the
accepted codeless personality did not establish that contract. The route chosen
here removes that dependency by extending existing boot-loaded IOKit machinery.

### Implementation and measured scope

`build_boot_transport.py` accepts only the native-SMC BootKC SHA
`da1e254ab81e31adae87c049da295b582dabbd4ba46096fc58f3e5467fc6e02c`.
It adds `/arm-io/dvm-transport` to a copied native DT and checks every pre-existing
property remains byte-identical, plus candidate range overlaps. The node owns
16 MiB at physical `0x4f0000000` and a 16 KiB register range at `0x4f1000000`.
SPTM/TXM are not patched. Every runtime uses a new disk child and new RAM file;
the installed parents and migrated baseline remain unchanged.

A 1,244-byte PIC shim occupies verified zero padding at
`0xfffffff00b34b480` in the copied BootKC. Guarded entry patches wrap
`IOService::newUserClient` (`0xfffffff00b1f1a10`) and the originally unsupported
`IOUserClient::clientMemoryForType` (`0xfffffff00b281de0`). Kernel C++ PAC ABI and
virtual-call slots/diversities are checked against this BootKC before building.
The existing RX segment's file extent grows within its unchanged VM extent.
The builder records preimages, output hashes and generated assembly.

Only the provider named `dvm-transport`, with open type `0x44564d54`, takes the
new allocation path. `IOUserClient` is abstract; the shim instead uses the
already-loaded concrete `IOKitDiagnosticsClient` for task/close lifecycle.
It sets the required `org.darwin-vm.transport` entitlement property on that
client and exposes only descriptor indices 0/1 through memory types
`0x44560000`/`0x44560001`. Stock paths are preserved for other providers.
This is a narrowly scoped **experimental boot shim**, not a shipped custom
kernel class. No diagnostics external method or debugger is used to transport
commands. Entitlement enforcement is supported by static control flow and an
entitled successful open; an unentitled boot-service negative trial is still
untested. The inherited diagnostic external-method surface has not been audited
for production use.

QEMU requires both the exact DT ranges and `DARWIN_GPU_SHM_PATH`. With neither,
the device is absent. A named `dvm_gpu_notify` socket selects external mode;
otherwise it performs a bounded synchronous XOR echo. The ABI has a random
16-byte session, monotonically increasing sequence, bounded length and CRC,
one outstanding command, separate request/reply areas, a submission doorbell
and a polled completion register. The external host receives a 16-byte
notification, reads shared bytes, executes the existing Metal worker, writes the
reply and sends a 16-byte completion. The runner selects the socket along with
UART readiness; it does not poll the payload mailbox at the NVMe interval.

The Metal helper requests `kIOMapCopybackCache` for RAM, leaves the doorbell as
an I/O mapping, and uses publication/acquisition barriers. It keeps JSON/base64
RPC and explicit host resource copies for this comparison. This is **not** a
zero-copy Metal allocation scheme. Completion is polling, **not an IRQ**.
Guest CPU time spent polling was not measured; iteration counts and doorbell
wall time are recorded and must not be mislabeled as CPU utilization.

### Observations, including failed contracts

* `MMIO_ECHO_BOOT1`: 16-byte echo passed, then the 4 KiB response was rejected
  with host code 6. Independent bytes/CRC matched. The comparison widened
  QEMU's signed `ldl_le_p` return against zlib's unsigned-long result; CRCs with
  bit 31 set failed. Explicit `uint32_t` operands corrected this host bug.
* `MMIO_ECHO_BOOT2`: six exact XOR responses passed (16 B, 4 KiB and 64 KiB,
  before and after close/reopen). Both mappings, unmaps and closes returned
  success; wrong open and third mapping were rejected with `0xe00002c2`.
  Doorbell round trips ranged 1.907–26.702 microseconds. This synchronous echo
  timing excludes payload preparation/verification and is **not GPU latency**.
  Independent final shared-file verification passed. Native observation passed
  at 112.45 seconds with 71 presentations and a fresh stable-input ACK.
* `MMIO_METAL_BOOT1`: mappings and bundle loading passed, but the worker received
  SIGBUS before the first doorbell. `MMIO_METAL_DIAG1` repeated it with an ordinary
  signal handler: ESR `0x92000061` (write alignment fault), address
  `0x101850055` = RAM base `0x101840000` + `0x10055`. Runtime PC
  `0x1a18c807c` was `memcpy+0xdc`. Exact-cache disassembly resolves that to
  `__platform_memmove` at `0x18c37407c`, instruction
  `stp q2, q3, [x3, #0x20]`. The default I/O mapping did not support this unaligned
  optimized copy. No command reached the host. QEMU's later disconnect error
  is teardown evidence, not the cause of SIGBUS.
* BUILD3 changed the RAM mapping request to `kIOMapCopybackCache` and retained
  the signal recorder. `MMIO_METAL_BOOT2` then completed all eight nonce workloads
  at 16.878 seconds: exact QuartzCore AIR, two actual host GPU dispatches per
  submission, verified intermediate/final results, six persistent resources
  reused and retired to zero. Native presentation/input independently passed
  at 106.125 seconds with 78 presentations, stable PID/epoch and a fresh ACK;
  the final image was visually checked as the normal lockscreen.

Across the Metal variants, DVMProxy SHA remains
`f8f67b25cc9ef2c62d3b09b612ee6306ecb2d27c9fe5b5309361f18b7918567a`
and host-worker SHA remains
`c1d916cdff496ee3b52f35a24f7411c85c7b4dca86ad6927fee868db965ce417`.
The frontend, host worker and shader workload are byte-identical to BUILD19.
The boot baseline is preserved: iOS 27 24A5430a, native SMC, original powerd,
migrated Data ancestry, SPTM/TXM, existing display/input and software renderer.
No second namespace, guest debugger, restored RAM or global Metal registration
is involved. The sole ordinary NVMe disk remains the guest's boot storage.

### Reproduction

All output directories/tags must be new. These commands describe the successful
variant; failures and intermediate builds are retained separately.

```sh
python3 tools/gpu/build_boot_transport.py --bootkc /Users/jdolbe1/dvm-artifacts/native-smc/bootkc --dtree /Users/jdolbe1/dvm-artifacts/native-smc/system.dtree --out /tmp/dvm/MMIO_BOOT_BUILD4
ninja -C qemu-sptm/build
cp qemu-sptm/build/qemu-system-aarch64 /tmp/dvm/MMIO_BOOT_BUILD4/qemu-system-aarch64
bash tools/gpu/build_driver.sh /tmp/dvm/MMIO_METAL_BUILD3 --mmio
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/MMIO_METAL_BUILD2 --build /tmp/dvm/MMIO_METAL_BUILD3 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/MMIO_METAL_STAGE2/system.tc --out /tmp/dvm/MMIO_METAL_STAGE3
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/MMIO_METAL_INSTALL2/warm-manifest.json --stage /tmp/dvm/MMIO_METAL_STAGE3 --tag MMIO_METAL_INSTALL3
python3 tools/gpu/prepare_mmio_manifest.py /tmp/dvm/MMIO_METAL_INSTALL3/warm-manifest.json /tmp/dvm/MMIO_BOOT_BUILD4 /tmp/dvm/MMIO_BOOT_BUILD4/metal-copyback-manifest.json
python3 tools/gpu/run_guest_load.py /tmp/dvm/MMIO_BOOT_BUILD4/metal-copyback-manifest.json --tag MMIO_METAL_BOOT2 --seconds 180 --driver-mmio --driver-worker /tmp/dvm/MMIO_METAL_BUILD3/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
python3 tools/gpu/verify_mmio_metal.py /tmp/dvm/MMIO_METAL_BOOT2 --output /tmp/dvm/MMIO_METAL_BOOT2/verification.json
```

For a fresh installation directly on production INSTALL10, use BUILD19 as the
preimage and STAGE12/system.tc as the trust-cache parent. Install through the
unmodified native-SMC boot configuration, then derive the runtime MMIO manifest;
the installer must not boot the MMIO DT without its paired RAM backend. The
shell installer verifies all file preimages before writes, mounts System only
inside its owned restore guest and verifies the writes. The host only attaches
a copied small installer ramdisk through `safe_attach.sh`.

`build_mmio_probe.sh BUILD19 NEW_BUILD` builds the standalone echo client;
install it with the same guarded update/manifest process, then run
`run_guest_load.py MANIFEST --tag NEW_TAG --seconds 150 --mmio-echo --probe-observe-display`.
An ordinary `build_driver.sh NEW_BUILD` retains the NVMe transport fallback.

Validation for the successful variant: 79 project regressions, 17 driver tests,
shell syntax, exact-cache import checks and code-signature verification passed.
New host tests exercise fragmented notifications against the real worker and
reject CRC/session/duplicate errors before forwarding. Those negative tests
are **host-peer tests**, not guest fault-injection claims. Runtime echo rejects
invalid open/memory types; malformed guest doorbell/CRC injection remains
untested.

### Post-display reporting and checkpoint guard

`MMIO_METAL_DISPLAY1` held readiness until native presentation, a stable input
PID/epoch for ten seconds and a fresh ACK (109.065 seconds, 88 presentations).
The host then processed eight real GPU submissions and retirement to zero, but
UART contained no later guest readiness/result messages. The runner therefore
failed its 30-second acknowledgement deadline. This is a failed **result
reporting contract**: it is not evidence that MMIO commands or GPU execution
stopped. That run cannot provide observed guest verification or guest timings.
It is retained as a failure, not promoted to a full pass based on host output.

BUILD4 mirrors the helper/workload's bounded result records into 64 fixed shared
RAM slots, with sequence, length and CRC, publishing each slot before the head.
The host consumes those records independently of stderr. Only result reporting
changed in the shader workload; the two-pass algorithm and oracle are unchanged.
DVMProxy and the host Metal worker remain byte-identical. The helper exits on ring
capacity overflow instead of wrapping or silently dropping results. The new tests
check unpublished records, exact publication, bad CRC and out-of-range head.
This is experiment telemetry; it is not the GPU command or completion protocol.

During DISPLAY1, an actual QMP `migrate` request and HMP `savevm` request each
returned `DVM shared-memory transport needs a drain/reset checkpoint contract`.
QMP `query-status` reported running before and after. The initial HMP call used
a misspelled argument and failed parsing; the corrected call is the rejection
proof. No checkpoint was created. This **proves rejection while the endpoint is
active**, not correct drain, GPU serialization, restore or reset. The earlier
copied-pixel checkpoint result remains a separate scope.

The default NVMe build was rebuilt after adding the mode switch. Its first
attempt exposed macOS Bash 3's `set -u` behavior for an empty array; using an
explicit `-UDVM_DRIVER_MMIO` default flag fixed that build-only issue. The fresh
fallback build passes ten real host-driver tests and preserves the original
DVMProxy and host-worker binary hashes. It was not reinstalled or benchmarked
as a new NVMe A/B trial. All experiment boots continue to use TCG with six guest
CPUs; these results do not establish HVF mapping/coherency or latency.

### Completed post-display measurements

`MMIO_METAL_DISPLAY2` and the identical fresh-process repeat `DISPLAY3` both pass
independent verification of shared audit CRCs/sequences, all eight guest nonce
oracles, actual host GPU completions and resource retirement. They finish at
105.647 and 110.761 seconds respectively. The readiness gate is native
presentation plus a stable input PID/epoch and fresh ACK, not an assertion that
the home screen was unlocked. Final screenshots show the normal lockscreen.

| Median, microseconds (8 samples per run) | DISPLAY2 | DISPLAY3 |
|---|---:|---:|
| Guest work, upload/encode through completion (verification follows) | 7,621 | 8,877.5 |
| Guest submit RPC, including serialization and response parsing | 4,994.5 | 5,582.5 |
| Doorbell through completion register observation | 1,171 | 1,196.5 |
| Host worker round trip and response publication | 876.583 | 891.792 |
| Actual host Metal GPU duration | 26 | 26 |
| Guest CPU reference, including input generation/half conversion | 488.5 | 537.5 |

Full-work minima were 7,089/7,293 us and maxima 25,384/29,657 us. Nearest-rank
p95/p99 equal the maxima at this sample size; these are descriptive samples,
not estimates of production tail reliability. Raw per-trial values and the
percentile method are in `verification*.json`. First submissions are included;
they are not discarded as warm-up. The previous roughly 11.5 ms NVMe medians
are historical context, **not a newly matched A/B control**. The current tiny
workload is still roughly 16 times slower than its CPU reference, so there is
no acceleration win to enable for it.

DISPLAY2 used 22 RPCs, 270,194 request bytes, 8,717 reply bytes and 38,804 guest
poll iterations. DISPLAY3 used 21 RPCs, 270,369 request bytes, 8,571 reply bytes
and 32,914 polls. The extra RPC is a retirement stats poll. Each RPC has one
16-byte command notification and one 16-byte completion notification; the
initial host READY notification is additional. These payload counts describe
wire representations, not total bytes copied by Foundation/Metal. They do not
include the separate bounded result log. Command/resource payloads do not flow
through UART or NVMe. Empty-command latency and guest CPU-time accounting remain
untested; no claim is made that the proposed empty-command p99 target passed.

### Route status and smallest next implementation

* **Proven, narrow scope:** boot-discovered existing IOKit provider/client
  machinery maps the dedicated ranges; guest commands/doorbells and host
  responses work without NVMe or debugger assistance; exact guest AIR executes
  through the process-local Metal frontend; guest verifies output and host
  resources retire. Software rendering remains the default system path.
* **Disproven within scope:** the legacy recognizable runtime mkext envelope is
  rejected; default I/O mapping cannot support the tested unaligned optimized
  copy; this tiny luma offload does not beat its CPU reference. None disproves
  a proper custom driver or larger GPU workloads.
* **Untested:** full custom DriverKit executable startup, a dedicated production
  kernel client class, interrupt/event completion, hostile/multiple-client
  isolation, reset/restore, HVF coherency, global Metal discovery, render-encoder
  coverage, new-transport IOSurface/DCP integration and system Liquid Glass.
  The earlier copied-pixel display/checkpoint results do not establish these.

The smallest next implementation is a fixed binary submission descriptor and
registered resource offsets over this same endpoint, retaining the exact
workload/oracles. That removes JSON/base64 serialization and avoidable copies
before broadening Metal coverage. Its major dependencies are explicit ownership
and publication rules for reusable buffers, measured guest/host CPU cost, and
an eventual bounded event/interrupt completion contract. Keep one request in
flight and fail closed on corruption, stale generation or timeout. Compare
empty commands and the same luma workload against CPU, then add a larger workload
to locate a useful crossover. Stop expanding the driver if useful workloads do
not demonstrate a win. Near-native Liquid Glass additionally needs proven render
pipelines, textures/IOSurfaces, compositor synchronization and checkpoint reset;
the present result does not settle those dependencies.

Durable evidence is under
`/Users/jdolbe1/dvm-artifacts/research/gpu-mmio-metal-ios27-20260906/`.
Its manifest pins the sources, generated shim assembly/ledgers, runtime and
installer records, failures, measurements, tests and screenshots. It retains
only the experiment's owned 16 MiB shared-RAM files, which contain generated
workload bytes/records; full guest RAM, disks, Apple binaries, firmware and AIR
libraries are excluded. Temporary boot/installed parents stay under `/tmp/dvm`.
The final source validation includes 79 project tests and 19 driver tests;
QEMU was rebuilt and its exact frozen binary used for all Metal trials.

## Binary submission follow-up (2026-09-06)

[Binary submission evidence](gpu-binary-submission-ios27.md) records four fresh
post-display ABBA boots. The same exact guest AIR workload now takes median
3.55/3.73 ms with typed binary submission, versus JSON controls at 5.81/6.47 ms.
Both binary medians improve 39–42% against the mean control median; every run
verifies all eight workloads and retirement to zero. Native display/input
readiness passes. The tiny CPU reference remains faster (about 0.5 ms), and
first binary submissions still take 23–31 ms. Global rendering and GPU
checkpoint state remain unproven. The boot transport/QEMU artifacts are unchanged.
