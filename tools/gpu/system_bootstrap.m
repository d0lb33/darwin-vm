// Opt-in backboardd dependency in a disposable System image. Register through
// the exact guest's MTLAddDevice; do not interpose the public Metal factory.
#include "driver_guest.m"
#include <IOKit/IOKitLib.h>
#include <objc/runtime.h>
#include <time.h>
#include <unistd.h>
static double now(void) {struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static uint32_t crc(const void *data,size_t n) {
    uint32_t c=~0u;const uint8_t *p=data;
    while(n--){c^=*p++;for(unsigned j=0;j<8;j++)c=(c>>1)^((0u-(c&1))&0xedb88320u);}return ~c;
}
static void fail(const char *s) {[NSException raise:NSInternalInconsistencyException format:@"DVM boot transport: %s",s];}
#define DVM_BOOT_PLUGIN 1
#define DVM_DRIVER_BINARY 1
#define DVM_DRIVER_PRESENT 1
#define DVM_TEST_RUNNER 1
#include "driver_mmio_transport.inc"
#include "driver_owned_mapping.inc"
#ifdef DVM_SURFACE_IMPORT
#include "driver_imported_mapping.inc"
#endif
#ifdef DVM_SURFACE_PIN_PROBE
static io_connect_t surfacePinClient;
static void DVMProbeSurfacePin(IOSurfaceRef surface) {
    static BOOL tested;
    if(!surface||tested)return;
    tested=YES;
    size_t length=IOSurfaceGetAllocSize(surface);
    uintptr_t base=(uintptr_t)IOSurfaceGetBaseAddress(surface);
    DVMReport(stderr,"GPU_LOAD_SURFACE_META id=%u width=%zu height=%zu row=%zu element=%zu format=%x planes=%zu bytes=%zu nonnull=%d base_offset=%zu tail=%zu\n",
        IOSurfaceGetID(surface),IOSurfaceGetWidth(surface),IOSurfaceGetHeight(surface),IOSurfaceGetBytesPerRow(surface),
        IOSurfaceGetBytesPerElement(surface),IOSurfaceGetPixelFormat(surface),IOSurfaceGetPlaneCount(surface),length,
        base!=0,(size_t)(base&16383),length&16383);
    // The surface remains in its caller's ownership throughout this synchronous
    // probe. No GPU or DCP work is submitted and no host page alias is created.
    for(unsigned trial=0;trial<5;trial++) {
        uint64_t input[3]={1,trial<2?0:base,length},output[4]={0};uint32_t count=4;
        kern_return_t kr=IOConnectCallScalarMethod(surfacePinClient,0x44565300,input,trial==0?2:3,output,&count);
        DVMReport(stderr,"GPU_LOAD_SURFACE_PIN trial=%u kr=%x count=%u stage=%llu pages=%llu bytes=%llu complete=%llu\n",
            trial,kr,count,output[0],output[1],output[2],output[3]);
        BOOL expected=trial<2?(kr==kIOReturnBadArgument):(kr==0&&count==4&&output[0]==5&&output[1]==((base&16383)+length+16383)/16384&&output[2]==length&&output[3]==0);
        if(!expected){DVMReport(stderr,"GPU_LOAD_SURFACE_PIN_STOP trial=%u\n",trial);break;}
    }
}
#endif
#ifdef DVM_BOOT_RUNTIME_PROBE
#include "system_revision_probe.inc"
#endif

static uint64_t systemRegistryID;
static id<MTLDevice> systemDevice;
static NSUncaughtExceptionHandler *previousExceptionHandler;
static void DVMSystemException(NSException *exception) {
    NSString *reason=[[exception.reason stringByReplacingOccurrencesOfString:@"\n" withString:@" "] stringByReplacingOccurrencesOfString:@"\r" withString:@" "];
    DVMReport(stderr,"GPU_LOAD_SYSTEM_UNCAUGHT name=%.60s reason=%.330s\n",exception.name.UTF8String,reason.UTF8String);
    if(previousExceptionHandler)previousExceptionHandler(exception);
}
@interface DVMDevice (BootRegistration)
- (void)initLimits;
- (void)initFeatureQueries;
- (void)initWorkarounds;
@end
@implementation DVMDevice (BootRegistration)
// DVM owns its queries rather than _MTLDevice's internal bit tables. Every
// exported capability still comes from the validated guest/host profile.
- (void)initLimits {(void)[self contractCapabilities];}
- (void)initFeatureQueries {(void)[self contractCapabilities];}
- (void)initWorkarounds {}
- (uint64_t)registryID {return systemRegistryID;}
- (void)doesNotRecognizeSelector:(SEL)selector {
    DVMReport(stderr,"GPU_LOAD_SYSTEM_MISSING class=DVMDevice selector=%s\n",sel_getName(selector));
    [super doesNotRecognizeSelector:selector];
}
@end

__attribute__((constructor)) static void DVMSystemBoot(void) {
    @autoreleasepool {
        // The bootstrap is installed only as a dependency of this executable.
        // A mismatched process must neither claim RAM nor advertise a device.
        if(strcmp(getprogname(),"backboardd"))return;
        fprintf(stderr,"GPU_LOAD_SYSTEM_BEGIN pid=%d\n",getpid());
        @try {
            NSUncaughtExceptionHandler *(*getHandler)(void)=dlsym(RTLD_DEFAULT,"NSGetUncaughtExceptionHandler");
            void (*setHandler)(NSUncaughtExceptionHandler *)=dlsym(RTLD_DEFAULT,"NSSetUncaughtExceptionHandler");
            if(!getHandler||!setHandler)fail("exception-audit-symbols");
            previousExceptionHandler=getHandler();setHandler(DVMSystemException);
            void *metal=dlopen("/System/Library/Frameworks/Metal.framework/Metal",RTLD_NOW);
            id<MTLDevice> (*create)(void)=dlsym(metal,"MTLCreateSystemDefaultDevice");
            void (*add)(id)=dlsym(metal,"MTLAddDevice");
            Protocol *spi=objc_getProtocol("MTLDeviceSPI");
            if(!create||!add||!spi)fail("registration-symbols");
            id<MTLDevice> before=create();
            if(before){fprintf(stderr,"GPU_LOAD_SYSTEM_SKIP existing_device=%s\n",before.name.UTF8String);return;}
            void *iokit=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_GLOBAL);
            Namespace *ns=openMMIO(iokit);
#ifdef DVM_SURFACE_PIN_PROBE
            surfacePinClient=ns.client;
#endif
#ifdef DVM_BOOT_RUNTIME_PROBE
            DVMSystemRevisionProbe(ns);
#endif
            io_registry_entry_t service=IORegistryEntryFromPath(0,"IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/dvm-transport@E0000000");
            kern_return_t kr=service?IORegistryEntryGetRegistryEntryID(service,&systemRegistryID):kIOReturnNotFound;
            if(service)IOObjectRelease(service);
            if(kr||!systemRegistryID)fail("registry-identity");
            DVMMetalRPC rpc=^NSDictionary *(NSDictionary *r,NSError **e){return [ns call:r error:e];};
            systemDevice=DVMCreateSharedMetalDevice(rpc,^id<DVMMetalOwnedMapping>(NSError **e){return DVMMapDriverPool(ns,e);});
#ifdef DVM_SURFACE_IMPORT
            DVMEnableSurfaceImports(systemDevice,DVMImportedSurfaceProvider(ns));
#endif
            (void)[(DVMDevice *)systemDevice contractCapabilities];
            // Registration asserts protocol identity, not complete selector
            // coverage. Unsupported selectors remain explicit failures.
            if(!class_conformsToProtocol(DVMDevice.class,spi)&&!class_addProtocol(DVMDevice.class,spi))fail("registration-protocol");
            DVMReport(stderr,"GPU_LOAD_SYSTEM_ADD pid=%d registry=%llu\n",getpid(),systemRegistryID);
            add(systemDevice);
            id<MTLDevice> after=create();
            if(after!=systemDevice)fail("default-device-registration");
            DVMReport(stderr,"GPU_LOAD_SYSTEM_REGISTERED pid=%d registry=%llu name=%s\n",getpid(),systemRegistryID,after.name.UTF8String);
        } @catch(NSException *e) {
            // Before registration, ordinary nil-device software selection stays
            // available. Post-registration failures need the recorded recovery
            // image; do not claim transparent mid-frame failover.
            fprintf(stderr,"GPU_LOAD_SYSTEM_EXCEPTION name=%s reason=%s\n",e.name.UTF8String,e.reason.UTF8String);
        }
    }
}
