#!/bin/bash
# rebuild_persistent_parent.sh - regenerate the persistent-NVMe parent image
# (the thing every display/Setup probe boots from) from the surviving base
# disk, end to end, with a timestamp per stage.
#
# The chain is the one recorded in docs/re/persistent-data-volume.md; it was
# first run by hand on 2026-09-03 and its outputs lived only under /tmp/dvm,
# which a host reboot wiped the same afternoon.  Everything here is derived, so
# a rerun is the recovery.  Stages, each a fresh qcow2 child of the previous:
#
#   format          restore-ramdisk boot: iOS newfs_apfs -E the Data slot, then
#                   the role-User volume (only iOS can make the encrypted volume)
#   ramdisk-helper  host-side: derived restore ramdisk carrying data_seed_helper
#   copy-data       restore boot: helper seeds Data from the /private/var template
#   manifest        restore boot: primary-user manifest on Data+User
#   layout          restore boot: UML user layout
#   marker          restore boot: /private/var/.dvm-data-seed-complete
#   normal x2       system-volume boots off NVMe; the second proves no recopy
#
# usage: tools/rootfs/rebuild_persistent_parent.sh [RUN_DIR]
#   BASE_DMG   raw image with Data/Preboot/Hardware slots
#              (default ~/dvm-artifacts/build/rootfs_cx_dual_roles.dmg, from the
#              `image` phase of bootstrap_data_volume.sh)
#   START_AT   stage name to resume from (default format)
# The result is $RUN_DIR/boot2.qcow2; a stable name is linked at
# /tmp/dvm/data-seed/persistent-parent.qcow2.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN="${1:-/tmp/dvm/data-seed/rebuild}"
BASE_DMG="${BASE_DMG:-$HOME/dvm-artifacts/build/rootfs_cx_dual_roles.dmg}"
START_AT="${START_AT:-format}"
WORK="$RUN/work"
RAMDISK="$RUN/ramdisk-data-seed.dmg"
HELPER=/libexec/dvm_data_seed_helper
B="$REPO/tools/rootfs/bootstrap_data_volume.sh"
QIMG="$REPO/qemu-sptm/build/qemu-img"
mkdir -p "$RUN" "$WORK" /tmp/dvm/probe
[[ -f "$BASE_DMG" ]] || { echo "missing base image $BASE_DMG" >&2; exit 1; }
[[ -f /tmp/dvm/dtree_raw ]] || ipsw img4 im4p extract --output /tmp/dvm/dtree_raw \
    "$REPO/ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p" >/dev/null

STAGES=(format ramdisk-helper copy-data manifest layout marker normal1 normal2)
active=0
stage() {  # stage NAME -- runs the command only once START_AT has been reached
    local name=$1; shift
    [[ "$name" == "$START_AT" ]] && active=1
    (( active )) || { echo "SKIP  $name"; return 0; }
    echo "STAGE $name start $(date +%T)"
    local t0=$SECONDS
    if "$@"; then
        echo "STAGE $name ok    $(date +%T) (+$((SECONDS-t0))s)"
    else
        echo "STAGE $name FAILED $(date +%T) (+$((SECONDS-t0))s) rc=$?"
        exit 1
    fi
}

do_format() {
    if [[ ! -e "$RUN/fresh-format.qcow2" ]]; then
        "$QIMG" create -f qcow2 -F raw -b "$BASE_DMG" "$RUN/fresh-format.qcow2" >/dev/null || return 1
    fi
    OUT="$BASE_DMG" OVL="$RUN/fresh-format.qcow2" WORK="$WORK" "$B" format
}
do_ramdisk_helper() {
    [[ -e "$RAMDISK" ]] && { echo "  reusing $RAMDISK"; return 0; }
    WORK="$WORK" RESTORE_RAMDISK_OUT="$RAMDISK" "$B" ramdisk-helper
}
do_restore_stage() {  # do_restore_stage MODE PARENT CHILD TAG
    RESTORE_RAMDISK="$RAMDISK" RESTORE_HELPER_SOURCE="$HELPER" WORK="$WORK" \
    PARENT="$2" OUT_OVL="$3" TAG="$4" RESTORE_STAGE_TIMEOUT="${RESTORE_STAGE_TIMEOUT:-900}" "$B" "$1"
}
do_normal() {  # do_normal PARENT CHILD TAG SECS
    WORK="$WORK" PARENT="$1" OUT_OVL="$2" TAG="$3" NORMAL_BOOT_SECS="$4" "$B" normal
}

stage format         do_format
stage ramdisk-helper do_ramdisk_helper
stage copy-data      do_restore_stage copy-data "$RUN/fresh-format.qcow2" "$RUN/copy.qcow2"     REBUILD_COPY1
stage manifest       do_restore_stage manifest  "$RUN/copy.qcow2"         "$RUN/manifest.qcow2" REBUILD_MANIFEST1
stage layout         do_restore_stage layout    "$RUN/manifest.qcow2"     "$RUN/layout.qcow2"   REBUILD_LAYOUT1
stage marker         do_restore_stage marker    "$RUN/layout.qcow2"       "$RUN/marker.qcow2"   REBUILD_MARKER1
stage normal1        do_normal "$RUN/marker.qcow2" "$RUN/boot1.qcow2" REBUILD_BOOT1 "${BOOT1_SECS:-300}"
stage normal2        do_normal "$RUN/boot1.qcow2"  "$RUN/boot2.qcow2" REBUILD_BOOT2 "${BOOT2_SECS:-150}"

ln -sf "$RUN/boot2.qcow2" /tmp/dvm/data-seed/persistent-parent.qcow2
echo "DONE parent=$RUN/boot2.qcow2 (linked as /tmp/dvm/data-seed/persistent-parent.qcow2) $(date +%T)"
"$QIMG" info --backing-chain "$RUN/boot2.qcow2" | grep -E '^image|disk size'
