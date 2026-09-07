// Explicit provider for factory ABI v1; only compiled into the isolated helper.
// A mapped object owns Namespace, so neither IOServiceClose nor unmap can
// overtake a driver's retained mapping. Physical pool lifetime remains the VM.
#include "surface_handoff.h"
@interface DVMServiceMapping : NSObject <DVMMetalOwnedMapping>
@property(nonatomic,strong) Namespace *owner;
@property(nonatomic) void *bytes;
@property(nonatomic) NSUInteger length;
@property(nonatomic,strong) id surfaceObject;
@end
@implementation DVMServiceMapping
- (NSUInteger)mappingVersion {return 1;}
- (IOSurfaceRef)iosurface {return (__bridge IOSurfaceRef)_surfaceObject;}
- (void)dealloc {
    if(_bytes&&_owner&&_owner.unmap(_owner.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),(uintptr_t)_bytes))
        fprintf(stderr,"GPU_LOAD_ERROR owned-mapping-unmap\n");
}
@end
static id<DVMMetalOwnedMapping> DVMMapDriverPool(Namespace *ns,NSError **error) {
    const char *handoff=getenv("DVM_RUNNER_SURFACE");
    if(handoff&&!strcmp(handoff,"1")){
        DVMSurfacePortAPI api;kern_return_t kr=KERN_NOT_SUPPORTED;
        IOSurfaceRef surface=DVMSurfacePortLoad(&api)?DVMSurfacePortsReceive(&api,&kr):NULL;
        fprintf(stderr,"GPU_LOAD_SURFACE_RECEIVE result=%x surface=%u\n",kr,surface?IOSurfaceGetID(surface):0);
        if(!surface||kr||!IOSurfaceGetBaseAddress(surface)||IOSurfaceGetAllocSize(surface)!=DVM_PRESENT_BUFFER_BYTES){
            if(surface)CFRelease(surface);if(error)*error=[NSError errorWithDomain:@"DVMSurfaceHandoff" code:kr?:KERN_INVALID_ARGUMENT userInfo:@{NSLocalizedDescriptionKey:@"inherited IOSurface port lookup/extent"}];return nil;
        }
        DVMServiceMapping *mapping=[DVMServiceMapping new];mapping.surfaceObject=CFBridgingRelease(surface);
        mapping.bytes=IOSurfaceGetBaseAddress(mapping.iosurface);mapping.length=IOSurfaceGetAllocSize(mapping.iosurface);
        if(IOSurfaceLock(mapping.iosurface,0,NULL))fail("surface-receive-lock");
        uint64_t *witness=(uint64_t *)((uint8_t *)mapping.bytes+DVM_PRESENT_BYTES);
        uint64_t job=witness[0];witness[1]=job^0x44564d48414e4446ULL;
        if(!job||IOSurfaceUnlock(mapping.iosurface,0,NULL))fail("surface-receive-witness");
        fprintf(stderr,"GPU_LOAD_SURFACE_RECEIVED job=%llu surface=%u bytes=%lu\n",job,IOSurfaceGetID(mapping.iosurface),(unsigned long)mapping.length);
        return mapping;
    }
    __typeof__(&IOConnectMapMemory64) map=dlsym(RTLD_DEFAULT,"IOConnectMapMemory64");
    mach_vm_address_t address=0;mach_vm_size_t length=0;
    kern_return_t kr=map?map(ns.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),&address,&length,kIOMapAnywhere|0x700):kIOReturnUnsupported;
    if(kr||!address||address%16384||length!=DVM_PRESENT_BUFFER_BYTES){
        if(!kr&&address)ns.unmap(ns.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),address);
        if(error)*error=[NSError errorWithDomain:@"DVMOwnedMapping" code:kr?:kIOReturnBadArgument userInfo:@{NSLocalizedDescriptionKey:@"owned service pool mapping contract"}];return nil;
    }
    DVMServiceMapping *mapping=[DVMServiceMapping new];mapping.owner=ns;mapping.bytes=(void *)address;mapping.length=length;
    fprintf(stderr,"GPU_LOAD_OWNED_MAPPING version=1 address=0x%llx bytes=%llu\n",address,length);
    return mapping;
}
static inline IOSurfaceRef DVMCreatePoolSurface(id<DVMMetalOwnedMapping> mapping) {
    CFStringRef *address=dlsym(RTLD_DEFAULT,"kIOSurfaceClientAddress"),*cache=dlsym(RTLD_DEFAULT,"kIOSurfaceCacheMode");
    if(!address||!*address||!cache||!*cache)fail("supervisor-surface-symbols");
    IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(__bridge id)*address:@((uintptr_t)mapping.bytes),(__bridge id)*cache:@0x700,
        (id)kIOSurfaceWidth:@DVM_PRESENT_WIDTH,(id)kIOSurfaceHeight:@DVM_PRESENT_HEIGHT,(id)kIOSurfaceBytesPerElement:@4,
        (id)kIOSurfaceBytesPerRow:@DVM_PRESENT_ROW,(id)kIOSurfaceAllocSize:@DVM_PRESENT_BUFFER_BYTES,(id)kIOSurfacePixelFormat:@(0x42475241)});
    if(!surface||IOSurfaceGetBaseAddress(surface)!=mapping.bytes)fail("supervisor-surface-alias");
    return surface;
}
