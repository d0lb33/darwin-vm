#!/bin/bash
# Build and ad-hoc sign the bounded iOS guest RTC/power-source inspection helper.
# helper.tc remains separate; the caller owns any disposable trust-cache merge.
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/POWER_SOURCE1}
mkdir -p "$out"
sdk=$(xcrun --sdk macosx --show-sdk-path)

clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror \
    -Wno-incompatible-sysroot "$repo/tools/re/power_rtc_probe.c" \
    -o "$out/power-rtc-probe"

codesign --force --sign - --timestamp=none --entitlements \
    "$repo/tools/input/entitlements.plist" "$out/power-rtc-probe"
codesign --verify --strict --verbose=2 "$out/power-rtc-probe"
codesign -d -vvv "$out/power-rtc-probe" 2>"$out/power-rtc-probe.codesign.txt"
sed -n 's/^CDHash=//p' "$out/power-rtc-probe.codesign.txt" \
    >"$out/power-rtc-probe.hashes.txt"
test "$(wc -l < "$out/power-rtc-probe.hashes.txt" | tr -d ' ')" = 1
test "$(wc -c < "$out/power-rtc-probe.hashes.txt" | tr -d ' ')" = 41
python3 "$repo/build_tc.py" "$out/power-rtc-probe.hashes.txt" \
    "$out/power-rtc-probe.tc"
file "$out/power-rtc-probe"
