#!/bin/sh
# Restore-guest only.  Install into one fresh R12 disk child, then stop the
# staging VM.  Buddy, activation, and user-preference state are deliberately
# untouched; only System cache, helpers, and their launchd registration change.
set -eu

mount_apfs /dev/disk1s1 /mnt1
root=/mnt1
launchd="$root/System/Library/xpc/launchd.plist"
dyld="$root/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e.01"

test -f "$launchd"
test -f "$dyld"
test "$(cksum < "$launchd")" = "$(cksum < /libexec/dvm-launchd-before.plist)"
test ! -e "$launchd.dvm-setup-runtime-original"

# Keep the exact signed display-policy preimage guard and update exactly the
# already measured instruction and CodeDirectory page hash.
. /libexec/dvm-policy-offset.sh
test "$(dd if="$dyld" bs=4096 count=1 | cksum)" = "$(cksum < /libexec/dvm-policy-header)"
test "$(dd if="$dyld" bs=1 skip=70968036 count=16 | cksum)" = "$(cksum < /libexec/dvm-policy-before)"
test "$(dd if="$dyld" bs=1 skip="$hash_offset" count=32 | cksum)" = "$(cksum < /libexec/dvm-policy-old-hash)"

for name in input graphics-probe power-rtc-probe activation-probe power-pv-service; do
    test ! -e "$root/usr/local/libexec/dvm-$name"
    test ! -e "$root/System/Library/LaunchDaemons/com.apple.dvm-$name.plist"
done

mkdir -p "$root/usr/local/libexec"
for name in input graphics-probe power-rtc-probe activation-probe power-pv-service; do
    source="/libexec/dvm-$name"
    target="$root/usr/local/libexec/dvm-$name"
    cp "$source" "$target"
    chmod 755 "$target"
    chown 0:0 "$target"
    test "$(cksum < "$target")" = "$(cksum < "$source")"
    service="$root/System/Library/LaunchDaemons/com.apple.dvm-$name.plist"
    cp "/libexec/dvm-$name-service.plist" "$service"
    chmod 644 "$service"
    chown 0:0 "$service"
    test "$(cksum < "$service")" = "$(cksum < "/libexec/dvm-$name-service.plist")"
done

cp "$launchd" "$launchd.dvm-setup-runtime-original"
cp /libexec/dvm-launchd-cache.plist "$launchd.dvm-new"
chmod 644 "$launchd.dvm-new"
chown 0:0 "$launchd.dvm-new"
test "$(cksum < "$launchd.dvm-new")" = "$(cksum < /libexec/dvm-launchd-cache.plist)"
mv "$launchd.dvm-new" "$launchd"
test "$(cksum < "$launchd")" = "$(cksum < /libexec/dvm-launchd-cache.plist)"

dd if=/libexec/dvm-policy-word of="$dyld" bs=1 seek=70968044 count=4 conv=notrunc
dd if=/libexec/dvm-policy-new-hash of="$dyld" bs=1 seek="$hash_offset" count=32 conv=notrunc
test "$(dd if="$dyld" bs=1 skip=70968036 count=16 | cksum)" = "$(cksum < /libexec/dvm-policy-after)"
test "$(dd if="$dyld" bs=1 skip="$hash_offset" count=32 | cksum)" = "$(cksum < /libexec/dvm-policy-new-hash)"

printf 'DVMINPUT1 1 H 0 0 0\n' | "$root/usr/local/libexec/dvm-input" --validate
sync
echo DVM_SETUP_RUNTIME_INSTALLED
echo DVM_GRAPHICS_PROBE_INSTALLED
