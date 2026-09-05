#!/bin/bash
# Transport only a derived launchd cache in the small restore ramdisk.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
cache=${1:?supply a derived launchd.plist}
image=${2:?supply a new ramdisk path}
test -s "$cache"
test ! -e "$image"
cp -p "$repo/firmware/ramdisk.dmg" "$image"
mnt=$("$repo/tools/rootfs/safe_attach.sh" attach "$image" --owners on)
trap '"$repo/tools/rootfs/safe_attach.sh" detach "$mnt"' EXIT
cp -X "$cache" "$mnt/libexec/dvm-launchd.plist"
cp -X "$repo/tools/input/install_cache_in_guest.sh" "$mnt/libexec/dvm-cache-install.sh"
cmp "$cache" "$mnt/libexec/dvm-launchd.plist"
sync
"$repo/tools/rootfs/safe_attach.sh" detach "$mnt"
trap - EXIT
