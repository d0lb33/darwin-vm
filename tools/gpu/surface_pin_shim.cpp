// Exact-24A5430a, opt-in probe of caller-owned IOSurface backing. No page is
// exported by the original probe: prepare/validation/complete is one call.
// DVM_SURFACE_REGISTRY separately enables retained registration and retirement.
// The builder guards the modern IOUserClient2022 dispatcher and every ABI.
#include <IOKit/IOUserClient.h>
#include <IOKit/IOMemoryDescriptor.h>
#include <stddef.h>
extern "C" OSObject *dvm_alloc_class(const char *);
extern "C" void *dvm_current_proc(void);
extern "C" int dvm_entitled(void *,const char *,bool *);
extern "C" task_t dvm_thread_task(void *);
extern "C" IOReturn dvm_original_dispatch(IOUserClient *,uint32_t,IOExternalMethodArguments *);
static_assert(offsetof(IOExternalMethodArguments,scalarInput)==0x20);
static_assert(offsetof(IOExternalMethodArguments,scalarInputCount)==0x28);
static_assert(offsetof(IOExternalMethodArguments,scalarOutput)==0x48);
static_assert(offsetof(IOExternalMethodArguments,scalarOutputCount)==0x50);
static_assert(sizeof(IOAddressRange)==16);
#ifdef DVM_SURFACE_REGISTRY
#include "surface_registry_kernel.inc"
#endif

extern "C" IOReturn dvm_surface_pin(IOUserClient *client,uint32_t selector,IOExternalMethodArguments *a) {
    auto *provider=client->getProvider();
    const char *name=provider?provider->getName():nullptr;
    const char expected[]="dvm-transport";
    bool ours=name;
    if(name)for(unsigned i=0;i<sizeof(expected);i++)if(name[i]!=expected[i]){ours=false;break;}
    if(!ours)return dvm_original_dispatch(client,selector,a);
    // Never expose the stock diagnostic methods through our transport nub.
    if(selector!=0x44565300
#ifdef DVM_SURFACE_REGISTRY
       &&selector!=0x44565301&&selector!=0x44565302&&selector!=0x44565303
#endif
       )return kIOReturnUnsupported;
    bool entitlement=false;
    void *proc=dvm_current_proc();
    if(!proc||dvm_entitled(proc,"org.darwin-vm.transport",&entitlement)||!entitlement)return kIOReturnNotPrivileged;
    if(!a||a->asyncWakePort||a->scalarInputCount!=3||a->scalarOutputCount!=4||
       !a->scalarInput||!a->scalarOutput||a->structureInputSize||a->structureOutputSize||
       a->structureInputDescriptor||a->structureOutputDescriptor)return kIOReturnBadArgument;
    for(unsigned i=0;i<4;i++)a->scalarOutput[i]=0;
#ifdef DVM_SURFACE_REGISTRY
    if(selector!=0x44565300)return dvm_surface_registry(client,provider,selector,a);
#endif
    uint64_t address=a->scalarInput[1],length=a->scalarInput[2];
    if(a->scalarInput[0]!=1||!address||!length||length>64*1024*1024||
       address>=(1ull<<47)||length>(1ull<<47)-address)
        return kIOReturnBadArgument;
    void *thread;
    __asm__ volatile("mrs %0, tpidr_el1":"=r"(thread));
    task_t task=dvm_thread_task(thread);
    if(!task)return kIOReturnNotPermitted;
    a->scalarOutput[0]=1;
    auto *memory=static_cast<IOMemoryDescriptor *>(dvm_alloc_class("IOGeneralMemoryDescriptor"));
    if(!memory)return kIOReturnNoMemory;
    IOAddressRange range={address,length};
    if(!memory->initWithOptions(&range,1,0,task,kIOMemoryTypeVirtual64|kIODirectionInOut)){
        memory->release();return kIOReturnBadArgument;
    }
    a->scalarOutput[0]=2;
    IOReturn kr=memory->prepare(kIODirectionInOut);
    if(kr){memory->release();return kr;}
    a->scalarOutput[0]=3;
    // No addresses leave the kernel. The future export must use only these
    // prepared descriptor segments, retain the descriptor, and await retirement.
    for(IOByteCount off=0;off<length;){
        IOByteCount available=0;
        addr64_t page=memory->getPhysicalSegment(off,&available,kIOMemoryMapperNone);
        IOByteCount take=16384-((address+off)&16383);
        if(take>length-off)take=length-off;
        if(!page||(page&16383)!=((address+off)&16383)||available<take||page<0x10000000000ull||page>=0x10300000000ull){
            kr=kIOReturnNotAligned;break;
        }
        a->scalarOutput[1]++;a->scalarOutput[2]+=take;off+=take;
    }
    if(!kr)a->scalarOutput[0]=4;
    IOReturn completed=memory->complete(kIODirectionInOut);
    a->scalarOutput[3]=(uint32_t)completed;
    // A failed complete cannot authorize backing reuse. Quarantine the held
    // descriptor until VM destruction rather than guessing that it unpinned.
    if(completed)return completed;
    memory->release();
    if(!kr)a->scalarOutput[0]=5;
    return kr;
}
