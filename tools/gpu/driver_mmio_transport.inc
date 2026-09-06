/* Deliberately one outstanding request. The kernel owns the two mappings;
 * publication uses barriers and completion is polled, not an interrupt. */
#include "../../qemu-sptm/include/xnu/darwin_gpu_transport.h"
#include <signal.h>
#include <sys/ucontext.h>
static void mmioCrash(int signal, siginfo_t *info, void *context) {
    ucontext_t *u=context;
    const char *names[]={"signal","code","address","pc","lr","x0","x1","x2","esr"};
    uint64_t values[]={(unsigned)signal,(unsigned)info->si_code,(uintptr_t)info->si_addr,
        u->uc_mcontext->__ss.__pc,u->uc_mcontext->__ss.__lr,u->uc_mcontext->__ss.__x[0],
        u->uc_mcontext->__ss.__x[1],u->uc_mcontext->__ss.__x[2],u->uc_mcontext->__es.__esr};
    char line[512];unsigned at=0;const char *prefix="GPU_LOAD_MMIO_FAULT";
    while(*prefix)line[at++]=*prefix++;
    for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) {
        line[at++]=' ';const char *name=names[i];while(*name)line[at++]=*name++;
        line[at++]='=';line[at++]='0';line[at++]='x';
        for(int j=60;j>=0;j-=4)line[at++]="0123456789abcdef"[(values[i]>>j)&15];
    }
    line[at++]='\n';write(2,line,at);_exit(128+signal);
}
static void mmioBarrier(void) { __asm__ volatile("dsb sy" ::: "memory"); }
// Single producer at a time, fixed 64 x 512-byte slots before request data.
// Guest publishes payload/header before the monotonic head. Host checks CRC.
static uint8_t *mmioReportRAM;
static NSObject *mmioReportLock;
static uint64_t mmioReportSequence;
int DVMReport(FILE *stream,const char *format,...) {
    va_list a,b;va_start(a,format);va_copy(b,a);
    if(mmioReportRAM) {
        char text[480];int n=vsnprintf(text,sizeof(text),format,b);
        if(n<=0||n>=(int)sizeof(text))_exit(121);
        @synchronized(mmioReportLock) {
            uint64_t seq=++mmioReportSequence;
            if(seq>64)_exit(122);
            uint8_t *slot=mmioReportRAM+0x1000+(seq-1)*512;
            memcpy(slot+16,text,(unsigned)n);
            uint32_t length=(uint32_t)n,c=crc(text,length);
            memcpy(slot,&seq,8);memcpy(slot+8,&length,4);memcpy(slot+12,&c,4);
            mmioBarrier();*(volatile uint64_t *)(mmioReportRAM+0x180)=seq;mmioBarrier();
        }
    }
    va_end(b);int result=vfprintf(stream,format,a);va_end(a);return result;
}
@interface Namespace : NSObject
@property(nonatomic) io_connect_t client;
@property(nonatomic) __typeof__(&IOServiceClose) closeClient;
@property(nonatomic) __typeof__(&IOConnectUnmapMemory64) unmap;
@property(nonatomic) uint8_t *shared;
@property(nonatomic) volatile uint8_t *regs;
@property(nonatomic,strong) NSData *identity;
@property(nonatomic) uint64_t sequence;
@property(nonatomic) BOOL failed;
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)error;
@end
@implementation Namespace
- (void)dealloc {
    mmioReportRAM=NULL;
    if (_regs) _unmap(_client,DVM_GPU_MEMORY_TYPE+1,mach_task_self(),(uintptr_t)_regs);
    if (_shared) _unmap(_client,DVM_GPU_MEMORY_TYPE,mach_task_self(),(uintptr_t)_shared);
    if (_client) _closeClient(_client);
}
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)error {
    @synchronized(self) {
        if (_failed) fail("mmio-retired-session");
        double started=now();
        uint64_t seq=++_sequence;
        NSMutableDictionary *r=[request mutableCopy];r[@"seq"]=@(seq);
        NSData *data=[NSJSONSerialization dataWithJSONObject:r options:0 error:error];
        if (!data.length || data.length>DVM_GPU_MAX_BYTES) fail("mmio-request-size");
        uint32_t n=(uint32_t)data.length,c=crc(data.bytes,data.length);
        memcpy(_shared+DVM_GPU_REQUEST_DATA,data.bytes,n);
        uint8_t *h=_shared+DVM_GPU_REQUEST_HEADER;
        memcpy(h,_identity.bytes,16);memcpy(h+16,&seq,8);memcpy(h+24,&n,4);memcpy(h+28,&c,4);
        double submitted=now();mmioBarrier();
        *(volatile uint64_t *)(_regs+DVM_GPU_REG_DOORBELL)=seq;
        uint64_t polls=0;
        while (*(volatile uint64_t *)(_regs+DVM_GPU_REG_DONE)!=seq) {
            ++polls;
            uint64_t fault=*(volatile uint64_t *)(_regs+DVM_GPU_REG_ERROR);
            if (fault) {fprintf(stderr,"GPU_LOAD_ERROR mmio-device=%llu seq=%llu\n",fault,seq);fail("mmio-device");}
            if (now()-submitted>15) fail("mmio-completion-timeout");
        }
        mmioBarrier();double completed=now();
        h=_shared+DVM_GPU_REPLY_HEADER;uint64_t returned;
        memcpy(&returned,h+16,8);memcpy(&n,h+24,4);memcpy(&c,h+28,4);
        if (memcmp(h,_identity.bytes,16)||memcmp(_shared+16,_identity.bytes,16)||returned!=seq||!n||n>DVM_GPU_MAX_BYTES||crc(_shared+DVM_GPU_REPLY_DATA,n)!=c)
            fail("mmio-reply-contract");
        NSDictionary *reply=[NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:_shared+DVM_GPU_REPLY_DATA length:n] options:0 error:error];
        if (![reply isKindOfClass:NSDictionary.class]||[reply[@"seq"] unsignedLongLongValue]!=seq) fail("mmio-reply-json");
        fprintf(stderr,"GPU_LOAD_DRIVER_MMIO_RPC seq=%llu op=%s total_us=%.3f doorbell_us=%.3f polls=%llu\n",seq,[request[@"op"] UTF8String],(now()-started)*1e6,(completed-submitted)*1e6,polls);
        if (![reply[@"ok"] boolValue]) {
            if (error) *error=[NSError errorWithDomain:reply[@"domain"] code:[reply[@"code"] integerValue] userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]}];
            return nil;
        }
        return reply;
    }
}
@end
static Namespace *openMMIO(void *lib) {
#define MMLOAD(n) __typeof__(&n) p_##n=dlsym(lib,#n); if(!p_##n)fail("mmio-symbol-" #n)
    MMLOAD(IORegistryEntryFromPath);MMLOAD(IOObjectGetClass);MMLOAD(IOObjectRelease);
    MMLOAD(IOServiceOpen);MMLOAD(IOServiceClose);MMLOAD(IOConnectMapMemory64);MMLOAD(IOConnectUnmapMemory64);
    const char *path="IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/dvm-transport@E0000000";
    io_service_t service=0;double until=now()+60;
    while(!(service=p_IORegistryEntryFromPath(0,path))&&now()<until)usleep(10000);
    if(!service)fail("mmio-service");
    char cls[128]={0};if(p_IOObjectGetClass(service,cls)||strcmp(cls,"AppleARMIODevice"))fail("mmio-class");
    struct sigaction action={0};action.sa_sigaction=mmioCrash;action.sa_flags=SA_SIGINFO;
    if(sigaction(SIGBUS,&action,NULL))fail("mmio-signal-handler");
    fprintf(stderr,"GPU_LOAD_DRIVER_MMIO_ADDRESSES memcpy=%p\n",dlsym(RTLD_DEFAULT,"memcpy"));
    Namespace *ns=[Namespace new];ns.closeClient=p_IOServiceClose;ns.unmap=p_IOConnectUnmapMemory64;
    io_connect_t client=0;kern_return_t kr=p_IOServiceOpen(service,mach_task_self(),DVM_GPU_OPEN_TYPE,&client);
    p_IOObjectRelease(service);if(kr)fail("mmio-open");ns.client=client;
    mach_vm_address_t ram=0,regs=0;mach_vm_size_t ramN=0,regsN=0;
    kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE,mach_task_self(),&ram,&ramN,kIOMapAnywhere|kIOMapCopybackCache);
    if(kr||ramN!=DVM_GPU_RAM_SIZE)fail("mmio-map-ram");ns.shared=(uint8_t *)ram;
    kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE+1,mach_task_self(),&regs,&regsN,kIOMapAnywhere);
    if(kr||regsN!=DVM_GPU_REG_SIZE)fail("mmio-map-regs");ns.regs=(volatile uint8_t *)regs;
    mmioBarrier();uint32_t magic;memcpy(&magic,ns.shared,4);
    if(magic!=DVM_GPU_MAGIC||*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_MODE)!=2||*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_DONE))fail("mmio-identity");
    ns.identity=[NSData dataWithBytes:ns.shared+16 length:16];
    mmioReportLock=[NSObject new];mmioReportRAM=ns.shared;
    fprintf(stderr,"GPU_LOAD_DRIVER_MMIO_MAPPED ram=%llu registers=%llu ram_va=%p regs_va=%p\n",ramN,regsN,ns.shared,ns.regs);
    fprintf(stderr,"GPU_LOAD_DRIVER_WAIT_READY\n");until=now()+420;
    while(!*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_READY)) {
        if(*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_ERROR)||now()>until)fail("mmio-ready");
        usleep(1000);
    }
    mmioBarrier();return ns;
#undef MMLOAD
}
