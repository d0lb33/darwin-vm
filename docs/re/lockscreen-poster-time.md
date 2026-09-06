# Lockscreen poster time: level ownership and graphics boundary

This note records a frozen, read-only inspection of the iPhone17,3 / T8140
24A5430a lockscreen poster path. It corrects an earlier hypothesis that the
absent large clock might follow from unbound foreground or floating poster
hosts.

## Time is an independent poster level

The extracted `PosterKit` image has these signed `PRPosterLevel` constants in
its `__TEXT,__const` section:

| Constant | Address | Value |
| --- | ---: | ---: |
| `PRPosterLevelBackdrop` | `0x1a91bf4c0` | -3000 |
| `PRPosterLevelBackground` | `0x1a91bf4c8` | -2000 |
| `PRPosterLevelForeground` | `0x1a91bf4d0` | -1000 |
| `PRPosterLevelFloatingUnder` | `0x1a91bf4d8` | -500 |
| `PRPosterLevelTime` | `0x1a91bf4e0` | 0 |
| `PRPosterLevelFloating` | `0x1a91bf4e8` | 1000 |

`PaperBoardUI`'s
`-[PBUIPosterLockViewController _updatePosterLayers]` at `0x201edef20`
recognizes `1000` and `-500` for `_realFloatingView`, `-1000` for
`_realForegroundView`, and `-2000` for `_realBackgroundView`. It has no
level-zero branch. The corresponding ivar symbols are at `0x26c1239f8`,
`0x26c1239f4`, and `0x26c1239f0`, respectively.

Therefore the local `PRPosterLevelTime` renderer is not configured through
those three remote scene-layer hosts. In particular, an unbound foreground or
floating host must **not** be used as an explanation for a missing large
clock.

`PosterUIFoundation` supplies the explicit local time container:

- `-[PUIPosterLayoutView setTimeView:]` is at `0x1b384fe44`.
- Its `_timeView` ivar symbol is at `0x1e54aeb38`; the separate
  `_timeContainerView` symbol is at `0x1e54aeb14`.
- The method only replaces the view, asks the layout view to lay out, and adds
  it to that container. It contains no display-scale getter, IOSurface call,
  Metal call, or `IFGraphicsContext` reference.
- `-[PUIPosterLayoutView layoutSubviews]` at `0x1b384fab0` propagates the
  layout view's bounds to its child containers. It likewise has no direct
  Metal, IOSurface, `UIScreen`, or display-scale call.

This establishes ownership of level zero and narrows the large-clock issue to
creation/attachment or later rendering of the local time view. It does not
identify that downstream renderer.

## Frozen runtime evidence

`CLOCK_POSTER_DIAG4` was stopped before this inspection. SpringBoard's
`PBUIPosterLockViewController` had its background host attached to scene
`0x79423b0600` with context ID `0xf6f18c4c`; foreground and floating hosts had
nil scene/presenter and context ID zero. The static level mapping above makes
that state compatible with ordinary wallpaper composition rather than a
clock-specific failure.

The title configuration was present:
`PRImmutablePosterTitleStyleConfiguration` held a
`PRPosterSystemTimeFontConfiguration` with identifier
`PRTimeFontIdentifierSoft`. This excludes a missing time-font configuration
from the observed state.

The `PRRenderingService` endpoint target PID was 35, verified as SpringBoard
from its current `proc` record. PosterBoard (PID 279) and ClockPosterExtension
(PID 342) were also live in the same frozen process list. Liveness alone does
not prove that either process created the time view.

Saved read-only artifacts are outside the source tree:

- `/tmp/dvm/checkpoints/WARM_DEV_LOCKSCREEN1/restores/CLOCK_POSTER_DIAG4/poster-level-host-mapping.txt`
- `/tmp/dvm/checkpoints/WARM_DEV_LOCKSCREEN1/restores/CLOCK_POSTER_DIAG4/frozen-ivars.txt`
- `/tmp/dvm/checkpoints/WARM_DEV_LOCKSCREEN1/restores/CLOCK_POSTER_DIAG4/processes-prev.txt`

The paused clone was quit through HMP before its LLDB client was terminated.
No guest memory was written and no guest execution was resumed.

## Graphics and scale result

`PosterUIFoundation` as a whole imports `Metal`, `IOSurface`, and
`UIRoundToScreenScale`; its image utility and snapshot code uses those
facilities. `PosterBoardUIServices` imports the
`kPaperboardIOSurfaceDeviceScalePropertiesKey` and has a `displayScale`
selector stub. These image-level imports do not appear in the local
`PUIPosterLayoutView` time attach/layout methods above.

Neither `PosterUIFoundation`, `PosterBoardUIServices`, nor `PaperBoardUI`
contains an `IFGraphicsContext` symbol or string in this 24A5430a static
scan. The available evidence therefore does not connect the generic zero-size
`IFGraphicsContext` bitmap result, or Settings' 28x28 image descriptor scale,
to the local lockscreen time container.

A future runtime probe should resolve the process and implementation that
calls `setTimeView:` and then inspect the passed view's bounds and backing
layer at attachment. That is the first point where a renderer, layer, or
scale dependency can be tied to the missing clock without changing flags or
host bindings.

## Reproduction commands

The static evidence was extracted without running a guest:

```sh
ipsw dyld extract /Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e \
  /System/Library/PrivateFrameworks/PosterKit.framework/PosterKit --objc -o /tmp/dvm/extracted-posterkit
ipsw dyld extract /Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e \
  /System/Library/PrivateFrameworks/PaperBoardUI.framework/PaperBoardUI --objc -o /tmp/dvm/extracted-paperboard
ipsw dyld extract /Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e \
  /System/Library/PrivateFrameworks/PosterUIFoundation.framework/PosterUIFoundation --objc -o /tmp/dvm/extracted-puif
nm -nm /tmp/dvm/extracted-puif/PosterUIFoundation | rg 'PUIPosterLayoutView|_time(View|ContainerView)'
```

## Root follow-up: explicit scene and retained date-view graph

The earlier clock breakpoint arithmetic was wrong by 0x2000: with cache
slide 0x150e0000, `PUIPosterLayoutView setTimeView:` is 0x1c892fe44 and
`layoutSubviews` is 0x1c892fab0. The earlier 0x1c892de44/0x1c892dab0
addresses do not identify those methods. The SpringBoard application must
also be read through its explicit pmap, not an arbitrary CPU's UIApp global.

In the later fresh `WARM_POWERD_GUARD1`, SpringBoard PID 35 has pmap root
0x1001ca02000 and UIApp 0x7d031c8000 (cache slide 0x0dafc000). The
wallpaper scene's `FBExtensionProcess._pid` is 296, MercuryPosterExt, while
`_hostPID` is 35. Host PID is not the renderer PID. ClockPosterExten PID 349
is a separate process. Neither this relationship nor a missing exact
PUIPosterLayoutView allocation proves who draws the large clock.

The retained cover-sheet graph is concrete: SBLockScreenManager
0x7d02fb16c0 -> SBCoverSheetPresentationManager 0x7d03a86400 -> sliding
controller 0x7d033f5500 -> CSCoverSheetViewController 0x7d031c9e00 ->
SBFLockScreenDateViewController 0x7d032da800 ->
CSProminentDisplayViewController 0x7d02e23000 -> CSProminentDisplayView
0x7d03af5900. Its `_timeView` (+0x1c8) is nil; `_subtitleView` (+0x1d0)
is a CSProminentSubtitleDateView. The controller's suppression dictionary
retains the native key `bespokeTimeSuppression`.

**This capture is from Settings with the cover sheet hidden.**
`CSCoverSheetViewController _updateDateTimeView` at 0x223e8b60c explicitly
considers `_appearState` before calling `setHidesTime:` at 0x223e8bb44.
Consequently these bytes do not establish the cause of a missing clock on
a visible lock screen. Capture the visible cover sheet before changing
policy. `tools/re/cache_objc_calls.py` resolves the actual shared selector
stubs; nearest-symbol labels on rewritten dyld stubs are not trustworthy.

Artifacts: `/tmp/dvm/WARM_POWERD_GUARD1/clock-*.json`, `coversheet-*.json`,
`scene-context.json`; exact disassembly under `/tmp/dvm/CLOCK_VIEW_RE1`.

## Visible clock: native label and material isolation

The `POWER_PERCENT_ROOT4` restore of `POWERD_GUARD_BATTERY1` supplies
visible-cover evidence. Its retained `CSProminentDisplayView` creates a
`CSProminentTimeView` with bounds 589.5 by 156 and a
`CSTitleElementViewAdapter` backed by a SwiftUI hosting controller. The
controller uses `CSGlassContentStyleRenderer`. Thus the earlier offscreen
nil time view was not the missing-clock cause. This object graph establishes
the local time renderer; it does not establish a chronod/widget dependency.

`tools/re/clock_label_callbacks.py` tests two existing native fallbacks on
the disposable restore, without changing guest code or date values:

* Redirect `+[CSProminentTextElementView textLabelClass]` at 0x1b38d1394
  to its existing `_UIAnimatingLabel` branch, 0x1b38d13b4.
* At 0x1b38d67f4, change the leaf
  `CSGlassContentStyleRenderer _supportsRenderingStylesWithGlassMaterial`
  result from one to zero. Its native base class returns zero at
  0x1b38d6874. The ordinary caller selects and applies vibrant styling.

| Native label | Glass capability | Visible result |
| --- | --- | --- |
| SwiftUI adapter | Original | Date visible, large time absent |
| UIKit label | Original | Large time absent (`clock-native-label/final.png`) |
| UIKit label | False | Large white **2:00** (`clock-no-glass/final.png`) |
| SwiftUI adapter | False | Large time absent (`clock-glass-only/final.png`) |

All screenshot paths are under `/tmp/dvm/POWER_PERCENT_ROOT4`. Each change
was exercised by native buffered swipes to dismiss and recreate the visible
cover sheet. Frozen pmap reads verify the actual label classes in
`native-label-state.json`, `no-glass-state.json`, and `glass-only-state.json`.
The two successful fallback selections therefore isolate a rendering-path
problem, rather than missing time data. Missing GPU acceleration is a
supported hypothesis, but this does not identify a particular Metal call or
prove that implementing GPU acceleration alone fixes it. Neither fallback
alone was sufficient. These are debugger-assisted snapshot results, not
persistent-fix or fresh-boot validation.

### First persistent disk validation

The guarded spec `tools/input/clock_software_patch_24A5430a.json` changes
subcache `.13` at file offsets 0x34d1394 (branch to native label fallback)
and 0x34d67f0 (glass capability returns zero), with 16-byte preimage guards
and rehashed code pages. `CLOCK_SOFTWARE_INSTALL1` applies it to a sealed
child of `POWER_PERCENT_INSTALL8`; the restore guest confirms
`DVM_CACHE_PATCH_INSTALLED` at serial offset 41141.

`WARM_CLOCK_SOFTWARE1` independently boots that disk with no GDB endpoint
and no restored RAM. `result.json` reports Early boot 11.783 s, first new
presentation 117.731 s, zero panics. The first frame catches the initial
fade; a bounded 15-second continuation renders the native colored **3:21**
clock and date (`settled-frame/final.png`). The clock therefore has one
positive persistent disk-boot validation. Repeated disk boots, complete UI
stability, and chronod startup are separate remaining gates.
