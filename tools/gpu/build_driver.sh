#!/bin/bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${1:?new output directory}
mode=${2:-nvme}
requested_mode=$mode
shared_flags=(-UDVM_SHARED_SURFACE -UDVM_MANAGED_ADDRESS_CONTRACT)
if [[ "$mode" == --mmio-present-managed-contract ]]; then mode=--mmio-present-contract; fi
if [[ "$mode" == --mmio-present-pool-contract ]]; then mode=--mmio-present-contract; shared_flags=(-DDVM_MANAGED_ADDRESS_CONTRACT -DDVM_SERVICE_POOL_CONTRACT); fi
if [[ "$mode" == --mmio-present-pool ]]; then mode=--mmio-present; shared_flags=(-DDVM_SHARED_SURFACE -DDVM_SERVICE_POOL_CONTRACT); fi
if [[ "$mode" == --mmio-present-shared-probe ]]; then mode=--mmio-present; shared_flags=(-DDVM_SHARED_SURFACE); fi
[[ "$mode" == nvme || "$mode" == --mmio || "$mode" == --mmio-binary || "$mode" == --mmio-blur || "$mode" == --mmio-present-contract || "$mode" == --mmio-present ]] || exit 2
extra_flags=(-UDVM_DRIVER_MMIO)
consumer_flags=(-UDVM_CA_PROBE)
consumer_frames=${DVM_CA_FRAMES:-1}
[[ "$consumer_frames" =~ ^[0-9]+$ ]] && (( (consumer_frames==1 || consumer_frames>=3) && consumer_frames<=4096 )) || exit 2
consumer_flags+=(-DDVM_CA_FRAMES="$consumer_frames")
if [[ ${DVM_CA_PROBE:-0} == 1 ]]; then consumer_flags+=(-DDVM_CA_PROBE); fi
if [[ ${DVM_TEST_RUNNER:-0} == 1 ]]; then
    [[ ${DVM_CA_PROBE:-0} == 1 && "$requested_mode" == --mmio-present-pool ]] || exit 2
    consumer_flags+=(-DDVM_TEST_RUNNER)
fi
if [[ "$mode" == --mmio || "$mode" == --mmio-binary ]]; then extra_flags=(-DDVM_DRIVER_MMIO); fi
if [[ "$mode" == --mmio-binary ]]; then extra_flags+=(-DDVM_DRIVER_BINARY); fi
if [[ "$mode" == --mmio-blur ]]; then extra_flags=(-DDVM_DRIVER_MMIO -DDVM_DRIVER_BINARY -DDVM_DRIVER_BLUR); fi
if [[ "$mode" == --mmio-present-contract ]]; then extra_flags=(-DDVM_DRIVER_MMIO -DDVM_DRIVER_BINARY -DDVM_PRESENT_CONTRACT); fi
if [[ "$mode" == --mmio-present ]]; then extra_flags=(-DDVM_DRIVER_MMIO -DDVM_DRIVER_BINARY -DDVM_DRIVER_PRESENT); fi
if [[ "$requested_mode" == --mmio-present-managed-contract ]]; then shared_flags=(-DDVM_MANAGED_ADDRESS_CONTRACT); fi
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
    optimize=(-O1)
    if [[ ( "$mode" == --mmio-blur || "$mode" == --mmio-present ) && "$name" == driver_probe ]]; then optimize=(-O3); fi
    xcrun clang -target arm64-apple-ios27.0 -isysroot "$sdk" -Wno-incompatible-sysroot "${flags[@]}" "${partial[@]}" "${extra_flags[@]}" "${shared_flags[@]}" "${consumer_flags[@]}" "${optimize[@]}" -c "$repo/tools/gpu/$name.m" -o "$out/$name.o"
done
python3 - "$out" "$mode" <<'PY'
from pathlib import Path
import plistlib,sys,subprocess
p=Path(sys.argv[1])
extra={
 'System/Library/Frameworks/Foundation.framework/Foundation.tbd':['OBJC_CLASS_$_NSBundle','OBJC_CLASS_$_NSMutableDictionary','OBJC_CLASS_$_NSArray','OBJC_CLASS_$_NSConstantDictionary'],
 'usr/lib/libSystem.tbd':['__memset_chk','bzero','clock_gettime','usleep','getpid','setvbuf','posix_memalign','arc4random','free','mach_task_self_',
 'malloc','calloc','dispatch_queue_create','dispatch_sync','dispatch_async','dispatch_get_global_queue','dispatch_group_create','dispatch_group_enter','dispatch_group_leave','dispatch_group_wait',
 'vfprintf','vsnprintf','sigaction','_exit','write','sched_yield','dispatch_queue_attr_make_with_qos_class','pthread_set_qos_class_self_np','setpriority',
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
if sys.argv[2]!='nvme':
 (p/'entitlements.plist').write_bytes(plistlib.dumps({'platform-application':True,'org.darwin-vm.transport':True,'com.apple.security.exception.iokit-user-client-class':['IOKitDiagnosticsClient','IOSurfaceRootUserClient']+(['IOMobileFramebufferUserClient'] if sys.argv[2] in ('--mmio-present-contract','--mmio-present') else [])}))
(p/'DVMProxy.bundle/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='org.darwin-vm.metal-driver',CFBundleName='DVMProxy',CFBundleExecutable='DVMProxy',CFBundlePackageType='BNDL',CFBundleVersion='1')))
PY
if [[ ${DVM_RUNNER_NO_SANDBOX:-0} == 1 ]]; then
    [[ ${DVM_TEST_RUNNER:-0} == 1 ]] || exit 2
    python3 - "$out/entitlements.plist" <<'PY'
from pathlib import Path
import plistlib,sys
p=Path(sys.argv[1]);data=plistlib.loads(p.read_bytes())
data['com.apple.private.security.no-sandbox']=True
p.write_bytes(plistlib.dumps(data))
PY
fi
link=(-target arm64-apple-ios27.0 -isysroot "$sdk" -Wno-incompatible-sysroot -F "$out/stubs/System/Library/Frameworks" -L "$out/stubs/usr/lib")
frameworks=(-framework Foundation -framework CoreFoundation -framework IOSurface -framework Metal -lobjc)
if [[ ${DVM_CA_PROBE:-0} == 1 ]]; then frameworks+=(-framework QuartzCore -framework CoreGraphics); fi
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
xcrun clang "${flags[@]}" "${partial[@]}" "${consumer_flags[@]}" "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_workload.m" "$repo/tools/gpu/driver_client.m" -framework Metal -framework Foundation -framework IOSurface -framework QuartzCore -framework CoreGraphics -o "$out/driver_client"
xcrun clang "${flags[@]}" "${partial[@]}" "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_contract_test.m" -framework Metal -framework Foundation -framework IOSurface -o "$out/driver_contract_test"
xcrun clang "${flags[@]}" "${partial[@]}" "$repo/tools/gpu/driver_guest.m" "$repo/tools/gpu/driver_capability_test.m" -framework Metal -framework Foundation -framework IOSurface -o "$out/driver_capability_test"
cp "$repo/tools/gpu/present_"* "$repo/tools/gpu/blur_"* "$repo/tools/gpu/driver_"* "$repo/tools/gpu/build_driver.sh" "$out/"

cp "$repo/qemu-sptm/include/xnu/darwin_gpu_transport.h" "$out/"
cp "$repo/tools/gpu/managed_host.h" "$out/"
cp "$repo/tools/gpu/managed_guest.h" "$out/"
cp "$repo/tools/gpu/consumer_"* "$out/"
printf "%s\n" "${DVM_CA_PROBE:-0}" > "$out/consumer-probe.txt"
printf "%s\n" "$consumer_frames" > "$out/consumer-frames.txt"
printf "%s\n" "${DVM_TEST_RUNNER:-0}" > "$out/test-runner.txt"
printf "%s\n" "$requested_mode" > "$out/transport-mode.txt"
