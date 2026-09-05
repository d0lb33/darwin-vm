#!/bin/bash
# Header-only macOS SDK use; link iOS objects against exact-guest declarations.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?usage: build_guest_forward.sh NEW_OUTPUT_DIRECTORY}
surface_access=${2:-}
test -z "$surface_access" || test "$surface_access" = --iosurface-client
test ! -e "$out"
mkdir -p "$out/DVMForward.bundle" "$out/sources"
cp "$repo"/tools/gpu/{guest_forwarding.m,guest_work.m,guest_transport.c,make_guest_link_stubs.py,verify_guest_imports.py,build_guest_forward.sh} "$out/sources/"
python3 "$repo/tools/gpu/make_guest_link_stubs.py" "$out/stubs"
sdk=$(xcrun --sdk macosx --show-sdk-path)
compile=(-target arm64-apple-ios27.0 -isysroot "$sdk" -fobjc-arc -Wall -Wextra -Werror -Wno-incompatible-sysroot -fno-objc-msgsend-selector-stubs)
link=(-target arm64-apple-ios27.0 -isysroot "$sdk" -Wno-incompatible-sysroot -F "$out/stubs/System/Library/Frameworks" -L "$out/stubs/usr/lib")
frameworks=(-framework Foundation -framework CoreFoundation -framework IOSurface -framework Metal -lobjc)
xcrun clang "${compile[@]}" -c "$repo/tools/gpu/guest_forwarding.m" -o "$out/forward.o"
xcrun clang "${link[@]}" -dynamiclib -Wl,-install_name,/usr/local/libexec/DVMForward.bundle/DVMForward "$out/forward.o" "${frameworks[@]}" -o "$out/DVMForward.bundle/DVMForward"
xcrun clang "${compile[@]}" -c "$repo/tools/gpu/guest_work.m" -o "$out/work.o"
xcrun clang "${link[@]}" "$out/work.o" "${frameworks[@]}" -o "$out/dvm-gpu-work"
# This C-only supervisor uses the previously proven minimal helper toolchain.
xcrun clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror -Wno-incompatible-sysroot "$repo/tools/gpu/guest_transport.c" -o "$out/dvm-gpu-transport"
python3 - "$out" "$surface_access" <<'PY'
from pathlib import Path
import plistlib,sys
p=Path(sys.argv[1])
(p/'DVMForward.bundle/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='org.darwin-vm.gpu-forward',CFBundleName='DVMForward',CFBundleExecutable='DVMForward',CFBundlePackageType='BNDL',CFBundleVersion='1')))
(p/'entitlements.plist').write_bytes(plistlib.dumps({'platform-application':True}))
worker={'platform-application':True}
if sys.argv[2]=='--iosurface-client':
    worker['com.apple.security.exception.iokit-user-client-class']=['IOSurfaceRootUserClient']
(p/'worker-entitlements.plist').write_bytes(plistlib.dumps(worker))
PY
codesign --force --sign - --timestamp=none "$out/DVMForward.bundle"
for item in dvm-gpu-work dvm-gpu-transport; do
    entitlement="$out/entitlements.plist"
    if test "$item" = dvm-gpu-work; then entitlement="$out/worker-entitlements.plist"; fi
    codesign --force --sign - --timestamp=none --entitlements "$entitlement" "$out/$item"
done
: > "$out/hashes.txt"
for item in "$out/DVMForward.bundle/DVMForward" "$out/dvm-gpu-work" "$out/dvm-gpu-transport"; do
    name=$(basename "$item")
    codesign --verify --strict --verbose=2 "$item"
    codesign -d -vvv "$item" 2> "$out/$name.codesign.txt"
    sed -n 's/^CDHash=//p' "$out/$name.codesign.txt" >> "$out/hashes.txt"
    nm -u "$item" > "$out/$name.nm-u"
    otool -l "$item" > "$out/$name.load-commands.txt"
done
python3 "$repo/tools/gpu/verify_guest_imports.py" --output "$out/import-provenance.tsv" "$out/"*.nm-u
test "$(wc -c < "$out/hashes.txt" | tr -d ' ')" = 123
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
