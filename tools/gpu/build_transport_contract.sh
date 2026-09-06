#!/bin/bash
# Separate probe build, preserving the production frontend bundle byte-for-byte.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
before=${1:?usage: build_transport_contract.sh BEFORE_BUILD NEW_OUTPUT_DIRECTORY}
out=${2:?new output directory required}
flags=()
case ${3:-} in
    '') ;;
    --manage-control|--manage-entitled) flags+=(-DDVM_CONTRACT_MANAGE) ;;
    *) echo 'expected --manage-control or --manage-entitled' >&2; exit 2 ;;
esac
test $# -le 3
test ! -e "$out"
mkdir -p "$out"
cp -R "$before/DVMProxy.bundle" "$out/"
cp "$before/entitlements.plist" "$out/"
if [[ ${3:-} == --manage-entitled ]]; then
    python3 - "$out/entitlements.plist" <<'PY'
import plistlib,sys
from pathlib import Path
p=Path(sys.argv[1]);v=plistlib.loads(p.read_bytes())
v['com.apple.private.security.kext-collection-management']=True
p.write_bytes(plistlib.dumps(v))
PY
fi
cp "$repo/tools/gpu/transport_contract.c" "$0" "$out/"
sdk=$(xcrun --sdk macosx --show-sdk-path)
xcrun clang -target arm64-apple-ios7.0 -isysroot "$sdk" -Os -Wall -Wextra -Werror -Wno-incompatible-sysroot \
    "${flags[@]}" "$out/transport_contract.c" -o "$out/dvm-gpu-load"
codesign --force --sign - --timestamp=none --entitlements "$out/entitlements.plist" "$out/dvm-gpu-load"
: > "$out/hashes.txt"
for item in "$out/dvm-gpu-load" "$out/DVMProxy.bundle/DVMProxy"; do
    codesign --verify --strict --verbose=2 "$item"
    codesign -d -vvv "$item" 2> "$out/$(basename "$item").codesign.txt"
    sed -n 's/^CDHash=//p' "$out/$(basename "$item").codesign.txt" >> "$out/hashes.txt"
done
nm -u "$out/dvm-gpu-load" > "$out/dvm-gpu-load.nm-u"
otool -l "$out/dvm-gpu-load" > "$out/dvm-gpu-load.load-commands.txt"
python3 "$repo/tools/gpu/verify_guest_imports.py" --output "$out/import-provenance.tsv" "$out/dvm-gpu-load.nm-u"
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
