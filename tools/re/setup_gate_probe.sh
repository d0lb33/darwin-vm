#!/bin/bash
# setup_gate_probe.sh - boot the persistent-NVMe iOS image paused (-S), resolve
# this boot's dyld shared-cache slide from a live EL0 instruction sequence, then
# attach lldb to QEMU's gdbstub with the SetupAssistant launch-gate callbacks
# (tools/re/setup_gate_callbacks.py).  The slide is never reused from a prior
# boot: docs/re/lldb-breakpoint-command-trap.md and setup-launch-gate.md.
#
# usage: tools/re/setup_gate_probe.sh [TAG]
#   SECS        wall seconds to let the guest run before probe.sh freezes it (300)
#   PARENT      qcow2 parent image (the persistent Data/User volume set; the
#               default is the link tools/rootfs/rebuild_persistent_parent.sh leaves)
#   CALLBACKS   python module with install(debugger, slide) (setup_gate_callbacks)
#   GDB_PORT    gdbstub TCP port (1234)
#   PROBE_STALL_SELECTOR selector whose unmatched PROBE_EVENT stops the run;
#               defaults to 0x4f for display_iokit_callbacks, empty otherwise
#   PROBE_STALL_SECS seconds an instrumented call may remain pending (30)
#   PROBE_STOP_SERIAL_REGEX / PROBE_STOP_LLDB_REGEX optional early-stop regexes
#   PROBE_SUCCESS_LABELS comma-separated callback labels that create stop events
#   AUTO_POSTMORTEM stall (default), all, or 0; runs after a conditional freeze
#   POSTMORTEM_RAM_SIZE fast first-pass bytes (0x80000000 = 2 GiB)
#   KEEP_GUEST  leave QEMU frozen after collection (1); 0 exits it cleanly
#
# outputs, all under /tmp/dvm:
#   <TAG>.probe.out   probe.sh verdict
#   <TAG>.slide.json  live slide proof (runtime pc, matched words, static pc)
#   <TAG>.lldb.cmd    the lldb command file actually run
#   <TAG>.lldb.log    breakpoint COMMAND_LIST_PROOF lines and every hit
#   <TAG>.watch.log   condition watcher decisions
#   <TAG>.stop        durable early-stop reason, when a condition fired
#   probe/<TAG>.serial.log, probe/<TAG>.stderr.log
# The guest is left frozen (--keep); resume with tools/hmp.py /tmp/dvm/<TAG>.sock cont.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-UI_SETUP_GATE1}"
SECS="${SECS:-300}"
PARENT="${PARENT:-/tmp/dvm/data-seed/persistent-parent.qcow2}"
CALLBACKS="${CALLBACKS:-setup_gate_callbacks}"
GDB_PORT="${GDB_PORT:-1234}"
PROBE_STALL_SELECTOR="${PROBE_STALL_SELECTOR-}"
if [[ -z "$PROBE_STALL_SELECTOR" && "$CALLBACKS" == "display_iokit_callbacks" ]]; then
    PROBE_STALL_SELECTOR=0x4f
fi
PROBE_STALL_SECS="${PROBE_STALL_SECS:-30}"
PROBE_STOP_SERIAL_REGEX="${PROBE_STOP_SERIAL_REGEX-}"
PROBE_STOP_LLDB_REGEX="${PROBE_STOP_LLDB_REGEX-}"
PROBE_SUCCESS_LABELS="${PROBE_SUCCESS_LABELS-}"
AUTO_POSTMORTEM="${AUTO_POSTMORTEM:-stall}"
POSTMORTEM_RAM_SIZE="${POSTMORTEM_RAM_SIZE:-0x80000000}"
POSTMORTEM_FULL_RAM_SIZE="${POSTMORTEM_FULL_RAM_SIZE:-0x300000000}"
KEEP_GUEST="${KEEP_GUEST:-1}"
LLDB_DRAIN_SECS="${LLDB_DRAIN_SECS:-5}"
DTREE=/tmp/dvm/data-seed/dt_nvme_welcome.bin
TC="$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin"
CACHE_GLOB="$HOME/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e*"
CHILD="/tmp/dvm/data-seed/$(printf '%s' "$TAG" | tr 'A-Z_' 'a-z-').qcow2"
BOOTARGS='rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort'
SOCK="/tmp/dvm/$TAG.sock"
SERIAL="/tmp/dvm/probe/$TAG.serial.log"
LLDB_LOG="/tmp/dvm/$TAG.lldb.log"
STOP_FILE="/tmp/dvm/$TAG.stop"
EVENT_DIR="/tmp/dvm/$TAG.events"
WATCH_LOG="/tmp/dvm/$TAG.watch.log"
QEMU_PID_FILE="/tmp/dvm/$TAG.qemu.pid"
PROBE_PID=""; LLDB_PID=""; WATCH_PID=""; FINISHED=0

[[ "$TAG" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "setup_gate_probe: unsafe tag: $TAG" >&2; exit 2; }
[[ "$CALLBACKS" =~ ^[A-Za-z0-9_]+$ ]] || { echo "setup_gate_probe: unsafe callback module: $CALLBACKS" >&2; exit 2; }
[[ "$SECS" =~ ^[1-9][0-9]*$ && "$GDB_PORT" =~ ^[1-9][0-9]*$ && "$LLDB_DRAIN_SECS" =~ ^[0-9]+$ ]] || {
    echo "setup_gate_probe: SECS, GDB_PORT, and LLDB_DRAIN_SECS must be integers" >&2; exit 2
}
case "$AUTO_POSTMORTEM" in 0|off|1|all|stall) ;; *) echo "setup_gate_probe: invalid AUTO_POSTMORTEM=$AUTO_POSTMORTEM" >&2; exit 2 ;; esac
case "$KEEP_GUEST" in 0|1) ;; *) echo "setup_gate_probe: KEEP_GUEST must be 0 or 1" >&2; exit 2 ;; esac
if [[ -n "$PROBE_STALL_SELECTOR" && ! "$PROBE_STALL_SELECTOR" =~ ^(0[xX][0-9a-fA-F]+|[0-9]+)$ ]]; then
    echo "setup_gate_probe: invalid PROBE_STALL_SELECTOR=$PROBE_STALL_SELECTOR" >&2; exit 2
fi
for _label in ${PROBE_SUCCESS_LABELS//,/ }; do
    [[ "$_label" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "setup_gate_probe: unsafe success label: $_label" >&2; exit 2; }
done

cleanup() {
    _rc=$?
    if [[ -n "$WATCH_PID" ]] && kill -0 "$WATCH_PID" 2>/dev/null; then kill "$WATCH_PID" 2>/dev/null || true; fi
    if [[ -n "$LLDB_PID" ]] && kill -0 "$LLDB_PID" 2>/dev/null; then kill -INT "$LLDB_PID" 2>/dev/null || true; fi
    if [[ "$FINISHED" -eq 0 && -S "$SOCK" ]]; then
        python3 "$REPO/tools/hmp.py" "$SOCK" quit >/dev/null 2>&1 || true
    fi
    if [[ -n "$PROBE_PID" ]] && kill -0 "$PROBE_PID" 2>/dev/null; then kill "$PROBE_PID" 2>/dev/null || true; fi
    if [[ "$FINISHED" -eq 0 && -f "$QEMU_PID_FILE" ]]; then
        _qpid=$(head -1 "$QEMU_PID_FILE" 2>/dev/null || true)
        if [[ "$_qpid" =~ ^[0-9]+$ ]] && kill -0 "$_qpid" 2>/dev/null; then
            _qcmd=$(ps -ww -p "$_qpid" -o command= 2>/dev/null || true)
            if [[ "$_qcmd" == *"unix:$SOCK"* ]]; then
                kill "$_qpid" 2>/dev/null || true
                _cleanup_wait=0
                while kill -0 "$_qpid" 2>/dev/null && (( _cleanup_wait < 20 )); do
                    perl -e 'select(undef,undef,undef,0.1)'; _cleanup_wait=$((_cleanup_wait+1))
                done
                kill -0 "$_qpid" 2>/dev/null && kill -KILL "$_qpid" 2>/dev/null || true
            fi
        fi
    fi
    return "$_rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

for f in "$PARENT" "$DTREE" "$TC" "$REPO/tools/re/$CALLBACKS.py"; do
    [[ -e "$f" ]] || { echo "setup_gate_probe: missing $f" >&2; exit 1; }
done
if lsof -nP -iTCP:"$GDB_PORT" >/dev/null 2>&1; then
    echo "setup_gate_probe: port $GDB_PORT is in use" >&2; exit 1
fi
if [[ -S "$SOCK" ]] && python3 "$REPO/tools/hmp.py" "$SOCK" 'info status' >/dev/null 2>&1; then
    echo "setup_gate_probe: live monitor already owns $SOCK; choose another TAG" >&2; exit 1
fi
if [[ ! -e "$CHILD" ]]; then
    "$REPO/qemu-sptm/build/qemu-img" create -f qcow2 -F qcow2 -b "$PARENT" "$CHILD" || exit 1
fi
rm -f "$SOCK" "$LLDB_LOG" "$STOP_FILE" "$WATCH_LOG" "$QEMU_PID_FILE"
rm -rf -- "$EVENT_DIR"

# The IOMFB level-4 answers and D120/D586 callback script are the established
# display-stack configuration (docs/re/quartz-corrected-runtime.md).
DARWIN_DCP_EPIC="${DARWIN_DCP_EPIC-all}" DARWIN_DCP_REPLY="${DARWIN_DCP_REPLY-1}" DARWIN_DCP_IOMFB="${DARWIN_DCP_IOMFB-4}" \
DARWIN_DCP_IOMFB_RPC_TRACE=1 \
DARWIN_DCP_IOMFB_OUT='A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000' \
DARWIN_DCP_IOMFB_CB='D120::4,D586:9b040000fc090000:4' \
"$REPO/tools/probe.sh" --dtree "$DTREE" --tc "$TC" --mem 12G --secs "$SECS" --tag "$TAG" \
    --bootargs "$BOOTARGS" --uart-socket "/tmp/dvm/$TAG.uart.sock" --stop-file "$STOP_FILE" \
    --pid-file "$QEMU_PID_FILE" --keep -- \
    -S -fb 1179x2556 -fbmode graphics \
    -drive "if=none,id=ans,file=$CHILD,format=qcow2" -gdb "tcp::$GDB_PORT" \
    > "/tmp/dvm/$TAG.probe.out" 2>&1 &
PROBE_PID=$!

_t=0
until [[ -S "$SOCK" ]]; do
    perl -e 'select(undef,undef,undef,0.25)'; _t=$((_t+1))
    if (( _t > 120 )) || ! kill -0 "$PROBE_PID" 2>/dev/null; then
        echo "setup_gate_probe: monitor socket never appeared" >&2; exit 1
    fi
done
echo "qemu up: $(date +%T)  socket $SOCK"

# Resumes the paused guest, freezes it at the first EL0 PC inside the cache
# range that matches exactly one place in the extracted cache, prints JSON.
python3 "$REPO/tools/re/resolve_live_dsc.py" "$SOCK" "$CACHE_GLOB" --cont \
    > "/tmp/dvm/$TAG.slide.json"
SLIDE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['slide'])" \
        "/tmp/dvm/$TAG.slide.json" 2>/dev/null)
if [[ -z "$SLIDE" ]]; then
    echo "setup_gate_probe: slide resolution failed, see /tmp/dvm/$TAG.slide.json" >&2
    cat "/tmp/dvm/$TAG.slide.json" >&2
    python3 "$REPO/tools/hmp.py" "$SOCK" cont >/dev/null 2>&1
    exit 1
fi
echo "slide resolved: $SLIDE  $(date +%T)"

cat > "/tmp/dvm/$TAG.lldb.cmd" <<CMD
settings set stop-disassembly-count 4
settings set target.process.stop-on-sharedlibrary-events false
gdb-remote $GDB_PORT
command script import $REPO/tools/re/$CALLBACKS.py
script $CALLBACKS.install(lldb.debugger, $SLIDE)
continue
quit
CMD

# lldb wants a tty for prompt-driven batch output; `script` supplies a pty.
if script -q /dev/null true </dev/null >/dev/null 2>&1; then
    DVM_PROBE_EVENT_DIR="$EVENT_DIR" DVM_PROBE_STALL_SELECTOR="$PROBE_STALL_SELECTOR" \
    DVM_PROBE_SUCCESS_LABELS="$PROBE_SUCCESS_LABELS" \
    script -q /dev/null lldb -b -s "/tmp/dvm/$TAG.lldb.cmd" > "$LLDB_LOG" 2>&1 &
else
    DVM_PROBE_EVENT_DIR="$EVENT_DIR" DVM_PROBE_STALL_SELECTOR="$PROBE_STALL_SELECTOR" \
    DVM_PROBE_SUCCESS_LABELS="$PROBE_SUCCESS_LABELS" \
    lldb -b -s "/tmp/dvm/$TAG.lldb.cmd" > "$LLDB_LOG" 2>&1 &
fi
LLDB_PID=$!
echo "lldb attached (pid $LLDB_PID): $(date +%T)  log /tmp/dvm/$TAG.lldb.log"

WATCH_ARGS=(--stop-file "$STOP_FILE" --max-secs "$SECS")
if [[ -n "$PROBE_STALL_SELECTOR" ]]; then
    WATCH_ARGS+=(--event-dir "$EVENT_DIR" --selector "$PROBE_STALL_SELECTOR" --pending-secs "$PROBE_STALL_SECS")
fi
if [[ -n "$PROBE_STOP_SERIAL_REGEX" ]]; then
    WATCH_ARGS+=(--stop-on "$SERIAL" "$PROBE_STOP_SERIAL_REGEX")
fi
if [[ -n "$PROBE_STOP_LLDB_REGEX" ]]; then
    WATCH_ARGS+=(--stop-on "$LLDB_LOG" "$PROBE_STOP_LLDB_REGEX")
fi
if [[ -n "$PROBE_SUCCESS_LABELS" ]]; then
    for _label in ${PROBE_SUCCESS_LABELS//,/ }; do
        WATCH_ARGS+=(--stop-on "$EVENT_DIR/success.$_label.json" '.')
    done
fi
if (( ${#WATCH_ARGS[@]} > 6 )); then
    python3 "$REPO/tools/re/probe_watch.py" "${WATCH_ARGS[@]}" > "$WATCH_LOG" 2>&1 &
    WATCH_PID=$!
    echo "condition watcher attached (pid $WATCH_PID): $WATCH_LOG"
fi

wait "$PROBE_PID"
PROBE_RC=$?
echo "probe.sh finished: $(date +%T)"
cat "/tmp/dvm/$TAG.probe.out"
if [[ -n "$WATCH_PID" ]] && kill -0 "$WATCH_PID" 2>/dev/null; then kill "$WATCH_PID" 2>/dev/null || true; fi
[[ -n "$WATCH_PID" ]] && wait "$WATCH_PID" 2>/dev/null || true
# Give lldb a moment to observe the HMP stop and run its `quit`.
_t=0; while kill -0 "$LLDB_PID" 2>/dev/null && (( _t < LLDB_DRAIN_SECS * 2 )); do perl -e 'select(undef,undef,undef,0.5)'; _t=$((_t+1)); done
kill -0 "$LLDB_PID" 2>/dev/null && { echo "lldb still running; sending SIGINT"; kill -INT "$LLDB_PID"; }
_t=0; while kill -0 "$LLDB_PID" 2>/dev/null && (( _t < 20 )); do perl -e 'select(undef,undef,undef,0.1)'; _t=$((_t+1)); done
if kill -0 "$LLDB_PID" 2>/dev/null; then
    echo "lldb ignored SIGINT; sending TERM"
    kill "$LLDB_PID" 2>/dev/null || true
fi
wait "$LLDB_PID" 2>/dev/null || true
echo "hits: $(grep -c '^=== ' "/tmp/dvm/$TAG.lldb.log" 2>/dev/null)  proofs: $(grep -c COMMAND_LIST_PROOF "/tmp/dvm/$TAG.lldb.log" 2>/dev/null)"

STOP_REASON=$([[ -f "$STOP_FILE" ]] && head -1 "$STOP_FILE" || true)
RUN_POSTMORTEM=0
case "$AUTO_POSTMORTEM" in
    1|all) [[ -n "$STOP_REASON" ]] && RUN_POSTMORTEM=1 ;;
    stall) [[ "$STOP_REASON" == selector-deadline* ]] && RUN_POSTMORTEM=1 ;;
    0|off) ;;
esac
if [[ "$RUN_POSTMORTEM" -eq 1 ]]; then
    echo "automatic post-mortem: $(date +%T)  fast RAM=$POSTMORTEM_RAM_SIZE"
    python3 "$REPO/tools/re/stall_postmortem.py" "$SOCK" "$TAG" \
        --ram-size "$POSTMORTEM_RAM_SIZE" --full-ram-size "$POSTMORTEM_FULL_RAM_SIZE" \
        --min-stacks 2 \
        --kext AppleFirmwareKit --kext driver.RTBuddy --kext AppleDCP --kext IOMobileGraphicsFamily \
        || PROBE_RC=$?
fi

if [[ "$KEEP_GUEST" == 0 ]]; then
    python3 "$REPO/tools/hmp.py" "$SOCK" quit >/dev/null 2>&1 || true
    _qpid=$(head -1 "$QEMU_PID_FILE" 2>/dev/null || true)
    _wait=0
    while [[ "$_qpid" =~ ^[0-9]+$ ]] && kill -0 "$_qpid" 2>/dev/null && (( _wait < 40 )); do
        perl -e 'select(undef,undef,undef,0.25)'; _wait=$((_wait+1))
    done
    if [[ "$_qpid" =~ ^[0-9]+$ ]] && kill -0 "$_qpid" 2>/dev/null; then
        _qcmd=$(ps -ww -p "$_qpid" -o command= 2>/dev/null || true)
        if [[ "$_qcmd" == *"unix:$SOCK"* ]]; then
            echo "QEMU did not exit after HMP quit; sending TERM"
            kill "$_qpid" 2>/dev/null || true
            _term_wait=0
            while kill -0 "$_qpid" 2>/dev/null && (( _term_wait < 20 )); do
                perl -e 'select(undef,undef,undef,0.1)'; _term_wait=$((_term_wait+1))
            done
            if kill -0 "$_qpid" 2>/dev/null; then
                echo "QEMU ignored TERM; sending KILL"
                kill -KILL "$_qpid" 2>/dev/null || true
            fi
            wait "$_qpid" 2>/dev/null || true
        fi
    fi
    echo "guest exited after collection"
else
    echo "guest remains frozen: $SOCK"
fi
FINISHED=1
exit "$PROBE_RC"
