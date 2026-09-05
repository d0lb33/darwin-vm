#!/bin/sh
# Runs only in the copied restore ramdisk with a fresh migrated disk child.
set -eu
mount_apfs /dev/disk1s1 /mnt1
cache=/mnt1/System/Library/xpc/launchd.plist
test "$(cksum < "$cache")" = "$(cksum < /libexec/gpu-load-before.plist)"
test ! -e "$cache.gpu-load-original"
test ! -e /mnt1/usr/local/libexec/dvm-gpu-load
test ! -e /mnt1/usr/local/libexec/DVMProxy.bundle
cp "$cache" "$cache.gpu-load-original"
cp /libexec/dvm-gpu-load /mnt1/usr/local/libexec/dvm-gpu-load
cp -R /libexec/DVMProxy.bundle /mnt1/usr/local/libexec/DVMProxy.bundle
chmod 755 /mnt1/usr/local/libexec/dvm-gpu-load /mnt1/usr/local/libexec/DVMProxy.bundle/DVMProxy
chown -R 0:0 /mnt1/usr/local/libexec/DVMProxy.bundle
chown 0:0 /mnt1/usr/local/libexec/dvm-gpu-load
for f in dvm-gpu-load DVMProxy.bundle/DVMProxy DVMProxy.bundle/Info.plist; do
    test "$(cksum < /mnt1/usr/local/libexec/$f)" = "$(cksum < /libexec/$f)"
done
service=/mnt1/System/Library/LaunchDaemons/org.darwin-vm.gpu-load.plist
cp /libexec/gpu-load-service.plist "$service"
chmod 644 "$service"
chown 0:0 "$service"
cp /libexec/gpu-load-cache.plist "$cache.gpu-new"
chmod 644 "$cache.gpu-new"
chown 0:0 "$cache.gpu-new"
test "$(cksum < "$cache.gpu-new")" = "$(cksum < /libexec/gpu-load-cache.plist)"
mv "$cache.gpu-new" "$cache"
sync
echo GPU_LOAD_INSTALLED
