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
#
# outputs, all under /tmp/dvm:
#   <TAG>.probe.out   probe.sh verdict
#   <TAG>.slide.json  live slide proof (runtime pc, matched words, static pc)
#   <TAG>.lldb.cmd    the lldb command file actually run
#   <TAG>.lldb.log    breakpoint COMMAND_LIST_PROOF lines and every hit
#   probe/<TAG>.serial.log, probe/<TAG>.stderr.log
# The guest is left frozen (--keep); resume with tools/hmp.py /tmp/dvm/<TAG>.sock cont.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-UI_SETUP_GATE1}"
SECS="${SECS:-300}"
PARENT="${PARENT:-/tmp/dvm/data-seed/persistent-parent.qcow2}"
CALLBACKS="${CALLBACKS:-setup_gate_callbacks}"
GDB_PORT="${GDB_PORT:-1234}"
DTREE=/tmp/dvm/data-seed/dt_nvme_welcome.bin
TC="$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin"
CACHE_GLOB="$HOME/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e*"
CHILD="/tmp/dvm/data-seed/$(printf '%s' "$TAG" | tr 'A-Z_' 'a-z-').qcow2"
BOOTARGS='rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort'

for f in "$PARENT" "$DTREE" "$TC" "$REPO/tools/re/$CALLBACKS.py"; do
    [[ -e "$f" ]] || { echo "setup_gate_probe: missing $f" >&2; exit 1; }
done
if lsof -nP -iTCP:"$GDB_PORT" >/dev/null 2>&1; then
    echo "setup_gate_probe: port $GDB_PORT is in use" >&2; exit 1
fi
if [[ ! -e "$CHILD" ]]; then
    "$REPO/qemu-sptm/build/qemu-img" create -f qcow2 -F qcow2 -b "$PARENT" "$CHILD" || exit 1
fi
rm -f "/tmp/dvm/$TAG.sock" "/tmp/dvm/$TAG.lldb.log"

# The IOMFB level-4 answers and D120/D586 callback script are the established
# display-stack configuration (docs/re/quartz-corrected-runtime.md).
NO_WATCHDOG=1 DARWIN_DCP_EPIC="${DARWIN_DCP_EPIC-all}" DARWIN_DCP_REPLY="${DARWIN_DCP_REPLY-1}" DARWIN_DCP_IOMFB="${DARWIN_DCP_IOMFB-4}" \
DARWIN_DCP_IOMFB_RPC_TRACE=1 \
DARWIN_DCP_IOMFB_OUT='A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000' \
DARWIN_DCP_IOMFB_CB='D120::4,D586:9b040000fc090000:4' \
"$REPO/tools/probe.sh" --dtree "$DTREE" --tc "$TC" --mem 12G --secs "$SECS" --tag "$TAG" \
    --bootargs "$BOOTARGS" --uart-socket "/tmp/dvm/$TAG.uart.sock" --keep -- \
    -S -fb 1179x2556 -fbmode graphics \
    -drive "if=none,id=ans,file=$CHILD,format=qcow2" -gdb "tcp::$GDB_PORT" \
    > "/tmp/dvm/$TAG.probe.out" 2>&1 &
PROBE_PID=$!

_t=0
until [[ -S "/tmp/dvm/$TAG.sock" ]]; do
    perl -e 'select(undef,undef,undef,0.25)'; _t=$((_t+1))
    if (( _t > 120 )) || ! kill -0 "$PROBE_PID" 2>/dev/null; then
        echo "setup_gate_probe: monitor socket never appeared" >&2; exit 1
    fi
done
echo "qemu up: $(date +%T)  socket /tmp/dvm/$TAG.sock"

# Resumes the paused guest, freezes it at the first EL0 PC inside the cache
# range that matches exactly one place in the extracted cache, prints JSON.
python3 "$REPO/tools/re/resolve_live_dsc.py" "/tmp/dvm/$TAG.sock" "$CACHE_GLOB" --cont \
    > "/tmp/dvm/$TAG.slide.json"
SLIDE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['slide'])" \
        "/tmp/dvm/$TAG.slide.json" 2>/dev/null)
if [[ -z "$SLIDE" ]]; then
    echo "setup_gate_probe: slide resolution failed, see /tmp/dvm/$TAG.slide.json" >&2
    cat "/tmp/dvm/$TAG.slide.json" >&2
    python3 "$REPO/tools/hmp.py" "/tmp/dvm/$TAG.sock" cont >/dev/null 2>&1
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
    script -q /dev/null lldb -b -s "/tmp/dvm/$TAG.lldb.cmd" > "/tmp/dvm/$TAG.lldb.log" 2>&1 &
else
    lldb -b -s "/tmp/dvm/$TAG.lldb.cmd" > "/tmp/dvm/$TAG.lldb.log" 2>&1 &
fi
LLDB_PID=$!
echo "lldb attached (pid $LLDB_PID): $(date +%T)  log /tmp/dvm/$TAG.lldb.log"

wait "$PROBE_PID"
echo "probe.sh finished: $(date +%T)"
cat "/tmp/dvm/$TAG.probe.out"
# Give lldb a moment to observe the HMP stop and run its `quit`.
_t=0; while kill -0 "$LLDB_PID" 2>/dev/null && (( _t < 40 )); do perl -e 'select(undef,undef,undef,0.5)'; _t=$((_t+1)); done
kill -0 "$LLDB_PID" 2>/dev/null && { echo "lldb still running; sending SIGINT"; kill -INT "$LLDB_PID"; }
echo "hits: $(grep -c '^=== ' "/tmp/dvm/$TAG.lldb.log" 2>/dev/null)  proofs: $(grep -c COMMAND_LIST_PROOF "/tmp/dvm/$TAG.lldb.log" 2>/dev/null)"
