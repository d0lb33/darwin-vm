// Guest-side negative contracts, outside the timed display batch.
static uint8_t *DVMCheckManagedOwner(Namespace *ns,uint8_t *pool) {
    __typeof__(&IOConnectMapMemory64) map=dlsym(RTLD_DEFAULT,"IOConnectMapMemory64");
    __typeof__(&IOConnectGetService) getService=dlsym(RTLD_DEFAULT,"IOConnectGetService");
    __typeof__(&IORegistryEntryFromPath) fromPath=dlsym(RTLD_DEFAULT,"IORegistryEntryFromPath");
    __typeof__(&IOObjectGetClass) getClass=dlsym(RTLD_DEFAULT,"IOObjectGetClass");
    __typeof__(&IOServiceOpen) open=dlsym(RTLD_DEFAULT,"IOServiceOpen");
    __typeof__(&IOObjectRelease) release=dlsym(RTLD_DEFAULT,"IOObjectRelease");
    if(!map||!getService||!fromPath||!getClass||!open||!release)fail("pool-contract-symbol");
    mach_vm_address_t address=0;mach_vm_size_t length=0;
    kern_return_t kr=map(ns.client,DVM_GPU_MEMORY_TYPE+3,mach_task_self(),&address,&length,kIOMapAnywhere);
    fprintf(stderr,"GPU_LOAD_POOL_MAP_REJECT kind=kernel-aperture rc=0x%x address=0x%llx bytes=%llu\n",kr,address,length);
    // IOConnect's outer mapping path can translate clientMemoryForType errors.
    // Acceptance is that no mapping is returned; retain the actual outer code.
    if(kr==kIOReturnSuccess||address||length)fail("pool-kernel-aperture-exposed");
    fprintf(stderr,"GPU_LOAD_POOL_KERNEL_APERTURE rejected=1 rc=0x%x\n",kr);
    io_service_t service=0;io_connect_t other=0;
    kr=getService(ns.client,&service);char cls[128]={0};
    if(!kr&&service)getClass(service,cls);
    fprintf(stderr,"GPU_LOAD_POOL_CONNECTION_SERVICE rc=0x%x class=%s\n",kr,cls);
    if(service)release(service);
    // Open the same observed device-tree provider used by openMMIO. A user
    // client's getService result need not be its provider.
    service=fromPath(0,"IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/dvm-transport@E0000000");
    if(!service)fail("pool-provider-path");
    kr=open(service,mach_task_self(),DVM_GPU_OPEN_TYPE,&other);
    fprintf(stderr,"GPU_LOAD_POOL_OTHER_OPEN rc=0x%x client=%u\n",kr,other);
    if(kr)fail("pool-other-open");
    release(service);
    kr=map(other,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),&address,&length,kIOMapAnywhere|0x700);
    fprintf(stderr,"GPU_LOAD_POOL_MAP_REJECT kind=wrong-owner rc=0x%x address=0x%llx bytes=%llu\n",kr,address,length);
    if(kr==kIOReturnSuccess||address||length)fail("pool-wrong-owner-accepted");
    if(ns.closeClient(other))fail("pool-other-close");
    fprintf(stderr,"GPU_LOAD_POOL_WRONG_OWNER rejected=1 rc=0x%x\n",kr);
    kr=map(ns.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),&address,&length,kIOMapAnywhere|kIOMapUnique|0x700);
    fprintf(stderr,"GPU_LOAD_POOL_MAP_AGAIN rc=0x%x address=0x%llx bytes=%llu\n",kr,address,length);
    if(kr||length!=DVM_PRESENT_BUFFER_BYTES)fail("pool-repeat-map");
    ((volatile uint32_t *)pool)[0]=0x44564d51;mmioBarrier();
    if(*(volatile uint32_t *)address!=0x44564d51)fail("pool-repeat-alias");
    if(ns.unmap(ns.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),address))fail("pool-repeat-unmap");
    address=0;length=0;
    kr=map(ns.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),&address,&length,kIOMapAnywhere|0x700);
    fprintf(stderr,"GPU_LOAD_POOL_MAP_AFTER_UNMAP rc=0x%x address=0x%llx bytes=%llu\n",kr,address,length);
    if(kr||length!=DVM_PRESENT_BUFFER_BYTES||*(volatile uint32_t *)address!=0x44564d51)fail("pool-remap-contents");
    fprintf(stderr,"GPU_LOAD_POOL_REMAP verified=1 bytes=%llu\n",length);
    return (uint8_t *)address;
}
