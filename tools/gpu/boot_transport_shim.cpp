// Experimental 24A5430a boot shim. Linked into guarded executable padding of
// an isolated BootKC, never loaded as a kext or used with an unreviewed ABI.
// Stock IOKit owns allocation, task attachment, mapping and descriptor lifetime.
#include <IOKit/IOUserClient.h>
#include <IOKit/IOMemoryDescriptor.h>

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
    if (type!=MEMORY_TYPE && type!=MEMORY_TYPE+1) return kIOReturnUnsupported;
    IOService *provider=client->getProvider();
    if (!owned(provider)) return kIOReturnUnsupported;
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
