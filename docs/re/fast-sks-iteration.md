# Fast SKS iteration at the late keybagd boundary

## Problem

The first userspace `keybagd` request that exposes a new SKS shape can arrive
roughly 40--45 wall minutes into a TCG boot.  Repeating that whole interval for
a parser typo is unnecessary.  This plan splits fast, host-only shape checks
from the one real guest run still required to prove behavior.

## Ordered strategy

### 1. Replay the exact request outside the guest

`hw/arm/darwin_sks.c` contains the pure migration-shape parser used by
`darwin_sep.c`.  The unit test feeds it the exact 164-byte Data-volume request
from `ROOT_SKS_OP0F_DATA_POS1.stderr.log:4117924-4117936`.  It also proves that
the old record-kind-3 bug, a wrong class/volume pairing, corrupted fixed
fields, and truncation are rejected.  The capture is pinned independently as
SHA-256 `405926d6f771cb768e3ceddcb0f147489a815f0c6d55ff3dac016086d0df8f0c`.

```sh
ninja -C qemu-sptm/build tests/unit/test-darwin-sks-migrate
qemu-sptm/build/tests/unit/test-darwin-sks-migrate --tap -k
```

This takes seconds after the initial Meson regeneration.  It proves parser
behavior only; it does not prove that the guest sends the request, consumes a
reply, or continues booting.

### 2. Launch QEMU under host LLDB (implemented, host authorization blocked)

macOS denied LLDB attaching to an already-running QEMU.  The alternative is
implemented: `tools/re/qemu_under_lldb.sh` starts QEMU as LLDB's child and
installs `host_sks_request_callbacks.py` before `run`.  The default callback is
read-only: it logs the exact request SHA-256 and decoded fields at
`sep_sks_validate_migrate_request()`.

This host did not authorize even launch-under-LLDB.  The clean smoke test
stopped at `process launch` in
`/tmp/dvm/HOST_LLDB_PROBE_SMOKE3.host.log:17`; `DevToolsSecurity -status`
reports `Developer mode is currently disabled.`  The wrapper and probe driver
now notice that startup failure and reap LLDB/debugserver/QEMU descendants, so
it no longer leaks a stopped guest.  When an interactive administrator is
present, enable developer-tool authorization and repeat the smoke test before
using any override:

```sh
sudo DevToolsSecurity -enable
```

An OS developer-tools/taskgated prompt may still need interactive approval.
Do not interpret an authorization failure as a guest result.

The diagnostic override requires an exact SHA-256, an explicit response
class, and the one-shot guard:

```sh
DVM_QEMU_WRAPPER=tools/re/qemu_under_lldb.sh \
DVM_HOST_LLDB_LOG=/tmp/dvm/TAG.host-lldb.log \
DVM_HOST_SKS_OVERRIDE_SHA256=<exact-request-sha256> \
DVM_HOST_SKS_OVERRIDE_CLASS=3 \
DVM_HOST_SKS_OVERRIDE_ONCE=1 \
  tools/re/setup_gate_probe.sh TAG
```

An override result is diagnostic-only.  It can show that a corrected result
unblocks downstream guest work during that same boot, but the final evidence
must come from a new boot with no override variables.

Because the IPC digest, time, context, and opaque record prefix can vary per
boot, an unknown request can instead pause on a strict size/class/kind match:

```sh
DVM_QEMU_WRAPPER=tools/re/qemu_under_lldb.sh \
DVM_HOST_LLDB_LOG=/tmp/dvm/TAG.host-lldb.log \
DVM_HOST_SKS_APPROVAL_FILE=/tmp/dvm/TAG.sks.approve \
DVM_HOST_SKS_MATCH_SIZE=164 \
DVM_HOST_SKS_MATCH_CLASS=3 \
DVM_HOST_SKS_MATCH_KIND=4 \
DVM_HOST_SKS_OVERRIDE_CLASS=3 \
DVM_HOST_SKS_OVERRIDE_ONCE=1 \
  tools/re/setup_gate_probe.sh TAG
```

At the match, QEMU is stopped inside the host callback and the callback writes
`TAG.sks.approve.challenge.json` plus the exact `TAG.sks.approve.request.bin`.
Review that capture, then put the challenge's exact SHA-256 in
`TAG.sks.approve`.  A stale or different approval cannot release the call; a
five-minute default timeout falls through to the unmodified validator.

### 3. Give a disposable normal boot a UART shell

The normal System-volume boot has no interactive console, so it cannot name
PID 56 (`keybagd`) or test its launch trigger.  The `debug-shell` bootstrap
phase creates a new qcow child, boots the restore ramdisk, mounts the System
volume inside iOS, and installs `tools/re/debug-shell.plist`.  It never mounts
the large APFS image on macOS and never modifies the parent.

```sh
PARENT=/tmp/dvm/data-seed/known-parent.qcow2 \
OUT_OVL=/tmp/dvm/data-seed/debug-shell.qcow2 \
TAG=DVM_DEBUG_SHELL_INSTALL1 \
  tools/rootfs/bootstrap_data_volume.sh debug-shell
```

Boot that child with `launchd_unsecure_cache=1`, then use the console to record
the exact `keybagd` launchd label and arguments.  In separate disposable
children, test increasingly invasive triggers:

1. inspect `ps` and `launchctl print` only;
2. invoke the same read-only/status action that precedes the late request, if
   a public command exists;
3. kickstart the exact service once;
4. restart `keybagd` only if the preceding evidence shows that launch is the
   relevant trigger.

The first trigger that reproduces the identical request SHA-256 and request
fields becomes the fast real-guest reproducer.  A debug-shell boot cannot be a
final normal-boot witness.

### 4. Add a narrow in-process replay command only if LLDB is insufficient

A QMP/HMP replay hook is justified only if a rejected request cannot be
continued safely from host LLDB.  It must retain one pending SKS request,
require its exact SHA-256 and opcode, invoke the normal parser and response
builder, deliver the reply through the normal SEP OOL/mailbox path, and erase
the retained copy after one use.  It must be debug-disabled by default.

This is preferable to a generic "force SKS success" command: the latter would
hide parser, response-digest, mailbox, and completion bugs.

### 5. Defer whole-VM checkpoints

QEMU can save generic CPU/RAM and block state, but the custom Darwin ANS, SEP,
DCP, IOMFB, ASC, AFK, and EPIC models currently declare no
`VMStateDescription`.  Their mailbox FIFOs, pending queues, DART/ring pointers,
timers, and in-flight storage work would therefore not be restored as a
coherent boundary.  A checkpoint that merely resumes without an immediate
panic is not a positive control.

Checkpoint work starts only after every participating device has an explicit
migration-state inventory and a save/load test proves an in-flight SEP round
trip and NVMe I/O both survive.  That is a larger project than the replay and
LLDB paths and is not the current inner-loop solution.

## What the 45-minute run actually spent time doing

The long positive run was not sitting in a single AppleKeyStore selector.
`ROOT_SKS_OP0F_DATA_POS1` produced 4,118,174 stderr lines (293,096,952 bytes),
including 363,874 successful wire-op `0x19` device-state requests.  Of those,
362,450 carried state `-501` and 1,424 carried state `-6`.  The request rate
rose from 83 in the first minute to roughly 14,000 per minute late in the run;
the reproducible minute buckets are in
`/tmp/dvm/ROOT_SKS_OP0F_DATA_POS1.op19-per-minute.txt`.  The first op `0x0f`
migration request arrived at host timestamp 2486.934 seconds, about 41.4
minutes into that boot.

That run also set `DARWIN_SKS_REQUEST_DEBUG=1`, so QEMU printed a request
hexdump and routine success messages for every query.  Logging was therefore a
substantial part of the host workload even though each guest request
completed.  Successful op19 traffic is now sampled: the first eight requests,
powers of two, and every 10,000th request remain visible.  Errors, malformed
requests, and every other opcode remain fully logged.  To capture one opcode
without restoring the global flood, use for example:

```sh
DARWIN_SKS_REQUEST_DEBUG_CODE=0x0f tools/re/setup_gate_probe.sh TAG
```

`DARWIN_SKS_REQUEST_DEBUG=1` retains the old full-capture behavior when a
short, deliberately noisy run is required.  A short validation with the new
default emitted 478 stderr lines (45,978 bytes) and still reached Early Boot
Complete with no panic (`ROOT_SKS_OP19_FILTERED1`).  A future long control is
still required to measure the exact wall-time improvement; the short run only
proves the sampling behavior and guest progress.

### The selector-7 “poller” hypothesis is false

The bounded three-result probe at public selector 7 found three different
clients: `logd`, `backboardd`, and `bluetoothd`.  Each call carried scalar
input zero and returned success with the established DER record
`31 07 0c 02 62 68 02 01 fa` (`bh=-6`) in 2--3 ms
(`/tmp/dvm/ROOT_SKS_SELECTOR7_CALLERS1.lldb.log:19-24`).  Selector 7 is a
shared state API, not one identifiable daemon spinning on a failed request.

A second breakpoint at the shared wire-op19 wrapper filtered specifically for
state `-501`.  The first three calls were all `backboardd`: public selector 17,
then 35, then 17, on one thread and context
(`/tmp/dvm/ROOT_SKS_OP19_FILTERED1.lldb.log:16-20`).  They occur during the
first normal userspace boot window, not at minute 41.

The return-following control then broke on the userspace
`aks_get_device_state` and `aks_get_extended_device_state` wrappers.  It
dynamically planted each live return breakpoint, read exactly the 0x42 bytes
AppleKeyStore copies to its caller, and stopped after three completed calls.
All three returned status zero in 3--12 ms with an all-zero decoded state
(`/tmp/dvm/ROOT_AKS_BKD_STATE2.lldb.log:16-27`); the guest reached Early Boot
Complete and had no XNU panic.  Symbolizing those backtraces attributes the
first normal state read to `_MKBGetDeviceLockState` in the
BiometricKit/Pearl HID-filter construction path.  The extended call, and the
following normal call, are in Biome stream/datastore publisher setup through
`_PASDeviceState isDeviceUnlocked`.

This proves that the current reply is accepted by those early clients.  It
does **not** yet name every late caller among the 362,450 requests.  The best
supported interpretation is a broad protected-data/filesystem workload, with
the full debug transcript amplifying its cost—not a single 45-minute wait.

### Do not synthesize `systembag.kb` or echo `-501`

The new runtime evidence reinforces the existing boundaries rather than
changing them.  Pre-creating `systembag.kb` does not skip `aks_get_system`, and
a useful file would require SEP-wrapped `KeybagxART` material
(`keybag.md`, “Can we pre-create a systembag.kb?”).  Likewise, the op19 codec
accepts `bh=-6`; copying the request's `-501` into the response was already
ruled out in `sks-userspace-selectors.md`.  The early `-501` callers now also
return success with the existing record.  Neither workaround is justified.

### DCP power traffic is assertion churn, not 449 full power transitions

The same long serial log contains only three completed `set_power_state`
events: power 0, power 1, and final power 0
(`/tmp/dvm/probe/ROOT_SKS_OP0F_DATA_POS1.serial.log:763-768,1219`).  It also
contains 225 `set_device_power on=0` and 224 `on=1` calls.  Those calls track
resource assertions and alternate with `kernelAssertCount`; they are not 449
full coprocessor boots.  Forcing DCP permanently asserted might hide a missing
release/acquire semantic, so first trace the assertion owner and correlate one
bounded run with the display state machine.  Only then compare an opt-in
keep-awake variant against the unmodified positive control.

## Inferno/qemu-t8030 comparison

The local Inferno tree is the emulator/device-model half of its restore flow;
the public workflow drives the restore externally with `idevicerestore` over
Inferno's emulated USB connection.  Public examples provision separate raw
NVMe namespace files for root, firmware, syscfg, control bits, NVRAM,
effaceable storage, and panic logs, then let Apple's restore ramdisk/ASR fill
them ([Inferno discussion #35](https://github.com/ChefKissInc/Inferno/discussions/35),
[discussion #256](https://github.com/ChefKissInc/Inferno/discussions/256)).
That is useful as a long-term architectural reference, but it is not a
seconds-long replay mechanism: it targets t8030/iOS 14-era hardware and relies
on SEP ROM/firmware plus storage namespaces this t8140 model does not expose.

Inferno's most useful immediate contribution remains older-generation SEP/SKS
protocol behavior, already recorded in `inferno-t8030-reuse.md`.  The local
qemu-t8030 `setup-ios` directory contains a launchd cache and a console-shell
plist; that console-shell pattern informed the isolated debug child above.  It
does not supply a t8140/iOS 27 keybag-migration trigger.

Inferno's project-specific device code is AGPL-3.0.  Protocol facts may inform
an independently derived implementation, but its code must not be copied into
this GPL-2.0-or-later device model.

## Evidence boundary

Before accepting a fix:

1. the capture-replay test must accept the exact positive request and reject
   its targeted negative mutations;
2. one normal boot with no host override must log the accepted request and the
   corresponding response;
3. the guest must log the selector return/next consumer action, with no SKS
   timeout or first XNU/SPTM panic;
4. the final display result still requires the independent SpringBoard,
   DCP/IOMFB, and non-null frame witnesses defined by the root goal.
