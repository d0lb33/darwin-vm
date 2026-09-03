# Persistent Data volume milestone

The real APFS Data volume now mounts from ANS/NVMe across clean boots. The
bootstrap path formats it inside the guest, populates it from the restore
ramdisk, creates the User-volume manifest/layout, and writes a completion
marker. The resulting derived qcow2 children and logs live under `/tmp/dvm`;
they are not committed.

## Evidence

The format phase has two independent witnesses: the console reports
`FMT_RC=0`, and the Data overlay grows after `newfs_apfs`. The implementation
keeps both checks in `tools/rootfs/bootstrap_data_volume.sh` (`phase_format`,
around lines 260–350). The helper is installed into the derived restore
ramdisk by `ramdisk-helper`; the guest-side `cksum` is checked against the host
source before execution (`bootstrap_data_volume.sh:95–124, 193–212`).

The corrected copy run is `/tmp/dvm/probe/BOOTSTRAP_COPY_ROLES_PREINIT_TZ1.serial.log`:

```
4713 DVM_SEED_TIMEZONE_PRECREATE_DB_RC=0
4714 DVM_SEED_TIMEZONE_PRECREATE_DIR_RC=0
4715 DVM_SEED_TIMEZONE_PRECREATE_LINK_RC=0 target=/var/db/timezone/zoneinfo/US/Pacific
4716 DVM_SEED_TIMEZONE_PRECREATE_RC=0
4748 DVM_SEED_TIMEZONE_SYMLINK=... mode=755 uid=0 gid=0
4749 DVM_SEED_TIMEZONE_SYMLINK_RC=0
4751 DVM_SEED_COPY_FILES=5992
4752 DVM_SEED_COPY_DATA_RC=0
4755 DVM_COPY_SHELL_RC=0 DVM_COPY_SYNC_RC=0
```

The manifest, layout, and marker stages independently completed in
`BOOTSTRAP_MANIFEST_ROLES_CKSUM1.serial.log:4663–4681`,
`BOOTSTRAP_LAYOUT_ROLES_CKSUM1.serial.log:5475–5483`, and
`BOOTSTRAP_MARKER_ROLES_CKSUM1.serial.log:4656–4666`. The marker stage also
checks `/private/var/.dvm-data-seed-complete`.

The first clean boot, `/tmp/dvm/probe/PERSIST_NVME_CLEAN_BOOT3.serial.log`,
mounts System (`:287–303`), Preboot (`:396`), Data at `/private/var`
(`:477–484`), Hardware (`:489–491`), and User (`:584`). The second chained
boot, `PERSIST_NVME_CLEAN_BOOT4.serial.log`, repeats those mounts at
`:286–302`, `:395`, `:455–462`, `:466–468`, and `:564`. It reaches
`Early boot complete` at line 625. Neither clean log contains `Copying ` or a
`panic(cpu` line.

These boots prove persistent volume mounting and the no-recopy path. They do
not prove a complete iOS graphical boot or a Welcome/Hello frame. A later
display-frontier run fixed the class-2 SKS issue far enough for the UI path to
be investigated, but SpringBoard-triggered restart ended in the known
`Halt/Restart Timed Out @IOPlatformExpert.cpp:900` consequence in
`PERSIST_NVME_ROLES_PREINIT_TZ1_CLASS2_BOOT2.serial.log:1072`; this is not a
Welcome-screen result.

## Repeatable stages

Use a fresh qcow2 child for each mutating restore-ramdisk stage. The script's
current modes are:

```
tools/rootfs/bootstrap_data_volume.sh image
tools/rootfs/bootstrap_data_volume.sh format
tools/rootfs/bootstrap_data_volume.sh ramdisk-helper
tools/rootfs/bootstrap_data_volume.sh copy-data
tools/rootfs/bootstrap_data_volume.sh manifest
tools/rootfs/bootstrap_data_volume.sh layout
tools/rootfs/bootstrap_data_volume.sh marker
tools/rootfs/bootstrap_data_volume.sh normal
tools/rootfs/bootstrap_data_volume.sh verify
```

Set `PARENT`, `OUT_OVL`, `TAG`, `OVL`, `WORK`, and (for the guest helper)
`RESTORE_RAMDISK`/`RESTORE_HELPER_SOURCE` as shown by the script's phase
headers and environment section (`bootstrap_data_volume.sh:40–78`). The
`copy-data`, `manifest`, `layout`, and `marker` phases require numeric guest
return-code witnesses and `sync` success before accepting a stage
(`bootstrap_data_volume.sh:416–456`).

