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
    local slog=/tmp/dvm/probe/BOOTSTRAP_FMT.serial.log
    local clog="$WORK/fmt.console.log"
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

    # probe.sh starts QEMU in a subshell, so killing only $probe_pid leaks the
    # guest.  Keep this armed on every error path, including a failed transport
    # handshake, and reap only our unique tag.
    cleanup_format() {
        kill "$probe_pid" 2>/dev/null || true
        pkill -f "BOOTSTRAP_FMT" 2>/dev/null || true
        pkill -f "unix:$sock" 2>/dev/null || true
    }
    trap cleanup_format EXIT INT TERM

    # Wait for the restore ramdisk's shell by watching the ACCUMULATING serial
    # log, not by reading the socket. serial.py *drains* the stream into its own
    # log, so polling it with `read` consumes the prompt and the next poll finds
    # nothing -- the first version of this script searched for evidence it had
    # already eaten, and declared "guest console never appeared" on a boot that
    # had reached the shell with 0 panics. --uart-socket still writes the serial
    # log (the chardev carries logfile=), so the log is the reliable witness.
    local waited=0
    while (( waited < 300 )); do
        perl -e 'sleep 5'; waited=$((waited+5))
        grep -qa "can't access tty" "$slog" 2>/dev/null && break
        grep -qa 'panic(cpu' "$slog" 2>/dev/null && {
            kill $probe_pid 2>/dev/null
            die "guest panicked before the shell; see $slog"
        }
    done
    grep -qa "can't access tty" "$slog" 2>/dev/null || {
        kill $probe_pid 2>/dev/null
        die "no shell after ${waited}s; see $slog"
    }
    say "[format] guest shell up after ${waited}s"

    # `command -v` is the positive control for a negative answer below.  The
    # earlier `cho: not found` came from a mangled echo, not from newfs_apfs, so
    # do not infer the formatter is absent until the guest has echoed and run
    # this checked command.  The probe chardev logfile, not socket output, is
    # the witness: it is QEMU's accumulating guest->host console record.
    rm -f "$clog"
    say "[format] positive control: locate newfs_apfs on the restore ramdisk"
    python3 "$REPO/tools/serial.py" "$sock" send \
        'command -v newfs_apfs; echo NEWFS_AVAILABLE_RC=$?' --secs 30 --log "$clog" \
        | tail -5
    local available=""
    waited=0
    while (( waited < 60 )); do
        available=$(grep -ao 'NEWFS_AVAILABLE_RC=[0-9]\+' "$slog" 2>/dev/null | tail -1 | cut -d= -f2)
        [ -n "$available" ] && break
        perl -e 'sleep 5'; waited=$((waited+5))
    done
    [ "$available" = "0" ] || die "newfs_apfs is unavailable on the restore ramdisk (rc=${available:-none}); see $slog"
    say "[format] positive control passed: restore ramdisk reported newfs_apfs rc=0"

    local before after
    before=$(stat -f%z "$OVL")
    say "[format] newfs_apfs -E on the Data slot (iOS makes the keys, not us)"
    python3 "$REPO/tools/serial.py" "$sock" send \
        'newfs_apfs -E -W -v Data -R D /dev/disk1s2; echo FMT_RC=$?' --secs 180 --log "$clog" \
        | tail -5

    # VERIFY, do not assert. The first version of this phase printed "done"
    # unconditionally: newfs_apfs never ran, FMT_RC came back as the literal
    # string, the overlay never grew, and the phase still reported success --
    # the exact "a silent no-op looks like success" failure this project keeps
    # being bitten by. Two independent witnesses now have to agree.
    local rc="" waited=0
    while (( waited < 180 )); do
        rc=$(grep -ao 'FMT_RC=[0-9]\+' "$slog" 2>/dev/null | tail -1 | cut -d= -f2)
        [ -n "$rc" ] && break
        perl -e 'sleep 5'; waited=$((waited+5))
    done
    after=$(stat -f%z "$OVL")

    say "[format] newfs_apfs rc=${rc:-<never reported>}  overlay ${before} -> ${after} bytes"
    [ "$rc" = "0" ] || die "newfs_apfs did not report success (rc=${rc:-none}); see $slog"
    (( after > before )) || die "newfs_apfs reported success but wrote nothing to $OVL -- a real format writes a superblock"
    say "[format] verified: the encrypted Data volume is in $OVL"
    cleanup_format
    trap - EXIT INT TERM
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
