#!/usr/bin/env python3
"""Make minimal iOS .tbd linker stubs from recorded 24A5430a exports.

These are link-only declarations. They do not copy or alter guest Mach-O files.
"""
from pathlib import Path
import sys

if len(sys.argv)!=2:
    raise SystemExit('usage: make_guest_link_stubs.py NEW_STUB_DIRECTORY')
OUT = Path(sys.argv[1])
OUT.mkdir(exist_ok=False)
def tbd(rel, install, symbols):
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    quoted = ', '.join('"' + x + '"' for x in sorted(symbols))
    path.write_text(f'''--- !tapi-tbd
tbd-version: 4
targets: [ arm64-ios ]
install-name: {install}
exports:
  - targets: [ arm64-ios ]
    symbols: [ {quoted} ]
...\n''')

foundation = {
 '_NSInternalInconsistencyException','_NSInvalidArgumentException','_NSLocalizedDescriptionKey',
 '_OBJC_CLASS_$_NSData','_OBJC_CLASS_$_NSDictionary','_OBJC_CLASS_$_NSError',
 '_OBJC_CLASS_$_NSException','_OBJC_CLASS_$_NSMutableArray','_OBJC_CLASS_$_NSMutableData',
 '_OBJC_CLASS_$_NSConstantIntegerNumber','_OBJC_CLASS_$_NSDate','_OBJC_CLASS_$_NSJSONSerialization',
 '_OBJC_CLASS_$_NSNumber','_OBJC_CLASS_$_NSString',
}
corefoundation = {
 '_CFRelease','_CFRetain','___CFConstantStringClassReference','___kCFBooleanTrue','___kCFBooleanFalse',
 # Clang emits this exception type reference for the parent harness's @catch.
 # Its 24A5430a Foundation symbol table records CoreFoundation as provider.
 '_OBJC_EHTYPE_$_NSException',
}
iosurface = {
 '_IOSurfaceCreate','_IOSurfaceGetAllocSize','_IOSurfaceGetBaseAddress','_IOSurfaceGetBytesPerElement',
 '_IOSurfaceGetBytesPerRow','_IOSurfaceGetHeight','_IOSurfaceGetPixelFormat','_IOSurfaceGetPlaneCount',
 '_IOSurfaceGetWidth','_IOSurfaceGetID','_IOSurfaceLock','_IOSurfaceUnlock','_kIOSurfaceAllocSize',
 '_kIOSurfaceBytesPerElement','_kIOSurfaceBytesPerRow','_kIOSurfaceHeight',
 '_kIOSurfacePixelFormat','_kIOSurfaceWidth',
}
metal = {'_OBJC_CLASS_$_MTLTextureDescriptor'}
objc = {
 '_OBJC_CLASS_$_NSObject','_OBJC_METACLASS_$_NSObject','__objc_empty_cache','_objc_alloc',
 '_objc_autorelease','_objc_autoreleasePoolPop','_objc_autoreleasePoolPush','_objc_autoreleaseReturnValue',
 '_objc_begin_catch','_objc_end_catch',
 '_objc_destroyWeak','_objc_loadWeakRetained','_objc_msgSend','_objc_msgSendSuper2','_objc_opt_class','_objc_opt_isKindOfClass','_objc_opt_new','_objc_release',
 '_objc_retain','_objc_retainAutorelease','_objc_retainAutoreleaseReturnValue','_objc_retainAutoreleasedReturnValue',
 '_objc_storeStrong','_objc_storeWeak','___objc_personality_v0',
}
# Do not declare compiler-generated objc_msgSend$<selector> aliases here.
# The build script uses -fno-objc-msgsend-selector-stubs, leaving only the
# actual libobjc objc_msgSend-family imports in the final image.
system = {
 '___error','___memcpy_chk','___sprintf_chk','___snprintf_chk','___stack_chk_fail','___stack_chk_guard','___stderrp',
 '_CC_SHA256','_close','_dlerror','_dlopen','_dlsym','_dup2','_execl','_exit','__exit','_fork','_fprintf','_getenv',
 '_memcmp','_memcpy','_memset','_pipe','_read','_setenv','_strcmp','_snprintf','_waitpid','_write','__Unwind_Resume','_dispatch_data_create',
 '_dispatch_data_create_map',
}
tbd('System/Library/Frameworks/Foundation.framework/Foundation.tbd','/System/Library/Frameworks/Foundation.framework/Foundation',foundation)
tbd('System/Library/Frameworks/CoreFoundation.framework/CoreFoundation.tbd','/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation',corefoundation)
tbd('System/Library/Frameworks/IOSurface.framework/IOSurface.tbd','/System/Library/Frameworks/IOSurface.framework/IOSurface',iosurface)
tbd('System/Library/Frameworks/Metal.framework/Metal.tbd','/System/Library/Frameworks/Metal.framework/Metal',metal)
tbd('System/Library/Frameworks/QuartzCore.framework/QuartzCore.tbd','/System/Library/Frameworks/QuartzCore.framework/QuartzCore', {'_OBJC_CLASS_$_CARenderer','_OBJC_CLASS_$_CALayer','_OBJC_CLASS_$_CATransaction','_kCARendererMetalCommandQueue'})
tbd('System/Library/Frameworks/CoreGraphics.framework/CoreGraphics.tbd','/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics', {'_CGColorCreateGenericRGB','_CGColorRelease'})
tbd('usr/lib/libobjc.A.tbd','/usr/lib/libobjc.A.dylib',objc)
tbd('usr/lib/libobjc.tbd','/usr/lib/libobjc.A.dylib',objc)
tbd('usr/lib/libSystem.tbd','/usr/lib/libSystem.B.dylib',system)
