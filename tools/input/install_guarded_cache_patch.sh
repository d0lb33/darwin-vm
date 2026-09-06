#!/bin/sh
# Run only in the prepared restore guest against a disposable System child.
set -eu
. /libexec/dvm-cache-offsets.sh
mount_apfs /dev/disk1s1 /mnt1
cache=/mnt1/System/Library/Caches/com.apple.dyld/$cache_name
test -f "$cache"
same_bytes() {
    set -- $(cksum "$1") $(cksum "$2")
    test "$1:$2" = "$4:$5"
}
test "$(dd if="$cache" bs=4096 count=1 | cksum)" = "$(cksum < /libexec/dvm-cache-header)"
if test -n "${helper_name:-}"; then
    helper=/mnt1/usr/local/libexec/$helper_name
    test -f "$helper"
    same_bytes /libexec/dvm-helper-before "$helper"
fi
# Check all code and signature preimages before the first write.
i=0
while test "$i" -lt "$patch_count"; do
    eval "offset=\$offset_$i length=\$length_$i"
    test "$(dd if="$cache" bs=1 skip="$offset" count="$length" | cksum)" = "$(cksum < "/libexec/dvm-cache-before-$i")"
    i=$((i + 1))
done
i=0
while test "$i" -lt "$patch_count"; do
    eval "offset=\$offset_$i length=\$length_$i"
    dd if="/libexec/dvm-cache-after-$i" of="$cache" bs=1 seek="$offset" conv=notrunc 2>/dev/null
    test "$(dd if="$cache" bs=1 skip="$offset" count="$length" | cksum)" = "$(cksum < "/libexec/dvm-cache-after-$i")"
    i=$((i + 1))
done
if test -n "${helper_name:-}"; then
    cp /libexec/dvm-helper-after "$helper.dvm-new"
    chmod 755 "$helper.dvm-new"
    chown 0:0 "$helper.dvm-new"
    same_bytes /libexec/dvm-helper-after "$helper.dvm-new"
    mv "$helper.dvm-new" "$helper"
    same_bytes /libexec/dvm-helper-after "$helper"
fi
sync
echo DVM_CACHE_PATCH_INSTALLED
