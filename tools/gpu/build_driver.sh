#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?new output directory}
test ! -e "$out"
mkdir -p "$out/DVMProxy.bundle"
python3 "$repo/tools/gpu/make_guest_link_stubs.py" "$out/stubs"
sdk=$(xcrun --sdk macosx --show-sdk-path)
# Rejected selector contracts throw Objective-C exceptions. ARC exception
# cleanup is required so those paths release their retained resources.
flags=(-fobjc-arc -fobjc-arc-exceptions -O1 -Wall -Wextra -Werror -fno-objc-msgsend-selector-stubs -Wno-deprecated-declarations)
# Partial public protocol implementation is deliberate, documented, and never
# globally advertised. Other compiler diagnostics remain errors.
partial=(-Wno-protocol -Wno-objc-protocol-property-synthesis)
for name in driver_guest driver_probe driver_workload; do
    xcrun clang -target arm64-apple-ios27.0 -isysroot "$sdk" -Wno-incompatible-sysroot "${flags[@]}" "${partial[@]}" -c "$repo/tools/gpu/$name.m" -o "$out/$name.o"
done
python3 - "$out" <<'PY'
from pathlib import Path
import plistlib,sys,subprocess
p=Path(sys.argv[1])
extra={
 'System/Library/Frameworks/Foundation.framework/Foundation.tbd':['OBJC_CLASS_$_NSBundle','OBJC_CLASS_$_NSMutableDictionary','OBJC_CLASS_$_NSArray','OBJC_CLASS_$_NSConstantDictionary'],
 'usr/lib/libSystem.tbd':['__memset_chk','bzero','clock_gettime','usleep','getpid','setvbuf','posix_memalign','arc4random','free','mach_task_self_',
 'dispatch_queue_create','dispatch_sync','dispatch_async','dispatch_get_global_queue','dispatch_group_create','dispatch_group_enter','dispatch_group_leave','dispatch_group_wait',
 'dispatch_semaphore_create','dispatch_semaphore_signal','dispatch_semaphore_wait','dispatch_time','_NSConcreteStackBlock','_Block_object_assign','_Block_object_dispose','fwrite'],
 'usr/lib/libobjc.tbd':['objc_retainBlock','objc_sync_enter','objc_sync_exit'],
}
# Clang's optimized ARC register helpers are ordinary libobjc exports. Declare
# only imports actually emitted; final image imports are checked against iOS.
symbols=set()
for o in p.glob('*.o'):
 symbols.update(subprocess.check_output(['nm','-u',str(o)],text=True).split())
extra['usr/lib/libobjc.tbd']+=sorted(s[1:] for s in symbols if s.startswith('_objc_'))
for rel,names in extra.items():
 f=p/'stubs'/rel;s=f.read_text();s=s.replace('symbols: [ ','symbols: [ '+''.join('"_'+n+'", ' for n in names));f.write_text(s)
(p/'entitlements.plist').write_bytes(plistlib.dumps({'platform-application':True,'com.apple.AppleNVMeNamespaceDevice.allow':True,'com.apple.security.exception.iokit-user-client-class':['AppleNVMeNamespaceUC','IOSurfaceRootUserClient']}))
(p/'DVMProxy.bundle/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='org.darwin-vm.metal-driver',CFBundleName='DVMProxy',CFBundleExecutable='DVMProxy',CFBundlePackageType='BNDL',CFBundleVersion='1')))
PY
link=(-target arm64-apple-ios27.0 -isysroot "$sdk" -Wno-incompatible-sysroot -F "$out/stubs/System/Library/Frameworks" -L "$out/stubs/usr/lib")
frameworks=(-framework Foundation -framework CoreFoundation -framework IOSurface -framework Metal -lobjc)
xcrun clang "${link[@]}" -dynamiclib -Wl,-install_name,/usr/local/libexec/DVMProxy.bundle/DVMProxy "$out/driver_guest.o" "${frameworks[@]}" -o "$out/DVMProxy.bundle/DVMProxy"
xcrun clang "${link[@]}" "$out/driver_probe.o" "$out/driver_workload.o" "${frameworks[@]}" -o "$out/dvm-gpu-load"
codesign --force --sign - --timestamp=none "$out/DVMProxy.bundle"
codesign --force --sign - --timestamp=none --entitlements "$out/entitlements.plist" "$out/dvm-gpu-load"
for item in "$out/DVMProxy.bundle/DVMProxy" "$out/dvm-gpu-load"; do
    name=$(basename "$item")
    codesign --verify --strict "$item"
    codesign -d -vvv "$item" 2> "$out/$name.codesign.txt"
    nm -u "$item" > "$out/$name.nm-u"
done
python3 "$repo/tools/gpu/verify_guest_imports.py" --output "$out/import-provenance.tsv" "$out/"*.nm-u
sed -n 's/^CDHash=//p' "$out/DVMProxy.codesign.txt" "$out/dvm-gpu-load.codesign.txt" > "$out/hashes.txt"
python3 "$repo/build_tc.py" "$out/hashes.txt" "$out/helper.tc"
xcrun clang "${flags[@]}" "$repo/tools/gpu/driver_host.m" -framework Metal -framework Foundation -o "$out/driver_host"
xcrun clang "${flags[@]}" "${partial[@]}" "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_workload.m" "$repo/tools/gpu/driver_client.m" -framework Metal -framework Foundation -framework IOSurface -o "$out/driver_client"
xcrun clang "${flags[@]}" "${partial[@]}" "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_contract_test.m" -framework Metal -framework Foundation -framework IOSurface -o "$out/driver_contract_test"
cp "$repo/tools/gpu/driver_"* "$repo/tools/gpu/build_driver.sh" "$out/"
