#!/bin/bash
# snap_at_marker.sh - wait for a line to appear on the guest serial log, then
# freeze the guest and copy its RAM out, once per requested delay.
#
# Why: the interesting userspace failures on this machine leave no serial trace
# and write only into the /private/var tmpfs, which lives in guest RAM (see
# tools/guest_memgrep.py). To catch one you have to stop the guest inside a
# window only a few seconds wide - between the failure and launchd's reboot -
# which is not something probe.sh's --secs can express.
#
# Guest time does not advance while the VM is paused, so a 40 GiB dump (about
# 42 s of host time) costs the guest nothing; several snapshots of the same boot
# are free apart from disk.
#
# usage:
#   snap_at_marker.sh <serial.log> <monitor.sock> <outdir> <marker> <delay>[,<delay>...]
#
# example:
#   snap_at_marker.sh /tmp/dvm/x.serial.log /tmp/dvm/x.sock /tmp/dvm/snap \
#       'Early boot complete' 8,16
set -uo pipefail
LOG="$1"; SOCK="$2"; OUT="$3"; MARKER="$4"; DELAYS="$5"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HMP="$REPO/tools/hmp.py"
DUMP="$REPO/tools/guest_memgrep.py"

echo "waiting for: $MARKER"
for i in $(seq 1 3000); do
    grep -aq "$MARKER" "$LOG" 2>/dev/null && break
    perl -e 'select(undef,undef,undef,1.0)'
done
grep -aq "$MARKER" "$LOG" 2>/dev/null || { echo "marker never appeared"; exit 1; }
echo "marker seen"

prev=0
IFS=, read -ra ds <<< "$DELAYS"
for d in "${ds[@]}"; do
    wait=$((d - prev)); prev=$d
    [ "$wait" -gt 0 ] && perl -e "select(undef,undef,undef,$wait)"
    python3 "$HMP" "$SOCK" stop >/dev/null
    mkdir -p "$OUT/t$d"
    echo "--- snapshot at +${d}s ---"
    python3 "$DUMP" "$SOCK" chunk "$OUT/t$d" --step 0x40000000 | tail -1
    python3 "$HMP" "$SOCK" cont >/dev/null
done
echo done
