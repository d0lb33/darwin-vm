# Setup completion and direct Home experiments (24A5430a)

Use the six-core integration checkout. This is an explicit debugger experiment,
not supported activation and not a default image change.

## Native completion in DISPLAY_SMP6_RESIZABLE_R9

`tools/re/setup_skip_probe.py` captured Setup's main-thread run-loop observer
at `0x1955e84e8`, SP `0x16f5ce610`, thread pointer `0xffffffeb02c13830`.
The current Setup slide is `0x830000`; the global at Setup static
`0x1003c0d90`, read by `+[BuddyApplicationAndSceneSharedStorage setupController]`
(`0x100052f6c..0x100052f74`), held controller `0x101413a70`.

The probe saved general/vector registers and invoked native methods on that
thread, returning to an observer-entry breakpoint and restoring all registers:

1. `-[SetupController markBuddyComplete]`, static `0x100013658`.
   At `0x100013bcc..0x100013be0` it writes `BYBuddyFinishedInitialRunKey`;
   at `0x100013d38` it synchronizes defaults.
2. `willEndLifecycleDueToCause:allowDismissal:`, `0x10001ef8c`, cause 1,
   allowDismissal 1. Cause 1 participates in the normal home-gesture branch
   (`0x10001f184`); no reboot cause is requested.
3. `endLifecycleDueToCause:`, `0x10001fa78`, cause 1.

All three returned. Afterwards the native `BYSetupAssistantNeedsToRun` return
was zero in bluetoothd and SpringBoard. SpringBoard's `updateInSetupMode`
(`0x2246d37f0`) nevertheless changed the reason from 1 to **2**: its bricked
check at `0x2246d38b0` returned true, log string at `0x2246d38e0`.
`SBLockdownManager brickedDevice` (`0x22458d184`) is simply lockdownState != 2
(`0x22458d194..0x22458d198`), so finishing Buddy does not establish activation.

A subsequent native SpringBoard wake call remained outstanding. Its saved
registers and stage are `/tmp/dvm/DISPLAY_SKIP_SETUP.pending-call.json`.
Checkpoint `/tmp/dvm/checkpoints/DISPLAY_SETUP_COMPLETE_WAKEWAIT9/manifest.json`
preserves the disk completion writes, pending call, and diagnostic state
(2.525 s migration, 13.415 s total). This is a **stalled diagnostic** checkpoint,
not the healthy display baseline.

The last A408 at R9 stderr lines 31780 onward had all four surface null flags
set (wire +0xfeb..+0xfee), followed by A414/A412/A484 requests. That is consistent
with display-off but does not alone identify idle sleep as the cause. The host
scanout intentionally rejected the unsupported no-surface profile and retained
old pixels, making the window look frozen. Later the model rejected a 164-byte
SKS op0f variant 3/class 2 request. It remains unsupported; do not silently claim
this is fixed or that it caused the earlier frame stop. The attempted DART read
`DISPLAY_SKIP_SETUP.op0f-candidate.bin` was all zeros, **not a request capture**.

## Direct startup experiment

`tools/re/home_startup_probe.py` is opt-in only. From the healthy
`DISPLAY_SMP6_ADFL3` checkpoint it returns false at native entry of
`BYSetupAssistantNeedsToRun` (`0x1cac11c18`) and SpringBoard-only
`SBLockdownManager brickedDevice` (`0x22458d184`), before PAC prologues.
This suppresses UI gates for a laboratory VM; it neither activates the device
with Apple nor changes persistent activation records.

Run `DISPLAY_HOME_R10` restores all six CPUs with native completion and scanout
on, Cocoa zoom-to-fit, GDB 1396. The cache-mode 0x700 allocation diagnostic and
FrontBoard watchdog extension remain necessary experiment settings.

R10 restored the ADFL PC `0x239464ba4` in 1.459 s. Native ADFL's
`updateInSetupMode` return at `0x23946674c` was **0**, unlike the live
completion attempt's reason-2 result. This establishes startup suppression;
it does not by itself establish a rendered Home Screen.

R10 never submitted a primary frame, and SpringBoard restarted after the native
wake attempt. The cause is not established. R10 was stopped and quit; do not
label it a successful Home run. `DISPLAY_HOME_LIVE_R11` instead restored the
known-visible `DISPLAY_SMP6_QEMU_SCANOUT8` in 1.643 s, preserving Cocoa pixels.
At its SpringBoard main observer:

- SBApp `0x78dedf0a00`, idle coordinator `0x78de5a4000` (ivar offset read
  from `0x26cf35748 + slide`), setup manager `0x78df133f60`.
- Native `acquireIdleTimerDisableAssertionForReason:` at `0x22487b064`
  returned `0x78de2d9bf0`; `objc_retain` at `0x18040526c` retained it so an
  autorelease-pool drain cannot end the diagnostic assertion. Reason is the
  existing command-string constant at `0x2732af6a8 + slide`.
- With explicit Setup/bricked-UI suppression installed, native
  `updateInSetupMode` returned zero.
- `_returnToHomeScreenWithCompletion:` at `0x2244a949c` returned. It schedules
  a repeating native test timer; its block at `0x2244a9550` invokes
  `SBPPTSynthesizeEventsForCommandString` while Home is not yet reached.
  This is an attempt to transition, not proof of Home pixels.

## Passcode and language picker (R11–R13)

R11's native Home request rendered `Enter Passcode` and an alphanumeric
keyboard (`/tmp/dvm/DISPLAY_HOME_LIVE_R11.after-home.png`). Debugger pauses
stop cursor animation; do not diagnose a frozen compositor while LLDB is stopped.
Direct `_finishUIUnlockFromSource:withOptions:` (`0x224c4cc00`, source 1,
nil options) raised an exception: its three-argument implementation checks
`isAuthenticated` at `0x224c4c6dc`, branches to the assertion at `0x224c4ca00`,
and reaches the cold assertion at `0x224d22284..0x224d222c0`. R11 was discarded
and R12 restored the visible checkpoint in 1.500 seconds.

At R12's native SpringBoard main observer, real queries on authentication
controller `0x78df25a800` returned:

- `SBFUserAuthenticationController hasPasscodeSet` (`0x1bb281de8`): 1.
- `isAuthenticated` (`0x1bb27afd8`): 0.

This establishes the UI's state, not whether a real passcode was provisioned.
`install_ui_auth_diagnostic` explicitly returns true at the latter method in
SpringBoard only. It neither submits a password nor unlocks SEP/keybag data.
With this diagnostic, the native finish-UI call returned 1 and the native Home
request returned. R12 subsequently rendered the full Setup language picker,
including globe, language rows, chevrons, and status area. Native PNG evidence:
`/tmp/dvm/DISPLAY_HOME_LIVE_R12.after-unlock.png`. Primary A408 presentations
appear at R12 stderr lines 19625, 19722, 19819, 19974, and 20153. This is positive
Setup rendering, **not Home icons**. DCP power-off/on activity still occurs even
with a retained idle assertion; it is not proven that the assertion eliminates
all screen-off behavior.

Checkpoint `DISPLAY_SMP6_LANGUAGE12` preserves this milestone: 7,339,844,543 RAM
bytes, 2.527 seconds migration, 14.540 seconds total. All LLDB breakpoints were
removed before capture. `DISPLAY_LANGUAGE_LIVE_R13` restored the exact witness
PC `0xfffffff02aa654c8` in 2.281 seconds, with saved pixels in native resizable
Cocoa, then reinstalled the diagnostic callbacks. The immutable manifest is
`/tmp/dvm/checkpoints/DISPLAY_SMP6_LANGUAGE12/manifest.json`.

R13 tried native Setup completion after restore. The first call did not return
before a new Setup `UIApplicationMain` was observed with executable slide
`0x834000`; do not reuse the old process's slide (`0x830000`) or saved call
registers in that new process. The cause of that relaunch is not yet established.

## Single-touch integration scope

Current `qemu-sptm/hw/arm/darwin_fb.c` registers only keyboard input, forwarding
it to UART; there is no mouse/digitizer device. A guest event bridge is a possible
prototype, distinct from emulating Apple's touch-controller protocol. Relevant
native entry points in SpringBoard are `SBPPTSynthesizeEventsForCommandString`
(`0x2247e0394`), `SBPPTSynthesizeEventsForEventActions` (`0x224379ec4`), and
`SBPPTSynthesizeEventsForEventStream` (`0x2247e041c`). The Home test proved a
native gesture path, not arbitrary coordinate tap/drag injection.

## Home screen achieved

R13 subsequently displayed native SpringBoard Home icons, labels, Search, and
Phone/Safari/Messages/Music dock. The user independently observed Home in the
Cocoa window. Native PNG `/tmp/dvm/DISPLAY_LANGUAGE_LIVE_R13.HOME.png` confirms
Settings, FaceTime, Calendar, Photos, Clock, App Store, and dock icons. Some
other icons are placeholders with cloud badges; complete app availability and
wallpaper rendering are not established. This run used the explicit Setup,
bricked-UI, and UI-authentication diagnostics above; it does not establish
normal activation or functioning passcode authentication.

The old Setup completion call's return was not observed, but the later native
Home pixels are unambiguous. A fresh Setup observer at SP `0x16f5ca620`, TP
`0xffffffeb02653060`, saw its controller global as zero. All 23 breakpoints were
removed at that stop before capturing `DISPLAY_SMP6_HOME13`. This checkpoint is
the preferred Home milestone; the earlier language and Welcome checkpoints
remain intact for independent testing.

Home checkpoint capture took 3.535 seconds migration / 14.910 seconds total
(7,332,699,323 RAM bytes). `DISPLAY_HOME_LIVE_R14` restored it in **1.871 seconds**,
with exact CPU witness PC. The pre-resume native PNG is byte-identical to R13:
SHA-256 `53095fdeafc8b22d62ef194b594cc1b58851b9f016f2149146146fdd19db7149`.

Reproduce from the integration checkout with a unique tag and unused GDB port:

```bash
python3 tools/restore_checkpoint.py \
  /tmp/dvm/checkpoints/DISPLAY_SMP6_HOME13/manifest.json \
  --tag YOUR_UNIQUE_TAG --gdb-port 1401 --leave-paused \
  --display cocoa,zoom-to-fit=on
lldb
```

Then in LLDB (adjust port to match):

```text
gdb-remote 1401
command script import tools/re/home_display_callbacks.py
script home_display_callbacks.install(lldb.debugger)
continue
```

Only one live VM at a time. Preserve/stop the current child before another
restore. The callbacks include explicit laboratory UI-authentication overrides,
cache-mode 0x700 allocation diagnostics, watchdog extension, and exception
stops; an exception stop deliberately freezes the window for diagnosis.
