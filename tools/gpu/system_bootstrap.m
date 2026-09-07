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
            io_registry_entry_t service=IORegistryEntryFromPath(0,"IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/dvm-transport@E0000000");
            kern_return_t kr=service?IORegistryEntryGetRegistryEntryID(service,&systemRegistryID):kIOReturnNotFound;
            if(service)IOObjectRelease(service);
            if(kr||!systemRegistryID)fail("registry-identity");
            DVMMetalRPC rpc=^NSDictionary *(NSDictionary *r,NSError **e){return [ns call:r error:e];};
            systemDevice=DVMCreateSharedMetalDevice(rpc,^id<DVMMetalOwnedMapping>(NSError **e){return DVMMapDriverPool(ns,e);});
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
