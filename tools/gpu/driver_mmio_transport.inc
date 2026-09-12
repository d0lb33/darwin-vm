/* Deliberately one outstanding request. The kernel owns the two mappings;
 * publication uses barriers and completion is polled, not an interrupt. */
#include "../../qemu-sptm/include/xnu/darwin_gpu_transport.h"
#include "driver_binary.h"
#include "blur_wire.h"
#if defined(DVM_DRIVER_BLUR) || defined(DVM_DRIVER_PRESENT)
static double blurRPCus,blurDoorbellus;
#define DVM_AUDIT_SLOTS 120
#else
#define DVM_AUDIT_SLOTS 64
#endif
#ifdef DVM_DRIVER_PRESENT
#undef DVM_GPU_REPLY_DATA
#define DVM_GPU_REPLY_DATA 0x200000u
#undef DVM_GPU_MAX_BYTES
#define DVM_GPU_MAX_BYTES 0x10000u
#define DVM_EXPECTED_MODE 3
#else
#define DVM_EXPECTED_MODE 2
#endif
#include <signal.h>
#include <limits.h>
#include <pthread.h>
#include <sys/ucontext.h>
#ifndef DVM_BOOT_PLUGIN
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
#endif
static void mmioBarrier(void) { __asm__ volatile("dsb sy" ::: "memory"); }
// Set by the bootstrap to "SYSTEM" or "POSTER" so the shared host journal
// attributes each request to backboardd or the wallpaper extension.
const char *DVMClientTag;
// Shared channel (2026-09-11): more than one guest process, thread, and
// driver image may use the one transport.  backboardd deliberately contains
// both the boot-trusted carrier and a staged revision, so neither a pid owner
// nor a dylib-local recursive lock serializes the process.  Keep the thread
// owner, its process and recursion depth in shared RAM.  pthread thread IDs
// are process-wide across every loaded image.  A holder is stolen only after
// its whole process is gone; the drain before the next sequence covers the
// abandoned in-flight request.
#define DVM_CHANNEL_LOCK 0x380u
#define DVM_CHANNEL_PID 0x388u
#define DVM_CHANNEL_DEPTH 0x390u
static uint64_t channelIdentity(void) {
    uint64_t identity=0;
    if(pthread_threadid_np(NULL,&identity)||!identity)fail("mmio-thread-identity");
    return identity;
}
static void channelLock(uint8_t *shared) {
    uint64_t me=channelIdentity();
    uint64_t *word=(uint64_t *)(shared+DVM_CHANNEL_LOCK);
    uint64_t *ownerPID=(uint64_t *)(shared+DVM_CHANNEL_PID);
    uint64_t *depth=(uint64_t *)(shared+DVM_CHANNEL_DEPTH);
    if(__atomic_load_n(word,__ATOMIC_ACQUIRE)==me){
        if(!__atomic_fetch_add(depth,1,__ATOMIC_ACQ_REL))fail("mmio-channel-depth");
        return;
    }
    double started=now(),checked=started;
    for(;;){
        uint64_t expected=0;
        if(__atomic_compare_exchange_n(word,&expected,me,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)){
            __atomic_store_n(ownerPID,(uint64_t)getpid(),__ATOMIC_RELAXED);
            __atomic_store_n(depth,1,__ATOMIC_RELEASE);
            return;
        }
        __asm__ volatile("yield");
        double t=now();
        if(t-checked>0.05){
            checked=t;
            uint64_t holder=__atomic_load_n(word,__ATOMIC_ACQUIRE);
            uint64_t pid=__atomic_load_n(ownerPID,__ATOMIC_ACQUIRE);
            if(holder&&holder!=me&&pid&&pid<=INT_MAX&&kill((pid_t)pid,0)&&errno==ESRCH&&
               __atomic_compare_exchange_n(word,&holder,me,false,__ATOMIC_ACQ_REL,__ATOMIC_ACQUIRE)){
                __atomic_store_n(ownerPID,(uint64_t)getpid(),__ATOMIC_RELAXED);
                __atomic_store_n(depth,1,__ATOMIC_RELEASE);
                return;
            }
        }
        if(t-started>30)fail("mmio-channel-lock-timeout");
    }
}
static void channelUnlock(uint8_t *shared) {
    uint64_t me=channelIdentity();
    uint64_t *word=(uint64_t *)(shared+DVM_CHANNEL_LOCK);
    uint64_t *ownerPID=(uint64_t *)(shared+DVM_CHANNEL_PID);
    uint64_t *depth=(uint64_t *)(shared+DVM_CHANNEL_DEPTH);
    if(__atomic_load_n(word,__ATOMIC_ACQUIRE)!=me)fail("mmio-channel-unlock-owner");
    uint64_t prior=__atomic_fetch_sub(depth,1,__ATOMIC_ACQ_REL);
    if(!prior)fail("mmio-channel-unlock-depth");
    if(prior==1){
        mmioBarrier();
        __atomic_store_n(ownerPID,0ull,__ATOMIC_RELAXED);
        __atomic_store_n(word,0ull,__ATOMIC_RELEASE);
    }
}
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
        // Channel lock before the process-local lock: call: holds the channel
        // while it reports, so the reverse order could deadlock two threads.
        channelLock(mmioReportRAM);
        @synchronized(mmioReportLock) {
#ifdef DVM_TEST_RUNNER
            mmioReportSequence=*(volatile uint64_t *)(mmioReportRAM+0x180);
#endif
            uint64_t seq=++mmioReportSequence;
#ifdef DVM_TEST_RUNNER
            double until=now()+10;
            while(seq-*(volatile uint64_t *)(mmioReportRAM+0x188)>DVM_AUDIT_SLOTS){
                if(now()>until)_exit(123);usleep(1000);
            }
            uint8_t *slot=mmioReportRAM+0x1000+((seq-1)%DVM_AUDIT_SLOTS)*512;
#else
            if(seq>DVM_AUDIT_SLOTS)_exit(122);
            uint8_t *slot=mmioReportRAM+0x1000+(seq-1)*512;
#endif
            memcpy(slot+16,text,(unsigned)n);
            uint32_t length=(uint32_t)n,c=crc(text,length);
            memcpy(slot,&seq,8);memcpy(slot+8,&length,4);memcpy(slot+12,&c,4);
            mmioBarrier();*(volatile uint64_t *)(mmioReportRAM+0x180)=seq;mmioBarrier();
        }
        channelUnlock(mmioReportRAM);
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
@property(nonatomic) uint8_t *staging;
@property(nonatomic) uint64_t stagingBytes;
@property(nonatomic) uint8_t *bufferPool;
@property(nonatomic) uint64_t bufferPoolBytes;
@property(nonatomic) BOOL ramPoll;
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)error;
- (NSDictionary *)call:(NSDictionary *)request payload:(NSData *)payload error:(NSError **)error;
- (NSData *)call:(NSDictionary *)request replyPayloadLength:(NSUInteger)length error:(NSError **)error;
@end
@implementation Namespace
- (void)dealloc {
    mmioReportRAM=NULL;
    if (_regs) _unmap(_client,DVM_GPU_MEMORY_TYPE+1,mach_task_self(),(uintptr_t)_regs);
    if (_shared) _unmap(_client,DVM_GPU_MEMORY_TYPE,mach_task_self(),(uintptr_t)_shared);
    if (_client) _closeClient(_client);
}
- (NSDictionary *)call:(NSDictionary *)request payload:(NSData *)payload error:(NSError **)error {
    @synchronized(self) {
        if(!payload)return [self call:request error:error];
        if(!_staging||payload.length>_stagingBytes)fail("mmio-staging-extent");
        // The staging region is shared with every other process on the
        // channel: hold the channel lock from the copy through the reply.
        channelLock(_shared);
        @try {
            memcpy(_staging,payload.bytes,payload.length);
            NSMutableDictionary *r=[request mutableCopy];
            r[@"staged"]=@{@"offset":@0,@"length":@(payload.length),@"crc":@(crc(payload.bytes,payload.length))};
            return [self call:r error:error];
        } @finally { channelUnlock(_shared); }
    }
}
- (NSData *)call:(NSDictionary *)request replyPayloadLength:(NSUInteger)length error:(NSError **)error {
    @synchronized(self) {
        if(!_staging||!length||length>_stagingBytes)fail("mmio-staged-reply-extent");
        // Keep the shared channel until the bytes are copied: another process
        // may reuse staging immediately after this method returns.
        channelLock(_shared);
        @try {
            NSMutableDictionary *r=[request mutableCopy];r[@"length"]=@(length);
            NSDictionary *reply=[self call:r error:error];
            if(!reply)return nil;
            uint64_t offset=[reply[@"stagedOffset"] unsignedLongLongValue];
            uint64_t returned=[reply[@"stagedLength"] unsignedLongLongValue];
            uint64_t expectedCRC=[reply[@"stagedCRC"] unsignedLongLongValue];
            if(offset||returned!=length||expectedCRC>UINT32_MAX||crc(_staging,length)!=(uint32_t)expectedCRC)
                fail("mmio-staged-reply-contract");
            return [NSData dataWithBytes:_staging length:length];
        } @finally {channelUnlock(_shared);}
    }
}
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)error {
    @synchronized(self) {
        if (_failed) fail("mmio-retired-session");
        double started=now();
        channelLock(_shared);
        @try {
        // Continue the device's sequence, not a per-process counter: another
        // process may have submitted since this one last did. Drain first so
        // a holder that died mid-request cannot leave us off by one.
        while(*(volatile uint64_t *)(_regs+DVM_GPU_REG_SUBMITTED)!=*(volatile uint64_t *)(_regs+DVM_GPU_REG_DONE)){
            if(*(volatile uint64_t *)(_regs+DVM_GPU_REG_ERROR))fail("mmio-device");
            if(now()-started>15)fail("mmio-channel-drain-timeout");
            __asm__ volatile("yield");
        }
        uint64_t seq=*(volatile uint64_t *)(_regs+DVM_GPU_REG_DONE)+1;_sequence=seq;
        NSMutableDictionary *r=[request mutableCopy];r[@"seq"]=@(seq);
        if(DVMClientTag)r[@"client"]=@(DVMClientTag);
        BOOL binary=NO;
#ifdef DVM_DRIVER_BINARY
        binary=[r[@"op"] isEqual:@"submit"];
#endif
        BOOL blur=[r[@"op"] isEqual:@"blurSubmit"];
        NSData *data=blur?DVMBlurEncode(r):binary?DVMBEncodeRequest(r):[NSJSONSerialization dataWithJSONObject:r options:0 error:error];
        if (!data.length || data.length>DVM_GPU_MAX_BYTES) fail("mmio-request-size");
        uint32_t n=(uint32_t)data.length,c=crc(data.bytes,data.length);
        memcpy(_shared+DVM_GPU_REQUEST_DATA,data.bytes,n);
        uint8_t *h=_shared+DVM_GPU_REQUEST_HEADER;
        memcpy(h,_identity.bytes,16);memcpy(h+16,&seq,8);memcpy(h+24,&n,4);memcpy(h+28,&c,4);
        double submitted=now();mmioBarrier();
        *(volatile uint64_t *)(_regs+DVM_GPU_REG_DOORBELL)=seq;
        uint64_t polls=0;
        if(_ramPoll){
            // The peer publishes the reply header in shared RAM before it acks
            // QEMU, so waiting on that word costs no MMIO exits and no BQL
            // round trips. The REG_DONE read below still gates use of the
            // reply: QEMU sets it only after checking the reply CRC, and the
            // ack that triggers it orders the peer's writes before our reads.
            volatile uint64_t *published=(volatile uint64_t *)(_shared+DVM_GPU_REPLY_HEADER+16);
            while(*published!=seq){
                if((++polls&1023u)==0){
                    uint64_t fault=*(volatile uint64_t *)(_regs+DVM_GPU_REG_ERROR);
                    if (fault) {fprintf(stderr,"GPU_LOAD_ERROR mmio-device=%llu seq=%llu\n",fault,seq);fail("mmio-device");}
                    if (now()-submitted>15) fail("mmio-completion-timeout");
                }
                __asm__ volatile("yield");
            }
        }
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
        NSData *returnedData=[NSData dataWithBytes:_shared+DVM_GPU_REPLY_DATA length:n];
        NSDictionary *reply=DVMBMagic(returnedData)==DVM_BLUR_REPLY?DVMBlurReplyDecode(returnedData):DVMBMagic(returnedData)==DVM_BIN_REPLY?DVMBDecodeReply(returnedData):[NSJSONSerialization JSONObjectWithData:returnedData options:0 error:error];
        if (![reply isKindOfClass:NSDictionary.class]||[reply[@"seq"] unsignedLongLongValue]!=seq) fail("mmio-reply-json");
#if defined(DVM_DRIVER_BLUR) || defined(DVM_DRIVER_PRESENT)
        (void)polls;blurRPCus+=(now()-started)*1e6;blurDoorbellus+=(completed-submitted)*1e6;
#else
        fprintf(stderr,"GPU_LOAD_DRIVER_MMIO_RPC seq=%llu op=%s total_us=%.3f doorbell_us=%.3f polls=%llu\n",seq,[request[@"op"] UTF8String],(now()-started)*1e6,(completed-submitted)*1e6,polls);
#endif
        if (![reply[@"ok"] boolValue]) {
            if (error) *error=[NSError errorWithDomain:reply[@"domain"] code:[reply[@"code"] integerValue] userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]}];
            return nil;
        }
        return reply;
        } @finally { channelUnlock(_shared); }
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
#ifndef DVM_BOOT_PLUGIN
    struct sigaction action={0};action.sa_sigaction=mmioCrash;action.sa_flags=SA_SIGINFO;
    if(sigaction(SIGBUS,&action,NULL))fail("mmio-signal-handler");
#endif
    fprintf(stderr,"GPU_LOAD_DRIVER_MMIO_ADDRESSES memcpy=%p\n",dlsym(RTLD_DEFAULT,"memcpy"));
    Namespace *ns=[Namespace new];ns.closeClient=p_IOServiceClose;ns.unmap=p_IOConnectUnmapMemory64;
    uint32_t openType=DVM_GPU_OPEN_TYPE;
#ifdef DVM_TEST_RUNNER
    const char *development=getenv("DVM_RUNNER_DEVELOPMENT");
    if(development&&!strcmp(development,"1"))openType=0x44564d4c;
#endif
    io_connect_t client=0;kern_return_t kr=p_IOServiceOpen(service,mach_task_self(),openType,&client);
    if(openType!=DVM_GPU_OPEN_TYPE)fprintf(stderr,"GPU_LOAD_DEVELOPMENT_OPEN type=%x result=%x\n",openType,kr);
    p_IOObjectRelease(service);if(kr)fail("mmio-open");ns.client=client;
    mach_vm_address_t ram=0,regs=0;mach_vm_size_t ramN=0,regsN=0;
    kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE,mach_task_self(),&ram,&ramN,kIOMapAnywhere|kIOMapCopybackCache);
    if(kr||ramN!=DVM_GPU_RAM_SIZE)fail("mmio-map-ram");ns.shared=(uint8_t *)ram;
    kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE+1,mach_task_self(),&regs,&regsN,kIOMapAnywhere);
    if(kr||regsN!=DVM_GPU_REG_SIZE)fail("mmio-map-regs");ns.regs=(volatile uint8_t *)regs;
    mmioBarrier();uint32_t magic;memcpy(&magic,ns.shared,4);
    if(magic!=DVM_GPU_MAGIC||*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_MODE)!=DVM_EXPECTED_MODE)fail("mmio-identity");
#ifdef DVM_TEST_RUNNER
    // Supervisor waits for each child before reacquiring the single producer.
    ns.sequence=*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_DONE);
    mmioReportSequence=*(volatile uint64_t *)(ns.shared+0x180);
#else
    if(*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_DONE))fail("mmio-used-session");
#endif
    ns.identity=[NSData dataWithBytes:ns.shared+16 length:16];
    mmioReportLock=[NSObject new];mmioReportRAM=ns.shared;
    fprintf(stderr,"GPU_LOAD_DRIVER_MMIO_MAPPED ram=%llu registers=%llu ram_va=%p regs_va=%p\n",ramN,regsN,ns.shared,ns.regs);
    fprintf(stderr,"GPU_LOAD_DRIVER_WAIT_READY\n");until=now()+420;
    while(!*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_READY)) {
        if(*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_ERROR)||now()>until)fail("mmio-ready");
        usleep(1000);
    }
    mmioBarrier();
#ifdef DVM_DRIVER_PRESENT
    // Optional staging descriptor, published by the peer before READY. Exact
    // geometry is a contract, not a negotiation: anything else keeps the
    // chunked base64 path.
    uint64_t magicWord,offsetWord,bytesWord,flagsWord;
    memcpy(&magicWord,ns.shared+DVM_STAGING_DESCRIPTOR,8);memcpy(&offsetWord,ns.shared+DVM_STAGING_DESCRIPTOR+8,8);
    memcpy(&bytesWord,ns.shared+DVM_STAGING_DESCRIPTOR+16,8);memcpy(&flagsWord,ns.shared+DVM_STAGING_DESCRIPTOR+24,8);
    if(magicWord==DVM_STAGING_MAGIC){
        if(offsetWord==DVM_STAGING_OFFSET&&bytesWord==DVM_STAGING_BYTES&&(flagsWord&DVM_STAGING_FLAG_ENABLED)){
            ns.staging=ns.shared+DVM_STAGING_OFFSET;ns.stagingBytes=DVM_STAGING_BYTES;
        }
        ns.ramPoll=(flagsWord&DVM_STAGING_FLAG_RAM_POLL)!=0;
    }
    if(flagsWord&DVM_STAGING_FLAG_BUFFER_POOL){
        uint64_t poolMagic,poolOffset,poolBytes,poolReserved;
        memcpy(&poolMagic,ns.shared+DVM_BUFFER_POOL_DESCRIPTOR,8);memcpy(&poolOffset,ns.shared+DVM_BUFFER_POOL_DESCRIPTOR+8,8);
        memcpy(&poolBytes,ns.shared+DVM_BUFFER_POOL_DESCRIPTOR+16,8);memcpy(&poolReserved,ns.shared+DVM_BUFFER_POOL_DESCRIPTOR+24,8);
        if(poolMagic!=DVM_BUFFER_POOL_MAGIC||poolOffset!=DVM_BUFFER_POOL_OFFSET||poolBytes!=DVM_BUFFER_POOL_BYTES||poolReserved||
           poolOffset%DVM_MANAGED_PAGE_BYTES||poolBytes%DVM_MANAGED_PAGE_BYTES||poolOffset<DVM_GPU_REPLY_DATA+DVM_GPU_MAX_BYTES||
           poolOffset+poolBytes!=DVM_GPU_RAM_SIZE)fail("mmio-buffer-pool-contract");
        ns.bufferPool=ns.shared+poolOffset;ns.bufferPoolBytes=poolBytes;
    }
    fprintf(stderr,"GPU_LOAD_DRIVER_STAGING enabled=%d bytes=%llu ram_poll=%d buffer_pool=%llu\n",ns.staging!=NULL,(unsigned long long)ns.stagingBytes,ns.ramPoll,(unsigned long long)ns.bufferPoolBytes);
#endif
    return ns;
#undef MMLOAD
}
