#!/bin/sh
# Disposable restore guest only; retain original input binary and service copy.
set -eu
mount_apfs /dev/disk1s1 /mnt1
cache=/mnt1/System/Library/xpc/launchd.plist
test "$(cksum < "$cache")" = "$(cksum < /libexec/gpu-load-before.plist)"
test ! -e "$cache.gpu-forward-original"
test -x /mnt1/usr/local/libexec/dvm-input
for f in dvm-gpu-work dvm-gpu-transport DVMForward.bundle; do
    test ! -e /mnt1/usr/local/libexec/$f
    cp -R /libexec/$f /mnt1/usr/local/libexec/$f
    chown -R 0:0 /mnt1/usr/local/libexec/$f
done
for f in dvm-gpu-work dvm-gpu-transport DVMForward.bundle/DVMForward; do
    chmod 755 /mnt1/usr/local/libexec/$f
    test "$(cksum < /mnt1/usr/local/libexec/$f)" = "$(cksum < /libexec/$f)"
done
test "$(cksum < /mnt1/usr/local/libexec/DVMForward.bundle/Info.plist)" = "$(cksum < /libexec/DVMForward.bundle/Info.plist)"
service=/mnt1$(cat /libexec/gpu-load-service-path.txt)
test -f "$service"
test ! -e "$service.gpu-forward-original"
cp "$service" "$service.gpu-forward-original"
cp /libexec/gpu-load-service.plist "$service"
chmod 644 "$service"
chown 0:0 "$service"
cp "$cache" "$cache.gpu-forward-original"
cp /libexec/gpu-load-cache.plist "$cache.gpu-new"
chmod 644 "$cache.gpu-new"
chown 0:0 "$cache.gpu-new"
test "$(cksum < "$cache.gpu-new")" = "$(cksum < /libexec/gpu-load-cache.plist)"
mv "$cache.gpu-new" "$cache"
sync
echo GPU_LOAD_INSTALLED
