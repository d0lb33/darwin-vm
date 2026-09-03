#!/bin/bash
# Build a bootable iOS disk with a real, persistent /private/var -- and rebuild
# it from scratch whenever the guest's Data volume gets into a bad state.
#
# This is the "restore" for this project. Everything it produces is derived, so
# losing the outputs costs a rerun rather than the work.
#
# WHY THREE PHASES
#
# /private/var has to be a real APFS volume, not the -ephemeral-data tmpfs that
# every boot re-seeds by copying 41,557 files (~213 s of guest time, roughly 85%
# of boot). Three things have to be true at once, and each defeated a simpler
# approach:
#
#   1. The image needs a Data volume slot. macOS can create that (`diskutil apfs
#      addVolume -role D`), so `build_data_volume.sh` does it -- see its header
#      for why it does NOT try to populate the volume, and how the earlier
#      clone-and-delete approach kernel-panicked this host three times.
#
#   2. The volume must be ENCRYPTED. XNU refuses otherwise:
#        panic: "unencrypted data volume is not allowed" @apfs_vfsops.c:2399
#      and a volume created by macOS `diskutil` is not encrypted in the way iOS
#      requires. Only iOS can make one, using its own keystore -- which is why
#      phase 2 boots the restore ramdisk and runs the guest's own `newfs_apfs`.
#      This works because the SEP `sks` endpoint is implemented; see
#      docs/re/sks-feasibility.md.
#
#   3. The volume must be POPULATED. We cannot do this from the host or from a
#      shell: the /private/var template files carry data-protection classes that
#      an empty keybag cannot unlock, and `cp` panics reading MobileAsset even
#      with --reflink=never. iOS's own `mount-phase-2` boot task can, because it
#      has the keybag. That is what phase 3 is for.
#
# Usage:
#   tools/rootfs/bootstrap_data_volume.sh [all|image|format|seed|verify]
#
# Env:
#   SRC   cryptex-merged system volume  (default ~/dvm-artifacts/build/rootfs_cx.dmg)
#   OUT   disk image to build           (default ~/dvm-artifacts/build/rootfs_cx_dual.dmg)
#   OVL   qcow2 overlay the guest writes to
#   WORK  scratch dir for trees, sockets and logs (default /tmp/dvm/bootstrap)
set -uo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
MODE=${1:-all}
SRC=${SRC:-$HOME/dvm-artifacts/build/rootfs_cx.dmg}
OUT=${OUT:-$HOME/dvm-artifacts/build/rootfs_cx_dual.dmg}
OVL=${OVL:-$HOME/dvm-artifacts/build/rootfs_cx_dual-overlay.qcow2}
TC=${TC:-$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin}
WORK=${WORK:-/tmp/dvm/bootstrap}
QEMU_IMG="$REPO/qemu-sptm/build/qemu-img"
BOOTARGS_COMMON='ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort'

die() { echo "!! $*" >&2; exit 1; }
say() { echo "==> $*"; }

mkdir -p "$WORK"
[ -f "$SRC" ] || die "missing source image: $SRC"
[ -f "$TC" ]  || die "missing trustcache: $TC (boots fail with 'code signature registration for shared cache failed')"
NVRAM=$(ls "$REPO"/.claude/worktrees/*/nvram.bin 2>/dev/null | head -1)
[ -n "$NVRAM" ] || die "no nvram.bin found; dt_fixup.py requires -nvram"

# ---------------------------------------------------------------- phase 1 ----
# Add the Data / Preboot / Hardware volume slots. Host-side, no mounting.
phase_image() {
    say "[image] build $OUT from $SRC"
    SRC="$SRC" "$REPO/tools/rootfs/build_data_volume.sh" "$OUT" 4 \
        || die "build_data_volume.sh failed"
    say "[image] fresh overlay $OVL"
    rm -f "$OVL"
    "$QEMU_IMG" create -f qcow2 -F raw -b "$OUT" "$OVL" >/dev/null \
        || die "could not create overlay"
}

# ---------------------------------------------------------------- phase 2 ----
# Let iOS create the encrypted Data volume. Only the guest can do this: the
# encryption keys come from the SEP keystore, so newfs_apfs must run inside.
phase_format() {
    [ -f "$OVL" ] || die "no overlay; run the image phase first"
    local dt="$WORK/dt_ramdisk.bin" sock="$WORK/fmt.sock"
    say "[format] device tree: ans + smc + sep on the restore ramdisk"
    python3 "$REPO/dt_fixup.py" "$REPO/../dtree_raw" "$dt" -nvram "$NVRAM" \
        -enable ans -enable smc -enable sep -dram 12G 2>/dev/null \
      || python3 "$REPO/dt_fixup.py" /tmp/dvm/dtree_raw "$dt" -nvram "$NVRAM" \
        -enable ans -enable smc -enable sep -dram 12G \
      || die "dt_fixup failed (need the raw device tree; see CLAUDE.md)"

    rm -f "$sock"
    say "[format] booting the restore ramdisk with a driveable console"
    "$REPO/tools/probe.sh" --dtree "$dt" --mem 12G --secs 240 --tag BOOTSTRAP_FMT \
        --uart-socket "$sock" \
        --bootargs "rd=md0 $BOOTARGS_COMMON" \
        -- -drive "if=none,id=ans,file=$OVL,format=qcow2" >/dev/null 2>&1 &
    local probe_pid=$!

    # Wait for the restore ramdisk's shell rather than sleeping a fixed time.
    local waited=0
    while (( waited < 200 )); do
        perl -e 'sleep 5'; waited=$((waited+5))
        [ -S "$sock" ] && python3 "$REPO/tools/serial.py" "$sock" read --secs 2 2>/dev/null \
            | grep -q "can't access tty" && break
    done
    [ -S "$sock" ] || { kill $probe_pid 2>/dev/null; die "guest console never appeared"; }

    say "[format] newfs_apfs -E on the Data slot (iOS makes the keys, not us)"
    python3 "$REPO/tools/serial.py" "$sock" send \
        'newfs_apfs -E -W -v Data -R D /dev/disk1s2; echo FMT_RC=$?' --secs 120 \
        | tail -5
    python3 "$REPO/tools/serial.py" "$sock" send 'diskutil list; echo LIST_RC=$?' --secs 30 \
        | grep -iE "disk1s|Data" | head -6

    kill $probe_pid 2>/dev/null
    pkill -f "unix:$sock" 2>/dev/null
    say "[format] done; the encrypted volume now lives in $OVL"
}

# ---------------------------------------------------------------- phase 3 ----
# Boot the system volume with NO -ephemeral-data and let mount-phase-2 seed the
# volume from the on-volume template. This is the only actor that can: it runs
# inside iOS with the keybag, so the protection classes come out right.
phase_seed() {
    [ -f "$OVL" ] || die "no overlay; run the image and format phases first"
    local dt="$WORK/dt_sysvol.bin"
    say "[seed] device tree: no -ephemeral-data, keybag skipped"
    python3 "$REPO/dt_fixup.py" /tmp/dvm/dtree_raw "$dt" -nvram "$NVRAM" \
        -enable ans -enable smc -enable sep -skip-keybag -dram 12G \
        || die "dt_fixup failed"
    say "[seed] booting the system volume off the NVMe disk (first boot pays the seed once)"
    "$REPO/tools/probe.sh" --dtree "$dt" --tc "$TC" --mem 12G --secs 1200 \
        --tag BOOTSTRAP_SEED \
        --bootargs "rootdev=disk1s1 $BOOTARGS_COMMON" \
        -- -drive "if=none,id=ans,file=$OVL,format=qcow2" | tail -8
}

# ---------------------------------------------------------------- verify -----
phase_verify() {
    local L=/tmp/dvm/probe/BOOTSTRAP_SEED.serial.log
    [ -f "$L" ] || die "no seed log; run the seed phase first"
    say "[verify] what the seed boot achieved"
    printf "    BSD root            : %s\n" "$(grep -ao 'BSD root: [a-z0-9]*' "$L" | head -1 | cut -d' ' -f3)"
    printf "    mount-phase-2       : %s\n" "$(grep -ao '(mount-phase-2) <Notice>: [A-Za-z -]*' "$L" | head -1)"
    printf "    Data mounted on var : %s\n" "$(grep -ao '/dev/disk1s[0-9]* on /private/var[^\"]*' "$L" | head -1)"
    printf "    files seeded        : %s\n" "$(grep -ac 'Copying ' "$L")"
    printf "    Early boot complete : %s\n" "$(grep -ac 'Early boot complete' "$L")"
    printf "    unencrypted panic   : %s\n" "$(grep -ac 'unencrypted data volume' "$L")"
    printf "    xnu panics          : %s\n" "$(grep -ac 'panic(cpu' "$L")"
    echo
    echo "    A second run of the seed phase should show 0 'Copying' lines and still"
    echo "    mount /private/var -- that is the persistence proof, and the point of"
    echo "    the whole exercise."
}

case "$MODE" in
    image)  phase_image ;;
    format) phase_format ;;
    seed)   phase_seed ;;
    verify) phase_verify ;;
    all)    phase_image; phase_format; phase_seed; phase_verify ;;
    *) die "usage: $0 [all|image|format|seed|verify]" ;;
esac
