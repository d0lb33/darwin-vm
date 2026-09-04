#!/bin/bash
# Launch a QEMU binary as LLDB's child from process creation time.  macOS can
# deny a later debugger attach even when the same executable is launchable by
# LLDB.  probe.sh may use this as DVM_QEMU_WRAPPER after its wrapper hook is
# enabled.
set -uo pipefail

[[ $# -ge 1 ]] || { echo "usage: qemu_under_lldb.sh QEMU [ARGS...]" >&2; exit 2; }
QEMU=$1
shift
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CALLBACKS="${DVM_HOST_LLDB_CALLBACKS:-$REPO/tools/re/host_sks_request_callbacks.py}"
LOG="${DVM_HOST_LLDB_LOG:-/tmp/dvm/host-qemu-lldb.log}"

[[ -x "$QEMU" ]] || { echo "qemu_under_lldb: not executable: $QEMU" >&2; exit 1; }
[[ -f "$CALLBACKS" ]] || { echo "qemu_under_lldb: missing callbacks: $CALLBACKS" >&2; exit 1; }
mkdir -p "$(dirname "$LOG")"

LAUNCH_COMMAND=run
if [[ -n "${DVM_HOST_QEMU_STDERR:-}" ]]; then
    [[ "$DVM_HOST_QEMU_STDERR" != *[[:space:]]* ]] || {
        echo "qemu_under_lldb: DVM_HOST_QEMU_STDERR cannot contain whitespace" >&2
        exit 2
    }
    mkdir -p "$(dirname "$DVM_HOST_QEMU_STDERR")"
    LAUNCH_COMMAND="process launch --stderr $DVM_HOST_QEMU_STDERR"
fi

# Keep this shell as probe.sh's owned process.  If probe.sh exits before QEMU's
# monitor socket exists, killing LLDB alone can orphan its stopped inferior.
# The wrapper therefore owns and reaps both processes on every exit path.
LLDB_PID=""
cleanup() {
    _rc=$?
    trap - EXIT INT TERM HUP
    if [[ -n "$LLDB_PID" ]] && kill -0 "$LLDB_PID" 2>/dev/null; then
        # debugserver is LLDB's direct child and may be waiting for taskgated
        # before the QEMU inferior exists.  Collect the small descendant tree
        # rather than assuming QEMU is the direct child.
        _frontier="$LLDB_PID"
        _descendants=""
        for _depth in 1 2 3 4; do
            _next=""
            for _parent in $_frontier; do
                _children=$(pgrep -P "$_parent" 2>/dev/null || true)
                [[ -n "$_children" ]] && _next="$_next $_children"
            done
            [[ -n "$_next" ]] || break
            _descendants="$_descendants $_next"
            _frontier="$_next"
        done
        for _child in $_descendants; do kill "$_child" 2>/dev/null || true; done
        kill "$LLDB_PID" 2>/dev/null || true
        wait "$LLDB_PID" 2>/dev/null || true
        for _child in $_descendants; do kill -KILL "$_child" 2>/dev/null || true; done
    fi
    exit "$_rc"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# QEMU arguments follow LLDB's `--`, so paths and boot arguments never require
# LLDB command-language escaping.
lldb -b \
    -o 'settings set stop-disassembly-count 0' \
    -o 'settings set target.process.stop-on-sharedlibrary-events false' \
    -o 'process handle SIGUSR1 -n false -p true -s false' \
    -o 'process handle SIGUSR2 -n false -p true -s false' \
    -o "command script import $CALLBACKS" \
    -o 'script host_sks_request_callbacks.install(lldb.debugger)' \
    -o "$LAUNCH_COMMAND" -- "$QEMU" "$@" > "$LOG" 2>&1 &
LLDB_PID=$!
wait "$LLDB_PID"
_lldb_rc=$?
LLDB_PID=""
exit "$_lldb_rc"
