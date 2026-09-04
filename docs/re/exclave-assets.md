# ExclaveOS assets are an AP display dependency

Build 24A5430a, shared-cache slide `0x4b30000`. The original boot image
merged the OS cryptex but left the Preboot ExclaveOS directory empty.
SILManager's manifest is therefore null and SpringBoard throws for the
otherwise valid secure indicator name `Camera`.

Native evidence:

- QuartzCore static `0x184568fd4` obtains the layer's indicator name;
  `0x184568fdc` calls the resolver. The return at `0x184568fe0` is `-1`
  in DISPLAY_UI_R18. Name captured by replaying this accessor in R19:
  CFString `0x1edb95020`, UTF-8 `0x18933ddbf`, length 6, `Camera`.
- Resolver static `0x184569dc0` uses the SILManager function pointer at
  `0x1e8d7d1f8`. `_SILManagerIndicatorTypeIDFromName`, `0x29d8fdc30`,
  calls `+[SILManifest manifest]` then `indicatorFromName:error:`;
  the former returns nil at runtime `0x2a242dc64`.
- Manifest initialization `0x29d9210bc` enumerates `.plist` assets.
  R19 native enumeration returned an empty array at `0x2a24511b4`.
  `0x29d9341fc` selects the primary directory at `0x29d95da40`, then
  the fallback at `0x29d95dac0`. Both live below
  `/private/preboot/Cryptexes/ExclaveOS/System/ExclaveKit/System/Library/`:
  `PrivateFrameworks/SILManagerAssets.framework/` or
  `Frameworks/SILManagerComponent.framework/secureindicatorassets/`.
- The matching `094-14052-182.dmg` contains the fallback directory with
  cam_mic.plist, cam_mic_constraints.plist, cam_mic.bin, cursor.plist,
  and cursor.bin. Read through a safe readonly APFS mount, not 7zip's
  compressed-file extraction. Hashes: `/tmp/dvm/exclave-sil-assets.json`.
- R19 provisions those unchanged files via native open/write/close, then
  independently reads them back through the guest and verifies SHA-256.
  Preboot mkdir returns EROFS (30); the control uses a mobile Library
  directory and redirects the two directory strings, preserving their
  Swift lengths with trailing separators. No indicator ID or result is
  written. The failed once cache is reset, then the native accessor and
  resolver are replayed. Manifest becomes `0x7cf713d9f0` and Camera
  returns 0. `SILManifest.manifest_Wz` may temporarily hold a dispatch-once
  quiescence generation rather than -1; replaying the native getter first
  canonicalizes the completed state. Do not reset an in-progress token.
- R19 subsequently reaches Setup UIApplicationMain and BuddySceneDelegate
  willConnect at runtime `0x1024d3a9c`, t=1788563415.342. Its scene-create
  watchdog expires after 20 wall seconds / 0.654 application CPU seconds.
  No watchdog was extended in this control. Still no nonblack framebuffer.

Artifacts: `/tmp/dvm/DISPLAY_INDICATOR_R19.guest-lldb.log`,
`DISPLAY_INDICATOR_R19.provision.json`, and the static SIL symbol/ObjC dumps
under the same prefix. R20 repeats asset provisioning and native resolution;
its path changes are recorded in `DISPLAY_ASSETS_R20.sil-redirect.json`.

## Bootstrap integration

`bootstrap_data_volume.sh image` now invokes `merge_exclave.py` after making
the volume slots and before creating the qcow2 overlay. `EXCLAVE` defaults
to the matching decrypted image in `~/dvm-artifacts/aea/out/`. The standalone
`exclave` phase supports an offline base. The persistent-parent rebuild clones
its source base into `RUN/base-exclave.dmg`, provisions that copy, and derives
the fresh-format child from it. Existing backing chains are never modified.

The installer copies the entire payload onto the volume named Preboot at
`Cryptexes/ExclaveOS`, validates every file hash and symlink, refuses a differing
existing tree, and detaches both images on exit. It does not merge Exclave
executables into the AP trust cache or claim secure-world execution works.
The live path redirect is only a checkpoint experiment; fresh bootstrap images
use SILManager's original path.

Actual image test: `bootstrap_data_volume.sh exclave` against a fresh disposable
Preboot image verifies 1,672 files/links, 640,575,240 file bytes. An independent
second attachment/run verifies the same contents and reuses the tree.
Reports: `/tmp/dvm/exclave-bootstrap-test/exclave-merge.json` and
`/tmp/dvm/exclave-bootstrap-test-repeat/exclave-merge.json`. This validates
assembly, not a new guest boot. R19 validates native manifest resolution.
