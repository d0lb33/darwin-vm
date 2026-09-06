#!/bin/sh
# Run inside the prepared restore ramdisk with a fresh migrated disk child.
set -eu
mount_apfs /dev/disk1s1 /mnt1
test -x /mnt1/usr/local/libexec/dvm-input
test -f /mnt1/System/Library/LaunchDaemons/com.apple.dvm-input.plist
cache=/mnt1/System/Library/xpc/launchd.plist
test -s "$cache"
test ! -e "$cache.dvm-original"
cp "$cache" "$cache.dvm-original"
cp /libexec/dvm-launchd.plist "$cache.dvm-new"
chmod 644 "$cache.dvm-new"
chown 0:0 "$cache.dvm-new"
set -- $(cksum /libexec/dvm-launchd.plist)
expected="$1:$2"
set -- $(cksum "$cache.dvm-new")
test "$expected" = "$1:$2"
mv "$cache.dvm-new" "$cache"
sync
echo "DVM_LAUNCH_CACHE_INSTALLED checksum=$1 bytes=$2"
