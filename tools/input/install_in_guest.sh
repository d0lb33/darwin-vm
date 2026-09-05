#!/bin/sh
# Execute only inside the restore ramdisk prepared by prepare_ramdisk.sh.
# Source System comes from the migrated disk child, never from a fresh image.
set -eu
test -x /libexec/dvm-input
test -f /libexec/dvm-input.plist
mount_apfs /dev/disk1s1 /mnt1
test -d /mnt1/System/Library/LaunchDaemons
mkdir -p /mnt1/usr/local/libexec
cp /libexec/dvm-input /mnt1/usr/local/libexec/dvm-input
chmod 755 /mnt1/usr/local/libexec/dvm-input
cp /libexec/dvm-input.plist /mnt1/System/Library/LaunchDaemons/com.apple.dvm-input.plist
chmod 644 /mnt1/System/Library/LaunchDaemons/com.apple.dvm-input.plist
cmp /libexec/dvm-input /mnt1/usr/local/libexec/dvm-input
cksum /mnt1/usr/local/libexec/dvm-input
printf 'DVMINPUT1 1 H 0 0 0\n' | /libexec/dvm-input --validate
sync
# This restore image has no umount executable. Flush before the host stops
# this staging VM; APFS replays its journal on the subsequent normal boot.
echo DVM_INPUT_INSTALL_DONE
