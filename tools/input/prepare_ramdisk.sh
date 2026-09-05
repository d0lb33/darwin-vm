#!/bin/bash
# Stage the input helper in a new, small restore image. Never mount the system disk.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/native-input}
image="$out/ramdisk-input.dmg"
test -f "$out/dvm-input"
test ! -e "$image"
cp -p "$repo/firmware/ramdisk.dmg" "$image"
mnt=$("$repo/tools/rootfs/safe_attach.sh" attach "$image" --owners on)
trap '"$repo/tools/rootfs/safe_attach.sh" detach "$mnt"' EXIT
test -d "$mnt/libexec"
cp -X "$out/dvm-input" "$mnt/libexec/dvm-input"
cp -X "$repo/tools/input/com.apple.dvm-input.plist" "$mnt/libexec/dvm-input.plist"
cp -X "$repo/tools/input/install_in_guest.sh" "$mnt/libexec/dvm-input-install.sh"
chmod 755 "$mnt/libexec/dvm-input"
cmp "$out/dvm-input" "$mnt/libexec/dvm-input"
sync
"$repo/tools/rootfs/safe_attach.sh" detach "$mnt"
trap - EXIT
python3 "$repo/tools/rootfs/merge_tc.py" "$out/restore.tc" \
    "$repo/firmware/ramdisk.tc" "$out/helper.tc"
