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

The host-injected helper was independently verified before each of these
stages: `DVM_HELPER_SOURCE_RC=0 DVM_HELPER_BYTES=74544` appears at line 520
in `BOOTSTRAP_MANIFEST_ROLES_RAMDISK1.serial.log`, line 510 in
`BOOTSTRAP_LAYOUT_ROLES_RAMDISK1.serial.log`, and line 512 in
`BOOTSTRAP_MARKER_ROLES_RAMDISK1.serial.log`. The corresponding guest stages
then report manifest `DVM_SEED_USER_MANIFEST_RC=0` and shell/sync RC 0 at
lines 540–542, layout `DVM_SEED_USER_LAYOUT_RC=0` and shell/sync RC 0 at
lines 520–522, and marker shell/sync/stat RC 0 at lines 526. The marker stage
also checks `/private/var/.dvm-data-seed-complete`.

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

Here is the actual derived chain used for the two clean boots. Each command is
run after the preceding command has completed, and each output image is a
derived artifact rather than a repository file:

```
REPO=/Users/jdolbe1/Downloads/darwin-vm
BASE=/tmp/dvm/data-seed/roles-fresh-format-033500.qcow2
RAMDISK=/tmp/dvm/data-seed/ramdisk-data-seed.dmg
HELPER=/libexec/dvm_data_seed_helper
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  OVL="$BASE" OUT_OVL=/tmp/dvm/data-seed/roles-manifest-ramdisk1.qcow2 \
  TAG=BOOTSTRAP_MANIFEST_ROLES_RAMDISK1 \
  tools/rootfs/bootstrap_data_volume.sh manifest
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  OVL=/tmp/dvm/data-seed/roles-manifest-ramdisk1.qcow2 \
  OUT_OVL=/tmp/dvm/data-seed/roles-layout-ramdisk1.qcow2 \
  TAG=BOOTSTRAP_LAYOUT_ROLES_RAMDISK1 \
  tools/rootfs/bootstrap_data_volume.sh layout
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  OVL=/tmp/dvm/data-seed/roles-layout-ramdisk1.qcow2 \
  OUT_OVL=/tmp/dvm/data-seed/roles-marker-ramdisk1.qcow2 \
  TAG=BOOTSTRAP_MARKER_ROLES_RAMDISK1 \
  tools/rootfs/bootstrap_data_volume.sh marker
PARENT=/tmp/dvm/data-seed/roles-marker-ramdisk1.qcow2 \
  OUT_OVL=/tmp/dvm/data-seed/roles-normal-persist-boot3.qcow2 \
  TAG=PERSIST_NVME_CLEAN_BOOT3 NORMAL_BOOT_SECS=45 \
  tools/rootfs/bootstrap_data_volume.sh normal
PARENT=/tmp/dvm/data-seed/roles-normal-persist-boot3.qcow2 \
  OUT_OVL=/tmp/dvm/data-seed/roles-normal-persist-boot4.qcow2 \
  TAG=PERSIST_NVME_CLEAN_BOOT4 NORMAL_BOOT_SECS=45 \
  tools/rootfs/bootstrap_data_volume.sh normal
```

The `copy-data`, `manifest`, `layout`, and `marker` phases require numeric
guest return-code witnesses and `sync` success before accepting a stage
(`bootstrap_data_volume.sh:416–456`).
