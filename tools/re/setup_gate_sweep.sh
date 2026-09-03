#!/bin/bash
# Run independent display-probe variants in small parallel batches.
#
# usage: tools/re/setup_gate_sweep.sh TAG_PREFIX [EPIC_VALUE ...]
#
# Defaults to the established `off` and `all` controls. Every run gets a unique
# tag, monitor/UART sockets, child overlay, LLDB log, scanner directory, and GDB
# port. MAX_PARALLEL defaults to 2 because each guest reserves 12 GiB.
#
# environment:
#   BASE_GDB_PORT=1234  MAX_PARALLEL=2  SECS=180
#   CALLBACKS=display_iokit_callbacks  SWEEP_KEEP_GUEST=0
# All setup_gate_probe.sh condition/post-mortem variables are inherited.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PREFIX="${1:-}"
[[ -n "$PREFIX" ]] || { sed -n '2,13p' "$0"; exit 2; }
shift
[[ "$PREFIX" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "setup_gate_sweep: unsafe prefix: $PREFIX" >&2; exit 2; }

BASE_GDB_PORT="${BASE_GDB_PORT:-1234}"
MAX_PARALLEL="${MAX_PARALLEL:-2}"
SECS="${SECS:-180}"
CALLBACKS="${CALLBACKS:-display_iokit_callbacks}"
SWEEP_KEEP_GUEST="${SWEEP_KEEP_GUEST:-0}"
[[ "$BASE_GDB_PORT" =~ ^[0-9]+$ && "$MAX_PARALLEL" =~ ^[1-9][0-9]*$ ]] || {
    echo "setup_gate_sweep: BASE_GDB_PORT and MAX_PARALLEL must be positive integers" >&2
    exit 2
}

if (( $# == 0 )); then
    VARIANTS=(off all)
else
    VARIANTS=("$@")
fi

host_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
needed_bytes=$((MAX_PARALLEL * 14 * 1024 * 1024 * 1024))
if (( host_bytes > 0 && needed_bytes > host_bytes )); then
    echo "setup_gate_sweep: warning: $MAX_PARALLEL simultaneous 12-GiB guests may exceed host RAM" >&2
fi
if [[ "$SWEEP_KEEP_GUEST" != 0 && ${#VARIANTS[@]} -gt MAX_PARALLEL ]]; then
    echo "setup_gate_sweep: warning: kept guests accumulate across batches" >&2
fi

batch_pids=()
batch_tags=()
failures=0

abort_sweep() {
    local pid
    trap - INT TERM HUP
    echo "setup_gate_sweep: interrupted; stopping active drivers" >&2
    for pid in "${batch_pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    for pid in "${batch_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    exit 130
}
trap abort_sweep INT TERM HUP

wait_batch() {
    local index pid tag rc
    for ((index=0; index<${#batch_pids[@]}; index++)); do
        pid=${batch_pids[$index]}; tag=${batch_tags[$index]}
        wait "$pid"; rc=$?
        if (( rc != 0 )); then
            echo "FAILED tag=$tag rc=$rc log=/tmp/dvm/$tag.driver.log" >&2
            failures=$((failures+1))
        else
            echo "DONE tag=$tag log=/tmp/dvm/$tag.driver.log"
        fi
    done
    batch_pids=()
    batch_tags=()
}

index=0
for variant in "${VARIANTS[@]}"; do
    slug=$(printf '%s' "$variant" | tr -c 'A-Za-z0-9.-' '_' | cut -c1-32)
    tag="${PREFIX}_${slug}_${index}"
    port=$((BASE_GDB_PORT + index))
    echo "START tag=$tag epic=$variant gdb=$port $(date +%T)"
    GDB_PORT="$port" CALLBACKS="$CALLBACKS" DARWIN_DCP_EPIC="$variant" \
    KEEP_GUEST="$SWEEP_KEEP_GUEST" SECS="$SECS" \
        "$REPO/tools/re/setup_gate_probe.sh" "$tag" > "/tmp/dvm/$tag.driver.log" 2>&1 &
    batch_pids+=("$!")
    batch_tags+=("$tag")
    index=$((index+1))
    if (( ${#batch_pids[@]} >= MAX_PARALLEL )); then
        wait_batch
    fi
done
(( ${#batch_pids[@]} > 0 )) && wait_batch

echo "=== sweep summary ==="
for ((index=0; index<${#VARIANTS[@]}; index++)); do
    variant=${VARIANTS[$index]}
    slug=$(printf '%s' "$variant" | tr -c 'A-Za-z0-9.-' '_' | cut -c1-32)
    tag="${PREFIX}_${slug}_${index}"
    printf '%-32s ' "$tag"
    if [[ -f "/tmp/dvm/$tag.probe.out" ]]; then
        grep -E 'STOPPED ON CONDITION|STOPPED EARLY|xnu panics' "/tmp/dvm/$tag.probe.out" | tr '\n' ';'
        echo
    else
        echo "no verdict"
    fi
done
(( failures == 0 ))
