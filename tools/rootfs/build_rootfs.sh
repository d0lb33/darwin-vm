#!/bin/bash
# Stage 1 of the root filesystem build: take the real iOS system volume, grow
# it, and put the OS cryptex's dyld shared cache where dyld will find it.
#
# Why the cache goes on the system volume rather than under a cryptex mount:
# /usr/lib/dyld statically links libignition, and sym.__dylib_cache_fire (file
# offset 0x19b40) openat()s "/System/Library/Caches/com.apple.dyld/" under the
# current dylib root first; if that succeeds it calls _boot_set_root and
# returns without attempting any cryptex graft at all
# ("libignition: %d: %12s: dylib cache directory present; not overriding",
# string at 0xad96d). Full derivation in docs/re/rootfs-assembly.md, Problem 1.
#
# Ownership is the hard part, and this is where the assembly doc is out of
# date: it concluded uid 501 on the cache was acceptable. It is not -- dyld
# enforces uid == 0 on the shared cache. We have no sudo and chown(2) is
# root-only, but rename(2) preserves an inode's on-disk uid and truncating a
# file in place keeps its inode. So for each cache file we rename a root-owned
# donor (an expendable non-English localisation resource) onto the target name,
# then overwrite its contents.
#
# Donors MUST have a link count of 1. Apple dedupes identical localisation
# resources with hardlinks (commonly 40 links to one inode); renaming two such
# donors into the cache directory would give two cache files the same inode, so
# writing one would silently corrupt the other.
#
# Usage: tools/rootfs/build_rootfs.sh [workdir]     (default /tmp/dvm)
set -uo pipefail
set -e

WORK=${1:-/tmp/dvm}
SYSVOL=${SYSVOL:-$WORK/aea/out/094-13182-141.dmg}
CRYPTEX=${CRYPTEX:-$WORK/aea/out/094-13150-145.dmg}
OUT=${OUT:-$WORK/build/rootfs.dmg}
GROW_MB=${GROW_MB:-12288}
SRCMNT=$WORK/b_src
TGTMNT=$WORK/b_tgt

CONT=""
cleanup() {
    diskutil unmount force "$TGTMNT" >/dev/null 2>&1 || true
    hdiutil detach "$SRCMNT" -quiet >/dev/null 2>&1 || true
    # Detach the target container too. The pre-wipe version of this script did
    # not, which left rootfs.dmg attached read-write after every run -- and a
    # second read-write attachment of one image is what silently corrupted an
    # image before. Detach only what we attached.
    [ -n "$CONT" ] && hdiutil detach "$CONT" -quiet >/dev/null 2>&1 || true
}
trap cleanup EXIT

[ -f "$SYSVOL" ]  || { echo "missing $SYSVOL (run tools/rootfs/fetch_payloads.sh)"; exit 1; }
[ -f "$CRYPTEX" ] || { echo "missing $CRYPTEX (run tools/rootfs/fetch_payloads.sh)"; exit 1; }

echo "==> fresh copy of the system volume"
rm -f "$OUT"; mkdir -p "$(dirname "$OUT")" "$SRCMNT" "$TGTMNT"
cp "$SYSVOL" "$OUT"

echo "==> grow by ${GROW_MB}MB"
# NEVER redirect dd's own stderr into the target: `>> "$OUT" 2>&1` mixes dd's
# "records in/out" text into the APFS bytes and hdiutil then reports
# "image not recognized". docs/re/rootfs-assembly.md, build recipe note 2.
dd if=/dev/zero bs=1m count=$GROW_MB >> "$OUT" 2>/dev/null
CONT=$(hdiutil attach -nomount "$OUT" | awk '/EF57347C/{print $1; exit}')
diskutil apfs resizeContainer "$CONT" 0 >/dev/null
VOL=$(diskutil list "$CONT" | awk '/APFS Volume/{print "/dev/"$NF; exit}')
[ -n "$VOL" ] || VOL=$(hdiutil info | awk -v c="$CONT" '$0~c{f=1} f&&/41504653/{print $1; exit}')
echo "    container $CONT volume $VOL"

echo "==> mount source cryptex and target"
hdiutil attach -readonly -nobrowse -mountpoint "$SRCMNT" "$CRYPTEX" >/dev/null
diskutil mount -mountPoint "$TGTMNT" -mountOptions noowners "$VOL" >/dev/null

SRC="$SRCMNT/System/Library/Caches/com.apple.dyld"
DST="$TGTMNT/System/Library/Caches/com.apple.dyld"
[ -d "$SRC" ] || { echo "no dyld cache at $SRC"; exit 1; }

echo "==> collect root-owned donors"
find "$TGTMNT/System/Library/PrivateFrameworks" \
     \( -path "*/ur.lproj/*" -o -path "*/he.lproj/*" -o -path "*/de.lproj/*" \
        -o -path "*/te.lproj/*" -o -path "*/pa.lproj/*" -o -path "*/mr.lproj/*" \) \
     -type f -links 1 -size +0 2>/dev/null | head -300 > "$WORK/b_donors.txt" || true
# The directory donor must not be one whose files we are also consuming, so
# take it from a language outside the file-donor set.
DONORDIR=$(find "$TGTMNT/System/Library/PrivateFrameworks" -maxdepth 2 -name "te.lproj" -type d 2>/dev/null | head -1 || true)
[ -n "$DONORDIR" ] || { echo "no donor directory found"; exit 1; }
echo "    $(wc -l < "$WORK/b_donors.txt" | tr -d ' ') file donors, dir donor: $(basename "$(dirname "$DONORDIR")")/$(basename "$DONORDIR")"

DUPS=$(tr '\n' '\0' < "$WORK/b_donors.txt" | xargs -0 stat -f '%i' | sort | uniq -d | wc -l | tr -d ' ')
[ "$DUPS" = "0" ] || { echo "donor list has $DUPS duplicate inodes; aborting"; exit 1; }
echo "    donors verified: all distinct inodes, link count 1"

echo "==> stage the cache directory (root-owned via rename)"
rm -rf "$DST"
mv "$DONORDIR" "$DST"
rm -f "$DST"/* 2>/dev/null || true

echo "==> stage the cache files"
ls "$SRC" > "$WORK/b_names.txt"
NEED=$(wc -l < "$WORK/b_names.txt" | tr -d ' ')
HAVE=$(wc -l < "$WORK/b_donors.txt" | tr -d ' ')
[ "$HAVE" -ge "$NEED" ] || { echo "only $HAVE donors for $NEED cache files"; exit 1; }
head -n "$NEED" "$WORK/b_donors.txt" > "$WORK/b_use.txt"
paste "$WORK/b_names.txt" "$WORK/b_use.txt" | while IFS=$'\t' read -r n d; do
    mv "$d" "$DST/$n"
    cat "$SRC/$n" > "$DST/$n"
done
sync

echo "==> verify"
python3 - "$SRC" "$DST" <<'PY'
import os, sys
s, d = sys.argv[1], sys.argv[2]
names = sorted(os.listdir(s))
bad = [(n, os.path.getsize(os.path.join(s, n)), os.path.getsize(os.path.join(d, n))) for n in names
       if os.path.getsize(os.path.join(s, n)) != os.path.getsize(os.path.join(d, n))]
print(f"    {len(names)} files, mismatches: {bad if bad else 'NONE'}")
sys.exit(1 if bad else 0)
PY
echo "==> done: $OUT"
