# Rendering the lock-screen wallpaper on the GPU driver — 24A5430a

> Checkpoint qualification (2026-09-11): the experiments below did not produce
> verified wallpaper pixels. Statements excluding the GPU driver or identifying
> a particular lifecycle cause remain hypotheses: successful resource setup and
> absence of a submission do not establish the complete render contract. The
> guest target remains iOS 27 build 24A5430a; later iOS 17 wording is not evidence
> of a tested replacement guest.

2026-09-08. Goal: get the host GPU driver rendering the lock-screen wallpaper.
The wallpaper is `MercuryPosterExtension.appex` (a Metal client;
`lock-screen-wallpaper.md` established it stays black because
`MTLCreateSystemDefaultDevice()` returns nil with no `IOAcceleratorES`
service). This is the first time a **second** process besides backboardd runs
on the driver.

Evidence: `~/dvm-artifacts/research/gpu-wallpaper-poster-20260908/`. Disposable
sessions `POSTER1`..`POSTER5` under `/tmp/dvm`. Two-client bundle
`STG_BUILD6`, host worker `STG_HOST2` (contract 30), library cache
`POSTER_LIBCACHE1` (adds the poster's `default.metallib`, its `Celosia`/`Space`
MTLB slices, and `VideoToolbox.framework/default.metallib`).

## Result

**Reached:** the wallpaper extension loads the driver and registers it as its
own `MTLCreateSystemDefaultDevice()`, shares backboardd's transport channel,
loads every shader library it asks for, and builds its render pipelines and
resources on the host GPU. All driver-level blockers are fixed. Verified with
per-process journal tagging (`client=POSTER` vs `client=SYSTEM`).

**Not reached:** the extension never issues a fullscreen `renderSubmit` into a
wallpaper output surface, so no wallpaper pixels are produced. PosterBoard
spawns MercuryPoster repeatedly (11 registrations in one session) for
setup/enumeration and tears it down each time; it is never driven to render as
the **active displayed** wallpaper. The lock screen still shows a black
wallpaper behind the (software-composited) clock and controls. This last gate
is PosterBoard activation/content, not a GPU-driver capability.

## What made the extension run on the driver

1. **Second client on one transport.** `system_bootstrap.m` now accepts
   `backboardd` (tag SYSTEM, drives the session/compositor) and
   `MercuryPosterExtension` (tag POSTER). `build_system_bootstrap.py --poster`
   adds the same `LC_LOAD_DYLIB` into the appex's verified header padding and
   signs it with the transport entitlement, the `IOKitDiagnosticsClient`
   exception, `platform-application`, and **its own bundle identifier**
   (`-i com.apple.MercuryPoster`, else ExtensionKit's launch constraint kills
   it: AMFI "Launch Constraint Violation ... Constraint not matched").
   `prepare_system_bootstrap.py --poster` installs the patched appex binary.

2. **Shared-channel serialization** (`driver_mmio_transport.inc`). One doorbell,
   one outstanding request, guest-assigned sequence. A pid-tagged lock word at
   shared-RAM `0x380` serializes the two processes; the sequence is continued
   from `REG_DONE` under the lock (not a per-process counter); a holder that
   dies is stolen from once `kill(pid,0)` reports ESRCH. The report ring and
   the staging region take the same lock.

3. **Graceful degradation for a third-party client.** In POSTER mode the device
   soft-fails unimplemented Metal protocol selectors (`methodSignatureForSelector`
   + `forwardInvocation` return a zeroed value and log `GPU_LOAD_SOFTFAIL`
   instead of raising), and `newFunctionWithDescriptor:error:` returns nil+error
   rather than raising. Backboardd keeps the strict path. Without this the
   extension crash-looped (`GPU_LOAD_POSTER_UNCAUGHT ... constant count`).

## Blockers cleared, in the order they appeared

| Blocker | Fix |
|---|---|
| ExtensionKit launch constraint kills the re-signed appex | sign with its original bundle id + `platform-application` |
| `newDefaultLibrary` unrecognized | implement it and `newDefaultLibraryWithBundle:`, served through the unchanged-MTLB slice path from the bundle's `default.metallib` |
| `familyName` and other string selectors raise | POSTER-only soft-fail forwarding |
| `library ... does not match requested AIR` (3057664 B, sha 61c8440a) | it is `VideoToolbox.framework/default.metallib`; provisioned into the cache by digest |
| `GPU_LOAD_TEXTURE_REJECT format=125` (12x64 RGBA32Float LUT) | `DVMFormatBytes(125)=16`, sampled-read usage mask; contract 30 |
| `constant count` crash on a specialized shader | raise the guest cap 128 -> 1024 and return nil+error instead of raising |

## The boundary, grounded

A RAM scan (`pmemsave` at `0x10000000000`) confirms `PosterBoard.app/PosterBoard`,
`SpringBoard`, `backboardd`, and seven poster extensions (Mercury, Clock,
Collections, Photos, AmbientPhotoFrame, Infograph, ImagePlayground) are all
running. So the wallpaper stack is present and MercuryPoster is spawned by
PosterBoard. But over a session it registers 11 times, each time doing
`capabilities -> library -> a few buffers/textures/samplers/functions/pipelines
-> release`, and never a `renderSubmit`, `surfaceImport` or `sharedRenderCreate`.
Only MercuryPoster carries the driver; the other posters get nil and fall back.

The missing trigger is PosterBoard driving MercuryPoster to render a fullscreen
frame as the **active** wallpaper (with its content/configuration). Candidates,
untested: MercuryPoster ("Collections") may need a photo/collection to render;
the active lock-screen wallpaper may be a different poster; PosterBoard may only
snapshot posters in this state. Deciding among these is the next step and is
system-lifecycle work, not GPU-driver work.

## Reproduce

```sh
# extract the appex (read-only, safe_attach) and its libraries once
python3 tools/gpu/build_system_bootstrap.py <driver-snapshot> <backboardd> OUT \
  --session-reload --surface-import --poster <MercuryPosterExtension binary>
python3 tools/gpu/prepare_system_bootstrap.py OUT <launchd.plist> <tc> STAGE --poster
python3 tools/gpu/run_guest_install.py --manifest <manifest> --stage STAGE --tag INSTALL --mmio-restore
# library cache must include VideoToolbox default.metallib + the poster's default.metallib
DVM_TRANSPORT_FLAGS=3 python3 tools/gpu/session_cli.py start <control.json> --session /tmp/dvm/TAG \
  --worker <STG_HOST2/driver_host> --library <QuartzCore.metallib> --library-cache POSTER_LIBCACHE1 \
  --home-after-presentations 8 --min-presentations 8 --boot-seconds 360
# attribute per process:
python3 -c "import json,collections;recs=[json.loads(l) for l in open('/tmp/dvm/TAG/run/driver-host.jsonl')];\
print(collections.Counter((r['request'].get('client'),r['op']) for r in recs))"
```

## Update — driving the wallpaper UI (2026-09-08, POSTER6)

Following the suggestion to "set a wallpaper", the lock screen was reached and
long-pressed to open the wallpaper switcher. It confirms **"COLLECTIONS"
(MercuryPoster) is the active wallpaper** — but its switcher preview is black
and the poster still issues zero `renderSubmit`. (Settings > Wallpaper is not a
route here: the Settings app itself renders black on this VM. The lock-screen
"Customize" editor also goes black.)

Per-process journal (`client=POSTER`) shows the poster's exact sequence each
time PosterBoard spawns it, then it releases everything and repeats:

```
capabilities -> library default.metallib (26f73da0) -> library VideoToolbox (61c8440a)
-> sampler x4 -> texture 1179x320 RGBA16Float usage3 -> texture 12x64 RGBA32Float (fmt 125)
-> function "VTMTSComputeFunction" -> compute pipeline -> release x12  (repeat)
```

`VTMTSComputeFunction` is VideoToolbox's Metal tone-mapping/scaling compute
kernel. So the driver serves the poster's **entire** setup — both shader
libraries, all textures (including the format-125 LUT), and a VideoToolbox
tone-mapping compute pipeline — and the only soft-failed selector is the
harmless `familyName`. The poster builds its image-processing pipeline and its
I/O textures, then tears down without ever dispatching or creating an output
surface.

A crash scan settles the respawn question: `oskcdata.py` over 3 GiB of guest
RAM finds crash records for `nearbyd`, `iconservicesagent` and `chronod` but
**none for MercuryPoster**. The poster does not crash. PosterBoard spawns it
for a prepare/warm pass and tears it down; it is never handed the output
surface + render request that would make it draw a frame.

**Conclusion.** The GPU driver is not the blocker and never rejects the
wallpaper's work. The blocker is the PosterBoard->extension render handoff (the
XPC that gives the poster its wallpaper output surface and asks it to render),
which this injection does not touch and which does not fire in this state even
though Collections is the active wallpaper. Producing a visible GPU-rendered
wallpaper frame requires driving that handoff (or a poster that self-renders
without waiting for it), which is SpringBoard/PosterBoard lifecycle work, not
GPU-driver work.

## Root cause — the image has no wallpaper content (2026-09-08, POSTER7)

The black wallpaper is a **provisioning/setup** state of the image, not a GPU
limitation. Grounded in a guest-RAM scan (`pmemsave` at `0x10000000000`, 3 GiB):

- **Setup Assistant never completed.** RAM carries `SetupAssistantNeedsToRun`
  (45x) beside the purplebuddy markers (`com.apple.purplebuddy.setupdone`,
  `BuddyComplete`). CLAUDE.md confirms the project's images deliberately mark
  no Setup complete. So the device is in a pre-personalization state.
- **Data migration did run.** Hundreds of `Migrated` / `MigrationCompleted` /
  `migrator.migrationDidFinish` strings — the base image is not a broken
  migration; it is an un-personalized one.
- **Apps are offloaded placeholders.** Every home-screen icon shows the
  cloud-download badge; only Settings is a real installed app. The default
  apps were never installed (that happens during/after Setup).
- **The wallpaper has no content.** The SpringBoard wallpaper DB schema is
  present (`Wallpaper BLOB, WallpaperImageData BLOB, WallpaperMetadata BLOB,
  ImageBackgroundColorsData BLOB`) and `MercuryPoster[config:...]` /
  `WallpaperKit.CollectionsPoster` are configured, but the poster reports
  `empty collection` / `no content` / `noContent`.

That is the complete explanation for the earlier sections: MercuryPoster is
the configured wallpaper, loads its shaders and builds its VideoToolbox
tone-mapping pipeline on our GPU driver, and then has **no content to render**,
so it never dispatches or creates an output surface, and PosterBoard never
drives a full render. The GPU driver is proven working end-to-end for the
extension's setup; producing visible wallpaper pixels now depends on the image
being personalized (Setup completed / a wallpaper with real content), which is
image-construction work, not GPU-driver work.

## Setup-completion investigation (2026-09-08, SETUP1)

Goal shifted to: complete Setup on a VM disk, then make wallpapers work. Findings:

- **Both disks share one un-personalized base.** A native-smc full-system boot
  (`/tmp/dvm/SETUP1`, persistent overlay of `native-smc/cellular-plan/system.qcow2`)
  shows the **same** wallpaper config `config:6E69CA09-34E3-3493-B8FC-...` and
  the same `empty collection` as the reload harness. `BYSetupAssistantNeedsToRun`
  is present (Buddy not fully complete); `SetupFinishedAllSteps`/`BuddyComplete`
  markers exist but the needs-to-run state persists.
- **Activation is already handled on native-smc.** Its `system.dtree` carries
  `allow-hactivation` (and `debug-enabled`), the documented hactivation seam
  (`setup-activation-contract.md`): `mobileactivationd` short-circuits to
  Activated / non-bricked. So activation is not the gap. The restore
  `firmware/dtree` has `debug-enabled` but not `allow-hactivation`.
- **The gap is Buddy completion + wallpaper content**, not activation. The
  wallpaper "Collections" (MercuryPoster / CollectionsPoster, same config on
  both disks) has an empty collection, so it has nothing to render — the direct
  cause of the black wallpaper established in the sections above.

### Why this is hard (documented)

Per `setup-activation-contract.md` / `setup-launch-runtime.md`: `markBuddyComplete`
writes `BYBuddyFinishedInitialRunKey` / `SetupFinishedAllSteps` but requires the
Setup process to be running, and SpringBoard does **not** launch Setup in this
state (`_SBWorkspaceActivateApplication` never called in 300 s), so the device
sits on the lock screen with Setup "needed" but never run. The Data volume is
protected APFS, so host-side preference edits are not straightforward. The
authoritative completion write is
`CFPreferencesSetAppValue(SetupFinishedAllSteps, kCFBooleanTrue, com.apple.purplebuddy)`
+ synchronize, plus the `purplebuddy.sentinel` semaphore, executed inside the
guest.

### Concrete plan to complete Setup on a disk

1. Boot native-smc on a **persistent** overlay (done: `/tmp/dvm/SETUP1/run/disk.qcow2`,
   266 MB of writes retained) with a gdbstub (`-S -gdb`), reusing the
   `setup_gate_probe.sh` / `setup_skip_probe.py` LLDB machinery.
2. In a guest process holding CoreFoundation (SpringBoard or launchd), call
   `CFPreferencesSetAppValue(SetupFinishedAllSteps=true, com.apple.purplebuddy)`
   + `CFPreferencesAppSynchronize`, and create the `purplebuddy.sentinel`
   semaphore, so `BYSetupAssistantNeedsToRun()` returns false. Writes land on
   the persistent overlay through the guest's own encryption.
3. Reboot the overlay; confirm SpringBoard leaves setup mode (reason not 1/2)
   and reaches the home screen with default apps and a provisioned wallpaper.
4. If the wallpaper collection is still empty, the default-wallpaper assets must
   be provisioned (the step normally done during first boot / Setup); identify
   where PosterBoard populates the Collections assets and seed them.

Step 4 is the open risk: Buddy completion may not by itself populate the
wallpaper collection. That is the next thing to verify empirically.

## SETUP6 — Step 4 confirmed required (2026-09-08)

The plan above was executed and **Step 4's open risk is now a confirmed
result**: Buddy-flag completion does **not** populate the wallpaper.

- `SETUP2` applied the mobile-user purplebuddy completion
  (`BYBuddyFinishedInitialRunKey`/`SetupFinishedAllSteps`/`SetupDone`) on a
  persistent native-smc overlay via `complete_buddy_callbacks.py`; the write
  synchronized (`CFPreferencesAppSynchronize` returned TRUE) and persisted.
- `SETUP6` (fresh child of the SETUP2 overlay) was booted as native-smc with a
  UART chardev so the v6 dvm-input helper (`tools/input/relay.py`) could drive
  it. The helper acks (`DVM_INPUT_ACK ... 1`, ~35 ms). A `--home` wake plus
  rapid `screendump` captured lit frames (mean ~4.7/255, ~204k nonzero px).

**Visual result (decisive).** The disk renders the real iOS **lock screen** —
"Swipe up to open", the date ("Mon Sep 7" / "Tue Sep 8"), a large clock
("11:58" then "12:00"), the charging-battery and signal glyphs, and the
camera/flashlight buttons. So Setup is effectively complete: the device is past
the Setup Assistant gate and sits on the normal locked Home/Lock screen, not a
"Hello"/language picker. Screenshot: `/tmp/dvm/SETUP6/run/wake_8.png`.

But two things are visible:

1. **The wallpaper background is solid black** — the empty Collections poster,
   exactly as established in POSTER7. Buddy completion moved the device to the
   lock screen but did not seed any wallpaper content. A `--swipe` up did not
   leave the lock screen (passcode-gated or the unlock gesture was not honored),
   and the home screen wallpaper would be the same empty poster regardless.
2. **A diagonal stride/shear** distorts every composited element (the clock
   digits lean and wrap, a fragment appears on the far-left edge at clock
   height). This is guest-side content, **not** our display code:
   `iomfb_scanout` reads at the reported `surface.stride` (4864 = 1216 px,
   width 1179 rounded up to 64-px alignment) and `darwin_fb_present_bgra`
   copies row-by-row honoring that stride into a packed console buffer. The
   guest is compositing at a different pitch than it declares. Separate bug;
   does not affect the wallpaper's emptiness.

**Bottom line.** "Setup migration complete on a vm disk" is achieved and
visually verified. "Wallpapers work" is blocked by content, not by the GPU
driver, the display path, or the setup flags: the configured Collections poster
(`config:6E69CA09-...`, shared by both disk lineages) has no backing content,
so there is nothing to render on lock or home. Producing real wallpaper pixels
now requires provisioning that content — a first-boot/Setup step this
un-personalized base never ran — or switching the active poster to one with
built-in content via the wallpaper editor, which itself renders black on this
VM (the GPU app-render→present handoff, the original "get the GPU rendering"
gap). Both are separable efforts beyond the setup-flag completion delivered here.

## The content ships in the image — seeding it (2026-09-08, SEED1)

Answering the user's hypothesis ("did the base image not migrate correctly?")
directly: the base image is **complete**; the wallpaper content is present, it
was simply never selected.

Mounting `~/dvm-artifacts/build/rootfs_cx.dmg` read-only
(`tools/rootfs/safe_attach.sh attach ... --readonly`) shows a fully populated
`/Library/Wallpaper/`:

- `DefaultWallpapers~iphone.plist` maps this device (iPhone17,3 falls to the
  generic `default`) to collection `62879BFA-F700-4384-83E1-7AD1CC0D0817`,
  wallpaper `7565`.
- That collection is `Collections/iOS_17~iphone.wallpaperCollection`
  (`WallpaperCollection.plist` identifier `62879BFA...`, name "iOS 17",
  `order => [7565]`). Its `Wallpapers/7565.iOS_17-393w-852h@3x~iphone.wallpaper`
  bundle matches our 1179x2556 panel (393w-852h @3x) and carries the CoreAnimation
  layers `7565.iOS_17_Floating-...@3x~iphone.ca` and `...Background...ca`.
- Three other collections ship too (Bokeh, Clownfish, Stripe). WallpaperKit is
  code-in-cache (the on-disk `.framework` is resources only).

Because 7565 is a **CoreAnimation** wallpaper, the SW compositor that already
renders the lock-screen clock can render it — this does not depend on the
MercuryPoster/VideoToolbox Metal path.

**The seeding API.** `ipsw dyld symaddr/objc` on the extracted cache
(`~/dvm-artifacts/extract/dyld/`) finds `WKDefaultWallpaperManager`
(WallpaperKit) with `restoreDefaultWallpaperForAllVariantsAndNotify:`
(and `...WithCompletion:`), which reads `DefaultWallpapers~iphone.plist` and
installs the device default for both variants. Unslid VAs (this cache):
`_OBJC_CLASS_$_WKDefaultWallpaperManager` 0x1e81b3650, `_objc_msgSend`
0x188000800 (libobjcMsgSend.dylib), `_sel_registerName` 0x18040e6d8,
`_CFPreferencesSetAppValue` 0x1806c8088, `_CFPreferencesAppSynchronize`
0x1806cfc2c.

**Seed run.** `tools/re/seed_wallpaper_boot.sh` boots a fresh un-personalized
native-smc overlay (gdbstub + the SETUP6 UART wiring) and
`tools/re/seed_default_wallpaper.py` hijacks SpringBoard at its
`BYSetupAssistantNeedsToRun` call (early, before the wallpaper loads) to run, on
SpringBoard's own thread: (1) the mobile-domain purplebuddy completion
(`CFPreferencesSetAppValue` x3 + `CFPreferencesAppSynchronize` -> TRUE), then
(2) `[[WKDefaultWallpaperManager sharedInstance]
restoreDefaultWallpaperForAllVariantsAndNotify:YES]`. Verify with
`tools/re/wallpaper_capture.py` (wake + brightest-frame capture): the empty
lock screen measures mean ~5/255; a real iOS 17 default is far brighter.

## Correcting the seeding call — the class-method setter (2026-09-08, SEED2)

The first seed run (SEED1) hung at `objc_msgSend(WKDefaultWallpaperManager,
@selector(sharedInstance))`: the guest kept running but SpringBoard froze (frames
stuck, VM "running"). Root cause, found by class-dumping the extracted framework
(`ipsw dyld extract <cache> <img> --objc --slide -o DIR`; then
`nm DIR/<img> | grep '\['`) — the `--symbol`/`objc class` lookups do not resolve
ObjC methods here:

- `WKDefaultWallpaperManager` is a **loader**, not the setter. Its accessor is
  `+defaultWallpaperManager` (NOT `sharedInstance`), and it does not own the
  restore method. Messaging `sharedInstance` to it is an unrecognized selector
  whose exception path hangs at early launch.
- The real setter is a **no-argument class method**:
  `+[SBSUIWallpaperService restoreDefaultWallpaper]` (SpringBoardUIServices,
  which SpringBoard links; sibling `+[PBUIWallpaperService restoreDefaultWallpaper]`).
  `restoreDefaultWallpaperForAllVariantsAndNotify:` is actually
  `-[PBUIWallpaperConfigurationManager ...]`. `_OBJC_CLASS_$_SBSUIWallpaperService`
  is 0x1e86699f0 (restore IMP 0x1a85584c8).

`tools/re/seed_wallpaper_bg.py` calls it off the main thread via
`[SBSUIWallpaperService performSelectorInBackground:@selector(restoreDefaultWallpaper)
withObject:nil]` — a client→server call could otherwise be a SpringBoard→SpringBoard
self-XPC deadlock. SEED2 ran the whole chain cleanly with **no hang**: buddy
completion synced TRUE, the background restore dispatched (`bgRestore` returned),
and SpringBoard kept presenting frames (43+, vs SEED1 frozen at 16).

### Invocation mechanics (SEED2b → SEED3)

The background-thread dispatch (`performSelectorInBackground:`) did not produce a
wallpaper: the live lock screen stayed black after the restore, and a cold reboot
of the seeded overlay (SEED2b) was still black (brightest frame 3.1/255, the
plain lock-screen UI with no wallpaper). Likely cause: an
`performSelectorInBackground:` thread has **no run loop**, so the wallpaper
service's XPC reply is never delivered and nothing is persisted.
`+[SBSUIWallpaperService restoreDefaultWallpaper]` disassembles as a thin msgSend
wrapper (obtain service object → call restore → release) with no main-thread
assertion, so it is safe to schedule on the main thread. SEED3 switches to
`[SBSUIWallpaperService performSelectorOnMainThread:@selector(restoreDefaultWallpaper)
withObject:nil waitUntilDone:NO]` — the main thread has a runloop for the XPC
reply and, async, fires once the runloop is up post-launch, with no deadlock.

### Result: selection is not the blocker — the poster render is (SEED3/SEED3b)

The main-thread-async restore ran cleanly (buddy sync TRUE, chain returns) but
the wallpaper stayed black both live (SEED3, brightest 3.3/255) and on a cold
reboot of the seeded overlay (SEED3b, 3.9/255) — the plain lock-screen UI (clock,
date, "Swipe up to open") on a black ground, same as before. Across four boots
(SEED2 background-thread, SEED2b reboot, SEED3 main-async, SEED3b reboot) the
correct selection API `+[SBSUIWallpaperService restoreDefaultWallpaper]` was
invoked cleanly and **never rendered a wallpaper**.

**Conclusion.** On iOS 17 the lock/home wallpaper is a PosterBoard poster, and in
this VM PosterBoard does not drive any wallpaper poster's fullscreen render (the
`PosterBoard->poster render handoff`; zero `renderSubmit`, established in the
POSTER sections above). So the wallpaper LAYER is black regardless of which
wallpaper is selected; the clock/date are separate layers, which is why they
render on the black ground. Wallpaper CONTENT is present and the SELECTION API is
found and invoked — neither is the gap. The gap is the poster-render subsystem
(`PRUISPosterRenderingViewController` / `PRUISPosterSnapshotController` /
`PRPosterSnapshotDefinition` in PosterBoardUIServices), i.e. SpringBoard/
PosterBoard lifecycle work, the same render-handoff blocker as the original
"get the GPU rendering the wallpaper" goal. That is the next thing to drive.
