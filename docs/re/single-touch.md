# QEMU single-finger diagnostic bridge (24A5430a)

`DARWIN_TOUCH_EVENTS=/tmp/dvm/TAG.events.jsonl` enables a QEMU absolute
mouse/left-button handler in `darwin_fb.c`. The Cocoa window remains resizable.
Coordinates use QEMU's 0..32767 absolute range. Input is committed at the
handler's sync boundary because Cocoa queues the button before its coordinates.
The append-only JSON contains host milliseconds, x, y, and full button state.
This is a host diagnostic input bridge, not an emulated Apple touch controller.

On an LLDB-connected warm Home restore, install the normal Home callbacks and:

```text
command script import tools/re/touch_bridge.py
script touch_bridge.install(lldb.debugger, '/tmp/dvm/TAG.events.jsonl')
continue
```

The file must be unique to the run. Capture a mouse gesture and release the
button: the current prototype replays the completed gesture, rather than
tracking the finger live while held. It queues up to eight gestures and samples
drags to at most twelve moves. Recap performs native single-finger playback.
LLDB must stay attached; disabling its observer disables the bridge.

## Native path and runtime evidence

All addresses below are unslid dyld-cache addresses; R21 uses slide 0x14f94000.
`RCPSyntheticEventStream` is allocated through `objc_alloc_init` (0x180409c0c).
The explicit touchscreen sender comes from 0x29b208234 using the guest's
`BKSDisplayUUIDMainKey` NSString at pointer 0x1e080b360. The stream stores this
sender through 0x29b214560. Taps use 0x29b20041c; down, move and up use
0x29b20069c, 0x29b2006fc and 0x29b20072c. The event buffer is finalized at
0x29b200040 and assigned through 0x29b209230. Playback options explicitly set
the display UUID (0x29b2056c0), then `RCPInlinePlayer` plays through 0x29b1fcdd8.

`/tmp/dvm/TOUCH_INPUT_R21.lldb.log` records the trace. HID virtual-service
dispatch returned 1; BackBoard's client connection dispatch returned 0 (success)
at 0x22a35623c. Events reached UIKit's fetch callback (0x184e3619c), transformer
(0x184e36a98), and SpringBoard `sendEvent:` (0x224325968). Absence of a hit at
SpringBoard `_handleHIDEvent:` alone was not evidence of failed delivery.

The original hardcoded 393x852 mapping was wrong. R21's native stream ivars,
located using offset globals 0x2cc7a7bfc, 0x2cc7a7c08 and 0x2cc7a7c0c, report
screenSize=1179x2556, gsScreenPointSize=1179x2556 and gsScreenScaleFactor=1.
The bridge now reads screenSize from each stream. Before the fix, Settings
input became (46,85) at BackBoard hit testing (0x22a414e48), then (23,42.5)
at `_UISystemGestureWindow hitTest:withEvent:` (0x184e40dc0). After the fix,
gesture 13 reaches that window at (69,127.5), then `SBIconView touchesBegan:`
(0x1c4b0c820) and `tapGestureDidChange:` (0x1c4b0ec04). The icon visibly
highlights in `/tmp/dvm/TOUCH_INPUT_R21.settings1.png`. App launch completion
and drag scrolling still need their own runtime witnesses.

The nil `BKDirectTouchState+0x28`/`isActive` result was a false lead: its branch
continues hit processing. No display-state object is fabricated to override it.

## Keeping the run responsive

The bridge installs a native 0.2-second NSTimer on SpringBoard's main run loop,
targeting its read-only `isShowingHomescreen` selector. At the CF observer
0x1806544e8 it stages native calls, preserving general registers, SIMD registers,
flags and stack. Every five host seconds it calls the native idle timer reset
(0x2242fa85c). This is a lab keep-awake policy, not a hardware power-button model.

Saved raw PC/SP registers are authoritative. R20's cached `SBFrame.GetPC()`
reported 0x195614724 while `FindRegister('pc')` reported the true observer
0x1955e84e8. Using the former as LR caused a probe-induced stall after guest
thread migration. R21 uses raw register snapshots and matches returns by guest
thread pointer plus raw SP. Host tests reproduce the stale metadata mismatch.
Do not reload the whole Python module during an injected call; it owns state.

R21's clock hand differs between `TOUCH_INPUT_R21.clockA.png` and `clockB.png`.
It also returned successfully from the previously failing User-class-2 SKS
request; see `sks-user-class2-home-freeze.md`. Debugger stop points still freeze
all guest animation. QEMU saying `running` alone never proves guest liveness.

Use the immutable `DISPLAY_SMP6_HOME13` baseline for restores. A checkpoint
taken after installing the timer contains that timer; blindly reinstalling the
bridge would duplicate it. Timer rediscovery and live streaming remain open.
