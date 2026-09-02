#!/bin/bash
# Fetch and decrypt the three AEA-encrypted IPSW payloads the root filesystem
# is built from, plus their trust caches.
#
# The IPSW is 12 GB and we need 11 of it, but `ipsw extract --remote` reads the
# zip64 central directory over HTTP range requests and pulls only the members
# that match --pattern, so nothing else is transferred.
#
# The payloads are AEA1-encrypted. `ipsw fw aea` fetches the decryption key from
# https://wkms-public.apple.com/fcs-keys/<id> with **no credentials** -- it is a
# public class-key fetch, not a FairPlay/SEP-personalized one. Derivation and
# proof in docs/re/rootfs-boot.md section 2.
#
# Footgun from that same section, do not "simplify" this: `ipsw extract
# --fcs-key --dmg <type>` downloads the entire matching DMG before doing
# anything. Fetch with --pattern, decrypt afterwards with `ipsw fw aea`.
#
#   094-13182-141.dmg  system volume   10,026,483,712 B   (Manifest key OS)
#   094-13150-145.dmg  OS cryptex                          (Cryptex1,SystemOS)
#   094-14052-182.dmg  ExclaveOS                           (Ap,ExclaveOS)
#
# Usage: tools/rootfs/fetch_payloads.sh [workdir]     (default /tmp/dvm)
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
WORK=${1:-/tmp/dvm}
URL=$(sed -n 2p "$REPO/firmware/info")
RAW="$WORK/aea/raw"
OUT="$WORK/aea/out"
TC="$WORK/tc"

PAYLOADS=(094-13182-141 094-13150-145 094-14052-182)

mkdir -p "$RAW" "$OUT" "$TC"
echo "==> IPSW: $URL"
echo "==> work: $WORK"

# One remote open, every member we want. The .trustcache/.root_hash siblings
# come along for free and we need the trustcaches.
PAT=$(IFS='|'; echo "(${PAYLOADS[*]})")
if [ -z "$(ls -A "$RAW" 2>/dev/null)" ]; then
    echo "==> fetching members matching $PAT"
    ipsw extract --remote "$URL" --output "$RAW" --flat --pattern "$PAT" 2>&1 | grep -v '^ *$' || true
else
    echo "==> $RAW already populated, skipping fetch"
fi
ls -l "$RAW"

echo "==> decrypting the AEA payloads (public fcs-key, no credentials)"
for p in "${PAYLOADS[@]}"; do
    aea=$(find "$RAW" -name "$p.dmg.aea" | head -1)
    [ -n "$aea" ] || { echo "!! $p.dmg.aea not fetched"; exit 1; }
    if [ -f "$OUT/$p.dmg" ]; then echo "    $p.dmg already decrypted"; continue; fi
    echo "    $p.dmg.aea -> $OUT/$p.dmg"
    ipsw fw aea "$aea" --output "$OUT"
done
ls -l "$OUT"

echo "==> unwrapping trust caches (Apple ships them IM4P-wrapped; -tc wants the payload)"
for p in "${PAYLOADS[@]}"; do
    tc=$(find "$RAW" -name "$p.dmg.aea.trustcache" -o -name "$p.dmg.trustcache" | head -1)
    [ -n "$tc" ] || { echo "    no trustcache for $p"; continue; }
    ipsw img4 im4p extract "$tc" -o "$TC/${p}_tc_raw" >/dev/null
    echo "    $TC/${p}_tc_raw  ($(stat -f%z "$TC/${p}_tc_raw") B)"
done
echo "==> done"
