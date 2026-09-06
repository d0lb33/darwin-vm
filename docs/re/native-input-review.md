# Native input review — 2026-09-05

This reviews the DVMI2 implementation introduced by parent commit `60628ac`
and QEMU commit `b0f7bc6`. The reviewed QEMU head is `8b46eb6` (including `31d0f88`); the revised
guest helper reports version 14. Earlier boot/restore observations remain
historical evidence in [native-input.md](native-input.md).

## Correctness fixes

- The host's four-record window now covers both acceptance and asynchronous
  HID dispatch. Previously, a fast reader could ACK an arbitrarily long stream
  into the helper's 32-operation queue while its worker was blocked. Queue
  saturation could drop an up edge after accepting its down. The helper also
  reserves cleanup capacity and cancels the gesture on overflow.
- Continuous wheel input has at most one active drag and one adjacent combined
  pending batch, clamped to eight notches. Opposing unsent batches cancel. This
  bounds the scrolling that can remain after trackpad input stops.
- The helper tracks each internal wheel contact. Cancellation interrupts the
  remaining wheel moves and releases it; a failed final up schedules an internal
  retry immediately. Stubbed dispatch tests exercise both cases.
- A ten-second ACK timeout or thirty-second dispatch timeout opens a new
  epoch and clears queued work. These are guest virtual-time deadlines, so
  pausing does not expire them. Previously the host marked input lost but
  continued transmitting already queued edges.
- Epoch transitions terminate a partially transmitted UART line before
  sending cancel. Dropping only the unsent suffix could splice the next cancel
  into a malformed record. The initial cancel waits for the helper's first
  announcement, and UI events are accepted only in state R.
- ACKs must match a live sequence and current epoch; duplicate/stale ACKs
  cannot change readiness or inflate success counts. Dispatch completions
  are similarly correlated, including the possible completion-before-ACK
  ordering from an older helper.
- Helper cancellation drops unsubmitted work, tracks an operation already in
  the worker, and places touch/Home/Power releases ahead of fresh input.
  Repeated C packets replace pending releases without losing them. Internal
  cleanup does not impersonate a user operation's dispatch completion.
- Recovery cancels/releases the old HID client and virtual service, ignores
  terminal notifications from replaced services, and does not let callbacks
  recover a half-initialized pair. Creation gets three attempts and a
  twenty-second recovery watchdog; a stuck worker exits 75 for launchd's
  existing KeepAlive job to restart. A failed initial creation also exits.
  This does not establish the cause of the historical silent first-instance
  disappearance.
- F5 autorepeat no longer repeats Home-down events. F5 and right-click share
  one aggregate Home state, so releasing one while holding the other does not
  release Home early. Ordinary keyboard bytes are suppressed while DVMI2 owns
  the console; otherwise typing can corrupt a touch packet. Native text entry
  from the host keyboard is not implemented; guest on-screen keys remain touch
  targets. Legacy console keyboard behavior is unchanged with DVMI2 disabled.
- Helper elapsed durations use its monotonic clock. The old cross-clock
  delivery estimate was invalid: QEMU_CLOCK_REALTIME is host monotonic time,
  not the guest wall clock. The helper now explicitly reports delivery -1.
- `native_input.py` waits for dispatch, rejects epoch changes and readiness
  drops, and includes ACK rejections and overflow in its verdict. Previously
  Home could return `ok:true` while both HID submissions subsequently failed.
  `ok` remains a **transport/dispatch** verdict; a changed frame hash is not
  proof that a specific UI transition occurred. The tool also accepts `power`
  for F6 and always attempts a release when gesture generation raises.

The client teardown follows Apple's
[HIDVirtualEventService.m](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/HID/HIDVirtualEventService.m)
(`activate`, `cancel`, `dispatchEvent:`), retaining the existing measured
24A5430a callback ABI and event properties. It adds no guessed hardware behavior.

## Review of the previous evidence

Only `NATIVE_HID_RESTORE2` directly proves UI process replacement: its eventual
RAM capture contains threadless backboardd 74 / SpringBoard 35 and live
replacements 410 / 429. It does not time replacement relative to the Home
press. RESTORE1 and RESTORE5 have analogous symptoms, not independent process
replacement proof. There is no evidence that Home causes those replacements.

The Settings failures on fresh boots establish a launch surface followed by
failure to render/remain open. Available FRESH3/FRESH4 RAM scans contain no
matching Preferences corpse; calling this a reproduced crash or watchdog kill
is premature. Similarly, PID87 disappears in five of six usable historical
fresh boots (FRESH2..7, except FRESH4), but no EOF or caught-signal log identifies
why. Absence of the v12 signal log cannot exclude SIGKILL.

The still-running v12 RESTORE5 was observed answering pings in I for several
minutes after `DVM_HID_RECOVER begin`, without a completion. It was paused for
preservation. The watchdog specifically addresses that permanent input outage.

## Runtime controls

`INPUT_REVIEW_BASELINE`, running the reviewed QEMU through `tools/probe.sh`,
reported **reached shell: yes; xnu panics: 0**, with 303 serial lines. Logs are
`/tmp/dvm/probe/INPUT_REVIEW_BASELINE.{serial,stderr}.log`.

`INPUT_REVIEW_COMPAT1` restored the existing HOME4 checkpoint with reviewed
QEMU and the old v12 helper at the exact saved PC. The first Settings tap did
not leave it open; a second tap rendered the native Settings list. The wheel
scrolled it. Home initially dispatched successfully without changing the
screen. The helper then received a type-5 reset and recreated both services;
a later terminal notification from the old button service caused another
recovery (`serial.log:3769..3854`). After recovery settled, a wheel notch
scrolled Settings and right-click visibly returned to Home. Thus consumer
usage 0x0c/0x40 works inside apps too; the earlier no-op was not sufficient
evidence for incorrect button semantics.

Evidence: `/tmp/dvm/INPUT_REVIEW_COMPAT1/ui/input.jsonl`,
`settings2-after.png`, `wheel2-after.png`, `postrecover-wheel-after.png`, and
`postrecover-home-after.png`; serial is under
`/tmp/dvm/checkpoints/NATIVE_HID_HOME4/restores/INPUT_REVIEW_COMPAT1/`.
This is a diagnostic RAM restore, not independent disk-boot proof.

## Independent v13 boots and restore limit

Two independent disk boots using `31d0f88` reached presentation without a
kernel panic. FRESH1 retained its original helper PID87; FRESH2 lost PID87
during initialization and launchd started PID138. The original silent
first-instance loss therefore remains reproducible; it is not fixed by v13.

| Run | Early boot | HID ready | First presentation | Helper at readiness |
|---|---:|---:|---:|---:|
| INPUT_REVIEW_FRESH1 | 17.195 s | 55.727 s | 140.379 s | 87 |
| INPUT_REVIEW_FRESH2 | 14.809 s | 59.702 s | 149.683 s | 138 |
| INPUT_REVIEW_FRESH3 (QEMU `8b46eb6`) | 17.877 s | 48.986 s | 135.645 s | 87 |

On FRESH1, right-click dispatched in 246.3 ms and visibly moved from lock
screen to Home. A Settings tap initially left Home visible. The frozen
capture contained live Preferences306 with its main thread in the normal
UIApplicationMain event loop and SpringBoard processing an FBS display-layout
transition. Resuming the same VM for roughly ten more seconds rendered
Settings (`ui/settings-later.png`). Thus this run disproves a universal
fresh-boot Settings launch crash. The analysis and resolved stacks are in
`/tmp/dvm/INPUT_REVIEW_FRESH1/settings-launch-analysis.md`.

A -3 wheel batch visibly scrolled that Settings list, dispatched in 696.8 ms,
and ended with no contact held. Right-click afterward dispatched both edges
in 297.2 ms but left Settings visible after three seconds and an additional
observation interval. Successful transport is not sufficient evidence for
reliable in-app Home behavior on this lineage.

FRESH2 right-click dispatched in 244.0 ms. Its checkpoint
`INPUT_REVIEW_HOME1` restored at the exact saved PC in `INPUT_REVIEW_RESTORE1`.
An initial tap dispatched successfully, but the restored kernel then panicked
with `AppleSEPManager panic for "AppleSEPKeyStore": sks request timeout`
(`checkpoints/INPUT_REVIEW_HOME1/restores/INPUT_REVIEW_RESTORE1/serial.log:2444`).
The host subsequently marked the guest lost, cleared queued input, and the
CLI rejected further input. This is a failed restore stability result; it
does not establish that the input caused the SEP panic. Helper recovery
cannot repair a panicked guest. This restore did not provide successful recovery proof. Separately, the
running FRESH1 later reported two failed button dispatches, then
`DVM_HID_RECOVER begin count=1` and `done attempt=1` with the same PID87
(`serial.log:29888..29900`). Both new services opened, epoch3 became ready,
and subsequent Home dispatches succeeded. No repeated recovery from stale
terminal notifications occurred. The framebuffer nevertheless remained at
presentation973, with continued IOMFB power toggling (which already occurred before Home). The user-visible frozen 6:40
clock was part of this stale frame, not proof that the RTC stopped. This
separates successful HID service recovery from unresolved display recovery.

On QEMU `8b46eb6` with helper v13, FRESH3 lock-to-Home dispatched in 350.0 ms. The first
Settings tap left Home visible; the second rendered Settings after a
12-second settle. A -3 wheel batch visibly scrolled the native list,
dispatched in 425.8 ms, and released contact with no errors. The clock
advanced from 6:56 on Home to 6:57 in Settings. Evidence is under
`/tmp/dvm/INPUT_REVIEW_FRESH3/ui/`; all input used QMP's normal pointer/key
handler. A manually operated Cocoa window was not independently verified by
the automation interface.

## Build and installation

All **74 host tests pass** (`/tmp/dvm/input-review-v14-tests.log`).
Tests include the real C queue/parser routines, replenished UART FIFO
capacity, duplicate/stale completions, timeout cancellation, in-flight touch
and Home cancellation, repeated cancel, queue overflow, and CLI false-success
regressions. The signed iOS cross-build and QEMU build pass.

The reviewed helper was installed by the restore guest into a new child of
`CLOCK_SOFTWARE_INSTALL1`; `INPUT_REVIEW_INSTALL2/result.json` records the
`DVM_HID_INSTALL_DONE` marker and seals the child read-only. The pinned source
for independent boots is `/tmp/dvm/native-input-reviewed-v14/warm-manifest.json`.
It retains the original disk lineage and trust cache plus the helper's new
CDHash. No kernel or Apple-userspace patch was added by this review.

Build/stage with the existing repository tools, using a new artifact directory
and tag for every candidate:

```sh
HELPER_SRC="$PWD/tools/input/dvm_hid.c" \
  BASE_TC=/tmp/dvm/CLOCK_SOFTWARE_PATCH1/system.tc \
  bash tools/input/build.sh /tmp/dvm/NEW_INPUT_BUILD
INSTALLER=install_hid_in_guest.sh INSTALLER_NAME=dvm-hid-install.sh \
  bash tools/input/prepare_ramdisk.sh /tmp/dvm/NEW_INPUT_BUILD
```

Copy the reviewed restore template and replace its QEMU and trust-cache paths
with the new pinned artifacts. Then run `tools/re/install_staged_helpers.py`
with `/libexec/dvm-hid-install.sh`, marker `DVM_HID_INSTALL_DONE`, and the
matching immutable parent. Use `tools/derive_warm_manifest.py` to pin the sealed
installed child, helper trust cache, reviewed QEMU and `DARWIN_INPUT_UART=1`.
All large disk attachments remain inside the restore guest; only the small
ramdisk is attached to the host through `safe_attach.sh`.
