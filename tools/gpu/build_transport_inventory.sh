#!/bin/bash
# Reuse the guarded load-probe packaging; its DVMProxy bundle is not loaded by
# this inventory helper. No launch/input service is replaced in load mode.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?new output directory required}
flags=()
case ${2:-} in
    '') ;;
    --probe) flags+=(-DDVM_AUX_PROBE) ;;
    --uc) flags+=(-DDVM_AUX_UC) ;;
    --uc-probe) flags+=(-DDVM_AUX_UC -DDVM_AUX_UC_TRANSFER) ;;
    *) echo 'expected --probe, --uc, or --uc-probe' >&2; exit 2 ;;
esac
if [[ ${3:-} != '' && ( ( ${2:-} != --uc && ${2:-} != --uc-probe ) || ${3:-} != --namespace-entitlement ) ]]; then exit 2; fi
if [[ ${4:-} != '' && ( ${3:-} != --namespace-entitlement || ${4:-} != --class-exception ) ]]; then exit 2; fi
bash "$repo/tools/gpu/build_guest_load.sh" "$out"
if [[ ${3:-} == --namespace-entitlement ]]; then
    python3 - "$out/entitlements.plist" <<'PY'
import plistlib,sys
from pathlib import Path
p=Path(sys.argv[1]);v=plistlib.loads(p.read_bytes())
v['com.apple.AppleNVMeNamespaceDevice.allow']=True
p.write_bytes(plistlib.dumps(v))
PY
fi
if [[ ${4:-} == --class-exception ]]; then
    python3 - "$out/entitlements.plist" <<'PY'
import plistlib,sys
from pathlib import Path
p=Path(sys.argv[1]);v=plistlib.loads(p.read_bytes())
v['com.apple.security.exception.iokit-user-client-class']=['AppleNVMeNamespaceUC']
p.write_bytes(plistlib.dumps(v))
PY
fi
cp "$repo/tools/gpu/transport_inventory.c" "$out/"
cp "$repo/tools/gpu/aux_transport_probe.h" "$out/"
cp "$0" "$out/build_transport_inventory.sh"
sdk=$(xcrun --sdk macosx --show-sdk-path)
xcrun clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror -Wno-incompatible-sysroot "${flags[@]}" "$repo/tools/gpu/transport_inventory.c" -o "$out/dvm-gpu-load"
codesign --force --sign - --timestamp=none --entitlements "$out/entitlements.plist" "$out/dvm-gpu-load"
codesign --verify --strict --verbose=2 "$out/dvm-gpu-load"
codesign -d -vvv "$out/dvm-gpu-load" 2> "$out/dvm-gpu-load.codesign.txt"
otool -l "$out/dvm-gpu-load" > "$out/dvm-gpu-load.load-commands.txt"
nm -u "$out/dvm-gpu-load" > "$out/dvm-gpu-load.nm-u"
python3 "$repo/tools/gpu/verify_guest_imports.py" --output "$out/import-provenance.tsv" "$out/dvm-gpu-load.nm-u"
sed -n 's/^CDHash=//p' "$out/dvm-gpu-load.codesign.txt" "$out/DVMProxy.codesign.txt" > "$out/hashes.txt"
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
