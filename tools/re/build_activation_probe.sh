#!/bin/bash
# Build only the signed, read-only activation probe.  This does not stage it,
# change a trust cache, or run a VM; the caller chooses the output directory.
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:-/tmp/dvm/activation-probe}
mkdir -p "$out"
sdk=$(xcrun --sdk macosx --show-sdk-path)

clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror \
    -Wno-incompatible-sysroot "$repo/tools/re/activation_probe.c" \
    -o "$out/activation-probe"
codesign --force --sign - --timestamp=none "$out/activation-probe"
codesign -d -vvv "$out/activation-probe" 2>"$out/codesign.txt"
sed -n 's/^CDHash=//p' "$out/codesign.txt" > "$out/hashes.txt"
test "$(wc -c < "$out/hashes.txt" | tr -d ' ')" = 41
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
base_tc=${DVM_ACTIVATION_PROBE_BASE_TC:-/Users/jdolbe1/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin}
python3 "$repo/tools/rootfs/merge_tc.py" "$out/system.tc" "$base_tc" "$out/helper.tc"
file "$out/activation-probe"
