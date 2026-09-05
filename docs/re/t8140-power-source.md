# T8140 native power source and status battery

Scope: iPhone17,3 / T8140, build 24A5430a. This note distinguishes the
public process-scoped IOPS publisher from the physical PMU battery-driver
path. It does not assign undocumented device-tree keys or MMIO semantics.

## Result

`IOPSCreatePowerSource` with a description whose `Type` is
`InternalBattery` is an input to the native BatteryCenter internal-battery
path. It is not reclassified as a UPS. A process-scoped source is therefore
a supported virtual-platform representation for a stable guest battery; it
does not emulate an `AppleSmartBattery` or PMU service.

The staged native publisher was observed in the 24A runtime before the first
Lock Screen frame:

```
POWER_RTC_PROBE_IOPS count=1
"Current Capacity" = 100;  "Max Capacity" = 100;
"Is Charged" = 1;          "Is Charging" = 0;
"Is Present" = 1;
Name = "Darwin VM";
"Power Source State" = "AC Power";
"Transport Type" = Internal;
Type = InternalBattery;
POWER_PV_VERIFY ... expected_id=2691976 count=1 matches=1
```

Source: `/tmp/dvm/checkpoints/WARM_DEV_EARLY_DIAG1/restores/DEV_ACTIVATION_DIAG1/serial.log:33-91`.
The matching check includes the powerd-assigned `Power Source ID`; it is not a
name-only false positive.

## powerd classification

The open PowerManagement source provides the source-level contract. In
`pmconfigd/BatteryTimeRemaining.m:4979-5006`, a supplied `Type` exactly equal
to `InternalBattery` changes the new process source to `kPSTypeIntBattery` and
retains its description. `IOPSCopyPowerSourcesByTypePrecise` uses the same
internal class (`:5117-5159`); it does not require the special physical-source
slot.

The physical source is separately reserved as `psid == 99` in list slot zero
(`:157`, `:649-663`, `:4686-4708`). A user publisher receives IDs from 5000
onward (`:4838-4907`). Thus a process ID is not evidence of a UPS or of a
missing `AppleSmartBattery`; it is the documented separation between physical
and user-published sources.

The physical publisher's usual description uses the same relevant values:
`Type = InternalBattery`, `Transport Type = Internal`, current/max capacity,
presence, charge state, and AC/Battery state
(`BatteryTimeRemaining.m:4435-4569`). It additionally supplies hardware
telemetry and timing values when available. Those telemetry values are not
required by the public user-source acceptance path.

## BatteryCenter conversion

24A5430a's `BatteryCenter` is at cache static base `0x2554d0000`.
`_BCPowerSourceController _orderedDevicesFromPowerSourcesBlob:powerSourcesList:`
starts at `0x2554d1620`.

It reads and compares the following source keys while constructing a
`BCBatteryDevice`:

| Key | static read / outcome |
|---|---|
| `Type` | read at `0x2554d1b24`; compared with `InternalBattery` at `0x2554d1b3c-0x2554d1b60` |
| `Current Capacity`, `Max Capacity` | read at `0x2554d1d20-0x2554d1d44`; converted by `_displayChargePercentForCurrentCapacity:andMaxCapacity:updateZeroValue:` at `0x2554d357c` |
| `Is Charging`, `Show Charging UI` | read at `0x2554d1d70-0x2554d1dbc` |
| `Low Warn Level` | read at `0x2554d1df8-0x2554d1e74` |
| `Power Source State`, `Transport Type`, `Name` | read at `0x2554d1af8-0x2554d1c40` |

The `Type` comparison result feeds `-[BCBatteryDevice setInternal:]` through
its `objc_msgSend` stub at `0x25807d200`; that stub loads selector string
`setInternal:` at `0x1f5ba3a88`. It feeds `setPowerSource:` at
`0x2554d1bd4-0x2554d1bdc` (selector `0x1f7f89dc7`), then writes the converted
percentage through `setPercentCharge:` at `0x2554d1de8-0x2554d1df4` (selector
`0x1f5e1a5e4`).

`_displayChargePercent...` at `0x2554d357c` divides current by maximum,
clamps the result to `[0,100]`, and rounds it. With 100 and 100 it returns
100; it has no PMU, provider, name, or source-ID condition.

The only initial filter,
`_shouldConsiderDeviceWithPowerSourceDescription:` at `0x2554d2f80`, rejects
a source when its `Max Capacity` value has the disqualifying type/value or its
`Power Source State` is `Off Line`. The staged source has `Max Capacity = 100`
and `AC Power`, so it does not meet either rejection branch
(`0x2554d2f9c-0x2554d2ff4`).

## SpringBoard selection

SpringBoard's
`-[BCBatteryDeviceController(SpringBoard) sb_deviceInternalBattery]` begins
at static `0x2243f54c0`. It enumerates BatteryCenter's `connectedDevices` and
returns the first device for which the `isInternal` predicate is true
(`0x2243f5544-0x2243f5584`; predicate call `0x228072d40`). The status provider
receives the same device sequence in
`-[SBSystemStatusBatteryDataProvider connectedDevicesDidChange:]` at
`0x2243f53c8` (`0x2243f544c-0x2243f5460`).

Therefore the observed process source is on the status battery's intended
selection path. It is not a UPS-only source and it does not need an
`AppleSmartBattery` IORegistry service merely to become an internal
`BCBatteryDevice`.

## What the first rendered frame does and does not prove

The available frame is from a 20-second visual observation after first-frame
presentation, while the source was published earlier in helper startup. A
blank/low-looking icon alone does not establish that BatteryCenter received a
0 percent source: the source snapshot is a powerd API result, not the
BatteryCenter object that SpringBoard consumes.

No additional publisher dictionary key is justified by static evidence:
`Show Charging UI`, `Low Warn Level`, `gas-gauge-battery`, and hardware
telemetry are parsed for presentation refinements, but the code above proves
that the existing type and capacities already set `internal` and
`percentCharge`. Adding any of those would claim a battery policy or hardware
state that the guest does not model.

The smallest decisive follow-up is a bounded in-guest post-publication query,
not a new hardware leaf or a key change:

1. Load `BatteryCenter.framework`.
2. Call `+[BCBatteryDeviceController _sharedPowerSourceController]` and its
   `connectedDevices` property (both metadata are present in 24A's
   `BatteryCenter`).
3. For each `BCBatteryDevice`, log `identifier`, `isInternal`,
   `isPowerSource`, `percentCharge`, `isCharging`, `isConnected`, and
   `isLowBattery`.
4. Correlate the internal device with the exact native `Power Source ID` from
   `IOPSCopyPowerSourcesByTypePrecise` in the same process and timestamp.

Expected if publication propagated: one internal, power-source device with
`percentCharge == 100`. If the object instead reports zero or no internal
device, capture that result before changing the publisher. If it reports 100,
the remaining issue is downstream status-item/renderer state, outside power
publication.

## Frozen SpringBoard status-path inspection

A paused restore of `WARM_DEV_NATIVE_HOME1` on 24A5430a established the state
of the actual SpringBoard status provider without guest execution or writes.
The saved process record at physical `0x10025c62390` identifies `SpringBoard`;
its pmap root is `0x1001ca1c400`. The cache slide is `0x1b78c000`.

`+[UIApplication sharedApplication]` in UIKitCore is static `0x184972d9c`; it
loads the application pointer from static `0x1e6fb4098`. In the frozen task,
that slot at `0x202740098` contained `0x105839450`. SpringBoard's
`-statusBarStateAggregator` at static `0x22439c898` loads its ivar offset from
static `0x26cf355d8`; the live signed offset was `0x620`, giving aggregator
`0x79430d4000`.

`-[SBStatusBarStateAggregator batteryDataProvider]` is static `0x224bdb888`
and reads offset `0x2520`. The live provider was `0x79432884e0`. Its known
accessors prove the following object state:

| Provider field | static accessor / offset | frozen value |
|---|---|---|
| `lastPublishedData` | `0x22468f7f4`, `+0x10` | `NULL` |
| `batteryDataPublisher` | `0x22468f7fc`, `+0x18` | `0x794335bb60` |
| `batteryDeviceController` | `0x22468f804`, `+0x20` | `0x7942ea6d50` |

This is stronger than an icon interpretation: the status provider had no
published `STBatteryStatusDomainData` at the capture point. Its `_updateData`
method starts at static `0x22468f430`, obtains the internal device from that
controller (`0x22468f454-0x22468f45c`), returns if the result is nil
(`0x22468f460`), and only after constructing a data object stores it to
`lastPublishedData` (`0x22468f578-0x22468f588`).

The BatteryCenter singleton itself is initialized: the once sentinel at
static `0x26f311190` plus slide is `-1`, and the object slot at static
`0x26f311198` plus slide points to `0x7943380400`, an
`_BCPowerSourceController` (its ISA decodes to the static class
`0x26fd7a470` plus slide). That controller retains notification tokens,
queue, and observer table; it does not retain a `connectedDevices` array.
`connectedDevices` is rebuilt from the IOPS list by static
`0x2554d2960`. A frozen field read therefore cannot honestly report a device
percent without executing a targeted query.

The next bounded probe should call or breakpoint the existing SpringBoard
`sb_deviceInternalBattery`/`_updateData` path after the publisher is known
ready, logging the returned device's `isInternal`, `isLowBattery`,
`isCharging`, `isConnected`, and `percentCharge`. The observed null status
publication does not justify adding unverified source keys or a hardware
provider.

## Notification gap for a process-published internal source

The status-path omission has a concrete powerd cause that is independent of
BatteryCenter's dictionary conversion.  In the PowerManagement source used for
24A analysis, `_io_ps_update_pspowersource` accepts the first description for a
user source whose `Type` is `InternalBattery`, sets `next->psType` to
`kPSTypeIntBattery` (`BatteryTimeRemaining.m:4979-5006`), and posts only
`kIOPSNotifyAttach` on that first description (`:4997-5004`).  It then
asynchronously calls `HandlePublishAllPowerSources`.

On this VM there is neither a physical `IOPMBattery` nor a UPS.  In that
condition `HandlePublishAllPowerSources` returns at
`BatteryTimeRemaining.m:1636-1645` before the percent, time-remaining, or
any-power-source publication code at `:1720-1771`.  A subsequent
`IOPSSetPowerSourceDetails` replaces the stored description and calls the same
function (`:4992-5005`); it does not post a second attach notification and the
same no-battery/no-UPS return applies.  Repeating an identical Set is therefore
not a supported notification workaround.

BatteryCenter's `_beginPowerSourceObservingIfNecessary` is static
`0x2554d3fd8`.  Its notification registrations are, in order,
`com.apple.system.powersources.percent` (`0x2554d4070-4080`),
`com.apple.system.powersources.timeremaining` (`0x2554d4088-4098`), and the
accessory source/time/attach plus charging-iconography names
(`0x2554d40a0-40f8`).  It does **not** register the generic
`kIOPSNotifyAttach` notification used for the first user internal source.

This explains a specific race without inventing a battery property: if
BatteryCenter first creates its observer while the IOPS list is empty, its
initial callback queries an empty list.  A subsequently published virtual
internal source is accepted by powerd, but in a no-physical-battery VM the
source's only first-publication notification is not one BatteryCenter
observes.  SpringBoard's status provider then retains a null
`lastPublishedData` even though a separate helper's IOPS snapshot verified the
source.

A fresh `POWER_BC_DIAG2` restore from `WARM_DEV_NATIVE_HOME1` independently
confirmed the relevant live state before the trace: the shared
`_BCPowerSourceController` had initialized (`once` sentinel `-1`), valid
notification tokens `0xf8,0xfa,0xfc,0xfe,0x100,0x103`, a queue, and a
non-null observer map; SpringBoard's status provider still had
`lastPublishedData == NULL`.  A 52-second trace armed static
`SBSystemStatusBatteryDataProvider _updateData` (`0x22468f430`) and
`BCBatteryDeviceController(SpringBoard) sb_deviceInternalBattery`
(`0x2243f54c0`) at slide `0x1b78c000`.  It recorded zero calls before its
independent HMP watchdog quit the disposable VM.  Evidence:
`/tmp/dvm/POWER_BC_DIAG2/bc-frozen.json`,
`/tmp/dvm/POWER_BC_DIAG2/bc-trace.jsonl`, and
`/tmp/dvm/POWER_BC_DIAG2/bc-watchdog.log`.

The bounded trace is evidence that no later event repaired the status state;
it does not establish the earlier publication order.

## Publisher lifecycle is a separate requirement

The public publisher currently calls `pause()` immediately after its one
successful Create/Set/verify transaction.  It has no connection-death handler,
run-loop source, or later Set transaction.  User source descriptions are held
in powerd's in-memory `gPSList` (`BatteryTimeRemaining.m:157`, `4838-5007`),
and the precise-query server constructs its reply from that list
(`:5106-5159`).  There is no durable handoff from that list to the suspended
publisher.

The current `INPUT_V6_TAP1` capture contains independently reaped crash
records for `powerd` PID 51 and its replacement PID 169, each with SIGSEGV and
FAR/subcode `0x20`; it later has a live PID 340.  This establishes powerd
restart during the lifetime of a one-shot publisher.  It does not attribute
either fault to the publisher.  It does mean `POWER_PV_READY` before a restart
is not proof that the current powerd owns the source: the helper will remain
asleep while the new server begins with a new in-memory list.

`IOPSNotificationCreateRunLoopSource` is not a broker-death callback.  Its
documented contract is source added/removed/changed notifications, scheduled
on a CFRunLoop; `pmset.m:3516-3538` shows the normal retain/add/release and
run-loop ownership.  The suspended helper has no run loop, so it cannot
receive this normal IOPS callback.  More importantly, that API does not claim
to signal a powerd restart.

Apple's own PowerManagement clients use a separate resync mechanism for that
case.  SmartPowerNap sets an XPC interruption handler, then registers
`kIOUserAssertionReSync` with `notify_register_dispatch`; its comment names
the purpose “re-establish connection on powerd's restart.”  When both the
interruption condition and resync notification occur, it calls `reRegister`
(`LowPowerMode/SmartPowerNap/_PMSmartPowerNap.m:81-115`; the Core variant is
identical at `:83-117`).  This is evidence for a dispatched reconnect path,
not evidence that an IOPS RunLoop source automatically reconnects a publisher.

The smallest evidence-based publisher candidate is therefore a serial dispatch
queue, not `pause()`:

1. Construct the already verified immutable details dictionary once.
2. `IOPSCreatePowerSource`, `IOPSSetPowerSourceDetails`, then verify the
   caller's newly assigned source ID in a successful snapshot.
3. After a root-helper probe resolves the actual notification name and confirms
   registration permission, register the native powerd-resync notification on
   that queue.  Record only successful registration; a failure leaves the
   helper alive and unready.
4. On resync, serialize a best-effort Release of the old opaque ID, discard
   it irrespective of the Release result, then Create, Set, and perform the
   same exact-ID verification again.  Exponential retry applies only to an
   unsuccessful Create/Set/verify sequence.  Do not reuse a pre-restart ID or
   claim readiness before the new verification succeeds.
5. On SIGTERM/SIGINT, cancel the notification token and Release only the
   currently verified ID.

The PowerManagement source proves Create gives a per-process ID from 5000 and
Release cancels the matching source's process-death dispatch source
(`BatteryTimeRemaining.m:4838-4908`, `5033-5036`).  It does not prove that an
old client opaque ID is valid after a server restart.  Releasing and creating
a new one is consequently the smallest lifecycle-safe sequence; the actual
resync name/permission and the Create-after-restart behavior require one
root-helper probe before staging it persistently.

## Bounded notification and liveness probes

`notify_post` is declared as a public C API in `notify.h`; the two relevant
guest strings are the mapped 24A literals
`com.apple.system.powersources.percent` and
`com.apple.system.powersources.timeremaining`.  The former is the name
BatteryCenter registers at `0x2554d406c-0x2554d4080`.

In disposable `POWER_NOTIFY_DIAG1`, a native call to the guest
`libsystem_notify` `notify_post` export at static `0x2c21686b8` used that
exact literal and returned zero.  It ran on a SpringBoard UID 501 observer
thread.  For roughly 70 seconds afterwards, breakpoints at BatteryCenter
`_queryConnectedDevices` (`0x2554d42ec`), SpringBoard
`connectedDevicesDidChange:` (`0x2243f53c8`), status `_updateData`
(`0x22468f430`), and the internal-device return site recorded no call.  The
captured Home screen still showed the red/low glyph.  This only rejects the
specific UID 501 post as a refresh mechanism in that run.  It does not prove
whether notifyd gives a root, entitled publisher different delivery rights;
the helper must not gain a persistent post mode from this result.

`POWER_IOPS_LIVE2` separately called the exact iOS ABI
`IOPSCopyPowerSourcesByTypePrecise(kIOPSSourceInternal, &out)` at static
`0x18f004698`.  Native disassembly proves only `w0` (type) and `x1` (nonnull
out pointer) are caller inputs; the function clears `x2` internally before it
constructs its XPC request (`0x18f0046b0-0x18f004730`).  Its real return was
`0xe00002bc`, `kIOReturnError`, so the out pointer was intentionally not
interpreted as a source list.

One possible error path precedes the XPC request: `getPMQueue` at
`0x18eff2d8c` can return null (`0x18f0046b8-0x18f0046cc`). Its complete
relevant state is per-process IOKit data: predicate at `0x1e6f50f40`, queue
slot at `0x1e6f50f48`.  It first returns a non-null cached queue; otherwise it
runs `dispatch_once(predicate, block)` through the cold entry at
`0x18f016c08`.  The block at `0x18f016c1c` calls
`dispatch_queue_create("PM Notifications", NULL)` (the static label is at
`0x18f092fd9`) and
writes its result to the queue slot.  If the predicate has completed while the
slot is still null, `getPMQueue` returns null without an XPC attempt
(`0x18eff2d9c-0x18eff2de0`).

The injection recorded only the final generic error, not the predicate/slot
or the executed error branch. It does not localize the failure until every
path returning that code has been audited. It also cannot prove
that BatteryCenter made this same call in that capture; its normal caller path
must be traced separately.  This is not evidence of broker health, source
absence, or a count of zero.  The call and all touched registers/output bytes
were restored before the disposable clone was stopped.

The next valid experiment is a root publisher-context post only after a
successful current exact-ID query and a verified BatteryCenter observer.  It
must record both the native notify return and the resulting BatteryCenter
query/device update.  Until that succeeds, preserve the verified source
dictionary and solve powerd restart/republication first.

Evidence artifacts:

- `/tmp/dvm/POWER_NOTIFY_DIAG1/notify-events.jsonl`
  (`729f023d1e0fe65cfc4eee6af6a6d50729b1076012684c38a0e7e48648a00e2f`)
- `/tmp/dvm/POWER_NOTIFY_DIAG1/after-percent-stopped.png`
  (`396da578c584ab062b078f659a6c6cd1b08a79adf5cc2f57e748940e1bceb884`)
- `/tmp/dvm/POWER_IOPS_LIVE2/events.jsonl`
  (`ee26e1c83eec00ec94516bcc069366ffd1f79e20b15496135102be7eac9366ef`)

## Publisher lifecycle candidate

The implementation keeps every transition on one serial dispatch queue.  A
resync received before initial Create/Set/verify is marked pending and runs
only after that transaction completes, so a callback cannot create a second
local ID during startup.  On failed startup it serializes `stopping`, drains
queued notify work, and only then releases the details dictionary.  SIGTERM
and SIGINT are dispatch signal sources on that same queue followed by
`dispatch_main`, eliminating the `while (!flag) pause()` lost-signal window;
the shutdown record carries the actual Release result and exits nonzero on a
Release failure.


Root additionally stores the API table in static storage because `dispatch_main`
can retire the main thread while callbacks continue. The combined candidate
is `/tmp/dvm/POWER_PV_RECOVERY_BUILD6/power-pv-service`, SHA-256
`c3a6535eb970deeb48f71d0ad185b5f69536146a6bebe9d7a736f986b2825bcb`,
CDHash `cc380f764bbee3b799adb648bc5751d117879233`. It is staged for a
fresh-boot validation; source recovery and battery UI remain unverified.

## Root percent-state bridge and native UI proof

The later `POWER_PERCENT_ROOT4` snapshot diagnostic identifies why a valid
virtual source dictionary could coexist with a zero battery percentage.
BatteryUsageUI's `+[PLBatteryUIBackendModel _getCurrentStateOfCharge]`
(loose image offset 0xc548, getter call 0xc570) calls
`IOPSGetPercentRemaining` (cache static 0x18f0092e0). That API consumes
`com.apple.system.powersources.percent`, not the source dictionary directly.
Its native valid-bit test is at 0x18f009374.

Apple's IOKitUser `ps.subproj/IOPowerSourcesPrivate.h` defines low-byte
percentage, external power bit 16, charging bit 17, validity bit 19, and
fully charged bit 21. The VM state is therefore **0x290064**. The native
PowerManagement `BatteryTimeRemaining.c`/`.m` implementation's
`HandlePublishAllPowerSources` returns without a physical IOPMBattery or
UPS; the userspace InternalBattery dictionary alone does not publish this
aggregate state. Local reference:
`/tmp/dvm/POWER_SOURCE1/PowerManagement-src/pmconfigd/BatteryTimeRemaining.m`.

Readonly native getter: 0xe00002d8 (invalid state), with fallback outputs
100/false/true. Publishing from SpringBoard returned zero but did not make
the state valid. Publishing from root powerd through native notify APIs
returned zero and changed the getter to success/100/false/true. Reopening
Settings Battery then showed **100%** (`POWER_PERCENT_ROOT4/battery-ui2/final.png`).
`tools/re/power_percent_callbacks.py` records the native calls and restores
the borrowed stack and all registers. This was a diagnostic restore.

BUILD8 in `tools/re/power_pv_service.c` owns this bridge in the root service,
after requiring exactly one source matching its own ID and dictionary. It
verifies both notify state and native percent getter before READY, and
withdraws only a state it owns and that still matches its value on resync
or release. SHA-256 `cbbfea6055b4b5b451e6bf127e5458aa4e38c49d00c814221e6ab48d79fc6f20`,
CDHash `0f0d0f104f046447d5900660d97c31d2ee52c884`.
Fresh `WARM_CLOCK_SOFTWARE1/serial.log:817..850` verifies startup and resync
readbacks, and the boot renders a full green battery. Reopening Battery on
this exact fresh boot is pending input initialization; do not substitute
the earlier diagnostic screenshot for that validation.
