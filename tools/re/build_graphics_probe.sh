#!/bin/bash
# Build and ad-hoc sign the standalone guest graphics diagnostic.  helper.tc
# is intentionally separate: the caller chooses whether/how to merge it into
# a disposable image's trust cache.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/GRAPHICS_PROBE1}
mkdir -p "$out"
sdk=$(xcrun --sdk macosx --show-sdk-path)
clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror \
    -Wno-incompatible-sysroot "$repo/tools/re/graphics_probe.c" -o "$out/graphics-probe"
codesign --force --sign - --timestamp=none --entitlements \
    "$repo/tools/input/entitlements.plist" "$out/graphics-probe"
codesign --verify --strict --verbose=2 "$out/graphics-probe"
codesign -d -vvv "$out/graphics-probe" 2>"$out/codesign.txt"
sed -n 's/^CDHash=//p' "$out/codesign.txt" > "$out/hashes.txt"
test "$(wc -l < "$out/hashes.txt" | tr -d ' ')" = 1
test "$(wc -c < "$out/hashes.txt" | tr -d ' ')" = 41
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
file "$out/graphics-probe"
