#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
before=${1:?existing driver build required}
out=${2:?new output directory required}
test $# = 2
test ! -e "$out"
mkdir -p "$out"
cp -R "$before/DVMProxy.bundle" "$out/"
cp "$repo/tools/gpu/mmio_probe.c" "$0" "$out/"
cp "$repo/qemu-sptm/include/xnu/darwin_gpu_transport.h" "$out/"
python3 - "$out/entitlements.plist" <<'PY'
import plistlib,sys
from pathlib import Path
Path(sys.argv[1]).write_bytes(plistlib.dumps({'platform-application':True,
    'org.darwin-vm.transport':True,
    'com.apple.security.exception.iokit-user-client-class':['IOKitDiagnosticsClient']}))
PY
sdk=$(xcrun --sdk macosx --show-sdk-path)
xcrun clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror -Wno-incompatible-sysroot \
    "$out/mmio_probe.c" -o "$out/dvm-gpu-load"
codesign --force --sign - --timestamp=none --entitlements "$out/entitlements.plist" "$out/dvm-gpu-load"
: > "$out/hashes.txt"
for item in "$out/dvm-gpu-load" "$out/DVMProxy.bundle/DVMProxy"; do
    codesign --verify --strict --verbose=2 "$item"
    codesign -d -vvv "$item" 2> "$out/$(basename "$item").codesign.txt"
    sed -n 's/^CDHash=//p' "$out/$(basename "$item").codesign.txt" >> "$out/hashes.txt"
done
nm -u "$out/dvm-gpu-load" > "$out/dvm-gpu-load.nm-u"
python3 "$repo/tools/gpu/verify_guest_imports.py" --output "$out/import-provenance.tsv" "$out/dvm-gpu-load.nm-u"
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
