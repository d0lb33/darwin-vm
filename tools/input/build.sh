#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/native-input}
mkdir -p "$out"
sdk=$(xcrun --sdk macosx --show-sdk-path)
clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra \
    -Werror -Wno-incompatible-sysroot "$repo/tools/input/dvm_input.c" -o "$out/dvm-input"
codesign --force --sign - --timestamp=none --entitlements "$repo/tools/input/entitlements.plist" "$out/dvm-input"
codesign -d -vvv "$out/dvm-input" 2>"$out/codesign.txt"
sed -n 's/^CDHash=//p' "$out/codesign.txt" > "$out/hashes.txt"
test "$(wc -c < "$out/hashes.txt" | tr -d ' ')" = 41
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
python3 "$repo/tools/rootfs/merge_tc.py" "$out/system.tc" \
    "$HOME/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin" "$out/helper.tc"
file "$out/dvm-input"
