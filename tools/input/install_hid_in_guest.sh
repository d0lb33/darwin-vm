#!/bin/sh
# Execute only inside the restore ramdisk prepared by prepare_ramdisk.sh with
# INSTALLER=install_hid_in_guest.sh. Replaces the cached launchd job's
# executable (/usr/local/libexec/dvm-input) with the DVMI2 helper; the plist
# and launchd cache entry are unchanged, so no cache_service.py step is needed.
set -eu
test -x /libexec/dvm-input
test -f /libexec/dvm-input.plist
mount_apfs /dev/disk1s1 /mnt1
test -d /mnt1/System/Library/LaunchDaemons
mkdir -p /mnt1/usr/local/libexec
if test -f /mnt1/usr/local/libexec/dvm-input; then
    set -- $(cksum /mnt1/usr/local/libexec/dvm-input)
    echo "DVM_HID_PREVIOUS checksum=$1 bytes=$2"
fi
cp /libexec/dvm-input /mnt1/usr/local/libexec/dvm-input.new
chmod 755 /mnt1/usr/local/libexec/dvm-input.new
chown 0:0 /mnt1/usr/local/libexec/dvm-input.new
mv /mnt1/usr/local/libexec/dvm-input.new /mnt1/usr/local/libexec/dvm-input
cp /libexec/dvm-input.plist /mnt1/System/Library/LaunchDaemons/com.apple.dvm-input.plist
chmod 644 /mnt1/System/Library/LaunchDaemons/com.apple.dvm-input.plist
set -- $(cksum /libexec/dvm-input)
source_crc=$1 source_bytes=$2
set -- $(cksum /mnt1/usr/local/libexec/dvm-input)
test "$source_crc:$source_bytes" = "$1:$2"
echo "DVM_HID_INSTALLED checksum=$1 bytes=$2"
# The validator never opens HID; it proves the record parser and ACK format.
# The restore ramdisk has no writable /tmp, so keep the output in a variable.
validation=$(printf 'DVMI2 1 1 P 0 0 0 0\nDVMI2 1 2 D 100 200 0 0\nDVMI2 1 3 X 0 0 0 0\n' | /libexec/dvm-input --validate 2>&1)
echo "$validation"
case "$validation" in *'DVMI2A 1 1 Q R'*) ;; *) echo DVM_HID_VALIDATE_FAILED; exit 1;; esac
case "$validation" in *'DVMI2A 1 2 Q R'*) ;; *) echo DVM_HID_VALIDATE_FAILED; exit 1;; esac
case "$validation" in *'DVM_HID_REJECT'*) ;; *) echo DVM_HID_VALIDATE_FAILED; exit 1;; esac
sync
echo DVM_HID_INSTALL_DONE
