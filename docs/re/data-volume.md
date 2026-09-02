# The Data volume: how far a host-built one gets, and the wall

Goal: replace `-ephemeral-data` (a tmpfs seeded from the on-volume template on
every boot, ~213 s of guest time) with a real APFS Data volume, so
`mount-phase-2` mounts instead of copying 41,557 files and `/private/var`
survives a reboot.

Status: **the Data volume mounts, and then XNU refuses it because it is not
encrypted.** Everything below that gate is solved.

## What the fstab demands

`/filesystems/fstab` in the device tree names six volumes. `DT_get_fstab_entries`
resolves each to a volume **by role**, and a role it cannot resolve stops the
table being usable:

| vol.fs_name | role | mount point | can macOS create it? |
|---|---|---|---|
| xART | 0x100 | `/private/xarts` | **no** |
| Preboot | 0x10 | `/private/preboot` | yes (`-role B`) |
| Data | 0x40 | `/private/var` | yes (`-role D`) |
| Baseband-Data | 0x80 | `/private/var/wireless/baseband_data` | **no** |
| Update | 0xc0 | `/private/var/MobileSoftwareUpdate` | **no** |
| Hardware | 0x140 | `/private/var/hardware` | yes (`-role H`) |

The role field is a **bitmask** — note `Update = Data|Baseband` (0xc0) and
`Hardware = xART|Data` (0x140) — and `diskutil` only grants some combinations.
Its letters are `B|R|V|I|T|S|D|E|X|H|C|Y`. `X` (xART) is refused at both
`addVolume` and `chrole` afterwards:

```
Error setting APFS Volume role: Unable to set the APFS Volume Role (-69599)
```

xART is secure-enclave storage and the host will not hand it out. Baseband and
Update have no letter at all. So three of the six can never exist on a
host-built image, and `dt_fixup.py`'s `fixup_fstab_drop_unavailable()` removes
those three entries instead.

## The gate moved once per boot, which is how it was found

| probe | fstab | result |
|---|---|---|
| `DUAL1` | untouched | `failed to get volume for role: 256` (xART); Data **never mounted**, `/private/var` stayed the sealed read-only copy, `fixup-mobile-tmp` died "Read-only file system" |
| `DUALA` | xART dropped | `failed to get volume for role: 128` (Baseband); **Preboot mounted** (`disk1s3 mount-complete volume Preboot`); Data still not mounted |
| `DUALB` | xART, Baseband-Data, Update dropped | **0 role failures**; Data mount attempted and refused |

`DUALB` gets all the way into the mount: the space manager runs, trims 2,556,133
free blocks in 8,304 extents, sets `cloneinfo_id_epoch`, and then:

```
apfs_log_op_with_proc:3279: disk1s2 mounting volume Data, requested by: mount_apfs (pid 10)
handle_mount:893: disk1s2 vol-uuid: BA8B10E8-... block size: 4096 block count: 6642176 (unencrypted; flags: 0x1; ...)
panic(cpu 0 ...): "unencrypted data volume is not allowed" @apfs_vfsops.c:2399
```

## Why encrypting it is not a matter of passing a flag

The device tree declares **`no-effaceable-storage`** (alongside
`cpx-encryption-mode`). Effaceable storage is the small NAND region that holds
the keybag and media keys; without it iOS has nowhere to keep data-protection
keys. The same fact appears downstream as `mount: failed to migrate Media Keys,
error = c002`.

So "let iOS encrypt it" means, in order:

1. model effaceable storage (drop `no-effaceable-storage`, back the region --
   tractable now that ANS, SART and DART work);
2. make `AppleSEPKeyStore` work. This is the hard part and the reason
   `sep-endpoint,sks` is **deliberately not advertised**: the driver starts its
   IPC the moment the endpoint appears and panics at strike 20 (`cmp w21, 0x14`
   at `0xfffffff00954c0b4`). `docs/re/sep-protocol.md` decodes the first request
   (ep 18, tag 77, op 1, 0x5c-byte OOL buffer, `AppleKeyStore` `ipc.c` header
   with `u32` body size 0x48) but not the key-wrapping protocol behind it;
3. return **deterministic** key material -- we need keys stable across reboots,
   not keys that are secret;
4. stop needing `-skip-keybag`.

Step 2 is required to retire `-skip-keybag` regardless, so it is not a detour.

The cheaper alternative is to patch the check at `apfs_vfsops.c:2399`, the way
`xnu_patch.c` already patches `bsd_rooted_ramdisk`. Worth doing as an
*experiment* even if the real path is wanted: it answers, for hours rather than
days, whether encryption is the only gate or the first of several -- iOS
per-file `cprotect` and Keychain both lean on data protection, so the patched
boot may fail again immediately.

## Building the image

`tools/rootfs/build_data_volume.sh` -- clone, extend sparsely, `addVolume`. It
deliberately does **not** build the `/private/var` template on the host: see the
header for why the old clone-and-delete approach kernel-panicked the host three
times, and why an empty volume that iOS seeds itself sidesteps the no-sudo
ownership problem.

Volumes present in `rootfs_dual.dmg`: `disk1s1` System, `disk1s2` Data,
`disk1s3` Preboot, `disk1s4` Hardware.
