# Setup Assistant launch gate (iOS 27 / D47)

## Source metadata

* **Guest / device tree:** iOS 27 cache (`dyld_shared_cache_arm64e`, cache UUID
  `58C54E82-C171-300E-AEEE-06DF937AA565`), D47AP / t8140.  The current input
  tree `/tmp/dvm/data-seed/dt_nvme_welcome.bin`, decoded through
  `tools/dt_dump.py`'s `dt_fixup.py` parser, has root `model = iPhone17,3`,
  `compatible = D47AP\\0iPhone17,3\\0AppleARM`, and `/product`
  `artwork-device-idiom = phone`, `product-name = iPhone 16`.
* **Binaries:** extracted
  `/System/Library/PrivateFrameworks/SetupAssistant.framework/SetupAssistant`
  (`__TEXT` static cache VA `0x1cac10000`),
  `/System/Library/PrivateFrameworks/SpringBoard.framework/SpringBoard`
  (`__TEXT` static cache VA `0x224216000`),
  `/usr/lib/libMobileGestalt.dylib`, and
  `/Users/jdolbe1/dvm-artifacts/extract/bin/backboardd` (arm64e).
* **Run under investigation:** `UI_OP19_DCP1`; serial log
  `/tmp/dvm/probe/UI_OP19_DCP1.serial.log` and frozen-process report
  `docs/re/ui-process-snapshot.md`.  Static VAs below require the *current*
  shared-cache slide before use as live breakpoint addresses; cache `maxSlide`
  is `0x20000000` (`ipsw dyld info` on the cache above).

SetupAssistant has a concrete early false-return gate: it queries MobileGestalt
`DeviceClassNumber`, accepts only `1` through `4` or `7`, and otherwise returns
`NO` before its launch-sentinel preference logic.  The available UI snapshot
proves that SpringBoard, backboardd, and runningboardd lived while no
Setup/PurpleBuddy process record was found, but it does not record this gate's
MobileGestalt return or SpringBoard's activation call, so it cannot identify
the failing branch.  The smallest decisive observation is a one-shot breakpoint
immediately after that query, followed only if needed by the cached-byte branch
and SpringBoard activation breakpoint listed below.

## Gate/layout evidence

| Item | Static VA / layout | Behaviour read and branched on | Evidence |
|---|---:|---|---|
| Device-class query | `SetupAssistant+0x1fac` (`0x1cac11fac`) | `adrp/add` materialises `@"DeviceClassNumber"`; `bl 0x1d01493e0` at `0x1cac11fc4` returns the integer in `w0`. | `llvm-objdump` of extracted SetupAssistant: `0x1cac11fb8`–`0x1cac11fc8`; `ipsw dyld str` maps the same cstring to SetupAssistant at `0x1cac62898`. |
| Accepted device classes | `0x1cac11fc8`–`0x1cac11fdc` | `sub w8,w0,#1; cmp w0,#7; ccmp w8,#4,#0,ne; cset w8,lo` stores one byte.  This accepts `{1,2,3,4,7}` and rejects every other value, including the MobileGestalt missing-value default observed in its helper. | SetupAssistant instructions at those VAs; storage symbol `__isSupportedDeviceClass.isSupported` is `0x1e72ba900` (`nm -nm`). |
| First early no-return | `0x1cac11d0c`–`0x1cac11df8` in `_BYSetupAssistantNeedsToRun` | After once-token initialization, `ldrb w8,[...+0x900]`, `cmp w8,#1`, and `b.ne 0x1cac11df4`; that target sets `w20=0` and returns through `0x1cac11cec`. | Extracted SetupAssistant disassembly at `0x1cac11d0c`–`0x1cac11df8`; the false-path log string is at `0x1cac5d05a`, “BYSetupAssistantNeedsToRun is NO due to unsupported device class.” |
| MobileGestalt missing answer | `libMobileGestalt+0xcbdac` (`0x1af7321ac`) | `_MobileGestalt_get_deviceClassNumber` initializes local `w21=-1`, calls `_MGCopyAnswer` with `DeviceClassNumber` at `0x1af7321d4`–`0x1af7321e0`, and returns that local at `0x1af732264`–`0x1af732280`; it therefore returns `-1` if the answer is absent/non-numeric. | Extracted `libMobileGestalt.dylib` disassembly at `0x1af7321cc`–`0x1af732280`.  This documents the helper's behaviour, not the current guest's result. |
| Non-UI and forced-no-Buddy gates | `0x1cac11c38`–`0x1cac11ce8` | A non-UI predicate reaches the log/zero return at `0x1cac11c48`–`0x1cac11ce8`; then the `@"ForceNoBuddy"` preference is read at `0x1cac11c8c`–`0x1cac11ca0`, and a true value takes the independent zero return at `0x1cac11ca4`–`0x1cac11ce8`. | Cstrings/logs at `0x1cac5ccd9` and `0x1cac5cd16`; instructions cited above. |
| Setup-launch state | `0x1cac11d2c`–`0x1cac11d44` | Only after supported class does the function call `_LaunchSentinelExists`, invert that result into `w20`, and check the `apple-internal-install` state; later it reads `lastPrepareLaunchSentinel` under domain `com.apple.purplebuddy` at `0x1cac11d84`–`0x1cac11db4`. | SetupAssistant disassembly; `_LaunchSentinelExists` materialises `purplebuddy.sentinel` at `0x1cac11ec4`–`0x1cac11ef0`. |
| SpringBoard launch edge | `SpringBoard+0xa39af8` / `+0xa39b78` (`0x224c4faf8` / `0x224c4fb78`) | The block belonging to `-[SBLockScreenManager _maybeLaunchSetupForcingCheckIfNotBricked:]` prepares an application object in `x20`, moves it to `x0`, then calls `_SBWorkspaceActivateApplication`. | Named block from `nm -nm`; extracted SpringBoard instructions at `0x224c4fb10`–`0x224c4fb78`. |
| WindowServer is separate evidence | backboardd imports `CAWindowServer`, `CAWindowServerDisplay` | backboardd contains `BKDisplayStartWindowServer` and `StartWindowServer: Setup complete` strings, but no direct reference to `BYSetupAssistantNeedsToRun` or `SBWorkspaceActivateApplication` was found in its import/string scan. | `/Users/jdolbe1/dvm-artifacts/extract/bin/backboardd`: `nm -nm` imports and `strings -a` hits.  Therefore this scan does not establish a WindowServer-readiness prerequisite for the SetupAssistant decision. |
| Current run state | process snapshot | PID 34 runningboardd, 35 SpringBoard, 71 backboardd, and 110 usermanagerd were live at both captures; `/Applications/Setup.app` / PurpleBuddy appeared only as catalog strings, not as a process record. | `docs/re/ui-process-snapshot.md:14`–`:19`, `:32`–`:37`. |
| Keybag observation | serial lines 545–589 | First-boot `systembag.kb` absence gave `-7`, but MKB init completed, User mounted encrypted, and EarlyBoot setup completed. | `/tmp/dvm/probe/UI_OP19_DCP1.serial.log:545`–`:589`; this is not evidence that keybag blocked the later launch decision. |

## Current `DeviceClassNumber` status

The current value is **not established**.  The decoded current tree proves that
the guest identifies as a D47AP phone (`iPhone17,3`), but neither that tree nor
the available serial log contains a `DeviceClassNumber` value or a documented
mapping from these properties to MobileGestalt's integer.  In particular,
claiming that a phone must resolve to `1` would be an uncited inference; it
must be observed at the call below.

No permitted file-level correction was identified.  `ForceNoBuddy` is a
negative preference gate (true suppresses Setup) rather than a source or
override for the MobileGestalt answer, and the inspected SetupAssistant code
contains no positive preference override.  Altering the DeviceTree production
path would implicate orchestrator-owned `dt_fixup.py`; neither it nor a
hand-edited DT/blob is proposed here, and no other repository file has evidence
of owning this MobileGestalt answer.

## Narrow, read-only runtime probe

Use the gdbstub only on a disposable reproduction and derive the **current**
cache slide first.  `docs/re/sep-protocol.md:187`–`:191` documents the existing
safe method: `tools/probe.sh --keep`, then `tools/hmp.py <monitor.sock>
"gva2gpa <VA>"` to validate the mapped/slid address; an older, different run's
SpringBoard dyld launch record maps the cache at `0x19f374000`, so it must not
be reused as an address for this run.

With `S` equal to this run's shared-cache slide, install auto-continuing,
one-shot breakpoints in this order:

1. `S + 0x1cac11fc8` (immediately after `_MGGetSInt32Answer`): record `w0`.
   `w0` outside `{1,2,3,4,7}` proves the unsupported-device branch; a member
   of that set proves it cannot be this branch.
2. `S + 0x1cac11d20`: record byte at `S + 0x1e72ba900` and the branch at
   `S + 0x1cac11d28`.  This cross-checks the returned value against the cached
   bit actually consumed by `_BYSetupAssistantNeedsToRun`.
3. If that bit is one, break at `S + 0x1cac23394`
   (`_BYSetupAssistantPrepareLaunchSentinel`) and then at
   `S + 0x224c4fb78` (the actual `_SBWorkspaceActivateApplication` call).

This is read-only: it only reads registers and one process-private byte and
does not alter preferences, the tree, keybag, disk image, or QEMU model.  The
outcomes discriminate exactly: no hit at (1) means this first-boot code path
was not evaluated in the observed window; a rejected `w0` identifies the
device-class gate; accepted `w0` without (3) leaves the sentinel/internal-build
or other later `NeedsToRun` state; and a hit at the final edge proves that
SpringBoard requested application activation, after which a fresh process
snapshot distinguishes launch-service failure from a rendered-display issue.

## Open questions

| Question | Observation that settles it |
|---|---|
| What does this D47 guest return for `DeviceClassNumber`? | Register `w0` at `S + 0x1cac11fc8` in a run that evaluates SetupAssistant. |
| Is Setup currently suppressed by non-UI, `ForceNoBuddy`, sentinel, or another post-class decision? | Auto-continuing hits plus return values at `S+0x1cac11c44`, `S+0x1cac11ca0`, `S+0x1cac11d28`, and `S+0x1cac23394`; retain the matching unified log. |
| Does a display/WindowServer condition prevent the launch? | First prove or disprove the SpringBoard activation call at `S+0x224c4fb78`; only an activation hit followed by an absent/failed Setup process makes display/launch service a downstream question. |
| Is activation state a required prerequisite here? | A trace of the return paths in `_BYSetupAssistantNeedsToRun` alongside the corresponding MobileGestalt/prefs values; current artifacts contain no activation-state result. |

