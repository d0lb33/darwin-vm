#!/bin/sh
# Restore-guest only. Target must be a fresh child of the migrated disk.
set -eu
mount_apfs /dev/disk1s1 /mnt1
cache=/mnt1/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e.01
test -f "$cache"
. /libexec/dvm-policy-offset.sh
# The full header includes the cache UUID and mapping identity.
test "$(dd if="$cache" bs=4096 count=1 | cksum)" = "$(cksum < /libexec/dvm-policy-header)"
test "$(dd if="$cache" bs=1 skip=70968036 count=16 | cksum)" = "$(cksum < /libexec/dvm-policy-before)"
test "$(dd if="$cache" bs=1 skip="$hash_offset" count=32 | cksum)" = "$(cksum < /libexec/dvm-policy-old-hash)"
dd if=/libexec/dvm-policy-word of="$cache" bs=1 seek=70968044 count=4 conv=notrunc
dd if=/libexec/dvm-policy-new-hash of="$cache" bs=1 seek="$hash_offset" count=32 conv=notrunc
test "$(dd if="$cache" bs=1 skip=70968036 count=16 | cksum)" = "$(cksum < /libexec/dvm-policy-after)"
test "$(dd if="$cache" bs=1 skip="$hash_offset" count=32 | cksum)" = "$(cksum < /libexec/dvm-policy-new-hash)"
sync
echo DVM_DISPLAY_POLICY_INSTALLED
