# tools/rootfs — building the bootable iOS images

Scripts that assemble the disk images we boot. **They live here, in the repo,
on purpose.**

## Why this directory exists

On 2026-09-02 the machine restarted and `/tmp/dvm` was wiped. That destroyed, in
one go: the decrypted IPSW payloads, four built disk images, the merged trust
caches, every scratch device tree, and **every build script** — `build_rootfs.sh`,
`merge_cryptex.sh`, `build_shell_rootfs.sh`, `mkshell_tc.py`, `merge_tc.py`,
`mkdcp.py`. A night's worth of work.

Recovery was only possible because the *method* had been written down in
`docs/re/`, and because one script (`merge_tc.py`) had been pasted verbatim into
`docs/re/rootfs-assembly.md` "for the record".

So: **anything you would mind losing goes in the repo.** Large binaries still do
not (`CLAUDE.md` — nothing under `firmware/`, `ipsw_db/` or `/tmp/dvm` is
committed), but the scripts that produce them do.

## What survives a `/tmp` wipe

| Survives | Does not |
|---|---|
| `firmware/` — bootkc, dtree, sptm, txm, ramdisk.dmg, ramdisk.tc | decrypted IPSW payloads (`/tmp/dvm/aea/out/*.dmg`) |
| `ipsw_db/<build>/` — DeviceTree, kernelcache, sptm, txm im4p (253 MB) | built images (`/tmp/dvm/build/*.dmg`) |
| everything in git: `dt_fixup.py`, `tools/`, `docs/re/` | trust caches (`/tmp/dvm/tc/`) |
| | scratch device trees, probe logs |

The restore-ramdisk boot path depends only on `firmware/`, so it keeps working
through a wipe. Only the real-system-volume work needs rebuilding.

## Recovering from a wiped /tmp

**1. Scratch dirs and the raw device tree.** Note the exact `ipsw` subcommand —
`img4 extract` and a directory `--output` both fail; it is `img4 im4p extract`
with a *file* path:

```bash
mkdir -p /tmp/dvm/probe /tmp/dvm/build /tmp/dvm/tc
ipsw img4 im4p extract --output /tmp/dvm/dtree_raw \
    ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p
```

**2. Check the baseline still boots** before rebuilding anything expensive:

```bash
python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/dt_base.bin -nvram nvram.bin
tools/probe.sh --dtree /tmp/dvm/dt_base.bin --secs 75 --tag RECOV
# expect: 0 panics, reached shell: yes
```

**3. Re-fetch and decrypt the payloads.** The IPSW URL is in `get_files.sh` and
in `firmware/info`. You do not need the whole archive — `ipsw extract --remote`
pulls individual files, which is what `get_files.sh` already does. The payloads
are AEA-encrypted and `ipsw fw aea --fcs-key` issues keys with **no
credentials** (see `docs/re/rootfs-boot.md`):

| payload | what |
|---|---|
| `094-13182-141.dmg` | system volume, 10,026,483,712 B |
| `094-13150-145.dmg` | OS cryptex |
| `094-14052-182.dmg` | ExclaveOS |

**4. Rebuild the images.** `docs/re/rootfs-assembly.md` has the full recipe under
"Build recipe (reproducible, no sudo anywhere)", including `merge_tc.py`'s
source inline. `docs/re/userspace-boot-state.md` documents the cryptex merge
(2,111 loose files, 32 dangling graft symlinks to replace with real
directories).

## Constraints that keep biting

- **No sudo on this machine.** Ownership is handled by the donor-rename trick:
  rename an expendable root-owned file with `nlink == 1` onto the target path so
  the inode, and therefore uid 0, is inherited, then overwrite the contents.
  Donors sharing an inode silently corrupt each other — Apple hardlinks
  localisation resources ~40-way — so always filter `-links 1` and verify
  distinct inodes.
- **One read-write attachment per image at a time.** Five concurrent rw
  attachments once corrupted an image silently.
- **Mount with owners on** when inspecting. `diskutil` defaults to `noowners`
  for disk images, and then every file misleadingly reads as uid 501.
- Never force-detach unrelated disks; the user has other volumes mounted.

## It panicked the Mac twice — read this before touching a disk image

On 2026-09-02 this work took macOS down with a kernel panic, twice, identically:

```
panic(cpu N, caller 0x...): "Data ObjId overflow" @jobj.c:1152
Panicked task: pid 109: fseventsd
macOS 27.0 (26A5421a), xnu-13432.1.9~3
```

`jobj.c` is APFS's object-ID allocator; `fseventsd` is the filesystem-events
daemon. Not memory, not QEMU — neither appears in either panic, and the machine
has 128 GB.

**Mechanism.** Each image here is an APFS container holding a ~500,000-file iOS
filesystem. Mounting one exposes all of that to the host, where Spotlight
indexes it (`mdutil -sa` showed indexing enabled on `/System/Volumes/Data`, and
there was no `.metadata_never_index` anywhere in the work area) and fseventsd
tracks every change. Add mass create/delete churn inside the container and APFS
runs out of data object IDs and panics.

The strongest correlate for the trigger is the Data-volume **restructure**:
`mv`-ing all 20,788 `/private/var` entries to a mounted volume's root and then
deleting everything else. That step ran shortly before both panics. Evidence for
the Spotlight/fseventsd involvement is solid; the restructure link is timing
only, so treat it as strong suspicion rather than proof.

A filesystem should return an error, not panic the kernel, so this is an APFS
bug on a beta OS. We trigger it; we can avoid it.

**Rules.**

- Attach through `tools/rootfs/safe_attach.sh`, which forces `-nobrowse`, drops
  `.metadata_never_index` at the volume root, and refuses a second concurrent
  attachment of the same image.
- Detach as soon as you are done. Never blanket force-detach — the user has
  other volumes mounted, including Xcode Simulator runtimes, and we clobbered
  one of those once.
- Do not build a volume by moving everything to its root and deleting the rest.
  Prefer copying the wanted subtree into a fresh volume.
- Keep at most one 21 GB clone alive at a time, and watch free space: the host
  Data volume was at 93% during both panics, which is where APFS gets fragile.

## The persistent-NVMe parent, and the second wipe (2026-09-03)

The host rebooted at 13:14 on 2026-09-03 and `/tmp/dvm` went with it, including
the persistent Data/User parent `sks-op09-complete-2.qcow2` and its entire
qcow2 chain, the probe logs, and the device tree. Two things made that cheap:

- **The chain is a script now.** `tools/rootfs/rebuild_persistent_parent.sh`
  runs `format → ramdisk-helper → copy-data → manifest → layout → marker →
  normal ×2` from the surviving base disk
  `~/dvm-artifacts/build/rootfs_cx_dual_roles.dmg` and leaves the result linked
  at `/tmp/dvm/data-seed/persistent-parent.qcow2`. Measured on the first rerun:
  format 27 s, ramdisk-helper 8 s, copy-data 106 s, manifest / layout / marker
  about 22 s each; the normal boots are bounded by `BOOT1_SECS` / `BOOT2_SECS`.
  Every stage is a fresh child, so a failed stage is rerun with `START_AT=`.
- **The device tree is one line**, recorded here because it was only in a
  session log before:

  ```bash
  ipsw img4 im4p extract --output /tmp/dvm/dtree_raw ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p
  python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/data-seed/dt_nvme_welcome.bin \
      -nvram nvram.bin -enable ans -enable smc -enable sep -enable dcp -dram 12G
  ```

  `-dram 12G` means every boot of it needs `--mem 12G`.

There is also a fast path that did not work from here: macOS keeps local
Time Machine APFS snapshots (`tmutil listlocalsnapshots /`), and the 10:39
snapshot that day still held all of `/private/tmp/dvm`. `mount_apfs -s` and
the auto-mounted `/Volumes/com.apple.TimeMachine.localsnapshots` both refuse
a shell without Full Disk Access, so recovering that way needs a terminal
granted that privilege. Rebuilding took less time than arranging it.
