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
not prove a complete iOS graphical boot or a Welcome/Hello frame. Subsequent
SKS work also completed the protected-file bootstrap: `SKS_OP09_COMPLETE_1`
mounts User at line 561 and reaches `Early boot complete` at line 617;
`SKS_OP09_COMPLETE_2` repeats those milestones at lines 568 and 637. Neither
log contains `fext_ek`, `apfs_unwrap_key`, `Copying `, or `panic(cpu` (zero
matches in each log). The first run's SEP witness is
`/tmp/dvm/probe/SKS_OP09_COMPLETE_1.stderr.log:681–684`: the guest's first
128-byte authenticated op09 reply was accepted and decoded as three blobs
(16-byte file key, 16-byte IV, empty third blob) plus scalar zero.

The call-chain capture `/tmp/dvm/FEXT_LLDB_1C.lldb.log:47–59,65–94,99–126`
puts the fext return at runtime `0xfffffff02957b5f0` (unslid
`0xfffffff00957b5f0`), with the caller chain through runtime
`0xfffffff02954b1b4`/`0xfffffff02a95d9d8` (unslid addresses are runtime minus
the `0x200000000` kernel slide). The decoder capture
`/tmp/dvm/SKS_OP09_DECODE_1.lldb.log:17–28,90–150,1032–1060` identifies the
native op09 call at runtime `0xfffffff029547544` (unslid
`0xfffffff009547544`) and its authenticated reply-buffer decoder; the failing
return was `0xe00002bc` only in the pre-fix capture, not in either completion
boot. This establishes the three-blob-plus-scalar contract rather than merely
correlating SEP log messages.

The protected-file/NVMe milestone is therefore complete: the persistent child
can be cold-booted twice without reseeding or protected-file unwrap failures.
It still does not prove a complete iOS graphical boot or a Welcome/Hello frame;
that is the separate display goal.

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

Starting from the verified fresh-format parent, this is a copy-pasteable
reproduction of the corrected chain. Each command runs after the preceding
command completes, and every output is a derived artifact rather than a
repository file:

```
REPO=/Users/jdolbe1/Downloads/darwin-vm
RUN=/tmp/dvm/data-seed/repro-persistent-1
BASE=/tmp/dvm/data-seed/roles-fresh-format-033500.qcow2
RAMDISK="$RUN/ramdisk-data-seed.dmg"
HELPER=/libexec/dvm_data_seed_helper
mkdir -p "$RUN"
cd "$REPO"
WORK="$RUN/work" RESTORE_RAMDISK_OUT="$RAMDISK" \
  tools/rootfs/bootstrap_data_volume.sh ramdisk-helper
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  WORK="$RUN/work" PARENT="$BASE" OUT_OVL="$RUN/copy.qcow2" \
  TAG=BOOTSTRAP_COPY_REPRO1 \
  tools/rootfs/bootstrap_data_volume.sh copy-data
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  WORK="$RUN/work" PARENT="$RUN/copy.qcow2" OUT_OVL="$RUN/manifest.qcow2" \
  TAG=BOOTSTRAP_MANIFEST_REPRO1 \
  tools/rootfs/bootstrap_data_volume.sh manifest
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  WORK="$RUN/work" PARENT="$RUN/manifest.qcow2" OUT_OVL="$RUN/layout.qcow2" \
  TAG=BOOTSTRAP_LAYOUT_REPRO1 \
  tools/rootfs/bootstrap_data_volume.sh layout
RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" \
  WORK="$RUN/work" PARENT="$RUN/layout.qcow2" OUT_OVL="$RUN/marker.qcow2" \
  TAG=BOOTSTRAP_MARKER_REPRO1 \
  tools/rootfs/bootstrap_data_volume.sh marker
WORK="$RUN/work" PARENT="$RUN/marker.qcow2" \
  OUT_OVL="$RUN/boot1.qcow2" TAG=PERSIST_NVME_REPRO1_BOOT1 \
  NORMAL_BOOT_SECS=45 \
  tools/rootfs/bootstrap_data_volume.sh normal
WORK="$RUN/work" PARENT="$RUN/boot1.qcow2" \
  OUT_OVL="$RUN/boot2.qcow2" TAG=PERSIST_NVME_REPRO1_BOOT2 \
  NORMAL_BOOT_SECS=45 \
  tools/rootfs/bootstrap_data_volume.sh normal
```

The `copy-data`, `manifest`, `layout`, and `marker` phases require numeric
guest return-code witnesses and `sync` success before accepting a stage
(`bootstrap_data_volume.sh:416–456`).

For a fast persistent-storage regression, use the verified parent and two fresh
qcow2 children (the second boot is the no-recopy check):

```
REPO=/Users/jdolbe1/Downloads/darwin-vm
WORK=/tmp/dvm/data-seed/repro-persistent-1
BASE=/tmp/dvm/data-seed/roles-normal-preinit-tz1-class2-boot2.qcow2
cd "$REPO"
WORK="$WORK/work" PARENT="$BASE" OUT_OVL="$WORK/boot1.qcow2" \
  TAG=SKS_OP09_COMPLETE_REPRO1 NORMAL_BOOT_SECS=45 \
  tools/rootfs/bootstrap_data_volume.sh normal
WORK="$WORK/work" PARENT="$WORK/boot1.qcow2" OUT_OVL="$WORK/boot2.qcow2" \
  TAG=SKS_OP09_COMPLETE_REPRO2 NORMAL_BOOT_SECS=45 \
  tools/rootfs/bootstrap_data_volume.sh normal
```

The parent already contains the formatted, seeded, manifested, laid-out and
marked volumes; `normal` creates only derived children. The commands require
the locally built QEMU containing the corresponding ANS/SEP changes.
