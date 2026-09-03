#!/bin/bash
# clock_test.sh - does guest time run at host speed, and do guest sleeps wake on time?
#
# Boots the restore ramdisk to its shell, then times `sleep N` and `date +%s`
# deltas from the host side.  Written on 2026-09-03 when backboardd's render
# server was seen receiving queued Mach messages only in ~100 s bursts
# (docs/re/setup-launch-runtime.md); a slow guest counter or a timer that does
# not fire would explain that without any display-model fault.
#
# usage: tools/clock_test.sh [TAG]   (outputs under /tmp/dvm, verdict on stdout)
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="${1:-CLOCKTEST}"
SOCK="/tmp/dvm/$TAG.uart.sock"
SLOG="/tmp/dvm/probe/$TAG.serial.log"
mkdir -p /tmp/dvm/probe
rm -f "$SOCK"
"$REPO/tools/probe.sh" --secs 400 --tag "$TAG" --uart-socket "$SOCK" --keep > "/tmp/dvm/$TAG.probe.out" 2>&1 &
PROBE=$!
t0=$(date +%s.%N)
w=0
until grep -qa "can't access tty" "$SLOG" 2>/dev/null; do perl -e 'sleep 2'; w=$((w+2)); (( w > 300 )) && { echo "no shell after ${w}s"; exit 1; }; done
echo "shell up after $(python3 -c "import time;print(round(time.time()-$t0,1))") s host"
wait_marker() {  # wait_marker MARKER TIMEOUT -> prints host seconds until it appears
    local m=$1 lim=$2 s; s=$(date +%s.%N)
    # match the marker on its own line, not the shell's echo of the command
    while ! grep -qaE -- "^$m"'\r?$' "$SLOG"; do perl -e 'select(undef,undef,undef,0.2)'; python3 -c "import time,sys; sys.exit(0 if time.time()-$s < $lim else 1)" || { echo "timeout"; return 1; }; done
    python3 -c "import time;print(round(time.time()-$s,2))"
}
python3 "$REPO/tools/serial.py" "$SOCK" send 'date +%s; echo T_A_DONE' --secs 5 --log "/tmp/dvm/$TAG.console.log" >/dev/null
wait_marker T_A_DONE 30 >/dev/null
hostA=$(date +%s.%N); guestA=$(grep -aoE '^[0-9]{6,}' "$SLOG" | tail -1)
for n in 5 20; do
    s=$(date +%s.%N)
    python3 "$REPO/tools/serial.py" "$SOCK" send "sleep $n; echo SLEEP_${n}_DONE" --secs 3 --log "/tmp/dvm/$TAG.console.log" >/dev/null 2>&1 || true
    took=$(wait_marker "SLEEP_${n}_DONE" $((n*6+30)))
    echo "guest sleep $n -> host ${took}s"
done
python3 "$REPO/tools/serial.py" "$SOCK" send 'date +%s; echo T_B_DONE' --secs 5 --log "/tmp/dvm/$TAG.console.log" >/dev/null 2>&1 || true
wait_marker T_B_DONE 30 >/dev/null
hostB=$(date +%s.%N); guestB=$(grep -aoE '^[0-9]{6,}' "$SLOG" | tail -1)
python3 - "$hostA" "$hostB" "$guestA" "$guestB" <<'PY'
import sys
ha,hb,ga,gb=map(float,sys.argv[1:])
print("guest clock advanced %.1f s while host advanced %.1f s  (ratio guest/host = %.2f)" % (gb-ga, hb-ha, (gb-ga)/(hb-ha) if hb>ha else 0))
PY
kill "$PROBE" 2>/dev/null; pkill -f "unix:/tmp/dvm/$TAG.sock" 2>/dev/null
