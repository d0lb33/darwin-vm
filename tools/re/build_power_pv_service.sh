#!/bin/bash
# Build and ad-hoc sign the explicit process-scoped iOS power-source publisher.
# This script creates an artifact and trust-cache fragment only; it never stages
# either artifact or installs a launchd job.
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/POWER_SOURCE1}
mkdir -p "$out"
sdk=$(xcrun --sdk macosx --show-sdk-path)

clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror -fblocks \
    -Wno-incompatible-sysroot "$repo/tools/re/power_pv_service.c" \
    -o "$out/power-pv-service"

codesign --force --sign - --timestamp=none --entitlements \
    "$repo/tools/re/power_pv_entitlements.plist" "$out/power-pv-service"
codesign --verify --strict --verbose=2 "$out/power-pv-service"
codesign -d -vvv "$out/power-pv-service" 2>"$out/power-pv-service.codesign.txt"
sed -n 's/^CDHash=//p' "$out/power-pv-service.codesign.txt" \
    >"$out/power-pv-service.hashes.txt"
test "$(wc -l < "$out/power-pv-service.hashes.txt" | tr -d ' ')" = 1
test "$(wc -c < "$out/power-pv-service.hashes.txt" | tr -d ' ')" = 41
python3 "$repo/build_tc.py" "$out/power-pv-service.hashes.txt" \
    "$out/power-pv-service.tc"
file "$out/power-pv-service"
