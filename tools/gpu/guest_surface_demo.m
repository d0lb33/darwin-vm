// One copied, guest-owned IOSurface workload. No global Metal registration.
#import <Foundation/Foundation.h>
#import <IOSurface/IOSurface.h>
#import <CommonCrypto/CommonDigest.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <time.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

enum { Page=4096, Bytes=12288, Width=64, Height=48, Row=256, RawBytes=64*1024*1024 };
static __typeof__(&IOConnectCallScalarMethod) scalar;
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec/1e9; }
static void fail(const char *s) { fprintf(stderr,"GPU_LOAD_ERROR surface=%s\n",s); exit(1); }
static uint32_t crc32(const void *data,size_t n) {
    const uint8_t *p=data; uint32_t c=~0u;
    while(n--) { c^=*p++; for(int k=0;k<8;k++) c=(c>>1)^((0u-(c&1u))&0xedb88320u); } return ~c;
}
static void io(io_connect_t c,unsigned sel,void *p,size_t n,uint64_t off) {
    if(n%Page||off%Page||n>RawBytes||off>RawBytes-n) fail("bounds");
    uint64_t args[]={(uintptr_t)p,n,off};
    kern_return_t kr=scalar(c,sel,args,3,NULL,NULL);
    if(kr) { fprintf(stderr,"GPU_LOAD_SURFACE_IO selector=%u kr=0x%x\n",sel,kr); fail("namespace-io"); }
}
static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3]; }
static void checkAIR(uint8_t digest[32]) {
    NSData *data=[NSData dataWithContentsOfFile:@"/System/Library/Frameworks/QuartzCore.framework/default.metallib"];
    const uint8_t *p=data.bytes;
    if(data.length<8||be32(p)!=0xcafebabe) fail("guest-library-format");
    uint32_t count=be32(p+4); if(count>128||data.length<8+count*20) fail("guest-library-table");
    const char *expected="8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364";
    for(uint32_t i=0;i<count;i++) {
        const uint8_t *a=p+8+i*20; uint32_t off=be32(a+8),n=be32(a+12);
        if(n!=2705796||(uint64_t)off+n>data.length) continue;
        CC_SHA256(p+off,n,digest); char hex[65]; for(int j=0;j<32;j++) snprintf(hex+2*j,3,"%02x",digest[j]);
        if(!strcmp(hex,expected)) { fprintf(stderr,"GPU_LOAD_SURFACE_AIR sha256=%s bytes=%u\n",hex,n); return; }
    } fail("exact-guest-air-not-found");
}
#define LOAD(name) __typeof__(&name) p_##name=dlsym(lib,#name); if(!p_##name) fail("symbol-" #name)
int main(void) { @autoreleasepool {
    setvbuf(stderr,NULL,_IONBF,0); fprintf(stderr,"GPU_LOAD_SURFACE_START version=1 pid=%d\n",getpid());
    uint8_t digest[32]; checkAIR(digest);
    void *lib=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_LOCAL);
    if(!lib) fail("iokit-load");
    LOAD(IORegistryEntryFromPath); LOAD(IOObjectGetClass); LOAD(IOObjectRelease); LOAD(IOServiceOpen); LOAD(IOServiceClose); LOAD(IOConnectCallScalarMethod);
    scalar=p_IOConnectCallScalarMethod;
    const char *path="IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/ans@79600000/AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)/RTBuddyService/AppleANS3CGv2Controller/NS_06";
    io_service_t service=0; double until=now()+60;
    do { service=p_IORegistryEntryFromPath(0,path); if(!service) usleep(100000); } while(!service&&now()<until);
    if(!service) fail("namespace-not-found");
    char cls[128]={0}; if(p_IOObjectGetClass(service,cls)||strcmp(cls,"AppleNVMeNamespaceDevice")) fail("namespace-class");
    io_connect_t client=0; kern_return_t kr=p_IOServiceOpen(service,mach_task_self(),0,&client);
    p_IOObjectRelease(service); fprintf(stderr,"GPU_LOAD_SURFACE_OPEN kr=0x%x\n",kr); if(kr) fail("namespace-open");
    for(unsigned sel=2;sel<=3;sel++) { uint64_t v=0; uint32_t n=1; kr=scalar(client,sel,NULL,0,&v,&n); if(kr||n!=1||v!=(sel==2?Page:RawBytes/Page)) fail("namespace-capacity"); }
    uint8_t *header=NULL,*packet=NULL,*reply=NULL,*input=NULL,*output=NULL;
    if(posix_memalign((void **)&header,16384,Page)||posix_memalign((void **)&packet,16384,Page)||posix_memalign((void **)&reply,16384,Page)||posix_memalign((void **)&input,16384,Bytes)||posix_memalign((void **)&output,16384,Bytes)) fail("allocation");
    io(client,0,header,Page,0);
    if(memcmp(header,"DVM-SURFACE-DEMO-v1",19)||memcmp(header+64,digest,32)) fail("namespace-session-or-air");
    fprintf(stderr,"GPU_LOAD_SURFACE_HEADER verified=1\n");
    unsigned gpu=0,fallback=0;
    IOSurfaceRef surface=NULL;
    for(uint32_t seq=1;seq<=3;seq++) {
        if(!surface) {
        fprintf(stderr,"GPU_LOAD_SURFACE_ALLOC seq=%u\n",seq);
        surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(Width),(id)kIOSurfaceHeight:@(Height),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@(Row),(id)kIOSurfaceAllocSize:@(Bytes),(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        if(!surface||IOSurfaceGetBytesPerRow(surface)!=Row||IOSurfaceGetAllocSize(surface)<Bytes) fail("surface-create-or-geometry");
        fprintf(stderr,"GPU_LOAD_SURFACE_ALLOCATED seq=%u id=%u\n",seq,IOSurfaceGetID(surface));
        }
        uint32_t nonce=arc4random();
        for(unsigned i=0;i<Bytes;i++) input[i]=(uint8_t)(i*17+(i>>8)*31+seq*43+(nonce>>((i%4)*8)));
        if(IOSurfaceLock(surface,0,NULL)) fail("surface-lock");
        void *base=IOSurfaceGetBaseAddress(surface); if(!base) fail("surface-address"); memset(base,0xa5,Bytes);
        if(!memcmp(base,input,Bytes)) fail("negative-control");
        if(IOSurfaceUnlock(surface,0,NULL)) fail("surface-unlock");
        // CPU fallback is exercised and verified independently, never accepted as GPU success.
        double cpu=now(); memcpy(output,input,Bytes); cpu=now()-cpu;
        if(memcmp(output,input,Bytes)) fail("cpu-reference");
        memset(output,0xcc,Bytes); memset(packet,0,Page); memcpy(packet,header,64);
        uint32_t fields[]={seq,nonce,Bytes,crc32(input,Bytes)}; memcpy(packet+64,fields,sizeof(fields));
        uint32_t c=crc32(packet,Page-4); memcpy(packet+Page-4,&c,4);
        double start=now(); io(client,1,input,Bytes,0x100000); io(client,1,packet,Page,0x10000);
        fprintf(stderr,"GPU_LOAD_SURFACE_SUBMIT seq=%u nonce=%u\n",seq,nonce);
        BOOL completed=NO; unsigned polls=0;
        do {
            io(client,0,reply,Page,0x20000); polls++;
            uint32_t rc; memcpy(&rc,reply+Page-4,4);
            if(!memcmp(reply,packet,80)&&rc==crc32(reply,Page-4)) { completed=YES; break; }
            usleep(1000);
        } while(now()-start<30);
        double transport=now()-start;
        if(completed) {
            io(client,0,output,Bytes,0x200000);
            uint32_t expected; memcpy(&expected,reply+80,4);
            if(crc32(output,Bytes)!=expected||memcmp(output,input,Bytes)) fail("gpu-output-mismatch"); gpu++;
        } else { memcpy(output,input,Bytes); fallback++; }
        if(IOSurfaceLock(surface,0,NULL)) fail("destination-lock");
        memcpy(IOSurfaceGetBaseAddress(surface),output,Bytes);
        if(IOSurfaceUnlock(surface,0,NULL)||IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)) fail("destination-unlock-or-read-lock");
        BOOL equal=!memcmp(IOSurfaceGetBaseAddress(surface),input,Bytes);
        if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL)||!equal) fail("surface-oracle");
        fprintf(stderr,"GPU_LOAD_SURFACE_RUN seq=%u backend=%s id=%u bytes=%u verified=1 crc=%08x polls=%u mailbox_us=%llu total_us=%llu cpu_copy_us=%llu\n",seq,completed?"host-metal":"cpu-fallback",IOSurfaceGetID(surface),Bytes,crc32(output,Bytes),polls,(unsigned long long)(transport*1000000),(unsigned long long)((now()-start)*1000000),(unsigned long long)(cpu*1000000));
#ifdef DVM_SURFACE_RECREATE
        CFRelease(surface); surface=NULL;
        fprintf(stderr,"GPU_LOAD_SURFACE_RETIRED seq=%u\n",seq);
#endif
        if(!completed) break; // Dispose session after timeout; late replies cannot affect another surface.
    }
    if(surface) {
        fprintf(stderr,"GPU_LOAD_SURFACE_RETIRE_BEGIN id=%u\n",IOSurfaceGetID(surface));
        CFRelease(surface);
        fprintf(stderr,"GPU_LOAD_SURFACE_RETIRE_DONE\n");
    }
    p_IOServiceClose(client); free(header); free(packet); free(reply); free(input); free(output);
    if(gpu!=3||fallback) fail("gpu-demo-incomplete-fallback-preserved");
    fprintf(stderr,"GPU_LOAD_COMPLETE result=pass scope=guest-surface-host-metal submissions=3\n"); return 0;
}}
