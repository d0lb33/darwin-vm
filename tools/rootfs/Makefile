# The restore ramdisk is arm64e/iOS; this host only has the macOS CLT SDK.
# It is sufficient for the public libc/copyfile headers and emits the iOS
# platform load command, but clang warns about the incompatible sysroot.  The
# helper uses dlopen for every private framework, so it does not link macOS
# framework binaries into the output.
SDK ?= $(shell xcrun --sdk macosx --show-sdk-path)
CLANG ?= clang
OUT ?= /tmp/dvm/data-seed/data_seed_helper
REPO ?= $(abspath ../..)
RAMDISK_HASHES ?= $(REPO)/firmware/all_hashes
HELPER_TC ?= /tmp/dvm/data-seed/data_seed_helper.tc

.PHONY: data-seed-helper data-seed-helper-tc
data-seed-helper: data_seed_helper.c
	mkdir -p $(dir $(OUT))
	$(CLANG) -target arm64-apple-ios7.0 -isysroot $(SDK) -Os -Wall -Wextra \
		-Wno-incompatible-sysroot $< -o $(OUT)
	codesign --force --sign - --timestamp=none $(OUT)
	@file $(OUT)

# Keep the restore ramdisk's deliberately unconstrained v1 entries and add
# precisely this helper's CDHash.  This changes only a disposable trust-cache
# artifact passed to QEMU; it never attaches or alters a disk image.
data-seed-helper-tc: data-seed-helper $(RAMDISK_HASHES) $(REPO)/build_tc.py
	@hash=$$(codesign -d -vvv $(OUT) 2>&1 | sed -n 's/^CDHash=//p'); test -n "$$hash" && printf '%s\n' "$$hash" | grep -Eq '^[0-9a-f]{40}$$' || { echo 'no helper CDHash' >&2; exit 1; }; (cat $(RAMDISK_HASHES); printf '%s\n' "$$hash") | $(REPO)/build_tc.py /dev/stdin $(HELPER_TC)
	@cmp -s $(HELPER_TC) $(REPO)/firmware/ramdisk.tc || true
	@echo "helper trustcache: $(HELPER_TC)"
