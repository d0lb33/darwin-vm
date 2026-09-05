# Warm disk boot and application stability — 2026-09-05

## Latest follow-up: percentage, clock, and chronod

The older narrative below is chronological evidence, not the current verdict.
Settings' zero-scale bitmap trap and powerd's null internal-battery merge
now have persistent guarded fixes (see their dedicated notes). Native Settings
and its Battery page work on the `POWERD_GUARD_BATTERY1` lineage.

`POWER_PERCENT_ROOT4`, a diagnostic RAM/device restore, proves that publishing
the root-owned `com.apple.system.powersources.percent` state `0x290064`
changes native `IOPSGetPercentRemaining` from invalid state (`0xe00002d8`)
to success, 100%, not charging, fully charged. The Settings Battery page
then displays **100%** (`battery-ui2/final.png`). A publication from
SpringBoard returned success but left the getter invalid; readback matters.

Publisher BUILD8 performs that root publication after verifying exactly one
matching virtual source, then checks both the notification state and the
native getter. SHA-256 `cbbfea6055b4b5b451e6bf127e5458aa4e38c49d00c814221e6ab48d79fc6f20`,
CDHash `0f0d0f104f046447d5900660d97c31d2ee52c884`. It withdraws its owned
state on resync/release. The sealed `POWER_PERCENT_INSTALL8` disk contains
this build. Independent disk run `WARM_PERCENT_FRESH8` logs verified native
100% on initial publication and resync (`serial.log:816..845`), but has
**zero presentations after 300 active seconds**, Early boot 10.730 s,
zero panics. This is startup getter proof, not a successful UI boot.

That same fresh boot **still contains a chronod crash**:
`WARM_PERCENT_FRESH8/kcdata.txt:8..20`, physical RAM chunk
`10080000000.bin`, CRASHINFO offset `0x49d0000`, PID 118, SIGABRT (signal 6).
`chronod-stacks.json` identifies its replacement PID 170, pmap
`0x1001c739000`. Its main thread is in `_ChronodStartupHelper.bootstrap`
(static `0x22abc7610`) through accessibility preference initialization
(`__copyValuePreferenceApp+160`, `0x18f748480`). This does not recover the
crashed PID's faulting PC or prove the same cause as an older QuartzCore
query_displays abort. Do not report chronod fixed or infer a GPU cause from
the signal alone.

The large clock now has a successful **snapshot-only** software-rendering
diagnostic: native UIKit label plus the existing non-glass capability path
displays **2:00**, while either fallback alone remains blank. See
`lockscreen-poster-time.md` for the full matrix and native addresses.
`tools/input/clock_software_patch_24A5430a.json` explicitly stages these two
changes for this non-accelerated VM; persistent/fresh-boot validation is pending.

Follow-up: `WARM_CLOCK_SOFTWARE1` now validates that persistent clock patch
in one independent disk boot (no debugger, no restored RAM): Early boot
11.783 s, first presentation 117.731 s, zero panics, visible **3:21** clock
after the initial fade. Its serial also verifies initial/resync native
100% and native MAE `Activated`. See `lockscreen-poster-time.md`.

The chronod corpse was subsequently recovered from the existing
`WARM_PERCENT_FRESH8` RAM without running the VM. Kcdata identifies crashed
thread ID 0x33d; a validated thread-signature candidate at physical
0x10041bde0c0 holds that ID at +0x4e8 and saved state
0xffffffe77f6f4b00. Its retained map (+0x420) leads to pmap root
0x100279fd800, through which `___progname_pointer` reads **chronod**.
The saved frame chain is `_pthread_kill` -> `abort` ->
`query_displays` (return static 0x1844e4eb4) -> `ensure_displays` ->
`+[CADisplay displays]` -> UIKit screen initialization -> SwiftUI platform
environment setup -> `_ChronodStartupHelper.bootstrap`.
At abort's FP 0x16affd2e0, saved caller x19 at FP-8 is **0xfb294002**;
abort's prologue at 0x187f727c4 establishes that spill. Query's logged
error at its FP-0x6c contains the same value. Native query code explicitly
retries that refusal (0x1844e4860..0x1844e4878) until its five-second
deadline, then aborts. This confirms the display-readiness refusal in this
run, not just an analogy to historical traces. Artifacts:
`chronod-corpse-stack.json`, `chronod-corpse-symbols.txt` and
`chronod-kcdata-items.json`. The readiness race is still unresolved.

The later native-Home input attempt on `WARM_CLOCK_SOFTWARE1` did not
complete: `home-relay.jsonl` contains only the initial buffered R down, no
ACK. Input logs identify original PID 87 and replacement PID 215, both with
INITIALIZING but no completed ready handshake. The observer paused after
12 seconds, before the relay's 30-second deadline; a further 25-second
continuation still did not complete initialization. This is not evidence of
a functioning Home gesture or stable input. The rendered clock above
remains positive first-boot evidence; overall VM stability is not a pass.
The source VM was then saved as
`/tmp/dvm/checkpoints/CLOCK_SOFTWARE_INPUT_WAIT1/manifest.json` and quit;
the saved device/RAM state supports the next diagnosis without a fresh boot.

The signed allocation-policy parent independently boots to native welcome
without a debugger endpoint and accepts native input. The combined candidate
now has advancing current calendar time, a native 100% virtual IOPS battery,
and keybag APIs reporting no passcode plus first unlock. The native baseline
bitmap probe passes. With explicit development-firmware identity, the latest
snapshot replay reports native MobileActivation `Activated`, BrickState false,
and 10/10 input ping ACKs. Powerd is alive and SpringBoard is constructing UIKit
views in that capture. A later continuation presents the native lock screen
with the correct date. Its large clock is missing and its battery icon still
looks low despite the published 100% source. Native Setup completion and
repeated Settings launches remain the acceptance targets. The historical
Settings bitmap trap is inherited from the original checkpoint; a fresh
launch/crash has not yet been reproduced.

## Separate disk boot from RAM restore

The control manifest is
`/tmp/dvm/checkpoints/NATIVE_HOME_UART_FIXED1/manifest.json`. Its pinned QEMU
is the UART-fixed app executable, SHA-256
`9d39357bd771a1089725654afc077de8925cdceca456b3561a0fd2def760abbd`.
All experiments used fresh qcow2 children; the original migrated disk and
saved RAM were not rewritten. No QEMU rebuild was performed for these runs.

| Run | Starting state | Debugger during observation | Result |
|---|---|---|---|
| `WARM_CLEAN_HOME1` | Migrated disk, fresh CPU/RAM | No endpoint | Early boot complete at 9.487 s; zero frame presentations over 180 s; black final frame; no kernel panic |
| `WARM_HOME_NO_LLDB1` | Previously rendered Home RAM restore | No LLDB attached | Native Home action reached the icon grid; Settings did not remain open after a native tap; a new launch/crash was not established |
| `WARM_INPUT_AUTO1` | Disk with native-input launch-cache entry | No endpoint | Helper automatically started and registered HID, then immediately hit stdin EOF |
| `WARM_INPUT_AUTO2` | Disk with launch-cache entry and helper v5 | No endpoint | Automatic HID registration, 10/10 ping ACKs, 2/2 Home-event ACKs; no EOF; still zero presentations and black frame |
| `WARM_DISPLAY_POLICY1` | v5 disk plus code-only allocation edit | No endpoint | Rejected: repeated backboardd code-signing invalid-page exits, zero frames over 420 s |
| `WARM_DISPLAY_POLICY2` | v5 disk plus allocation edit, updated page signature and developer trust cache | No endpoint | Early boot 19.939 s; HID ready 226.746 s; first presentation 288.305 s; native welcome screen after fade; swipe ACKs without progression |

`WARM_HOME_NO_LLDB1.settings-perf.json` recorded 170 presentation submissions
over 20.058 seconds, with all 189 QMP samples running. This is **not** a
measurement of distinct frames or proof of a disk boot. The restored RAM
already contains the effects of earlier display/debugger accommodations.

`WARM_INPUT_AUTO2/result.json` covers its initial 240-second observation,
which ended before HID readiness. The VM was then resumed; readiness and
input were verified in the continuation recorded in
`WARM_INPUT_AUTO2/input-verification.json`, `ping.jsonl`, `home.jsonl`, and
`after-home.png`. Readiness latency is not established by the continuation.
Ping ACK latency was 21.2–63.2 ms. Home ACKs prove native event submission,
not a visible UI transition. Early helper restart and slow registration are
still present; this is not a startup-latency regression pass.

## Native input: two missing pieces fixed

The original System volume already contained the root-owned v4 executable
and `/System/Library/LaunchDaemons/com.apple.dvm-input.plist`. However,
`/System/Library/xpc/launchd.plist` contained 730 cached services and no
native-input entry. This image boots with `launchd_unsecure_cache=1` and
did not discover the loose plist during `WARM_CLEAN_HOME1`.

`tools/input/cache_service.py` adds that one service to a copied cache,
preserving other entries and rejecting conflicting labels. Its installer
backs up the guest's original cache, checks the copied bytes, and atomically
replaces the cache inside a disposable restore guest. Evidence:

* Original cache: `/tmp/dvm/WARM_RUNTIME_STAGE2/launchd-original.plist`.
* Installation: `/tmp/dvm/WARM_CACHE_INSTALL1/serial.log`, marker
  `DVM_LAUNCH_CACHE_INSTALLED checksum=50508643 bytes=1304719`.
* `WARM_INPUT_AUTO1/serial.log`: automatic v4 startup, `DVM_INPUT_READY`,
  then `DVM_INPUT_EOF error=0 errno=25`. Inherited stdin was unusable despite
  the plist's `StandardInputPath`; the errno is the earlier terminal call's
  ENOTTY, not evidence that `fgets` itself failed with ENOTTY.

Helper v5 explicitly opens `/dev/console` after acquiring the singleton
lock, matching the established manual spawn's input endpoint. `--validate`
continues to read the caller's pipe. It was built/signed with
`tools/input/build.sh`, installed through `prepare_ramdisk.sh` and
`install_in_guest.sh`, and passed its in-guest packet validation.
`WARM_HELPER5_INSTALL1/serial.log` records the installation.

The preserved disk-only candidate manifest is:

```
/tmp/dvm/warm-input-v5/warm-manifest.json
```

It pins `/tmp/dvm/warm-input-v5/disk.qcow2`, the backing chain, and the new
`system.tc`. Both this parent and its cache-install parent were made
read-only after their staging VMs exited. It has no saved-RAM state.
The original rendered-Home checkpoint remains the separate visual control.

Run a new disk-only observation with a unique tag:

```sh
python3 tools/warm_boot_probe.py /tmp/dvm/warm-input-v5/warm-manifest.json \
  --tag WARM_NEXT1 --seconds 420 --stop-on DVM_INPUT_READY --keep-paused
python3 tools/hmp.py /tmp/dvm/WARM_NEXT1/monitor.sock cont
python3 tools/input/relay.py --events /tmp/dvm/WARM_NEXT1/events.jsonl \
  --uart /tmp/dvm/WARM_NEXT1/uart.sock \
  --log /tmp/dvm/WARM_NEXT1/ping.jsonl --ping --ping-count 10
python3 tools/hmp.py /tmp/dvm/WARM_NEXT1/monitor.sock stop
```

Only run the ping after observing readiness. The boot tool verifies input
hashes and disk parents, creates a fresh child, strips saved RAM and debugger
endpoints, and collects launch arguments, logs, frames, and a result file.
It terminates its VM unless `--keep-paused` is requested. A successful process
exit means observation finished; inspect the result for boot success.

To regenerate the cached service artifact:

```sh
python3 tools/input/cache_service.py COPIED_GUEST_CACHE NEW_CACHE
bash tools/input/prepare_cache_ramdisk.sh NEW_CACHE NEW_SMALL_RAMDISK
```

Boot that ramdisk with a fresh migrated disk child and run
`sh /libexec/dvm-cache-install.sh` inside the restore guest. Never attach the
large System/Data disk to the host. The cache installer deliberately refuses
an already-backed-up target; reuse the preserved parent or start a new child.

## Settings failure: exact trap, unresolved cause

The Home grid had incomplete/cloud-placeholder icons. A native Settings tap
at normalized `(3863, 3269)` was acknowledged, but the UI remained on Home.
Physical RAM from the paused run was decoded with `tools/oskcdata.py`.
The added corpse fields expose process start time and exception codes so
historical corpses can be distinguished from any newly captured failure.

**Correction from `SETTINGS_TRAP2`:** the same PID 1533, start time, and trap
are already present in the original `NATIVE_HOME_UART_FIXED1/vmstate.bin`.
Its matching start-time fields occur at file offsets `0x1d42365e6`,
`0x1e3add6cf`, and `0x1e3b1c987`, with Preferences and the trapping PC in
each surrounding record. `/tmp/dvm/SETTINGS_TRAP2/inherited-crash-proof.json`
records this comparison. The earlier statement that PID 1533 was the new
tap's crash was incorrect. A subsequent trap-address breakpoint got zero hits;
the cache slide was independently reverified as `0x6fa0000`. Treat the analysis
below as the checkpoint's historical Settings failure until a fresh launch
is captured.

`/tmp/dvm/WARM_SETTINGS1/preferences-crashes.json` contains Preferences
PID 1533, start time `2462.400249`, exception codes
`[0x5600001, 0x1db183ea0]`. Older Preferences corpses have start times around
1332–1517. Multiple physical copies of a corpse do not mean multiple crashes.
With the verified shared-cache slide `0x6fa0000`, the trapping PC is static
`0x1d41e3ea0`, a `brk #1` in `_IconServices_SwiftUI` (24A5430a).

The relevant native path is:

1. Function starting at `0x1d41e3ce8` calls
   `+[IFGraphicsContext bitmapContextWithSize:scale:derivePixelFormatFromCGImage:]`
   at `0x1d41e3db0`.
2. It calls the resulting context's `image` method at `0x1d41e3df4`, then
   `CGImage` at `0x1d41e3e04`.
3. The nil branch at `0x1d41e3e10` reaches the observed trap at `0x1d41e3ea0`.
4. `IFGraphicsContext.image` at `0x22ff43570` calls
   `_CGBitmapContextCreateImage` at `0x22ff43590`. The bitmap-context creation
   call and nil check are at `0x22ff431a4` and `0x22ff431a8`.

Selectors were verified from the actual Objective-C stubs and selector
strings, not inferred from ipsw's sometimes misleading inline stub labels.
Evidence is in `icon-crash.dis`, `bitmap-context.dis`, and
`bitmap-pixel-format.dis` under `WARM_SETTINGS1`.

This identifies an absent bitmap image, but does not yet distinguish a nil
context, unsupported pixel format, invalid dimensions, allocation failure,
or another upstream failure. Do not patch out the Swift trap. A later bounded
LLDB diagnostic on this disposable restore captured no matching bitmap calls;
its transcript is `icon-probe.lldb.log` and establishes no additional cause.
The earlier no-LLDB observations and RAM dump predate that attachment.

## Persistent first-frame allocation policy

QuartzCore `IOMFBDisplay::create_surface` requests cache mode `0x400` at
`0x1847ae2e8`. The `tbnz w10,#2` at `0x1847ae2ec` skips the existing
UC-normal-memory capability selection for software cached surfaces. The
IOMFB mapping path rejects `0x400`, requiring `0x700` at
`0xfffffff00a0b9190`. Replacing only that branch with NOP lets the existing
capability test select `0x700` where supported, preserving its fallback and
the driver's validation. Earlier diagnostic evidence is in
[surface-cache-and-completion.md](surface-cache-and-completion.md).

`tools/input/prepare_display_policy.py` guards the source instructions and
stages the edit at `.01` file offset `0x43ae2ec`. The cache already has an
ad-hoc SHA-256 CodeDirectory using 16 KiB pages. Updating just the instruction
was insufficient: `WARM_DISPLAY_POLICY1` produced backboardd
OS_REASON_CODESIGNING / INVALID_PAGE (namespace 3, code 2). Its reported
fault `0x19c5924c4`, minus slide `0x17de4000`, is on the edited code page.
That rejected run is not evidence against the allocation policy.

The successful candidate also updates that page's 32-byte signature hash
at file offset `0x7d55e42`, recomputes CodeDirectory hash
`0061459f204dcb574cf6aa2208acaa77e466fe0d`, and adds it to the developer
trust cache. Native code-page validation remains enabled. The restore-guest
installer checks the header, original instructions, and original hash before
writing, then checks both results. `DISPLAY_POLICY_INSTALL2/serial.log`
contains `DVM_DISPLAY_POLICY_INSTALLED`.

Disk-only candidate: `/tmp/dvm/display-policy2/warm-manifest.json`.
`WARM_DISPLAY_POLICY2/running.png` and `after-swipe.png` show the native
welcome screen and changing localized swipe instruction; the central Hello
animation is absent. `perf.json` records 191 presentation submissions in
20.08459 s, with all 194 QMP samples running. This measures submissions,
not distinct frame rate. Native pings passed 10/10; the gesture log has
nine coalesced state ACKs including release. The swipe did not advance UI.

The run was captured without ever adding a debugger endpoint:
`/tmp/dvm/checkpoints/WARM_NO_LLDB_WELCOME1/manifest.json`.
Use the disk-only candidate to prove boot behavior; the new RAM checkpoint
is a convenience for subsequent diagnostics.

## Calendar and power remain open

The read-only `WARM_CLEAN_HOME1` postmortem located live backboardd and
SpringBoard and resolved the cache slide to `0x17ca0000` by a unique 64-byte
instruction match. One rendering stack included `_kern_GetProtectionOptions`
at static `0x22a39743c`, `IOMFBDisplay::protection_options` at `0x184431b3c`,
and `render_for_time` at `0x18441065c`. One snapshot cannot establish a
persistent wait. `tools/re/warm_boot_postmortem.py` inspects a complete physical
RAM dump and its still-paused source VM through HMP, without guest writes.

Guest serial logs show 1969-12-31 Pacific / 1970-01-01 UTC, and APFS reports
future timestamps (`WARM_CLEAN_HOME1/serial.log`, line 392 onward).
`dt_fixup.py` creates a dummy `no-rtc` resource around lines 630–642.
There is no verified T8140 calendar-clock implementation in this experiment.
XNU initializes calendar time via `PEGetUTCTimeOfDay`; the default platform
`getGMTTimeOfDay` returns zero. See Apple's
[IOPlatformExpert.cpp](https://github.com/apple-oss-distributions/xnu/blob/main/iokit/Kernel/IOPlatformExpert.cpp)
and [clock.c](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/clock.c).
Host time synchronization needs an explicit implementation and reboot checks.
Native calendar and power provider contracts are now documented in
[t8140-calendar-source.md](t8140-calendar-source.md) and
[t8140-power-source.md](t8140-power-source.md); runtime support remains unverified;
displaying a hardcoded percentage would not establish working power support.

## Acceptance gates and validation

The first-frame candidate has one successful disk boot. Next prove native
Setup progression and the Settings bitmap path, then calendar/power services.
The user prefers completing Setup itself; activation, Buddy completion, and
SpringBoard UI authentication must be tracked separately. See
[setup-activation-contract.md](setup-activation-contract.md). Completion requires repeated **disk-only** boots to
Home without debugger/register/memory changes, native input with visible
responses, repeated successful Settings launches/navigation, advancing correct
calendar time across reboot, and coherent battery/power reporting. Include
lock/wake and an idle soak with process-restart evidence; framebuffer activity
alone is insufficient.

Current checks: 45 host unit tests and 2 input tests pass; Python compilation,
shell syntax, and `git diff --check` pass. The restore-shell probe
`WARM_RUNTIME_STAGE1` reached shell with zero XNU panics. Helper build/sign,
in-guest validation, and the no-debugger automatic-start/input run passed.
No full warm-boot or Settings-stability pass is claimed.

## Native services follow-up

`GRAPHICS_RUNTIME1` used a separate child with the signed 64x64 bitmap probe.
Its initial 180-second observation had zero frames. A further condition-bounded
150-second observation rendered welcome, but emitted no graphics-probe result.
The paused 180-second RAM snapshot found the probe inside libSystem
initialization; this is not a bitmap failure. A native Home-button event was
acknowledged twice and visibly reached the passcode screen in `after-home.png`.
This run had no GDB endpoint and is preserved as
`/tmp/dvm/checkpoints/WARM_NO_LLDB_PASSCODE1/manifest.json`.

`tools/re/warm_boot_postmortem.py` now selects a frozen privileged CPU before
using HMP kernel translation. CPU 0 was in EL0 in `GRAPHICS_RUNTIME1` and
reported valid kernel mappings as unmapped; CPU 1 at EL2 resolved the same
address. Two regression tests cover that selection and the all-EL0 refusal.
The parser also retains failed candidate-validation diagnostics without
abandoning the rest of the scan.

The opt-in RTC adapter is documented in [rtc-pv-wall-clock.md](rtc-pv-wall-clock.md).
Its first leaf omitted a BTI landing pad and failed at the entry in
`RTC_PV_RESTORE1`. The corrected nine-instruction leaf preserves `bti c`.
`RTC_PV_RESTORE2` then reached an ANS configuration failure because the test
had no ANS disk despite its NVMe DeviceTree. With a fresh disk child,
`tools/probe.sh` reported for `RTC_PV_RESTORE3`:

```
xnu panics   : 0
reached shell: yes
```

The restore log reports current UTC `2026-09-05 17:05:26`, rather than 1970.
`--keep` did not retain that probe process after the tool command lifetime;
no later live date query from it is claimed.

`WARM_NATIVE_SERVICES1` added the experimental passthrough power leaf and
failed at `AppleARMPassthroughPowerSource: could not create power source`,
static `0xfffffff0085bbc5c`, within 3.862 s. The leaf lacks the native
`function-dock_parent` callable-provider contract. It is rejected; see
[t8140-power-source.md](t8140-power-source.md).

`WARM_NATIVE_SERVICES2` removed that power leaf and retained the RTC adapter,
native `product:allow-hactivation=u32:1`, and signed graphics/power probes.
The power probe reported zero native IOPS sources and no passthrough provider,
as expected with the leaf absent. Its two native time samples are
`1788628345.206296` and `1788628354.422000`, both current and advancing.
The graphics probe reached `main` and loaded CoreGraphics, then its UIKit load
did not finish within the observation. No bitmap creation result is available.
This version explicitly opens console output and runs with Interactive
process type; which change affected its earlier output is not isolated.

The combined run reached Early Boot at 9.949 s but produced zero presentations
and zero kernel panics over 360 s. Native input's first process aborted and its
replacement had not registered HID. A full paused RAM scan found both
SpringBoard and backboardd waiting for framebuffer discovery. With verified
cache slide `0xc958000`, the IOMFB globals at static `0x26fdcc558` and
`0x26fdcc55c` read expected=2/current=1. SpringBoard's main thread is parked
through `___iomfb_populate_all_display_infos_block_invoke` at
`0x22a39655c`, after `_pthread_dependency_wait_np` at `0x22a396558`.
Its enumeration worker is in the run loop at `0x22a395b30`. The earlier
`GRAPHICS_RUNTIME1` 180-second snapshot also had expected=2/current=1,
before subsequently rendering. `WARM_RTC_ONLY1` removed hactivation and still produced zero frames over
330 seconds; removing that property did not resolve the startup failure. Current clock APIs
alone are not a warm-boot stability pass, and native activation has not yet
been queried to verify the daemon's resulting state.


## Display enumeration and native service candidate

`WARM_SINGLE_DISPLAY1` removes only `arm-io/dispext0:device_type` from the
inactive external display node. IOMFB counts exact `display-subsystem` and
`ext-display-subsystem` types at static `0x22a395fcc`, incrementing the expected
count at `0x22a3961ac`. Driver matching had already removed that node's
`compatible`, but this independent enumeration still counted it. The canonical
`dt_fixup.py` now removes that type when the external driver is disabled,
preserving the node and its MMIO properties. A host check against the actual
warm DeviceTree verified those retained properties and the internal type.

The full paused RAM snapshot `/tmp/dvm/WARM_SINGLE_DISPLAY1/ram` and
`process-stacks.json` verifies expected/current=1/1 in both backboardd and
SpringBoard, with cache slide `0xdd84000`. This is an enumeration fix: the run
still produced zero frames over 240 seconds. Backboardd's later main-thread
frame is static `0x2c2169160` in libsystem_notify; SpringBoard waits through
`-[BSXPCServiceConnectionMessage _sendWithMode:]` at `0x18f8c4bc8` and
`-[BKSHIDEventObserver _lock_postObservingStateToServerIncludingNegative:]`
at `0x18a0f5d00`. The remaining service dependency is under investigation.

The SKS device-state candidate adds native DER `sls=3`, while preserving
`ss=4` and `bh=-6`; these fields serve different APIs. The native parser stores
`sls` at record+4, consumed by `MKBGetDeviceLockState`, while `ss` supplies
first-unlock state. See [setup-activation-contract.md](setup-activation-contract.md)
for encoder/parser addresses and the rejected `ss=3` interpretation. Its isolated
QEMU build passes all 24 SKS tests. Native query/UI confirmation remains pending.

`/tmp/dvm/native-services3/warm-manifest.json` pins a new installed disk child,
QEMU with RTC/SKS changes, the single-display/hactivation DeviceTree, and a
3938-entry trust cache. The restore installer reported
`DVM_GRAPHICS_PROBE_INSTALLED` after guarded copies and launchd-cache installation
in `NATIVE_SERVICES_INSTALL3/serial.log`. The runtime helper set contains native
input v5, graphics probe6, power/time probe2, read-only activation/keybag queries,
and the virtual IOPS power-source service. No GDB endpoint is created by its
warm-boot harness. This candidate is not yet a boot-stability pass.


`WARM_NATIVE_SERVICES3` finished its 300-second RTC-on observation with zero
presentations and zero kernel panics. The battery service's native Create and
Set calls returned success, but its snapshot verification did not return.
The graphics helper loaded CoreGraphics but did not finish loading UIKit.
A full HMP dump includes a live notifyd with a worker executing dispatch Mach
message processing; notifyd is not absent. Its main thread is normally waiting
in `__dispatch_sigsuspend`. This disproves the simple absent-broker hypothesis.

The controlled `WARM_NATIVE_NO_RTC1` changes only the bootkc back to
`DISPLAY_SMP6.bootkc` and sets `DARWIN_RTC_PV=0`, retaining the same disk lineage,
QEMU binary, DeviceTree, trust cache, and helpers. It stops at 263.274 seconds
on `GRAPHICS_PROBE_COMPLETE failed-stages=0`, with zero presentations and
zero kernel panics. Both the explicit-pixel-format and source-derived native
IFGraphicsContext paths create contexts, draw the source, and return a UIImage
with a non-null borrowed CGImage. The baseline is 64x64 BGRA8, scale 1; it does
not reproduce the exact historical Settings icon/size. This is positive native
bitmap evidence, not a successful Settings launch.

That RTC-off run also verifies the exact published IOPS source:
`expected_id=2691976 count=1 matches=1`, followed by `POWER_PV_READY`.
The snapshot has current/max capacity 100/100, present and charged true,
charging false, AC Power, InternalBattery, Internal transport, and name Darwin
VM. The native ID equals `(41 << 16) | 5000`, tying it to the publisher. These
are virtual device values. Visible battery status still requires a rendered UI.
Activation queries return `Unactivated`, nil error, and brick-state 1. The native
local activation writes have not been verified; the DeviceTree input alone is
insufficient evidence.

The comparison prompted a native RTC ABI audit. The hardware provider at
`0xfffffff009622178..0xfffffff009622194` writes seconds and **nanoseconds**:
its fractional tick remainder is multiplied by 1,000,000,000 and shifted by 15.
The original PV leaf incorrectly wrote microseconds. It also failed to match
AppleARMPE's zero success return. A coordinated register/leaf correction is
being prepared; whether those defects cause the observed startup stall remains
an experimental question.


`WARM_NATIVE_RTC_NS1` passes the same bitmap completion condition in 189.888 s
with the corrected nanosecond/status ABI, and verifies the native battery
source while gettimeofday advances from `1788631164.038328` to
`1788631173.512019`. The first observation has zero frames; a later bounded
continuation is separate from that initial result. A single native ping timed
out after five seconds; do not infer a healthy input service from its earlier
START log.

`NATIVE_SERVICES_INSTALL4` validates the reusable
`tools/re/install_staged_helpers.py` workflow: exact input hashes, fresh child,
restore shell, echoed installer command, guest success marker, HMP shutdown,
then read-only child. Its `result.json` reports success and mode 0444. It uses
no GDB endpoint and leaves failed children writable for inspection. The helper
change moves keybag queries before activation XPC and emits call-stage markers.

`WARM_NATIVE_SERVICES4/serial.log:642..646` now supplies native keybag proof:
`mkb.device-lock-state=3` and `mkb.unlocked-since-boot=1`. Thus the added
`sls=3` decodes to native no-passcode state while retained `ss=4` preserves
first-unlock state. This validates the model's method-17/selector-7 distinction.
The main QEMU source now includes that change and corrects its stale op19
message to describe the actual 30-byte DER record.

For activation, `WARM_NATIVE_SERVICES3/extra-process-stacks.json` and its
frozen process pmap identify mobileactivationd's executable slide as `0xf24000`
(UUID `aead3cf45cec31c583fed807c412c24e`). Its migration completion byte at
static `0x1003fcbf0` is 1; migration did invoke `_dealwith_activation`.
The DeviceType singleton slot static `0x1003fcbc8` points to runtime
`0x74d4c1c1c0`, whose `should_hactivate` byte at +0x14 is 0. That rules out
an exclusively stale UI/consumer state or unfinished migration. The remaining
question was the daemon's actual IORegistry input versus its explicit opt-outs;
the ENV2 probe and internal-policy analysis below resolve it.
The native converter accepts exactly four NSData bytes and reads a little-endian
integer at `0x1002ec0c0`; the candidate's u32:1 source-tree encoding is correct.


`WARM_NATIVE_SERVICES5` reports the live runtime activation input as CFData,
length 4, raw `01000000` / integer 1; chosen fairplay is absent and
`/AppleInternal/Lockdown/.hactivateoff` is absent (ENOENT). No later preference-section output is recorded within the initial 180 s
plus a bounded 90 s continuation. This is not proof that the lookup blocked:
the 270-second RAM snapshot subsequently finds the helper in a later Lockdown
query, so its console-stage reporting is incomplete. Dedicated direct console
logging is being added before interpreting missing stage lines. Its first version incorrectly used the symbol name
`kMADisableHactivation` as the preference literal. The native CFString at static
slot `0x1003cd820` is actually **DisableHactivation**, proven through the frozen
daemon's pmap. An absent/wrong key does not itself explain a blocked lookup.
The daemon reads boot arguments using `sysctlbyname("kern.bootargs")` at
`0x1002ebfac..0x1002ebfc4`; the next probe reports that native value as well.

The NS1 189.888-second snapshot puts SpringBoard's Cover Sheet construction
behind `-[CHSChronoServicesConnection cachedExtensionsWithOptions:]`, static
`0x18bfb7aec`, waiting for its serial dispatch queue. The queue owner waits in
`_xpc_connection_send_message_with_reply_sync` from
`_subscribeToExtensions`' block at `0x18bf8ff50`. The same snapshot contains a
chronod SIGABRT record. Backboardd's render server is instead in its normal
Mach receive at `CA::Render::Server::server_thread+416`, static `0x1843f9804`;
its IOMFB workers are ordinary receivers. No historical GetBlock/display-power
mutex stall appears in that capture. Current native ChronoServices failure
needs diagnosis before any attempt to bypass the caller.

After the separate no-debugger NS1 observations, a bounded LLDB diagnostic was
attached to that disposable run. It records dvm-input's display query returning
success (w0=0) after 5.9 s, and a successful replicatord query. These later
instrumented observations do not count as a debugger-free UI pass. The initial
input PID87 SIGABRT is real; the snapshot's PID165 is its replacement, caught
runnable at `open("/var/run/dvm-input.lock", O_CREAT|O_RDWR|O_CLOEXEC, 0600)`
before START/Recap. Later PID165 START lines were emitted after resuming and
must not be attached to that frozen stack.

Powerd PID51 also has a SIGSEGV corpse with subcode 0x20, but no preserved fault
PC/register context. Its process start time does not establish whether it
crashed before or after virtual-battery publication; no battery/RTC causal
claim follows. Old Services3 powerd page tables are stale after exit, and
cannot be used to infer the faulting instruction.

### Snapshot diagnosis and the activation policy gate

Use saved RAM/device states for service inspection, input tests, and Settings
crash reproduction. Fresh disk boots validate persistent fixes and startup
ordering; they are not the default way to repeat a diagnostic experiment.

`WARM_ACTIVATION_ENV2/serial.log:639` reports the native `kern.bootargs` value,
which has no `disable-hactivation-ma=1`. Lines 660–664 report a null persistent
domain and absent **DisableHactivation** preference under UID 0. Together with
the live IORegistry values and absent marker, the four explicit opt-outs are
ruled out for this probe context.

The missing prerequisite is `os_variant_allows_internal_security_policies`:
mobileactivationd stores its result in DeviceType `+0x15` at
`0x1002eb5d0..0x1002eb5dc`. The test at `0x1002ebaf8..0x1002ebb00` skips the
whole hactivation-setting block when false, including application of the
already-read DeviceTree value at `0x1002ebb9c..0x1002ebbd0`. The Services3 frozen
singleton has both `+0x15=0` and `+0x14=0`. Thus the correctly encoded property
alone cannot enable this native development path on the current variant.

The later `WARM_NATIVE_SERVICES5` 270-second snapshot has a different current
SpringBoard wait from NS1: `_IOPSCopyPowerSourcesByTypePrecise+168`, static
`0x18f004740`, called through BatteryCenter `connectedDevices+124`, static
`0x2554d29dc`, waits for a synchronous XPC reply. Chronod is runnable in
`_ChronodStartupHelper.bootstrap` and daemon construction, rather than blocked
on a service request in that snapshot. Successful earlier PV publication does
not prove this later client request received its reply. Native power-service
diagnosis therefore precedes a ChronoServices workaround.

### Development-identity replay: native activation succeeds

`WARM_DEV_EARLY1` boots the same services6 disk with only the chosen
`debug-enabled` byte changed (DT offset `0x2ea0`, zero to one). The DT SHA-256 is
`9f760dd23f777f0d43c1a370c5b20c51afae5a033142ce8c5b2a387910789fa6`.
It reaches Early boot complete at 14.220 s, with no panic, then is saved as
`checkpoints/WARM_DEV_EARLY_DIAG1` for repeated diagnosis. Capture takes 12.813 s;
the `DEV_ACTIVATION_DIAG1` replay restores the exact PC in 1.595 s.

No debugger attaches to this replay. Its serial lines 198–201 report MAE
`Activated`, error null, and BrickState 0. The native bitmap baseline reports
`failed-stages=0`, the virtual battery publishes successfully, and input becomes
ready; `ping10.log` records 10/10 replies. Lockdown's separate client query has
not yet returned, so its consumer state remains unverified. After the bounded
first-frame continuation, there are still zero presentations.

The frozen full-RAM capture has cache slide `0x1b78c000`. Powerd is now live
(proc PA `0x1002a70eab0`, pmap root `0x1002a226400`, nine threads), and
SpringBoard's main thread `0xffffffec1b9ec950` is runnable in UIKit view
traversal (`+[_UIViewVisitor _startTraversalOfVisitor:withView:]+192`, static
`0x1848915e8`). This is positive progress beyond the earlier NS5 IOPS wait,
not proof that powerd can never crash again.

`dt_fixup.py -development-activation` now explicitly sets both development
identity properties during normal tree construction. Native opt-outs remain
intact. Host checks verify the exact property values and native u32 encoding;
all 46 host regressions pass. A full independent disk-boot/UI repeatability
test remains required after the remaining service/application issues are fixed.

The subsequent `ui-construction` continuation produces its first fresh
`iomfb: presented 1179x2556 BGRA` after 9.771 s. A 20-second fade observation
renders the lock screen with **Sat Sep 5** and **Swipe up to open**, without
LLDB. The large clock is absent; the battery is visibly low rather than 100%.
This state is preserved in `checkpoints/WARM_DEV_LOCKSCREEN1/manifest.json`
(14.032-second capture, 3,985,183,289-byte migration stream). Its
`DEV_HOME_INPUT1` restore takes 1.541 s and is the current native input test
baseline. All elapsed times here describe their own bounded segments, not a
single uninterrupted disk-boot latency.


## Native Setup and app-input snapshots

`WARM_DEV_NATIVE_HOME1` captures the Home grid after an ordinary native Home
button transition from `WARM_DEV_LOCKSCREEN1`. No passcode, authentication,
or UI predicate override was used on that route. Calendar shows Saturday 5
and the status clock advances; the large lock-screen clock remains absent.
This RAM checkpoint is the app/input diagnostic baseline, not another
independent boot result.

A separate disk validation, `WARM_SETUP_NATIVE1`, starts from the R12
pre-Buddy-completion parent with the guarded signed display policy, cached
helpers, development activation DT, RTC and SKS fixes. It independently
reports native Activated/error-null/BrickState false, zero bitmap probe
failures, and HID readiness. No presentation arrived in the initial 330 s.
`SETUP_NATIVE_READY_DIAG1` preserves this state for diagnosis. On its
`SETUP_GATE_DIAG1` replay, native Setup PID572 is alive in its Foundation/
UIKit event loop; no task thread is in the activation query. Lockdownd PID93
is receiving server messages at static `0x10002f9c8` after `_mach_msg`;
its optional diagnostic client remains pending. That client is not the
observed Setup no-frame dependency. Native Setup completion remains unproven.

Input diagnosis also isolated a false crash trigger: direct touch submission
blocks long enough to turn a supposed Settings tap into a long press. See
`native-input.md` for the paired-record evidence and v6 native Recap P
candidate. The earlier 28×28, scale-zero factory capture was unattributed;
it is not a Settings-process failure witness. The continuous bitmap callback
now records guest program name and thread identity before correlating a
factory call with the nil-CGImage trap.


## V6 tap validation and fresh failures

`WARM_INPUT_V6_NATIVE1` independently disk-boots the input-v6 child with no
GDB endpoint: Early boot 16.675 s, native HID ready 209.363 s, and baseline
bitmap probe `failed-stages=0`. `INPUT_V6_READY1` captures that initialized
state; its `INPUT_V6_UI1` replay produces a first presentation after 40.129 s
and reaches native Home after ordinary Home button events. No debugger was
attached. `INPUT_V6_NATIVE_HOME1` is the resulting repeatable app-launch
diagnostic checkpoint.

`INPUT_V6_TAP1` queues one native P tap at (3863,3269), ACK 9809.94 ms,
shows a white launch surface after 30 s, then returns to Home during the
following 45 s. A separate `SETTINGS_V6_TRACE1` replay attributes an actual
nil-CGImage trap to `Preferences`: runtime `0x1e92c3ea0`, independently
verified cache slide `0x150e0000`, static `0x1d41e3ea0`, `brk #1`. The
factory was missed during an accidental debugger detach in that diagnostic,
so its geometry is not established from the trap's registers. The persistent
input fix makes the actual application failure reproducible.

The root no-debugger TAP1 run's full frozen RAM is captured under its `ram/`,
with chunk SHA-256 hashes in `capture.json`. `corpses.txt` shows current-boot
powerd SIGSEGV/FAR `0x20` records for PIDs51 and169; the live powerd is now
PID340 (proc PA `0x100931b38e8`, pmap root `0x1010a131000`, eight threads).
A one-time PV publication before broker restarts does not prove the source
still exists afterward. The PV helper currently waits forever after its
initial successful verification; broker reconnection and the native powerd
fault are separate unresolved stability requirements.


`SETTINGS_V6_TRACE2` then captured the actual factory in Preferences. At
runtime `0x1e92c3db0`, d0/d1 are 28/28 and d2 is zero. Preserved target x21
is `0x7412004540`; its ISA resolves to `ISImageDescriptor`, not UIImage.
Source CGImage x2/x19 is `0x7411b82bc0`. Static return site
`0x1d41e3d9c` is immediately after the descriptor's `scale` message. The
IF factory has no automatic-scale branch: `0x22ff42ffc..0x22ff43008`
multiply and round dimensions using the unchanged scale; zero becomes a
0×0 CoreGraphics allocation. TRACE2 did not catch a later trap within its
bound; TRACE1 separately proves the Preferences nil-CGImage trap. The next
fix target is the descriptor's invalid scale origin.

The pre-Setup snapshot also retains multiple Setup scene-create watchdog
terminations (`0x8badf00d`) in its full RAM despite an empty zombie list.
Current Setup925 waits on ManagedConfiguration; profiled155 has 24 workers
waiting on DMCUtilities' serial power-assertion queue. Its release operation
waits on IOKit's PM queue. This is a concrete client dependency; a causal
link to powerd's restarts still needs verification. See
`SETUP_GATE_DIAG1/corpses.txt` and its profiled stack/identity artifacts.

## Combined Settings/power candidate (2026-09-05)

`WARM_STABILITY_INSTALL2` installs the guarded 12-byte SwiftUI/ISImageDescriptor
scale lower bound plus the serialized native IOPS restart publisher into a new
child of input-v6's installed disk. See `settings-zero-scale-bitmap.md` for
native call geometry, source/cache signature offsets, and snapshot controls.
The merged TC has 3941 entries; existing input, allocation policy, RTC, SKS,
and development-activation configuration remain selected in its pinned manifest.

`WARM_STABILITY_FRESH1` boots `/tmp/dvm/WARM_STABILITY_PATCH2/warm-manifest.json`
from disk with no saved RAM or guest debugger. It reaches Early boot complete
at 13.008 seconds, native input ready at 123.152 seconds, and first scanout
presentation at 204.433 seconds. First-phase counters: one presentation, zero
kernel panics. A further 30-second observation shows the native lock screen
with Sat Sep 5. Its large time text is still missing and the battery glyph
is still low/red. Do not call either UI defect fixed.

The new publisher records successful resync registration, Create and Set, but
has not yet completed its initial exact-ID snapshot verification. These are
separate milestones; its readiness or recovery cannot be inferred from Set.
Settings launch and subsequent service diagnosis continue from this owned run.

Settings main and General both render after native P taps in the continuous
no-debugger fresh run. Date & Time navigation had not produced a populated
page in its 30-second interval; `STABILITY_SETTINGS_DATE1` preserves that
state. Subsequent frozen analysis identifies the precise-source powerd null
read in `powerd-precise-battery-null.md`. The previously suspected blocked
publisher verification was corrected by reading its actual ready state.
