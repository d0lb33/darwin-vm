#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/native-input}
# HELPER_SRC selects the daemon source; the installed name stays dvm-input so
# the cached launchd job (cache_service.py) keeps starting it.
src=${HELPER_SRC:-$repo/tools/input/dvm_input.c}
mkdir -p "$out"
sdk=$(xcrun --sdk macosx --show-sdk-path)
clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra \
    -DDVM_HID_TOUCH_BUILTIN="${DVM_HID_TOUCH_BUILTIN:-0}" \
    -Werror -Wno-incompatible-sysroot "$src" -o "$out/dvm-input"
codesign --force --sign - --timestamp=none --entitlements "$repo/tools/input/entitlements.plist" "$out/dvm-input"
codesign -d -vvv "$out/dvm-input" 2>"$out/codesign.txt"
sed -n 's/^CDHash=//p' "$out/codesign.txt" > "$out/hashes.txt"
test "$(wc -c < "$out/hashes.txt" | tr -d ' ')" = 41
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
python3 "$repo/tools/rootfs/merge_tc.py" "$out/system.tc" \
    "${BASE_TC:-$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin}" "$out/helper.tc"
file "$out/dvm-input"
