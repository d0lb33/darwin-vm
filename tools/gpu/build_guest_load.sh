#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?usage: build_guest_load.sh NEW_OUTPUT_DIRECTORY}
test ! -e "$out"
mkdir -p "$out/DVMProxy.bundle"
cp "$repo/tools/gpu/guest_load_probe.c" "$repo/tools/gpu/guest_bundle.c" "$out/"
cp "$0" "$out/build_guest_load.sh"
sdk=$(xcrun --sdk macosx --show-sdk-path)
flags=(-target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror -Wno-incompatible-sysroot)
clang "${flags[@]}" "$repo/tools/gpu/guest_load_probe.c" -o "$out/dvm-gpu-load"
clang "${flags[@]}" -dynamiclib "$repo/tools/gpu/guest_bundle.c" \
    -Wl,-install_name,/usr/local/libexec/DVMProxy.bundle/DVMProxy -o "$out/DVMProxy.bundle/DVMProxy"
python3 - "$out" <<'PY'
from pathlib import Path
import plistlib, sys
p = Path(sys.argv[1])
(p/'DVMProxy.bundle/Info.plist').write_bytes(plistlib.dumps(dict(
    CFBundleIdentifier='org.darwin-vm.gpu-feasibility', CFBundleName='DVMProxy',
    CFBundleExecutable='DVMProxy', CFBundlePackageType='BNDL', CFBundleVersion='1')))
(p/'entitlements.plist').write_bytes(plistlib.dumps({'platform-application': True}))
PY
codesign --force --sign - --timestamp=none --entitlements "$out/entitlements.plist" "$out/dvm-gpu-load"
codesign --force --sign - --timestamp=none "$out/DVMProxy.bundle"
: > "$out/hashes.txt"
for item in "$out/dvm-gpu-load" "$out/DVMProxy.bundle/DVMProxy"; do
    codesign --verify --strict --verbose=2 "$item"
    codesign -d -vvv "$item" 2> "$out/$(basename "$item").codesign.txt"
    sed -n 's/^CDHash=//p' "$out/$(basename "$item").codesign.txt" >> "$out/hashes.txt"
    otool -l "$item" > "$out/$(basename "$item").load-commands.txt"
done
test "$(wc -c < "$out/hashes.txt" | tr -d ' ')" = 82
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
