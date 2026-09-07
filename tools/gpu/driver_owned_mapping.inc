// Explicit provider for factory ABI v1; only compiled into the isolated helper.
// A mapped object owns Namespace, so neither IOServiceClose nor unmap can
// overtake a driver's retained mapping. Physical pool lifetime remains the VM.
@interface DVMServiceMapping : NSObject <DVMMetalOwnedMapping>
@property(nonatomic,strong) Namespace *owner;
@property(nonatomic) void *bytes;
@property(nonatomic) NSUInteger length;
@end
@implementation DVMServiceMapping
- (NSUInteger)mappingVersion {return 1;}
- (void)dealloc {
    if(_bytes&&_owner.unmap(_owner.client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),(uintptr_t)_bytes))
        fprintf(stderr,"GPU_LOAD_ERROR owned-mapping-unmap\n");
}
@end
static id<DVMMetalOwnedMapping> DVMMapDriverPool(Namespace *ns,NSError **error) {
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
