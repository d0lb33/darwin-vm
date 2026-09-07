// Opt-in retained registration. The modern dispatcher already checked the
// provider, current caller entitlement and scalar-only shape. Kernel owns all
// page discovery; resource IDs and byte extents are the only userspace outputs.
struct DVMSurfaceProviderLock {
    IOService *provider;bool locked;
    explicit DVMSurfaceProviderLock(IOService *p):provider(p),locked(p->lockForArbitration()){}
    ~DVMSurfaceProviderLock(){if(locked)provider->unlockForArbitration();}
};
static void dvm_surface_key(uint64_t id,char *key){
    const char prefix[]="DVMSurface";const char hex[]="0123456789abcdef";
    for(unsigned i=0;i<sizeof(prefix)-1;i++)key[i]=prefix[i];
    for(unsigned i=0;i<16;i++)key[10+i]=hex[(id>>(60-4*i))&15];
    key[26]=0;
}
static IOReturn dvm_surface_registry(IOUserClient *client,IOService *provider,uint32_t selector,IOExternalMethodArguments *a){
    if(a->scalarInput[0]!=1)return kIOReturnBadArgument;
    DVMSurfaceProviderLock lock(provider);if(!lock.locked)return kIOReturnBusy;
    if(provider->getProperty("DVMSurfaceOwner")&&provider->getProperty("DVMSurfaceOwner")!=client)return kIOReturnNotPermitted;
    if(provider->getProperty("DVMManagedOwner")&&provider->getProperty("DVMManagedOwner")!=client)return kIOReturnNotPermitted;
    if(!provider->getProperty("DVMSurfaceOwner")){
        if(!provider->getProperty("DVMManagedOwner")&&!provider->open(client))return kIOReturnExclusiveAccess;
        if(!provider->setProperty("DVMSurfaceOwner",client))return kIOReturnNoMemory;
    }
    if(provider->getResources())return kIOReturnNotReady;
    auto *io=provider->getDeviceMemoryWithIndex(2);if(!io)return kIOReturnNotFound;
    auto *map=io->map();if(!map)return kIOReturnNoMemory;
    auto *reg=reinterpret_cast<volatile UInt64 *>(map->getVirtualAddress());
    if(reg[0x138/8]!=1){map->release();return kIOReturnUnsupported;}
    if(selector==0x44565302){
        uint64_t id=a->scalarInput[1];char key[27];dvm_surface_key(id,key);
        if(!id||a->scalarInput[2]){map->release();return kIOReturnBadArgument;}
        auto *memory=static_cast<IOMemoryDescriptor *>(client->getProperty(key));
        if(!memory){map->release();return kIOReturnNotFound;}
        __asm__ volatile("dsb sy":::"memory");reg[0x120/8]=id;__asm__ volatile("dsb sy":::"memory");
        UInt64 status=reg[0x128/8];a->scalarOutput[0]=1;a->scalarOutput[1]=id;a->scalarOutput[2]=status;
        if(status){map->release();return kIOReturnBusy;}
        IOReturn kr=memory->complete(kIODirectionInOut);a->scalarOutput[3]=(uint32_t)kr;
        if(kr){reg[0x130/8]=1;__asm__ volatile("dsb sy":::"memory");map->release();return kr;}
        // The owning client is kept by the provider. A failed owner never
        // hands these pages to a replacement process; restart is unsupported.
        client->removeProperty(key);map->release();return kIOReturnSuccess;
    }
    uint64_t address=a->scalarInput[1],length=a->scalarInput[2];
    if(!address||!length||length>64*1024*1024||address>=(1ull<<47)||length>(1ull<<47)-address){map->release();return kIOReturnBadArgument;}
    void *thread;__asm__ volatile("mrs %0,tpidr_el1":"=r"(thread));task_t task=dvm_thread_task(thread);
    if(!task){map->release();return kIOReturnNotPermitted;}
    auto *memory=static_cast<IOMemoryDescriptor *>(dvm_alloc_class("IOGeneralMemoryDescriptor"));
    if(!memory){map->release();return kIOReturnNoMemory;}
    IOAddressRange range={address,length};
    if(!memory->initWithOptions(&range,1,0,task,kIOMemoryTypeVirtual64|kIODirectionInOut)){memory->release();map->release();return kIOReturnBadArgument;}
    IOReturn kr=memory->prepare(kIODirectionInOut);
    if(kr){memory->release();map->release();return kr;}
    reg[0x108/8]=address&16383;reg[0x100/8]=length;__asm__ volatile("dsb sy":::"memory");
    UInt64 status=reg[0x128/8];
    for(IOByteCount off=0;!status&&off<length;){
        IOByteCount available=0;addr64_t physical=memory->getPhysicalSegment(off,&available,kIOMemoryMapperNone);
        IOByteCount take=16384-((address+off)&16383);if(take>length-off)take=length-off;
        if(!physical||(physical&16383)!=((address+off)&16383)||available<take||physical<0x10000000000ull||physical>=0x10300000000ull){status=4;break;}
        reg[0x110/8]=physical&~16383ull;off+=take;
        __asm__ volatile("dsb sy":::"memory");status=reg[0x128/8];
    }
    uint64_t id=0;
    if(!status){reg[0x118/8]=1;__asm__ volatile("dsb sy":::"memory");status=reg[0x128/8];id=reg[0x118/8];}
    if(!status&&!id){
        // A successful publication without an identity is uncertain: leak the
        // prepared descriptor and stop all reuse rather than unpin an export.
        reg[0x130/8]=1;__asm__ volatile("dsb sy":::"memory");map->release();return kIOReturnIOError;
    }
    if(status||!id){
        // Abort staged pages without publishing. The invalid begin rolls back
        // scratch ownership, leaving every previously active record intact.
        reg[0x100/8]=0;__asm__ volatile("dsb sy":::"memory");
        kr=memory->complete(kIODirectionInOut);a->scalarOutput[2]=status;a->scalarOutput[3]=(uint32_t)kr;
        if(kr){reg[0x130/8]=1;__asm__ volatile("dsb sy":::"memory");map->release();return kr;}
        memory->release();map->release();return kIOReturnNoResources;
    }
    char key[27];dvm_surface_key(id,key);
    if(!client->setProperty(key,memory)){
        // Publication already happened: never unpin after an uncertain export.
        // Retain the descriptor and poison future registrations until VM exit.
        reg[0x130/8]=1;__asm__ volatile("dsb sy":::"memory");map->release();return kIOReturnNoMemory;
    }
    memory->release();map->release();
    a->scalarOutput[0]=1;a->scalarOutput[1]=id;a->scalarOutput[2]=length;
    a->scalarOutput[3]=((address&16383)+length+16383)&~16383ull;
    return kIOReturnSuccess;
}
