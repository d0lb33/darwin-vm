# Native HID handoff after the warm-clock boot

Read `CLAUDE.md` in full first. Baseline pushed to origin/main:
darwin-vm `fbc53c9`, qemu-sptm `00c4a4c`. Work directly; the user asked
to stop delegating this investigation. Do not touch the separate GPU task,
its worktree, or its VMs.

## Objective and current verdict

Make native guest input initialize reliably and support ordinary taps,
drags/swipes, and Home without per-event LLDB assistance. Preserve the
working disk-boot display, RTC, battery, keybag, and Settings fixes.

The most recent fresh disk boot **did render successfully**. On
`WARM_CLOCK_SOFTWARE1`, Early boot was 11.783 s and first presentation
117.731 s, with no debugger endpoint, no RAM restore, and zero panics.
`/tmp/dvm/WARM_CLOCK_SOFTWARE1/settled-frame/final.png` shows the large
native clock (3:21), date, and full green battery. The prior 300-second
no-output run was `WARM_PERCENT_FRESH8`, an earlier candidate.

The latest boot did **not** pass input validation. Its serial has helper v6
PID 87, then replacement PID 215. Both logged INITIALIZING; neither has a
completed READY handshake in the observed interval. The Home swipe attempt
sent only the first buffered R down and received no ACK. The observer
paused the guest at 12 seconds, before the relay's 30-second deadline;
that timeout cannot independently establish a 30-second guest hang. A
further 25 active seconds still did not finish initialization. Do not
classify this as a successfully delivered swipe or retry it blindly.

## Start with the checkpoint

Newest diagnostic state:
`/tmp/dvm/checkpoints/CLOCK_SOFTWARE_INPUT_WAIT1/manifest.json`.
Capture quit the original VM and sealed its disk. Saved PC
`0xfffffff02ab136e0`; vmstate 3,414,322,469 bytes. Restore into a new child:

```sh
python3 tools/restore_checkpoint.py \
  /tmp/dvm/checkpoints/CLOCK_SOFTWARE_INPUT_WAIT1/manifest.json \
  --tag HID_STARTUP_DIAG1 --out /tmp/dvm/HID_STARTUP_DIAG1 \
  --observe-seconds 0 --leave-paused
```

Choose a new tag if that output already exists. Add `--gdb-port` with a
verified free port only if the diagnostic needs it. Read the generated
`launch.json` and restore report for this clone's HMP, UART, event-file,
and PID paths; do not use an older VM's socket. The checkpoint already
contains the helper. Do not spawn a competing console reader.

First recover the helper's current process/thread state, exact initialization
boundary, bootstrap/service-client state, and any original-process crash
record. Missing READY alone does not identify the blocking API. Tools:
`capture_warm_memory.py`, `warm_boot_postmortem.py`, `oskcdata.py`,
`frozen_objc.py`, and `sparse_warm_memory.py` under `tools/re`/`tools`.
The sparse reader is useful once a pmap and object addresses are known;
it cannot establish absence of processes or crashes. Full postmortems
overwrite `process-stacks.json`, so preserve distinct captures.

Keep diagnosis on RAM/device restores. Use fresh disk boots only after a
persistent fix is ready, for startup ordering and repeatability validation.
Fresh candidate manifest:
`/tmp/dvm/CLOCK_SOFTWARE_PATCH1/warm-manifest.json`.
Pinned QEMU: `/tmp/dvm/warm-runtime-qemu3/qemu-system-aarch64`.

## What input has actually worked

Implementation: `tools/input/dvm_input.c`, `relay.py`, `build.sh`, and
their staging/install scripts. Helper v6 binary:
`/tmp/dvm/warm-input-v6-native-tap/dvm-input`, SHA-256
`fae5c079f465447f8b52ea9f44440f0466f0f18d91f31e3195ce8197a359db12`,
CDHash `023cff3b44925a3dc16882553f7717f5b3b144b9`.

Transport is host pointer events -> relay -> UART -> native guest helper
-> Recap virtual HID service / IOHID dispatch. No physical touch-controller
MMIO model is established by this work.

* `P` / `relay.py --tap X Y` uses Recap's dedicated native tap generator.
  It visibly launched Settings and operated native Setup buttons. Coordinates
  are normalized 0..32767. ACK means playback queued, not UI completion.
* `R` / `--swipe X1 Y1 X2 Y2` buffers 20 moves of 20 ms and a lift.
  It scrolled Setup's country list and dismissed/reopened the cover sheet.
  Host UART latency therefore does not set the gesture's duration.
* `T` is the direct live digitizer path still used for ordinary host mouse
  events. Direct down/up once took 2.501/3.896 seconds and produced a
  long-press menu. Fixing the dedicated P path did not prove direct T taps
  or Cocoa clicking reliable. This is a key remaining distinction.
* `H` sends native consumer-page Home. Older controls worked, but one later
  diagnostic saw backboardd/SpringBoard restart after Home. Causality is
  unresolved; a working buffered cover-sheet swipe is a useful control.

Known-good v6 input control checkpoint:
`/tmp/dvm/checkpoints/POWERD_GUARD_BATTERY1/manifest.json`.
It includes persistent Settings/powerd fixes but predates the latest percent
bridge and clock patch. `POWER_PERCENT_ROOT4` was its disposable diagnostic
clone, with successful P taps and buffered swipes. That clone has diagnostic
LLDB state; prefer a fresh restore of the immutable checkpoint.

Native Setup control: `/tmp/dvm/checkpoints/NATIVE_SETUP_WIFI1/manifest.json`.
It reached Appearance, Quick Start, Written/Spoken Languages, and Wi-Fi with
native P/R input and no LLDB. Some Continue buttons were invisible but had
real measured geometry; do not equate invisible rendering with missing input.

## Traps and relevant adjacent failure

UART FIFO full/empty ambiguity was already fixed and tested in QEMU.
Launchd's development cache already contains the input job; v6 explicitly
opens `/dev/console`, holds a singleton lock, and runs a main dispatch loop.
Do not restart by assuming the job is absent or stdin still uses the old EOF
path. Native service registration checks the underlying non-null
HIDVirtualEventService `serviceClient`; direct posting checks its BOOL.

`relay.py --events` is an **existing input event file**, while `--log` is a
new relay diagnostic log. Do not pass the guest serial log as `--log`.
Bound the VM's running interval long enough to observe helper initialization
and the relay deadline. With LLDB attached, resume through LLDB and then use
`observe_warm_boot.py --already-running`; HMP cont alone does not reliably
release a debugger stop. HMP quit the owned clone before LLDB teardown:
detaching LLDB can resume a guest that was HMP-paused.

Chronod remains an independent readiness failure worth correlating:
`WARM_PERCENT_FRESH8` corpse PID 118 aborted in QuartzCore `query_displays`,
return PC static 0x1844e4eb4, with saved error **0xfb294002**. Query explicitly
retries display-server refusal until a five-second deadline. The stack is
Chronod bootstrap -> SwiftUI environment -> UIScreen initialization ->
CADisplay -> query_displays -> abort. Evidence is in
`/tmp/dvm/WARM_PERCENT_FRESH8/chronod-corpse-stack.json` and
`chronod-corpse-symbols.txt`. Do not assume the helper has the same cause
until its own stack/crash proves it; do not bypass display registration.

The large clock's separate rendering failure is already isolated: native
UIKit label **and** non-glass styling are both necessary on this VM. The
persistent opt-in patch has one fresh-boot display pass. Preserve it during
input work; GPU implementation belongs to the user's separate task.

Acceptance evidence: automatic READY, native service registration, transport
ACKs, visible ordinary tap/swipe/Home results with no debugger assists, and
repeatability on fresh disk boots after the persistent changes. Report each
layer separately. All 50 host tests and 2 input tests passed at handoff.
See `docs/re/warm-boot-stability.md` for current status; older sections of
`docs/re/native-input.md` are historical and may describe obsolete helpers.
