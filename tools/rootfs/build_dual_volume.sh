#!/bin/bash
# Stage 3: turn the single-volume root filesystem image into a multi-volume
# APFS container a real storage controller can boot from -- a System volume
# (role 0x1) and a *persistent* Data volume (role 0x40) pre-populated with the
# /private/var template, plus the small satellite roles iOS's fstab names
# (Preboot 0x10, Update 0xc0, xART 0x100, Hardware 0x140).
#
# Worth doing because 228.1 of the 256.6 seconds to "Early boot complete" is
# mount-phase-2 rebuilding /private/var as a tmpfs from 41,557 files on every
# single boot. With a real Data volume it mounts instead of copying, and the
# result survives reboot.
#
# ---------------------------------------------------------------------------
# How the Data volume gets *correctly owned* content without sudo
# ---------------------------------------------------------------------------
#
# The /private/var template is owned by root, mobile(501), _networkd,
# _appstore, _driverkit, _installassistant, uid 64, ... , so ownership is not
# optional: launchd rejects plists with bad ownership, and daemons running as
# those users must be able to write their own directories.
#
# Measured on this machine, 2026-09-02:
#
#   * chown(2) is refused for uid 501 on a disk image we own, on both an
#     owners-on and a noowners mount:
#         chown: /tmp/dvm/dv_data/testfile: Operation not permitted
#     and files created by uid 501 land on disk as 501:20 (confirmed by
#     unmounting and remounting with -mountOptions owners).
#
#   * The donor-rename trick used by build_rootfs.sh does not reach across
#     volumes: rename(2) is EXDEV between two APFS volumes even inside one
#     container. A freshly created volume has exactly one root-owned inode --
#     its own root directory, which cannot be renamed -- so there is nothing
#     to donate. `diskutil apfs addVolume` then mount gives a root:wheel 0755
#     volume root that uid 501 cannot even create a file in.
#
#   * What does work, with no authentication prompt at all:
#       - hdiutil hands the attaching user a *writable* device node
#             brw-r-----  1 jdolbe1  staff  /dev/disk15s2
#       - `diskutil apfs addVolume <cont> ... -role D` succeeds as uid 501
#         ("Ownership of the affected disks is required" is satisfied by
#          owning the device node, not by being root)
#       - `asr restore --source <live volume> --target /dev/diskNsM --erase`
#         succeeds as uid 501 and replicates at the APFS object level,
#         preserving on-disk ownership.
#
#     Ownership preservation was verified directly: a scratch volume whose
#     /.fseventsd had been created by the root fseventsd daemon, and whose
#     root directory had been created by uid 501, restored to a target where
#     an owners-on mount read .fseventsd as uid 0 and the root as uid 501.
#     asr did not flatten either one.
#
# So the Data volume's content is staged by *restructuring a copy-on-write
# clone of the system volume in place*: every entry of /private/var is moved
# to the volume root with mv, which is rename(2) inside one volume, so no
# inode is ever recreated and every uid/gid/mode/flag/xattr survives untouched.
# Everything that is not the template is deleted. That staged volume is then
# asr-restored into the Data volume of the target container.
#
# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------
# The container is NOT grown by default. The source image is 22.9 GB with
# 16.8 GB consumed, so 6.1 GB is already unallocated and APFS shares free
# space across all volumes in the container; seeding the ~700 MB template
# leaves the Data volume better than 5 GB of headroom. Growing the file costs
# guest RAM one-for-one while we still boot this as a memdev ramdisk, and a
# 40G guest is already at the limit (XNU panics "Unsupported memory
# configuration" at mem_size >= 56 GiB). Set GROW_MB to override.
#
# Usage: tools/rootfs/build_dual_volume.sh [stage|assemble|verify|all] [workdir]
set -uo pipefail
set -e

MODE=${1:-all}
WORK=${2:-/tmp/dvm}
SRC=${SRC:-$WORK/build/rootfs_sh.dmg}
OUT=${OUT:-$WORK/build/rootfs_dual.dmg}
STAGE=${STAGE:-$WORK/build/var_stage.dmg}
GROW_MB=${GROW_MB:-0}

STAGEMNT=$WORK/dv_stage
DATAMNT=$WORK/dv_data
mkdir -p "$STAGEMNT" "$DATAMNT" "$(dirname "$OUT")"

# Everything the system volume has at its root that is *not* part of the
# /private/var template. Deleted before the template is lifted to the root so
# that no name collides (the root's "tmp" is a symlink to private/var/tmp).
ROOT_JUNK=(.b .file .nofollow .resolve .fseventsd .DS_Store .VolumeIcon.icns \
           Applications Developer Library System bin cores dev etc sbin tmp usr var)

die() { echo "!! $*" >&2; exit 1; }
detach_ours() { for d in "$@"; do [ -n "$d" ] && hdiutil detach "$d" -quiet >/dev/null 2>&1 || true; done; }
# role name as printed by `diskutil apfs list`: "APFS Volume Disk (Role):   disk15s2 (Data)"
volof() { diskutil apfs list "$1" | awk -v r="($2)" '/APFS Volume Disk \(Role\)/ && index($0,r){print "/dev/"$5; exit}'; }
setrole() {  # $1 volume device, $2 role letter -- clear first, roles are not a plain bitmask
    diskutil apfs chrole "$1" clear >/dev/null 2>&1 || true
    diskutil apfs chrole "$1" "$2"  >/dev/null
}

# ---------------------------------------------------------------- stage ----
stage() {
    [ -f "$SRC" ] || die "missing $SRC"
    echo "==> [stage] clone $SRC -> $STAGE (APFS copy-on-write, instant, no space cost)"
    rm -f "$STAGE"; cp -c "$SRC" "$STAGE"

    local cont vol
    cont=$(hdiutil attach -nomount "$STAGE" 2>/dev/null | awk '/EF57347C/{print $1; exit}')
    [ -n "$cont" ] || die "no APFS container in $STAGE"
    vol=$(diskutil list "$cont" | awk '/APFS Volume/{print "/dev/"$NF; exit}')
    echo "    container $cont, volume $vol"
    # A snapshot would keep deleted blocks alive and would be replicated by asr.
    echo "    snapshots on the staged volume: $(diskutil apfs listSnapshots "$vol" 2>/dev/null | grep -c 'Snapshot UUID' || true)"

    # noowners: the tree is root-owned and we are not root, so the kernel has to
    # stop enforcing it. rename(2) never touches an inode's uid, so how the
    # volume is mounted has no effect on the ownership recorded on disk.
    diskutil mount -mountPoint "$STAGEMNT" -mountOptions noowners "$vol" >/dev/null
    [ -d "$STAGEMNT/private/var" ] || die "no private/var in the staged volume"
    echo "    template: $(find "$STAGEMNT/private/var" | wc -l | tr -d ' ') entries, $(du -sh "$STAGEMNT/private/var" | awk '{print $1}')"

    echo "==> [stage] delete everything that is not the template"
    for j in "${ROOT_JUNK[@]}"; do
        [ -e "$STAGEMNT/$j" ] || [ -L "$STAGEMNT/$j" ] || continue
        rm -rf "$STAGEMNT/$j" 2>/dev/null || {
            chflags -R nouchg "$STAGEMNT/$j" 2>/dev/null || true
            rm -rf "$STAGEMNT/$j" || die "could not remove $j"
        }
    done
    local leftover
    leftover=$(ls -A "$STAGEMNT" | grep -vx private || true)
    [ -z "$leftover" ] || die "unexpected leftovers at the volume root: $leftover"
    for p in $(ls -A "$STAGEMNT/private" | grep -vx var || true); do rm -rf "$STAGEMNT/private/$p"; done

    echo "==> [stage] lift private/var to the volume root (rename, inodes preserved)"
    local n=0
    while IFS= read -r e; do
        mv "$STAGEMNT/private/var/$e" "$STAGEMNT/$e" || die "mv $e"
        n=$((n+1))
    done < <(cd "$STAGEMNT/private/var" && ls -A)
    rmdir "$STAGEMNT/private/var" "$STAGEMNT/private" || die "private/ not empty after the lift"
    sync
    echo "    lifted $n top-level entries; $(du -sh "$STAGEMNT" | awk '{print $1}') used, $(find "$STAGEMNT" -mindepth 1 | wc -l | tr -d ' ') entries"

    # asr copies the source volume's name and role onto the target, so set them
    # here. Both need the volume mounted ("Volume must be mounted" otherwise).
    diskutil rename "$vol" Data >/dev/null
    setrole "$vol" D
    diskutil unmount "$STAGEMNT" >/dev/null
    diskutil apfs list "$cont" | grep -E "Role\)|Name:"
    detach_ours "$cont"
    echo "==> [stage] done: $STAGE"
}

# ------------------------------------------------------------- assemble ----
assemble() {
    [ -f "$SRC" ]   || die "missing $SRC"
    [ -f "$STAGE" ] || die "missing $STAGE (run the stage phase first)"
    echo "==> [assemble] clone $SRC -> $OUT"
    rm -f "$OUT"; cp -c "$SRC" "$OUT"
    if [ "$GROW_MB" -gt 0 ]; then
        echo "==> [assemble] grow by ${GROW_MB} MB"
        dd if=/dev/zero bs=1m count="$GROW_MB" >> "$OUT" 2>/dev/null
    fi

    local cont sysvol datavol scont svol
    cont=$(hdiutil attach -nomount "$OUT" 2>/dev/null | awk '/EF57347C/{print $1; exit}')
    [ -n "$cont" ] || die "no APFS container in $OUT"
    [ "$GROW_MB" -gt 0 ] && diskutil apfs resizeContainer "$cont" 0 >/dev/null
    sysvol=$(volof "$cont" System)
    echo "    container $cont, system volume $sysvol"

    echo "==> [assemble] create Data (role D)"
    datavol=$(diskutil apfs addVolume "$cont" "Case-sensitive APFS" Data -role D -nomount \
              | awk '/Created new APFS Volume/{print "/dev/"$NF}')
    [ -n "$datavol" ] || die "addVolume Data failed"

    echo "==> [assemble] asr-restore the staged template into $datavol"
    scont=$(hdiutil attach -nomount "$STAGE" 2>/dev/null | awk '/EF57347C/{print $1; exit}')
    svol=$(diskutil list "$scont" | awk '/APFS Volume/{print "/dev/"$NF; exit}')
    diskutil mount -mountPoint "$STAGEMNT" -mountOptions owners "$svol" >/dev/null
    asr restore --source "$STAGEMNT" --target "$datavol" --erase --noprompt 2>&1 | tail -4
    diskutil unmount "$STAGEMNT" >/dev/null
    detach_ours "$scont"

    # apfs_restore recreates the target volume, so re-resolve it and re-assert
    # name and role rather than trusting what asr left behind.
    datavol=$(diskutil apfs list "$cont" | awk '/APFS Volume Disk \(Role\)/ && !index($0,"(System)"){print "/dev/"$5; exit}')
    diskutil mount -mountPoint "$DATAMNT" -mountOptions noowners "$datavol" >/dev/null
    diskutil rename "$datavol" Data >/dev/null 2>&1 || true
    setrole "$datavol" D
    diskutil unmount "$DATAMNT" >/dev/null
    echo "    data volume is $datavol"

    echo "==> [assemble] create the satellite role volumes"
    #  Preboot  0x10   -> /private/preboot                  (cryptex graft target)
    #  Update   0xc0   -> /private/var/MobileSoftwareUpdate
    #  xART     0x100  -> /private/xarts
    #  Hardware 0x140  -> /private/var/hardware
    # Empty is the correct first-boot state for all four. They cost ~1 MB of
    # APFS metadata each and remove a class of "no volume with role N" failures
    # from mount-phase-1/-2. All four mount points already exist: /private/
    # preboot and /private/xarts on the system volume, MobileSoftwareUpdate and
    # hardware inside the template now on the Data volume.
    for spec in "Preboot:B" "Update:E" "xART:X" "Hardware:H"; do
        local nm=${spec%%:*} rl=${spec##*:}
        diskutil apfs addVolume "$cont" "Case-sensitive APFS" "$nm" -role "$rl" -nomount \
            | awk -v n="$nm" '/Created new APFS Volume/{print "    "n" -> /dev/"$NF}'
    done

    sync
    detach_ours "$cont"
    echo "==> [assemble] done: $OUT"
}

# --------------------------------------------------------------- verify ----
verify() {
    echo "==> [verify] re-attach $OUT"
    local cont datavol
    cont=$(hdiutil attach -nomount "$OUT" 2>/dev/null | awk '/EF57347C/{print $1; exit}')
    [ -n "$cont" ] || die "re-attach produced no container"
    diskutil apfs list "$cont"

    datavol=$(volof "$cont" Data)
    [ -n "$datavol" ] || die "no volume with role Data after re-attach"
    echo "==> [verify] Data volume $datavol mounted with owners ON"
    diskutil mount -mountPoint "$DATAMNT" -mountOptions owners "$datavol" >/dev/null
    ls -aln "$DATAMNT" | head -40
    echo "==> [verify] uid:gid histogram over the whole Data volume"
    find "$DATAMNT" -mindepth 1 -exec stat -f '%u:%g' {} + | sort | uniq -c | sort -rn
    echo "==> [verify] entries: $(find "$DATAMNT" -mindepth 1 | wc -l | tr -d ' ')"
    echo "==> [verify] setuid/setgid preserved: $(find "$DATAMNT" -type f -perm +6000 2>/dev/null | wc -l | tr -d ' ')"
    diskutil unmount "$DATAMNT" >/dev/null
    detach_ours "$cont"
    ls -l "$OUT"
}

case "$MODE" in
  stage)    stage ;;
  assemble) assemble ;;
  verify)   verify ;;
  all)      stage; assemble; verify ;;
  *) die "usage: $0 [stage|assemble|verify|all] [workdir]" ;;
esac
