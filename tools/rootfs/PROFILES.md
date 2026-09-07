# Native and patched bootstrap profiles (24A5430a)

Use `rebuild_persistent_parent.sh --profile patched-native-battery` for the
compatibility baseline. The option interface defaults to this profile;
`--profile patched` is now an alias. Every profile uses the native SMC battery
and unmodified powerd. The old battery patcher/publisher sources and staging
paths have been removed. See [native-battery-smc.md](../../docs/re/native-battery-smc.md).

The positional `rebuild_persistent_parent.sh RUN_DIR` remains storage-only and
does not install runtime accommodations.

| Property | `native` | `patched-native-battery` / `patched` |
|---|---|---|
| Battery / powerd | Native SMC / original | Native SMC / original |
| Clock | Native SPMI/PMU RTC | Native SPMI/PMU RTC |
| Apple-userspace edits | None added | Display allocation, Settings scale, software clock rendering |
| Runtime helpers | None added | Current input helper |
| CPUs / kernel adapter | 6 / native PMGR, stock kernel | 6 / native PMGR, stock kernel (SMP adapter until 2026-09-06) |
| Setup completion / saved RAM | Unchanged / none | Unchanged / none |

All profiles enable SMC, SPMI and PMGR and force `DARWIN_RTC_PV=0`. The
installed package (`~/dvm-artifacts/native-smc/default.json`) pins the stock
`firmware/bootkc`, a tree built with `-enable ans smc sep dcp spmi pmgr
-development-activation -dram 12G`, and no `DARWIN_SMP_PV`; re-pin it with
`tools/rootfs/repin_native_smc.py` when the build or tree recipe changes, and
`--restore-backup pmgr` returns the previous inputs. They retain the
project firmware adaptations, SEP/SKS storage models, and Data seeder. The
compatibility profile's software clock rendering is separate from RTC time.

## Inputs

The entry point starts from **an already merged raw System image with
Data/Preboot/Hardware slots**, or an explicit seeded qcow2 parent. It does not
fetch an IPSW or reconstruct the complete System/cryptex merge. The older
`build_rootfs.sh` is not a complete recipe for that merge; do not use its
historical full-System host-mount workflow. See `CLAUDE.md` host-safety rules.

Provide these artifacts explicitly; no old `/tmp` staging names are defaults:

- Original merged System/cryptex trust cache.
- Raw device tree and the repository NVRAM blob.
- Original shared-cache exports `.01`, `.13`, `.21` in `--cache-dir`.
- Original `/System/Library/CoreServices/powerd.bundle/powerd` export.
- Original `/System/Library/xpc/launchd.plist` export (without dvm jobs).
- Matching decrypted ExclaveOS payload when using `--base-image`.
- Prepared `firmware/{bootkc,ramdisk.dmg,ramdisk.tc,sptm,txm}` and a built QEMU.

The small file exports can be obtained through a disposable restore guest with
`prepare_file_export.py`; the cache exports must be the original matching
24A5430a artifacts. Do not host-mount the full System volume to obtain them.
The runner checks original code pages against their signature hashes, pins
inputs with SHA-256, and checks those preimages again inside the restore guest.
It rejects existing dvm helper files/services and checks the two loose cryptex
libraries whose absence previously prevented userspace startup. These checks
exclude our known runtime patches; they are **not a full-image Apple manifest
verification** or proof that arbitrary supplied Data state is pristine.

Build the pinned QEMU submodule before running either profile; see `CLAUDE.md`.
Never rebuild a QEMU binary while another VM is using it. `--qemu` and
`--qemu-img` allow an explicitly selected existing build. Both profiles reject a
binary without the native SMC battery and SPMI/PMU device models. The feature check does not prove that a
binary was built from the current commit; its exact SHA-256 is recorded.

The default firmware directory is the checkout's `firmware`. A different
`--firmware` directory is supported with `--parent`; the storage seeding stages
currently require firmware in the canonical checkout. Building patched helpers
requires the Apple command-line SDK, clang and codesign. Their CDHashes are
regenerated and merged into the selected trust cache on each host.

## Run

Example with paths under a user-owned artifact directory (replace them with
your actual source paths). Select a **new output directory for every run**:

```bash
tools/rootfs/rebuild_persistent_parent.sh \
  --profile native \
  --base-image "$HOME/dvm-artifacts/build/rootfs_cx_dual_roles.dmg" \
  --exclave "$HOME/dvm-artifacts/aea/out/094-14052-182.dmg" \
  --tc "$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin" \
  --dtree-raw "$HOME/dvm-artifacts/extract/dtree_raw" \
  --cache-dir "$HOME/dvm-artifacts/extract/dyld" \
  --powerd "$HOME/dvm-artifacts/extract/powerd.original" \
  --launchd-cache "$HOME/dvm-artifacts/extract/launchd-original.plist" \
  --out /tmp/dvm/native-baseline-1
```

For the compatibility baseline, use `--profile patched-native-battery` and a different
`--out`. Add `--development-activation` only when that experiment is intended;
it changes the device tree and does not mark Buddy complete.

To avoid reseeding for every comparison, substitute
`--parent /absolute/path/to/read-only-seeded.qcow2` for `--base-image` and
`--exclave`. Both profiles should start from the **same unpatched parent**.
Do not feed the patched result back into native: the guest guards reject known
patches, rather than silently inheriting them. An existing parent's Data,
migration and Setup history is inherited and recorded as such.

`--prepare-only` checks inputs and builds the guarded restore installer,
kernelcache, device trees and trust cache. It does not create a baseline disk
or certify boot success. It requires a new output directory just like a full
run; rerun the full command with another output directory to install and boot.

## Outputs and validation

The full run performs the following, sequentially:

1. Builds the selected artifacts. Native builds no runtime helpers.
2. With `--base-image`, seeds new Data/User on owned disk children. With
   `--parent`, uses the supplied immutable parent directly.
3. Boots a disposable restore guest, verifies **all** preimages before any
   System write, and either verifies native System read-only or applies the
   patched payload. Success requires the guest's installer marker and owned
   QEMU shutdown. Only the small restore ramdisk is attached to the host.
4. Runs two chained, fresh disk boots through `tools/probe.sh`, default 180
   seconds each (`--boot-seconds 1..600`). No saved RAM or guest debugger.
5. Checks root disk, protected/encrypted Data, User/Preboot/Hardware, early
   boot, native `AppleARMRTC` publication, no template recopy and no kernel
   panic/critical-process reboot.
   Patched additionally requires a fresh `iomfb: presented` witness on **each**
   boot. That witness does not establish that the displayed UI is correct.

`bootstrap.json` records the selected profile, input hashes, source kind,
status and per-boot verdicts. `patch-inventory.json` records reviewed cache
edits. `candidate.json` is emitted after verified installation, before normal
boot validation. `baseline.json` is emitted only after both boot verdicts pass.
Failed candidates and their evidence remain available; no global parent
symlink is replaced by profile runs. A failed or interrupted run must use a
new output directory on retry.

The manifests work with the existing bounded disk-boot observer:

```bash
python3 tools/warm_boot_probe.py /tmp/dvm/patched-baseline-1/baseline.json \
  --tag PATCHED_BASELINE_CHECK1 --seconds 180
```

Battery percentage, advancing clock, app launches, native input, service crash
loops and complete Setup still require separate acceptance checks. A storage
or frame witness must not be reported as fully interactive stability. Native
may legitimately fail to draw while missing hardware is brought up; its
storage validation remains useful and does not imply display parity.

Outputs contain qcow2 backing dependencies. Copying just `baseline.json` and
the top qcow2 to another Mac is insufficient. Preserve the complete chain and
rebase paths, or flatten an offline disk and verify its guest-visible contents;
then generate a manifest with the destination's paths. Inputs, outputs and
firmware binaries stay out of git.


## Native RTC configuration

Both normal boot and restore/seed device trees enable the SPMI controller and
Dialog PMU. AppleDialogSPMIPMURTC supplies calendar time through IORTC using its
original kernel code. The native profile copies the original boot kernel; the
patched profile applies only the SMP adapter to that kernel. Both keep
`DARWIN_RTC_PV=0`, and inherited clock/debug environment overrides are removed.
The legacy storage-only command can opt in with `NATIVE_RTC=1`; profile builds
set this explicitly for their seeding stages.

This replaces the **time-source kernel patch**, not the shared-cache patches
that make the large clock render without GPU acceleration. Those remain in
patched and are absent from native. See `docs/re/native-rtc-spmi.md` for the
SPMI/PMU, ANS SET_TIME and SART power-gate evidence.

Default power-on seeds the PMU from host/QEMU RTC time. Profile builds do not
set `DARWIN_PMU_STATE`: persisting a guest-adjusted clock offset is a separate
option, not necessary for correct time on a fresh boot. Do not share a writable
PMU state file between concurrent VMs or snapshot clones. Existing checkpoint
manifests remain pinned to their old device configuration; switching an old
PV checkpoint to SPMI requires a fresh disk boot and a new checkpoint.

## Native RTC integration verification (2026-09-05/06)

RTC implementation `qemu-sptm` commit `2187536` was merged on top of the
bootstrap profiles. The shared HID work was preserved as uncommitted changes;
these RTC runs used the isolated, already built RTC executable, not a rebuild
of the shared HID checkout.

- `RTC_BP_NATIVE2`: guarded native install and two chained 90-second disk boots
  passed. Both published `AppleDialogSPMIPMURTC` and IORTC, reached early boot,
  mounted protected/encrypted Data and the required roles, and recorded zero
  XNU panics and no template recopy. No frame was claimed for this storage
  baseline. Evidence: `boot1/verdict.json`, `boot2/verdict.json`, and the final
  stricter recheck `/tmp/dvm/RTC_MERGE1/native-final-verdicts.json`.
- The native kernel SHA-256 stayed
  `dc0f5b6a6fa848053c301949c8376c216c6223c047203b93e408a93d3440f906`.
  `RTC_BP_PATCHED1` preparation produced the SMP-only kernel
  `da1e254ab81e31adae87c049da295b582dabbd4ba46096fc58f3e5467fc6e02c`.
  Both retain the original 40 bytes at `AppleARMPE::getGMTTimeOfDay`
  (`0xfffffff0085cdfd0`); neither invokes `rtc_pv_patch.py`.
- `RTC_MERGE_UTC1`: a fresh restore boot with the original kernel and native
  SPMI tree read `date -u +%s` over UART. Guest values `1788655282` and
  `1788655286` fell within their host sampling windows
  `[1788655281.644, 1788655283.938]` and
  `[1788655285.947, 1788655288.203]`. The guest clock advanced four seconds.
  Probe verdict: `STOPPED ON CONDITION`, zero panics, reached shell yes.
  Evidence: `/tmp/dvm/RTC_MERGE_UTC1/utc-samples.json`.
- `RTC_MERGE_WARM1`: fresh boot of the migrated display disk with the new
  generated SPMI tree and SMP-only kernel, no RTC PV or saved RAM/debugger.
  Early boot: 10.726 s; first frame: 315.665 s; zero panics. The observer stopped
  at the first presentation, so its dim initial frame is not a settled-UI
  acceptance result. The generated DT/kernel match the RTC branch's SYS4
  inputs byte-for-byte. No `DARWIN_PMU_STATE` was supplied, so this also tested
  ordinary host-time initialization without a carried PMU state file.
- `RTC_MERGE_WARM2`: a second fresh disk boot chained from WARM1 reached early
  boot at 12.131 s and its first frame at 169.623 s, with zero panics. A bounded
  20-second continuation produced a legible lock screen showing Sat Sep 5 and
  5:47, with no panic or critical-process reboot markers. It used no debugger
  or restored RAM; only the owned VM was paused for capture and then stopped.
  Evidence: `/tmp/dvm/RTC_MERGE_WARM2/result.json` and
  `/tmp/dvm/RTC_MERGE_WARM2/settled/{result.json,final.png}`.
- Host regressions: 66 tests under `tools/tests`, two input tests, Python
  compilation and shell syntax checks passed. The native verdict checks require
  both the IORTC publication and the concrete AppleDialogSPMIPMURTC driver.

The full fresh-Data seeding pipeline was not rerun in this integration. Existing
PV checkpoint configurations remain available unchanged. Native RTC does not
resolve the separate variable display-start delay or certify input stability.

## Historical verification before native RTC integration (2026-09-05)

The profile installer and boot runner were exercised from an isolated,
read-only copy of the earlier storage parent
`/tmp/dvm/data-seed/rebuild/boot2.qcow2`. These tests did **not** rerun the entire
fresh-Data seeding sequence or reconstruct the merged source image.

- `BP_NATIVE1`: restore guest confirmed every preimage. `qemu-img compare`
  reported **Images are identical** between the supplied parent and the native
  install child (`/tmp/dvm/BP_NATIVE1/native-disk-compare.log`). Both 90-second
  chained disk boots passed storage validation: zero XNU panics, no template
  recopy, protected/encrypted Data, User/Preboot/Hardware and early boot. Neither
  presented a frame. `baseline.json` is a native storage baseline only. The
  serial clock remained at the Unix epoch, as expected without the RTC adapter.
- `BP_PATCHED3`: restore guest confirmed all patches/helper copies and emitted
  `DVM_PROFILE_INSTALLED`. The input helper CDHash was
  `023cff3b44925a3dc16882553f7717f5b3b144b9`; the battery helper CDHash was
  `0f0d0f104f046447d5900660d97c31d2ee52c884`, matching the tested v6/BUILD8
  artifacts. The first fresh boot passed storage checks with zero XNU panics;
  serial recorded `POWER_PV_PERCENT ... percent=100 ... verified=1` and
  `DVM_INPUT_READY`. No frame appeared within 180 seconds, so validation failed
  and **no patched baseline manifest was published**. The installed candidate
  remains at `/tmp/dvm/BP_PATCHED3/candidate.json`. This earlier Data lineage
  must not be conflated with the later migrated warm image.
- First development attempt `BP_PATCHED2` correctly withheld success because
  the restore shell lacks `umount`. The installer now follows the existing
  `sync` + owned QEMU shutdown convention. That failed child was not reused.
- Host regressions: 62 tests under `tools/tests`, two input tests, Python compile
  and shell syntax checks passed. The new tests cover profile/env separation,
  signed-page guards, refusing existing helper registrations, verifying native
  without writes, checking all preimages before patching, and withholding
  patched validation when no new frame is observed.

Evidence: `/tmp/dvm/BP_NATIVE1/{install/result.json,boot1/verdict.json,boot2/verdict.json}`
and `/tmp/dvm/BP_PATCHED3/{install/result.json,boot1/verdict.json}`. Their logs
and manifests record the actual launch inputs. The binaries used for these
runs came from the already tested `warm-runtime-qemu3` build; no QEMU rebuild
or changes to another task's running VM were performed.
