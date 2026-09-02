#!/bin/bash
# probe.sh - boot the VM headless, let it run, then report where it got to.
#
# This is the main inner-loop tool for bring-up work: it boots qemu-sptm with a
# given device tree / kernelcache, waits, freezes the guest, and prints a short
# verdict (serial progress, XNU panic, SPTM panic text, CPU state) plus the paths
# of the full logs.
#
# usage:
#   tools/probe.sh [options]
#
# options:
#   --dtree FILE      device tree (default: firmware/dtree)
#   --bootkc FILE     kernelcache (default: firmware/bootkc)
#   --secs N          seconds to let the guest run before freezing (default: 60)
#   --bootargs STR    XNU boot-args (default: rd=md0 serial=3 -v wdt=-1 wlan-olyhal-abort)
#   --out DIR         where logs go (default: /tmp/dvm/probe)
#   --tag NAME        name for this run; lets several probes run in parallel
#   --ramdisk FILE    ramdisk image (default: firmware/ramdisk.dmg)
#   --tc FILE         trustcache (default: firmware/ramdisk.tc)
#   --mem SIZE        guest RAM, eg. 24G (default: 8G). Must match the device
#                     tree's dram-size, which dt_fixup sets with -dram.
#   --grep PATTERN    extra egrep pattern to pull out of the serial log
#   --keep            leave the VM running (frozen) instead of killing it
#   NO_WATCHDOG=1     wait the full --secs even if the guest is visibly dead
#   STALL_AFTER_PANIC seconds of silence after a panic before giving up (20)
#   STALL_SECS        seconds of total silence before calling it hung (180)
#   --                everything after this is passed straight to qemu
#
# environment:
#   DARWIN_AIC_DEBUG / DARWIN_ASC_DEBUG / DARWIN_DART_DEBUG / DARWIN_UNIMP_DEBUG
#     set any of these to 1 to trace that device model into the stderr log.
#
# output files (under --out, prefixed by --tag):
#   <tag>.serial.log   guest serial console
#   <tag>.stderr.log   qemu stderr, including device model traces
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QEMU="$REPO/qemu-sptm/build/qemu-system-aarch64"
HMP="$REPO/tools/hmp.py"

DTREE="$REPO/firmware/dtree"
BOOTKC="$REPO/firmware/bootkc"
SECS=60
BOOTARGS="rd=md0 serial=3 -v wdt=-1 wlan-olyhal-abort"
OUT=/tmp/dvm/probe
TAG=probe
RAMDISK=""
TC=""
MEM=8G
GREP_EXTRA=""
KEEP=0
EXTRA_QEMU=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dtree)    DTREE="$2"; shift 2 ;;
        --bootkc)   BOOTKC="$2"; shift 2 ;;
        --secs)     SECS="$2"; shift 2 ;;
        --bootargs) BOOTARGS="$2"; shift 2 ;;
        --out)      OUT="$2"; shift 2 ;;
        --tag)      TAG="$2"; shift 2 ;;
        --ramdisk)  RAMDISK="$2"; shift 2 ;;
        --tc)       TC="$2"; shift 2 ;;
        --mem)      MEM="$2"; shift 2 ;;
        --grep)     GREP_EXTRA="$2"; shift 2 ;;
        --keep)     KEEP=1; shift ;;
        --)         shift; EXTRA_QEMU=("$@"); break ;;
        -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
        *)          echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ -z "$RAMDISK" ]] && RAMDISK="$REPO/firmware/ramdisk.dmg"
[[ -z "$TC" ]] && TC="$REPO/firmware/ramdisk.tc"

for f in "$QEMU" "$DTREE" "$BOOTKC" "$RAMDISK" "$TC"; do
    [[ -e "$f" ]] || { echo "probe: missing $f" >&2; exit 1; }
done

mkdir -p "$OUT"
# UNIX socket paths are capped near 104 bytes, so sockets always live in /tmp/dvm
mkdir -p /tmp/dvm
SOCK="/tmp/dvm/$TAG.sock"
SERIAL="$OUT/$TAG.serial.log"
ERR="$OUT/$TAG.stderr.log"
rm -f "$SOCK" "$SERIAL" "$ERR"

ARGS=(
    -M darwin
    -bootkc "$BOOTKC"
    -dtree "$DTREE"
    -tc "$TC"
    -ramdisk "$RAMDISK"
    -args "$BOOTARGS"
    -display none
    -monitor "unix:$SOCK,server,nowait"
    -serial "file:$SERIAL"
    -m "$MEM"
)
[[ -f "$REPO/firmware/sptm" ]] && ARGS+=(-sptm "$REPO/firmware/sptm" -txm "$REPO/firmware/txm")
[[ ${#EXTRA_QEMU[@]} -gt 0 ]] && ARGS+=("${EXTRA_QEMU[@]}")

( "$QEMU" "${ARGS[@]}" 2> "$ERR" & )

# Wait, but stop early once the guest can no longer make progress. Without this
# every dead guest costs the full --secs: on 2026-09-02 three separate agents
# (and the orchestrator, three times) sat watching a spinning VM to its timeout,
# because a panicked guest looks exactly like a working one from outside -- the
# process stays at ~100% CPU and only the serial log going quiet gives it away.
#
# Terminal conditions, each measured on this machine:
#   * "Nested panic count exceeds limit" -- the guest says outright it will
#     "reset or spin", and this machine has no reset path, so it spins forever.
#   * "Halt/Restart Timed Out @IOPlatformExpert.cpp:900" -- a launchd reboot
#     with nowhere to go. Always a consequence, never the cause.
#   * any panic(cpu, once the log has also stopped growing -- the panic printer
#     itself emits a few hundred lines, so allow it to finish first.
#   * no output at all for STALL_SECS, panic or not -- a hang.
#
# --no-watchdog restores the old blind wait.
STOP_REASON=""
if [[ -n "${NO_WATCHDOG:-}" ]]; then
    perl -e "sleep $SECS"
else
    STALL_AFTER_PANIC=${STALL_AFTER_PANIC:-20}
    STALL_SECS=${STALL_SECS:-180}
    _elapsed=0; _last_size=0; _quiet=0
    while (( _elapsed < SECS )); do
        perl -e 'sleep 3'; _elapsed=$((_elapsed+3))
        _size=$(stat -f%z "$SERIAL" 2>/dev/null || echo 0)
        if [[ "$_size" == "$_last_size" ]]; then _quiet=$((_quiet+3)); else _quiet=0; _last_size=$_size; fi
        if grep -qa 'Nested panic count exceeds limit' "$SERIAL" 2>/dev/null; then
            STOP_REASON="nested panic limit -- the guest will spin forever"; break
        fi
        if grep -qa 'Halt/Restart Timed Out' "$SERIAL" 2>/dev/null; then
            STOP_REASON="Halt/Restart Timed Out -- guest asked to reboot; no reset path"; break
        fi
        if (( _quiet >= STALL_AFTER_PANIC )) && grep -qa 'panic(cpu' "$SERIAL" 2>/dev/null; then
            STOP_REASON="panicked and quiet for ${_quiet}s"; break
        fi
        if (( _quiet >= STALL_SECS )); then
            STOP_REASON="no serial output for ${_quiet}s -- hung"; break
        fi
    done
    (( _elapsed >= SECS )) && STOP_REASON=""
fi

python3 "$HMP" "$SOCK" stop >/dev/null 2>&1
REGS=$(python3 "$HMP" "$SOCK" info registers 2>/dev/null)
PC=$(printf '%s' "$REGS" | grep -oE 'PC=[0-9a-f]+' | head -1 | cut -d= -f2)
X3=$(printf '%s' "$REGS" | grep -oE 'X03=[0-9a-f]+' | head -1 | cut -d= -f2)

LINES=$(wc -l < "$SERIAL" 2>/dev/null | tr -d ' ')
PANICS=$(grep -c 'panic(cpu' "$SERIAL" 2>/dev/null)
SHELL_UP=$(grep -c "can't access tty" "$SERIAL" 2>/dev/null)

echo "=== probe: $TAG ==="
if [[ -n "$STOP_REASON" ]]; then
    echo "STOPPED EARLY : $STOP_REASON"
    echo "               (the guest is dead; do not wait on it. Find the FIRST"
    echo "                panic(cpu line -- the nested-panic register dump is the"
    echo "                panic printer faulting, not your bug.)"
fi
echo "serial lines : ${LINES:-0}"
echo "xnu panics   : ${PANICS:-0}"
echo "reached shell: $([[ "${SHELL_UP:-0}" -gt 0 ]] && echo yes || echo no)"
echo "PC           : ${PC:-?}"

# An SPTM panic parks the CPU in a branch-to-self with x3 -> message.
if [[ -n "${PC:-}" ]]; then
    INSN=$(python3 "$HMP" "$SOCK" "x/1i 0x$PC" 2>/dev/null | tail -1)
    if printf '%s' "$INSN" | grep -q "b *#0x$PC\|b *#0x$(printf '%x' $((0x$PC - 4)))"; then
        MSG=$(python3 "$HMP" "$SOCK" "x/256xb 0x$X3" 2>/dev/null | python3 -c "
import sys, re
bs = [int(x, 16) for x in re.findall(r'(?<![0-9a-fx])0x([0-9a-f]{2})(?![0-9a-f])', sys.stdin.read())]
s = ''.join(chr(b) if 32 <= b < 127 else '\0' for b in bs)
print(s.split('\0\0')[0].strip('\0').strip())")
        [[ -n "$MSG" ]] && echo "SPTM PANIC   : $MSG"
    fi
fi

if [[ "${PANICS:-0}" -gt 0 ]]; then
    echo "--- first xnu panic ---"
    grep -m1 -A3 'panic(cpu' "$SERIAL" | cut -c1-200
fi

echo "--- last serial lines ---"
grep -v 'ACMTRM: waitForSEPEndpoint' "$SERIAL" 2>/dev/null | tail -8 | cut -c1-200

if [[ -n "$GREP_EXTRA" ]]; then
    echo "--- matches for '$GREP_EXTRA' ---"
    grep -nE "$GREP_EXTRA" "$SERIAL" 2>/dev/null | head -25 | cut -c1-200
fi

TRACE=$(grep -cE '^(aic|asc|dart|unimp|afk|dcp|sep)[:(]' "$ERR" 2>/dev/null)
if [[ "${TRACE:-0}" -gt 0 ]]; then
    echo "--- device model trace: $TRACE lines (see $ERR) ---"
    grep -vE ': (read|write) ' "$ERR" 2>/dev/null | grep -v terminating | head -15
fi

echo "logs: $SERIAL  $ERR"

if [[ "$KEEP" -eq 0 ]]; then
    pkill -f "unix:$SOCK" >/dev/null 2>&1
else
    echo "guest left frozen; resume with: python3 $HMP $SOCK cont"
fi
