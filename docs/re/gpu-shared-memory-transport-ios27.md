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
