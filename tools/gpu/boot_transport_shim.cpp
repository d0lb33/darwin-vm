// Experimental 24A5430a boot shim. Linked into guarded executable padding of
// an isolated BootKC, never loaded as a kext or used with an unreviewed ABI.
// Stock IOKit owns allocation, task attachment, mapping and descriptor lifetime.
#include <IOKit/IOUserClient.h>
#include <IOKit/IOMemoryDescriptor.h>
#ifdef DVM_MANAGED_POOL
#include <IOKit/IOBufferMemoryDescriptor.h>
extern "C" OSObject *dvm_alloc_class(const char *);
struct PoolLock {
    IOService *service;
    bool locked;
    explicit PoolLock(IOService *s):service(s),locked(s->lockForArbitration()) {}
    ~PoolLock() { if(locked)service->unlockForArbitration(); }
};
#endif

static constexpr UInt32 OPEN_TYPE = 0x44564d54;
static constexpr UInt32 MEMORY_TYPE = 0x44560000;

extern "C" IOReturn dvm_original_uc(IOService *, task_t, void *, UInt32,
                                    OSDictionary *, IOUserClient **);

static bool owned(IOService *service) {
    if (!service) return false;
    const char *name=service->getName();
    const char *expected="dvm-transport";
    if (!name) return false;
    for (unsigned i=0;i<sizeof("dvm-transport");i++)
        if (name[i]!=expected[i]) return false;
    return true;
}

extern "C" IOReturn dvm_uc(IOService *service, task_t task, void *security,
                            UInt32 type, OSDictionary *properties,
                            IOUserClient **result) {
    if (!owned(service))
        return dvm_original_uc(service,task,security,type,properties,result);
    *result=nullptr;
    if (type!=OPEN_TYPE) return kIOReturnBadArgument;
    // IOUserClient itself has an abstract metaclass and cannot be allocated
    // through the stock factory. This concrete stock subclass supplies task
    // lifecycle/close; no diagnostic external method is used by the transport.
    if (!service->setProperty("IOUserClientClass","IOKitDiagnosticsClient"))
        return kIOReturnNoMemory;
    IOReturn kr=dvm_original_uc(service,task,security,type,properties,result);
    if (kr!=kIOReturnSuccess || !*result) return kr;
    // io_service_open_extended checks this property after newUserClient.
    // Require the dedicated signed-helper entitlement, not just a known name.
    if (!(*result)->setProperty("IOUserClientEntitlements","org.darwin-vm.transport") ||
        !(*result)->setProperty("IOUserClientDefaultLocking",true) ||
        !(*result)->setProperty("IOUserClientDefaultLockingSetProperties",true) ||
        !(*result)->setProperty("IOUserClientDefaultLockingSingleThreadExternalMethod",true)) {
        (*result)->terminate();
        (*result)->release();
        *result=nullptr;
        return kIOReturnNoMemory;
    }
    return kIOReturnSuccess;
}

extern "C" IOReturn dvm_map(IOUserClient *client, UInt32 type,
                             IOOptionBits *options, IOMemoryDescriptor **result) {
    // Every other invocation preserves the original unsupported result.
    if (type!=MEMORY_TYPE && type!=MEMORY_TYPE+1
#ifdef DVM_MANAGED_POOL
        && type!=MEMORY_TYPE+2
#endif
       ) return kIOReturnUnsupported;
    IOService *provider=client->getProvider();
    if (!owned(provider)) return kIOReturnUnsupported;
#ifdef DVM_MANAGED_POOL
    if (type==MEMORY_TYPE+2) {
        // Serialize allocation/publication even for concurrent map calls from
        // the same client. Per-client external-method locking is insufficient.
        PoolLock lock(provider);
        if(!lock.locked)return kIOReturnBusy;
        // Claim only from a mapping call, AFTER stock open's entitlement check.
        // One VM-lifetime pool prevents recycling pages after client death.
        // IOService::open serializes competing clients through arbitration.
        if (!provider->getProperty("DVMManagedOwner")) {
            if (!provider->open(client))return kIOReturnExclusiveAccess;
            if (!provider->setProperty("DVMManagedOwner",client))return kIOReturnNoMemory;
        }
        if (provider->getProperty("DVMManagedOwner")!=client)
            return kIOReturnNotPermitted;
        auto *memory=static_cast<IOBufferMemoryDescriptor *>(provider->getProperty("DVMManagedPool"));
        if (!memory) {
            memory=static_cast<IOBufferMemoryDescriptor *>(dvm_alloc_class("IOBufferMemoryDescriptor"));
            if (!memory) return kIOReturnNoMemory;
            if (!memory->initWithPhysicalMask(nullptr,
                    kIODirectionInOut|kIOMemoryKernelUserShared|0x700,
                    12435456,16384,0)) {
                memory->release();return kIOReturnNoMemory;
            }
            if (!provider->setProperty("DVMManagedPool",memory)) {
                memory->release();return kIOReturnNoMemory;
            }
            memory->release();
#ifdef DVM_MANAGED_EXPORT
            // Index 2 is kernel-only: clientMemoryForType never exports this
            // registration aperture. Only descriptor-derived physical pages
            // enter the host resource; the client supplies no addresses.
            if (memory->prepare(kIODirectionInOut))return kIOReturnNotReady;
            if (provider->getResources())return kIOReturnNotReady;
            auto *registerMemory=provider->getDeviceMemoryWithIndex(2);
            if (!registerMemory)return kIOReturnNotFound;
            auto *mapping=registerMemory->map();
            if (!mapping)return kIOReturnNoMemory;
            auto *registers=reinterpret_cast<volatile UInt64 *>(mapping->getVirtualAddress());
            registers[0]=12435456;
            for (IOByteCount off=0;off<12435456;off+=16384) {
                IOByteCount length=0;
                addr64_t page=memory->getPhysicalSegment(off,&length,kIOMemoryMapperNone);
                if (!page||(page&16383)||length<16384) {
                    mapping->release();return kIOReturnNotAligned;
                }
                registers[1]=page;
            }
            __asm__ volatile("dsb sy" ::: "memory");
            registers[2]=1;
            __asm__ volatile("dsb sy" ::: "memory");
            UInt64 registered=registers[2];mapping->release();
            if (registered!=1)return kIOReturnNotReady;
            if (!provider->setProperty("DVMManagedReady",true))return kIOReturnNoMemory;
#endif
        }
#ifdef DVM_MANAGED_EXPORT
        if (!provider->getProperty("DVMManagedReady"))return kIOReturnNotReady;
#endif
        memory->retain();*options=0x700;*result=memory;
        return kIOReturnSuccess;
    }
#endif
    IOReturn resources=provider->getResources();
    if (resources!=kIOReturnSuccess) return resources;
    IODeviceMemory *memory=provider->getDeviceMemoryWithIndex(type-MEMORY_TYPE);
    if (!memory) return kIOReturnNoMemory;
    // The hash-guarded DT owns exactly two fixed ranges; no caller-supplied GPA.
    memory->retain();
    *options=0;
    *result=memory;
    return kIOReturnSuccess;
}
