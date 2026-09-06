#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?new output directory}
flags=(-DDVM_SURFACE_DEMO=1)
if [[ ${2:-} == --recreate ]]; then flags+=(-DDVM_SURFACE_RECREATE); elif [[ -n ${2:-} ]]; then exit 2; fi
bash "$repo/tools/gpu/build_guest_load.sh" "$out"
python3 "$repo/tools/gpu/make_guest_link_stubs.py" "$out/stubs"
python3 - "$out" <<'PY'
from pathlib import Path
import plistlib,sys
p=Path(sys.argv[1]); e=plistlib.loads((p/'entitlements.plist').read_bytes())
e['com.apple.AppleNVMeNamespaceDevice.allow']=True
e['com.apple.security.exception.iokit-user-client-class']=['AppleNVMeNamespaceUC','IOSurfaceRootUserClient']
(p/'entitlements.plist').write_bytes(plistlib.dumps(e))
# New libSystem imports are independently checked against the exact guest cache below.
f=p/'stubs/usr/lib/libSystem.tbd'
s=f.read_text().replace('symbols: [ ', 'symbols: [ "___memset_chk", "_bzero", "_fwrite", "_clock_gettime", "_usleep", "_getpid", "_setvbuf", "_posix_memalign", "_arc4random", "_free", "_mach_task_self_", ')
f.write_text(s)
PY
sdk=$(xcrun --sdk macosx --show-sdk-path)
xcrun clang -target arm64-apple-ios27.0 -isysroot "$sdk" -fobjc-arc -fno-objc-msgsend-selector-stubs -O0 -Wall -Wextra -Werror -Wno-incompatible-sysroot "${flags[@]}" -c "$repo/tools/gpu/guest_surface_demo.m" -o "$out/surface.o"
xcrun clang -target arm64-apple-ios27.0 -isysroot "$sdk" -Wno-incompatible-sysroot -F "$out/stubs/System/Library/Frameworks" -L "$out/stubs/usr/lib" "$out/surface.o" -framework Foundation -framework CoreFoundation -framework IOSurface -lobjc -o "$out/dvm-gpu-load"
codesign --force --sign - --timestamp=none --entitlements "$out/entitlements.plist" "$out/dvm-gpu-load"
codesign --verify --strict "$out/dvm-gpu-load"
codesign -d -vvv "$out/dvm-gpu-load" 2> "$out/dvm-gpu-load.codesign.txt"
nm -u "$out/dvm-gpu-load" > "$out/dvm-gpu-load.nm-u"
python3 "$repo/tools/gpu/verify_guest_imports.py" --output "$out/import-provenance.tsv" "$out/dvm-gpu-load.nm-u"
sed -n 's/^CDHash=//p' "$out/dvm-gpu-load.codesign.txt" "$out/DVMProxy.codesign.txt" > "$out/hashes.txt"
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
xcrun clang -fobjc-arc -O2 -Wall -Wextra -Werror "$repo/tools/gpu/metal_proxy_server.m" -framework Foundation -framework Metal -framework IOSurface -o "$out/metal_proxy_server"
cp "$repo/tools/gpu/guest_surface_demo.m" "$repo/tools/gpu/metal_proxy_server.m" "$0" "$out/"
