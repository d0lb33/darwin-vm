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
#   --uart-socket FILE expose the logged UART on a UNIX socket for guest input
#   --stop-file FILE   stop when FILE appears; its first line becomes the reason
#   --pid-file FILE    write the owned QEMU PID for an outer orchestrator
#   --launch-manifest FILE write exact QEMU argv and Darwin-model environment
#   --keep            leave the VM running (frozen) instead of killing it
#   NO_WATCHDOG=1     disable panic/quiet checks (a --stop-file still works)
#   STALL_AFTER_PANIC seconds of silence after a panic before giving up (20)
#   STALL_SECS        seconds of total silence before calling it hung (180)
#   DVM_QEMU_WRAPPER  executable that launches QEMU (for example
#                     tools/re/qemu_under_lldb.sh); receives QEMU then its args
#   DVM_HOST_LLDB_LOG host-LLDB transcript when that wrapper is used
#   DVM_QEMU_STARTUP_SECS seconds to wait for the monitor socket (30)
#   --                everything after this is passed straight to qemu
#
# environment:
#   DVM_QEMU alternate executable for explicit build comparisons
#   DARWIN_AIC_DEBUG / DARWIN_ASC_DEBUG / DARWIN_DART_DEBUG / DARWIN_UNIMP_DEBUG
#     set any of these to 1 to trace that device model into the stderr log.
#
# output files (under --out, prefixed by --tag):
#   <tag>.serial.log   guest serial console
#   <tag>.stderr.log   qemu stderr, including device model traces
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QEMU="${DVM_QEMU:-$REPO/qemu-sptm/build/qemu-system-aarch64}"
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
UART_SOCKET=""
STOP_FILE=""
PID_FILE=""
LAUNCH_MANIFEST=""
QEMU_PID=""
PROBE_COMPLETE=0
EXTRA_QEMU=()

cleanup_probe() {
    _rc=$?
    if [[ "$PROBE_COMPLETE" -eq 0 && -n "$QEMU_PID" ]] && kill -0 "$QEMU_PID" 2>/dev/null; then
        [[ -S "${SOCK:-}" ]] && python3 "$HMP" "$SOCK" quit >/dev/null 2>&1 || true
        perl -e 'select(undef,undef,undef,0.25)'
        kill -0 "$QEMU_PID" 2>/dev/null && kill "$QEMU_PID" 2>/dev/null || true
        _cleanup_wait=0
        while kill -0 "$QEMU_PID" 2>/dev/null && (( _cleanup_wait < 20 )); do
            perl -e 'select(undef,undef,undef,0.1)'; _cleanup_wait=$((_cleanup_wait+1))
        done
        kill -0 "$QEMU_PID" 2>/dev/null && kill -KILL "$QEMU_PID" 2>/dev/null || true
        wait "$QEMU_PID" 2>/dev/null || true
    fi
    return "$_rc"
}
trap cleanup_probe EXIT
trap 'exit 130' INT TERM HUP

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
        --uart-socket) UART_SOCKET="$2"; shift 2 ;;
        --stop-file) STOP_FILE="$2"; shift 2 ;;
        --pid-file) PID_FILE="$2"; shift 2 ;;
        --launch-manifest) LAUNCH_MANIFEST="$2"; shift 2 ;;
        --keep)     KEEP=1; shift ;;
        --)         shift; EXTRA_QEMU=("$@"); break ;;
        -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
        *)          echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[[ "$TAG" =~ ^[A-Za-z0-9_.-]{1,80}$ ]] || { echo "probe: unsafe tag: $TAG" >&2; exit 2; }

[[ -z "$RAMDISK" ]] && RAMDISK="$REPO/firmware/ramdisk.dmg"
[[ -z "$TC" ]] && TC="$REPO/firmware/ramdisk.tc"

for f in "$QEMU" "$DTREE" "$BOOTKC" "$RAMDISK" "$TC"; do
    [[ -e "$f" ]] || { echo "probe: missing $f" >&2; exit 1; }
done

QEMU_WRAPPER="${DVM_QEMU_WRAPPER:-}"
if [[ -n "$QEMU_WRAPPER" ]]; then
    [[ "$QEMU_WRAPPER" = /* ]] || QEMU_WRAPPER="$REPO/$QEMU_WRAPPER"
    [[ -x "$QEMU_WRAPPER" ]] || {
        echo "probe: QEMU wrapper is not executable: $QEMU_WRAPPER" >&2
        exit 1
    }
fi

mkdir -p "$OUT"
# UNIX socket paths are capped near 104 bytes, so sockets always live in /tmp/dvm
mkdir -p /tmp/dvm
SOCK="/tmp/dvm/$TAG.sock"
SERIAL="$OUT/$TAG.serial.log"
ERR="$OUT/$TAG.stderr.log"
if [[ -S "$SOCK" ]] && python3 "$HMP" "$SOCK" 'info status' >/dev/null 2>&1; then
    echo "probe: live monitor already owns $SOCK; choose another --tag" >&2
    exit 1
fi
rm -f "$SOCK" "$SERIAL" "$ERR"
: > "$SERIAL"
[[ -n "$UART_SOCKET" ]] && rm -f "$UART_SOCKET"
[[ -n "$STOP_FILE" ]] && rm -f "$STOP_FILE"
[[ -n "$PID_FILE" ]] && rm -f "$PID_FILE"

ARGS=(
    -M darwin
    -bootkc "$BOOTKC"
    -dtree "$DTREE"
    -tc "$TC"
    -ramdisk "$RAMDISK"
    -args "$BOOTARGS"
    -display none
    -monitor "unix:$SOCK,server,nowait"
    -m "$MEM"
)
if [[ -n "$UART_SOCKET" ]]; then
    ARGS+=(
        -chardev "socket,id=probe_uart,path=$UART_SOCKET,server=on,wait=off,logfile=$SERIAL"
        -serial chardev:probe_uart
    )
else
    ARGS+=(-serial "file:$SERIAL")
fi
[[ -f "$REPO/firmware/sptm" ]] && ARGS+=(-sptm "$REPO/firmware/sptm" -txm "$REPO/firmware/txm")
[[ ${#EXTRA_QEMU[@]} -gt 0 ]] && ARGS+=("${EXTRA_QEMU[@]}")

if [[ -n "$LAUNCH_MANIFEST" ]]; then
    mkdir -p "$(dirname "$LAUNCH_MANIFEST")"
    python3 - "$LAUNCH_MANIFEST" "$QEMU" "${ARGS[@]}" <<'PY'
import json, os, sys
from pathlib import Path

path = Path(sys.argv[1])
value = {
    "format": "darwin-vm-qemu-launch-v1",
    "argv": sys.argv[2:],
    "env": {k: v for k, v in os.environ.items()
            if k.startswith("DARWIN_") or k.startswith("GXFSTAT_")},
}
tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
os.replace(tmp, path)
PY
fi

if [[ -n "$QEMU_WRAPPER" ]]; then
    DVM_HOST_QEMU_STDERR="$ERR" \
    DVM_HOST_LLDB_LOG="${DVM_HOST_LLDB_LOG:-/tmp/dvm/$TAG.host-lldb.log}" \
        "$QEMU_WRAPPER" "$QEMU" "${ARGS[@]}" 2> "$ERR" &
else
    "$QEMU" "${ARGS[@]}" 2> "$ERR" &
fi
QEMU_PID=$!
if [[ -n "$PID_FILE" ]]; then
    _pid_tmp="$PID_FILE.$$.tmp"
    printf '%s\n' "$QEMU_PID" > "$_pid_tmp"
    mv -f "$_pid_tmp" "$PID_FILE"
fi

# Do not charge debugger/taskgated startup against the experiment budget.  A
# launch-under-LLDB authorization stall otherwise looks like a guest failure
# and can leave debugserver's stopped inferior behind.
QEMU_STARTUP_SECS="${DVM_QEMU_STARTUP_SECS:-30}"
[[ "$QEMU_STARTUP_SECS" =~ ^[1-9][0-9]*$ ]] || {
    echo "probe: DVM_QEMU_STARTUP_SECS must be a positive integer" >&2
    exit 2
}
_startup_elapsed=0
while [[ ! -S "$SOCK" ]] && (( _startup_elapsed < QEMU_STARTUP_SECS * 10 )); do
    if ! kill -0 "$QEMU_PID" 2>/dev/null; then
        echo "probe: QEMU launcher exited before creating $SOCK" >&2
        [[ -n "$QEMU_WRAPPER" ]] && echo "probe: host debugger log: ${DVM_HOST_LLDB_LOG:-/tmp/dvm/$TAG.host-lldb.log}" >&2
        exit 1
    fi
    perl -e 'select(undef,undef,undef,0.1)'
    _startup_elapsed=$((_startup_elapsed+1))
done
if [[ ! -S "$SOCK" ]]; then
    echo "probe: QEMU monitor did not appear within ${QEMU_STARTUP_SECS}s" >&2
    [[ -n "$QEMU_WRAPPER" ]] && echo "probe: LLDB or macOS developer authorization may be waiting; see ${DVM_HOST_LLDB_LOG:-/tmp/dvm/$TAG.host-lldb.log}" >&2
    exit 1
fi

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
STOP_KIND=""
if [[ -n "${NO_WATCHDOG:-}" && -z "$STOP_FILE" ]]; then
    perl -e "sleep $SECS"
else
    STALL_AFTER_PANIC=${STALL_AFTER_PANIC:-20}
    STALL_SECS=${STALL_SECS:-180}
    _interval=$([[ -n "$STOP_FILE" ]] && echo 1 || echo 3)
    _elapsed=0; _last_size=0; _quiet=0
    while (( _elapsed < SECS )); do
        perl -e "sleep $_interval"; _elapsed=$((_elapsed+_interval))
        if ! kill -0 "$QEMU_PID" 2>/dev/null; then
            STOP_REASON="QEMU exited before the probe condition"; STOP_KIND="dead"; break
        fi
        if [[ -n "$STOP_FILE" && -f "$STOP_FILE" ]]; then
            STOP_REASON=$(head -1 "$STOP_FILE" 2>/dev/null)
            STOP_REASON="${STOP_REASON:-external stop requested}"
            STOP_KIND="condition"
            break
        fi
        [[ -n "${NO_WATCHDOG:-}" ]] && continue
        _size=$(stat -f%z "$SERIAL" 2>/dev/null || echo 0)
        if [[ "$_size" == "$_last_size" ]]; then _quiet=$((_quiet+_interval)); else _quiet=0; _last_size=$_size; fi
        if grep -qa 'Nested panic count exceeds limit' "$SERIAL" 2>/dev/null; then
            STOP_REASON="nested panic limit -- the guest will spin forever"; STOP_KIND="dead"; break
        fi
        if grep -qa 'Halt/Restart Timed Out' "$SERIAL" 2>/dev/null; then
            STOP_REASON="Halt/Restart Timed Out -- guest asked to reboot; no reset path"; STOP_KIND="dead"; break
        fi
        if (( _quiet >= STALL_AFTER_PANIC )) && grep -qa 'panic(cpu' "$SERIAL" 2>/dev/null; then
            STOP_REASON="panicked and quiet for ${_quiet}s"; STOP_KIND="dead"; break
        fi
        if (( _quiet >= STALL_SECS )); then
            STOP_REASON="no serial output for ${_quiet}s -- hung"; STOP_KIND="dead"; break
        fi
    done
    if (( _elapsed >= SECS )) && [[ -z "$STOP_KIND" ]]; then STOP_REASON=""; fi
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
    if [[ "$STOP_KIND" == "condition" ]]; then
        echo "STOPPED ON CONDITION : $STOP_REASON"
    else
        echo "STOPPED EARLY : $STOP_REASON"
        echo "               (the guest is dead; do not wait on it. Find the FIRST"
        echo "                panic(cpu line -- the nested-panic register dump is the"
        echo "                panic printer faulting, not your bug.)"
    fi
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
    python3 "$HMP" "$SOCK" quit >/dev/null 2>&1 || true
    _wait=0
    while kill -0 "$QEMU_PID" 2>/dev/null && (( _wait < 40 )); do
        perl -e 'select(undef,undef,undef,0.25)'; _wait=$((_wait+1))
    done
    if kill -0 "$QEMU_PID" 2>/dev/null; then
        echo "probe: QEMU did not exit after HMP quit; sending TERM" >&2
        kill "$QEMU_PID" 2>/dev/null || true
        _term_wait=0
        while kill -0 "$QEMU_PID" 2>/dev/null && (( _term_wait < 20 )); do
            perl -e 'select(undef,undef,undef,0.1)'; _term_wait=$((_term_wait+1))
        done
        if kill -0 "$QEMU_PID" 2>/dev/null; then
            echo "probe: QEMU ignored TERM; sending KILL" >&2
            kill -KILL "$QEMU_PID" 2>/dev/null || true
        fi
    fi
    wait "$QEMU_PID" 2>/dev/null || true
else
    echo "guest left frozen; resume with: python3 $HMP $SOCK cont"
fi
PROBE_COMPLETE=1
