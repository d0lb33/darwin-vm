# Boot-time Metal registration in the exact iOS compositor

2026-09-07. Exact iOS 27 build 24A5430a, iPhone17,3/T8140.

## Current result

**Proven:** the real backboardd process loads our arm64e Metal driver during
boot and the exact guest's ordinary `MTLCreateSystemDefaultDevice()` returns
that device after `MTLAddDevice`. This is per-process boot registration in
backboardd, not stock IOAcceleratorES enumeration or universal app discovery.
No injected CALayer, replacement Metal factory, debugger, or test-helper
consumer participates in this experiment.

**Not yet proven:** a compositor GPU submission, GPU-produced system UI
pixels, or preserved display/input during accelerated composition. Prior
CARenderer/UIKit helper results do not establish those outcomes.

## Evidence and failed contracts

| Trial | Observed result | Scope / response |
|---|---|---|
| CA_SYSTEM_BOOT_GUEST1 | No registration record before the boot deadline; read-only process inspection found no backboardd instance. | Console log redirection was changed to an ordinary mobile-owned log path. The specific launch error was not captured; do not claim a proven console errno. |
| CA_SYSTEM_BOOT_GUEST2 | Backboardd DYLD failure: `incompatible architecture (have 'arm64', need 'arm64e')`. | Rebuilt the plugin and its exact-cache link stubs for arm64e. Guest version and backboardd instruction sections unchanged. |
| CA_SYSTEM_BOOT_GUEST3 | `GPU_LOAD_SYSTEM_REGISTERED pid=73 registry=4294968234`; then `-[DVMQueue setSubmissionQueue:]: unrecognized selector`. | Exact constructor also calls `setCompletionQueue:`. Implemented retained caller queues, ordered submission, callback delivery, and bounded command buffers together. |
| CA_SYSTEM_BOOT_GUEST5 | Registration, exact QuartzCore library load and two private RGBA16Float mipmapped allocations succeeded; then `-[DVMCommand blitCommandEncoder]: unrecognized selector`. | Exact caller requests texture copies and optional mipmap generation. No GPU submission yet. |
| CA_SYSTEM_BOOT_GUEST4 | Registration again; queue-pair exception passed; `-[DVMQueue setGPUPriority:]: unrecognized selector`. No render submissions. | Exact constructor also requests background priority. The two optional setters now explicitly return NO; GPU priority tuning remains unsupported. |

First records: `/Users/jdolbe1/dvm-artifacts/research/gpu-system-boot-20260907-first`.
Registration and queue records: `/Users/jdolbe1/dvm-artifacts/research/gpu-system-boot-20260907-registration`.
Later trial paths under `/tmp/dvm` remain temporary until copied into the
final experiment evidence package.

The first GUEST1 diagnostic initially assumed a wrong DRAM address and read
zeros. Those reads are invalid evidence. Correct managed DRAM starts at
`0x10000000000` and spans `0x300000000` bytes; the managed RAM file supports
read-only postmortem inspection without another provisioning boot.

## Mechanism and scope

`system_bootstrap.m` is compiled together with the existing driver frontend
and MMIO/shared-RAM transport. It is added as an LC_LOAD_DYLIB dependency to
an isolated copy of backboardd, using verified empty header padding. The
builder verifies that all instruction sections retain their original hashes.
The executable keeps its original entitlements and adds only platform status,
the dedicated transport entitlement and its IOKit client exception. Both
binaries are signed and their hashes are added to the disposable boot trust
cache. Backboardd remains the mobile launchd service.

The constructor checks the process name, opens the existing transport,
negotiates the supported guest/host profile, obtains the real registry ID,
adds MTLDeviceSPI protocol identity and calls the guest's exported MTLAddDevice.
It then requires pointer identity from the ordinary default-device factory.
Protocol identity is not a claim of complete private-interface compatibility.
Unsupported selectors still raise an exception; a bounded uncaught-exception
record now reaches the host audit ring directly.

Static exact-cache evidence: MTLAddDevice `0x1a54ff634`; public default factory
`0x1a54bfc28`; submission/completion setters at QuartzCore `0x184588ce4` and
`0x184588cf0`. Priority/background setters at `0x184588da0` / `0x1845891c4`
have ignored return values. The exact IOGPU priority implementation returns
success as a boolean (`0x20b8e62e0` / `0x20b8e62e4`). Returning NO therefore
declines an optional policy; it does not fake successful scheduling changes.

QEMU, kernel, DT, SPTM/TXM, native SMC and migrated disk ancestry are pinned
from the existing validated manifest. This change does not modify them.
The original software image remains untouched, and original backboardd and
launchd-cache copies are retained inside each disposable installation.
Before registration, nil-device software selection remains available.
Transparent fallback after an accelerated compositor failure is unproven;
recover by discarding the experimental child and using the software parent.

## Reproduction

From this worktree, with Xcode-beta selected through DEVELOPER_DIR:

```sh
python3 tools/gpu/build_system_bootstrap.py \
  /tmp/dvm/CA_UIKIT_INVALIDATION_RED_BUILD1 \
  /Users/jdolbe1/dvm-artifacts/extract/bin/backboardd \
  /tmp/dvm/CA_SYSTEM_BOOT_BUILD5
python3 tools/gpu/prepare_system_bootstrap.py \
  /tmp/dvm/CA_SYSTEM_BOOT_BUILD5 \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-render-ready-ios27/launchd.plist \
  /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-handoff-ios27/tc \
  /tmp/dvm/CA_SYSTEM_BOOT_STAGE5
python3 tools/gpu/run_guest_install.py \
  --manifest /Users/jdolbe1/dvm-artifacts/gpu-quartzcore-regions-ios27/control.json \
  --stage /tmp/dvm/CA_SYSTEM_BOOT_STAGE5 --tag CA_SYSTEM_BOOT_INSTALL5 --mmio-restore
python3 tools/gpu/run_system_boot.py \
  /tmp/dvm/CA_SYSTEM_BOOT_INSTALL5/warm-manifest.json \
  --worker /tmp/dvm/CA_UIKIT_INVALIDATION_RED_BUILD1/driver_host \
  --library /Users/jdolbe1/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib \
  --tag CA_SYSTEM_BOOT_GUEST5
```

Each tag/output must be new. The installer verifies preimages before changing
its own child and checks backing-chain hashes afterward. The boot harness
stops on the first captured failed compositor contract or its boot deadline.
Its positive stop condition records rendering and native presentation as
separate observations, explicitly leaving pixel verification false.
This is an automated boot experiment; it does not impose a lifetime on the
separate persistent interactive GPU session.

## Validation

Focused host tests passed for queue FIFO, configured submission and completion
targets, retained callback order, per-queue bounds, unsupported priority
rejection, resource ownership/error handling, writeback-before-completion,
capability negotiation, and the existing two-pass public Metal workload.
The scheduling unit is a host CPU test, not guest GPU evidence.

The next acceptance is actual compositor rendering and verified output,
followed independently by native presentation/input and sustained pacing.
Generic compositor IOSurface import and synchronization remain major
unresolved dependencies; existing owned-pool tests do not cover arbitrary
client or display IOSurfaces.
