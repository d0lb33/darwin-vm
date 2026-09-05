# Warm booting iOS display without LLDB

## Goal and starting point

The next task is to make a six-core iOS warm boot reach a visible Setup,
Lock, or Home screen with **no guest LLDB intervention**.  Do not restart from
a cold Data migration: use a migrated-disk checkpoint and a fresh unique
restore tag.  The current native-input lineage has a verified Home fallback at
`/tmp/dvm/checkpoints/NATIVE_HOME_UART_FIXED1/manifest.json`; the earlier
pre-Setup checkpoint is
`/tmp/dvm/checkpoints/TOUCH_NATIVE_CURSOR1/manifest.json`.  They already carry
the development image and avoid the long first-boot attribution pass.

The QEMU-side D594 completion ordering fix is a real model fix, not a debugger
workaround.  It is required for continued visible updates after a warm restore:
the old asynchronous D594 caused an IOMFB power/completion lock cycle.  Its
protocol derivation and migration validation are in
`docs/re/display-completion-deadlock.md`.

`DISPLAY_NESTED_R3` proved that an already-visible restored Home screen can
continue presenting after all LLDB breakpoints are removed and LLDB detaches.
It does **not** prove that a fresh warm boot can allocate its initial software
surface or enter the visible display state without LLDB.

## LLDB actions used during the successful display sessions

The following were used in the native-input/Setup lineage.  They must be
treated as individually removable development accommodations, not as evidence
that the guest boots independently.

1. Install the display allocation/watchdog callbacks after resolving the
   runtime shared-cache slide:

   ```lldb
   command script import tools/input/display_callbacks.py
   script display_callbacks.install(lldb.debugger, SHARED_CACHE_SLIDE)
   ```

   This installs two narrow SpringBoard/FrontBoard callbacks:

   * `surface_cache_probe.ENABLE = True` changes the observed requested
     software-surface cache mode from `0x400` to `0x700` while a surface is
     allocated.  The cache-mode derivation and early display evidence are in
     `docs/re/surface-cache-and-completion.md`.
   * `frontboard_watchdog_callbacks.EXTEND = True` lengthens the FrontBoard
     startup watchdog.  It avoids killing the UI while TCG is slow; it does
     not make a display surface or submit a frame.

2. For the stalled Setup launch only, install the ringer-cache diagnostic:

   ```lldb
   command script import tools/re/cached_audio_probe.py
   script cached_audio_probe.install(lldb.debugger, SHARED_CACHE_SLIDE)
   ```

   It intercepts `-[SBAVSystemControllerCache isRingerMuted]` at shared-cache
   static address `0x22463c690` (iOS 27, 24A5430a), returns the receiver's
   existing byte at `+0x61`, and skips a synchronous audio-queue/XPC wait.  It
   neither writes that cache nor emulates audio.  In `NATIVE_SETUP_UART_R2`
   the cached value was `0`.  The source is `tools/re/cached_audio_probe.py`.

3. When iOS had powered the panel off, issue exactly one native SpringBoard
   unblank call from a verified SpringBoard callback:

   ```lldb
   command script import tools/re/native_home_probe.py
   script native_home_probe.install(lldb.debugger, SHARED_CACHE_SLIDE, unblank=True, return_home=False)
   ```

   The probe calls `_BKSDisplayServicesSetScreenBlanked(false)` at static
   `0x18a1330d4`; at `0x18a133138` that argument reaches
   `_BKSDisplayNotifySetDisplayBlanked`.  It preserves the interrupted
   SpringBoard frame and disables its breakpoint after the call returns.
   This made the physical Cocoa framebuffer move from a powered-off/black
   state back to Lock/Home during the recorded session.  It is a manual UI
   action, not a substitute for a display-power implementation.

4. To spawn Setup from an already-running SpringBoard, the same one-shot probe
   can follow the native SpringBoard path:

   ```lldb
   script native_home_probe.install(lldb.debugger, SHARED_CACHE_SLIDE, setup=True, return_home=False)
   ```

   It follows `applicationController` (`0x224287cd8`) -> `setupApplication`
   (`0x2243bf920`) -> `_SBWorkspaceActivateApplication` (`0x22433ff18`).
   The activation can restart SpringBoard before the injected call returns.
   In that case **never restore the saved registers**: clear the pending
   injected-call state and record the abandoned call, as was done in
   `/tmp/dvm/NATIVE_SETUP_UART_R1.abandoned-activate.json`.

The Home-auth diagnostic was also present in some runs.  Do not carry it into
a debugger-free boot claim: it bypasses passcode/authentication for development
UI exploration and is unrelated to creating display output.

## Recommended debugger-removal experiment

Use one warm restore per row and leave its disk image untouched.  A failure
should be captured with a frame dump, serial tail, QEMU stderr tail, and the
last presentation count before changing another row.

| Variant | LLDB retained | What it establishes |
|---|---|---|
| A | all current accommodations | Control: current Setup/Home behavior |
| B | D594 model only; no breakpoints | Whether the saved visible state keeps rendering |
| C | surface-cache + watchdog only | Whether ringer bypass is specific to Setup audio startup |
| D | watchdog only | Whether the cache-mode rewrite is needed for the first surface |
| E | no guest LLDB | Actual warm-boot result |

Before detaching, delete/disable every guest breakpoint and ensure no
`touch_bridge.STATE` / native-call state is active.  A debugger stop freezes
all virtual CPUs and can make a healthy frame look like a compositor freeze.
Use condition-bounded QMP observation rather than a long sleep.  The display
and touch callbacks are particularly expensive because breakpoints force
single-instruction translation on their pages.

For every variant, keep the visible QEMU configuration:

```sh
--display cocoa,zoom-to-fit=on,show-cursor=on
```

and use the fixed UART QEMU build used by the native helper.  The UART's
16-byte full/empty bug is fixed in `hw/char/exynos4210_uart.c`; the helper and
relay are unrelated to first-frame allocation, but they are needed when testing
actual QEMU mouse input after the UI becomes visible.

## Evidence and pitfalls

* Do not describe a relay ACK as a visible touch result.  The evidence for
  native input is a frame change (Home grid -> Search -> Home) plus the helper
  ACK log; see `docs/re/native-input.md`.
* A black Cocoa view can be an iOS display-power-off state.  QEMU can remain
  running while the guest logs display power state `0` and `AP stopped the
  IOP`.  The unblank call above is diagnostic evidence, not a general fix.
* If Setup activation restarts SpringBoard, an injected call cannot safely
  return.  Do not resume a stale stack/register image on a different thread.
* Keep the QEMU D594 nested-completion state migration-compatible; its optional
  state must be present if capture occurs inside an outstanding completion.
* The old fully rendered language picker checkpoint predates the native helper.
  It is useful as a visual control, but not as an input-test target.

The target completion criterion is simple: restore a migrated, pre-Setup or
Home-adjacent checkpoint under the final QEMU binary, attach no guest LLDB,
and collect two visibly different Cocoa frame dumps while the VM remains
running.  Then test one real Cocoa click through the native input relay and
record its corresponding visible UI transition.
