#!/bin/sh
# Run inside the staged restore image with an unused disposable disk child.
set -eu
mount_apfs /dev/disk1s1 /mnt1
cache=/mnt1/System/Library/xpc/launchd.plist
test "$(cksum < "$cache")" = "$(cksum < /libexec/dvm-graphics-before.plist)"
test ! -e "$cache.dvm-graphics-original"
test ! -e /mnt1/usr/local/libexec/dvm-graphics-probe
cp "$cache" "$cache.dvm-graphics-original"
cp /libexec/dvm-graphics-probe /mnt1/usr/local/libexec/dvm-graphics-probe
chmod 755 /mnt1/usr/local/libexec/dvm-graphics-probe
chown 0:0 /mnt1/usr/local/libexec/dvm-graphics-probe
test "$(cksum < /mnt1/usr/local/libexec/dvm-graphics-probe)" = "$(cksum < /libexec/dvm-graphics-probe)"
service=/mnt1/System/Library/LaunchDaemons/com.apple.dvm-graphics-probe.plist
cp /libexec/dvm-graphics-service.plist "$service"
chmod 644 "$service"
chown 0:0 "$service"
for name in power-rtc-probe activation-probe; do
    test -f "/libexec/dvm-$name" || continue
    power="/mnt1/usr/local/libexec/dvm-$name"
    test ! -e "$power"
    cp "/libexec/dvm-$name" "$power"
    chmod 755 "$power"
    chown 0:0 "$power"
    test "$(cksum < "$power")" = "$(cksum < "/libexec/dvm-$name")"
    power_service="/mnt1/System/Library/LaunchDaemons/com.apple.dvm-$name.plist"
    cp "/libexec/dvm-$name-service.plist" "$power_service"
    chmod 644 "$power_service"
    chown 0:0 "$power_service"
done
cp /libexec/dvm-graphics-cache.plist "$cache.dvm-new"
chmod 644 "$cache.dvm-new"
chown 0:0 "$cache.dvm-new"
test "$(cksum < "$cache.dvm-new")" = "$(cksum < /libexec/dvm-graphics-cache.plist)"
mv "$cache.dvm-new" "$cache"
sync
echo DVM_GRAPHICS_PROBE_INSTALLED
