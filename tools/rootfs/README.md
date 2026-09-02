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
