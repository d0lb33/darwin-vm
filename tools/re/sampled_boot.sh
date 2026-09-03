#!/bin/bash
# sampled_boot.sh - boot the persistent parent with the display configuration
# (no debugger) and profile the vCPU with tools/pc_sampler.py while it boots.
# usage: tools/re/sampled_boot.sh [TAG] ; SECS (300), INTERVAL (0.4), PARENT
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-PCSAMPLE1}"
SECS="${SECS:-300}"
INTERVAL="${INTERVAL:-0.4}"
PARENT="${PARENT:-/tmp/dvm/data-seed/persistent-parent.qcow2}"
DTREE=/tmp/dvm/data-seed/dt_nvme_welcome.bin
TC="$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin"
CHILD="/tmp/dvm/data-seed/$(printf '%s' "$TAG" | tr 'A-Z_' 'a-z-').qcow2"
[[ -e "$CHILD" ]] || "$REPO/qemu-sptm/build/qemu-img" create -f qcow2 -F qcow2 -b "$PARENT" "$CHILD" >/dev/null || exit 1
rm -f "/tmp/dvm/$TAG.sock"
NO_WATCHDOG=1 DARWIN_DCP_EPIC=all DARWIN_DCP_REPLY=1 DARWIN_DCP_IOMFB=4 \
DARWIN_DCP_IOMFB_RPC_TRACE=1 \
DARWIN_DCP_IOMFB_OUT='A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000' \
DARWIN_DCP_IOMFB_CB='D120::4,D586:9b040000fc090000:4' \
"$REPO/tools/probe.sh" --dtree "$DTREE" --tc "$TC" --mem 12G --secs "$SECS" --tag "$TAG" \
    --bootargs 'rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort' \
    --keep -- -fb 1179x2556 -fbmode graphics -drive "if=none,id=ans,file=$CHILD,format=qcow2" \
    > "/tmp/dvm/$TAG.probe.out" 2>&1 &
PROBE=$!
until [[ -S "/tmp/dvm/$TAG.sock" ]]; do perl -e 'select(undef,undef,undef,0.25)'; kill -0 $PROBE 2>/dev/null || exit 1; done
echo "qemu up $(date +%T); sampling every ${INTERVAL}s for $((SECS-10))s"
python3 "$REPO/tools/pc_sampler.py" "/tmp/dvm/$TAG.sock" --secs $((SECS-10)) --interval "$INTERVAL" --out "/tmp/dvm/$TAG.samples" --report
wait $PROBE
cat "/tmp/dvm/$TAG.probe.out" | head -12
