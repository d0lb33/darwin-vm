# Native and patched bootstrap profiles (24A5430a)

Use `rebuild_persistent_parent.sh --profile native|patched` for the explicit
profile pipeline. The legacy positional `rebuild_persistent_parent.sh RUN_DIR`
remains the storage-only recovery command; it does not install runtime fixes.

| Configuration | Native | Patched |
|---|---|---|
| Existing project boot firmware adaptations, Data seeding, ANS/SEP/SMC | Yes | Yes |
| DCP model and measured software scanout configuration | Yes | Yes |
| Additional SMP / RTC kernelcache adapters | No; one CPU | Yes; six CPUs and host wall clock |
| Apple-userspace binary edits | None added | QuartzCore allocation, Settings scale, clock UIKit/non-glass fallback, powerd null guard |
| Runtime helper services | None added | Current input helper and virtual battery publisher, built from repo source |
| Development activation | Only with `--development-activation` | Only with `--development-activation` |
| Buddy completion / activation records | Unchanged | Unchanged |
| Saved RAM, debugger callbacks, diagnostic graphics/activation probes | None | None |

“Native” is a hardware bring-up baseline, not an unmodified physical-iPhone
boot or a guarantee of usable UI. It still uses the project's prepared boot
firmware and emulated device models. In particular, it does not inherit the
patched profile's RTC or SMP adapters. Neither profile claims native Setup.app
completion, reliable HID startup, or freedom from service crashes.

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
`--qemu-img` allow an explicitly selected existing build. Patched rejects a
binary without the RTC PV feature. The feature check does not prove that a
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

For the compatibility baseline, use `--profile patched` and a different
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
   boot, no template recopy and no kernel panic/critical-process reboot.
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

## Verification on 2026-09-05

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
