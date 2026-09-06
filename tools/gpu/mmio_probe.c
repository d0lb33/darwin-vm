/* Guest-native mapping/doorbell experiment. No NVMe calls or debugger. */
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <mach/mach_time.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "darwin_gpu_transport.h"

#define LOAD(h,n) __typeof__(&n) p_##n=dlsym(h,#n); if(!p_##n){fprintf(stderr,"GPU_LOAD_ERROR mmio-symbol=%s\n",#n);return 4;}
static void fail(const char *stage,kern_return_t kr) {
    fprintf(stderr,"GPU_LOAD_ERROR mmio=%s kr=0x%x\n",stage,kr);exit(1);
}
static uint32_t crc(const uint8_t *p,size_t n) {
    uint32_t c=~0u;
    while(n--) {c^=*p++;for(int i=0;i<8;i++)c=(c>>1)^((0u-(c&1))&0xedb88320u);}
    return ~c;
}
static void barrier(void) {__asm__ volatile("dsb sy" ::: "memory");}
static uint64_t read_reg(volatile uint8_t *p,unsigned offset) {return *(volatile uint64_t *)(p+offset);}

int main(void) {
    setvbuf(stderr,NULL,_IONBF,0);
    fprintf(stderr,"GPU_LOAD_MMIO_START version=1 pid=%d\n",getpid());
    mach_timebase_info_data_t timebase;mach_timebase_info(&timebase);
    void *io=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_LOCAL);
    if(!io)fail("dlopen",0);
    LOAD(io,IORegistryGetRootEntry);LOAD(io,IORegistryEntryCreateIterator);LOAD(io,IOIteratorNext);
    LOAD(io,IORegistryEntryGetName);LOAD(io,IORegistryEntryGetPath);LOAD(io,IOObjectGetClass);
    LOAD(io,IOObjectRelease);LOAD(io,IOServiceOpen);LOAD(io,IOServiceClose);
    LOAD(io,IOConnectMapMemory64);LOAD(io,IOConnectUnmapMemory64);
    io_registry_entry_t root=p_IORegistryGetRootEntry(0),service=0;
    io_iterator_t it=0;
    kern_return_t kr=p_IORegistryEntryCreateIterator(root,"IOService",kIORegistryIterateRecursively,&it);
    p_IOObjectRelease(root);if(kr)fail("iterator",kr);
    io_registry_entry_t entry;unsigned count=0,matches=0;
    while((entry=p_IOIteratorNext(it))) {
        char name[128]={0},class_name[128]={0},path[512]={0};
        p_IORegistryEntryGetName(entry,name);
        if(!strcmp(name,"dvm-transport")) {
            matches++;p_IOObjectGetClass(entry,class_name);p_IORegistryEntryGetPath(entry,"IOService",path);
            fprintf(stderr,"GPU_LOAD_MMIO_SERVICE class=%s path=%s\n",class_name,path);
            if(strcmp(class_name,"AppleARMIODevice"))fail("unexpected-provider-class",0);
            if(service)fail("duplicate-service",0);
            service=entry;
        } else p_IOObjectRelease(entry);
        if(++count>4096)fail("registry-bound",0);
    }
    p_IOObjectRelease(it);
    fprintf(stderr,"GPU_LOAD_MMIO_DISCOVERY entries=%u matches=%u\n",count,matches);
    if(!service)fail("service-missing",0);
    io_connect_t wrong=0;
    kr=p_IOServiceOpen(service,mach_task_self(),0,&wrong);
    fprintf(stderr,"GPU_LOAD_MMIO_WRONG_OPEN kr=0x%x\n",kr);
    if(!kr){p_IOServiceClose(wrong);fail("ungated-open",0);}
    uint64_t previous=0;
    for(unsigned cycle=0;cycle<2;cycle++) {
        io_connect_t client=0;
        fprintf(stderr,"GPU_LOAD_MMIO_OPEN_BEGIN cycle=%u\n",cycle);
        kr=p_IOServiceOpen(service,mach_task_self(),DVM_GPU_OPEN_TYPE,&client);
        fprintf(stderr,"GPU_LOAD_MMIO_OPEN cycle=%u kr=0x%x\n",cycle,kr);
        if(kr)fail("open",kr);
        mach_vm_address_t ram=0,regs=0;mach_vm_size_t ram_n=0,regs_n=0;
        fprintf(stderr,"GPU_LOAD_MMIO_MAP_BEGIN cycle=%u\n",cycle);
        kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE,mach_task_self(),&ram,&ram_n,kIOMapAnywhere);
        fprintf(stderr,"GPU_LOAD_MMIO_MAP_RAM cycle=%u kr=0x%x bytes=%llu\n",cycle,kr,ram_n);
        if(kr||ram_n!=DVM_GPU_RAM_SIZE)fail("map-ram",kr);
        kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE+1,mach_task_self(),&regs,&regs_n,kIOMapAnywhere);
        fprintf(stderr,"GPU_LOAD_MMIO_MAP_REGS cycle=%u kr=0x%x bytes=%llu\n",cycle,kr,regs_n);
        if(kr||regs_n!=DVM_GPU_REG_SIZE)fail("map-registers",kr);
        mach_vm_address_t extra=0;mach_vm_size_t extra_n=0;
        kr=p_IOConnectMapMemory64(client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),&extra,&extra_n,kIOMapAnywhere);
        fprintf(stderr,"GPU_LOAD_MMIO_WRONG_MAP kr=0x%x bytes=%llu\n",kr,extra_n);
        if(!kr){p_IOConnectUnmapMemory64(client,DVM_GPU_MEMORY_TYPE+2,mach_task_self(),extra);fail("unbounded-mapping",0);}
        uint8_t *shared=(uint8_t *)ram;volatile uint8_t *mmio=(volatile uint8_t *)regs;
        barrier();
        uint32_t magic;memcpy(&magic,shared,4);
        if(magic!=DVM_GPU_MAGIC || read_reg(mmio,0)!=DVM_GPU_MAGIC ||
           read_reg(mmio,8)!=DVM_GPU_RAM_SIZE || read_reg(mmio,DVM_GPU_REG_MODE)!=1 ||
           !read_reg(mmio,DVM_GPU_REG_READY) || read_reg(mmio,DVM_GPU_REG_DONE)!=previous)
            fail("mapped-identity",0);
        uint8_t identity[16];memcpy(identity,shared+16,16);
        const unsigned sizes[]={16,4096,65536};
        for(unsigned trial=0;trial<3;trial++) {
            unsigned n=sizes[trial];uint64_t seq=previous+1;
            uint8_t *data=shared+DVM_GPU_REQUEST_DATA;
            for(unsigned i=0;i<n;i++)data[i]=(uint8_t)(seq*17+i*13+(i>>8));
            uint8_t *h=shared+DVM_GPU_REQUEST_HEADER;
            memcpy(h,identity,16);memcpy(h+16,&seq,8);memcpy(h+24,&n,4);
            uint32_t checksum=crc(data,n);memcpy(h+28,&checksum,4);
            uint64_t started=mach_absolute_time();barrier();
            *(volatile uint64_t *)(mmio+DVM_GPU_REG_DOORBELL)=seq;
            unsigned polls=0;
            while(read_reg(mmio,DVM_GPU_REG_DONE)!=seq) {
                polls++;
                if(read_reg(mmio,DVM_GPU_REG_ERROR))fail("device-rejected",(kern_return_t)read_reg(mmio,DVM_GPU_REG_ERROR));
                if((mach_absolute_time()-started)*timebase.numer/timebase.denom>2000000000ULL)fail("completion-timeout",0);
            }
            barrier();uint64_t elapsed=mach_absolute_time()-started;
            h=shared+DVM_GPU_REPLY_HEADER;uint64_t returned;unsigned returned_n;uint32_t returned_crc;
            memcpy(&returned,h+16,8);memcpy(&returned_n,h+24,4);memcpy(&returned_crc,h+28,4);
            if(memcmp(h,identity,16)||returned!=seq||returned_n!=n||crc(shared+DVM_GPU_REPLY_DATA,n)!=returned_crc)fail("reply-header",0);
            for(unsigned i=0;i<n;i++)if(shared[DVM_GPU_REPLY_DATA+i]!=(uint8_t)(data[i]^0xa5))fail("reply-bytes",0);
            previous=seq;
            fprintf(stderr,"GPU_LOAD_MMIO_ECHO seq=%llu bytes=%u verified=1 doorbell_ns=%llu polls=%u\n",seq,n,elapsed*timebase.numer/timebase.denom,polls);
        }
        kr=p_IOConnectUnmapMemory64(client,DVM_GPU_MEMORY_TYPE+1,mach_task_self(),regs);if(kr)fail("unmap-regs",kr);
        kr=p_IOConnectUnmapMemory64(client,DVM_GPU_MEMORY_TYPE,mach_task_self(),ram);if(kr)fail("unmap-ram",kr);
        kr=p_IOServiceClose(client);
        fprintf(stderr,"GPU_LOAD_MMIO_CLOSE cycle=%u kr=0x%x\n",cycle,kr);if(kr)fail("close",kr);
    }
    p_IOObjectRelease(service);
    fprintf(stderr,"GPU_LOAD_COMPLETE result=pass scope=owned-shared-ram-mmio-echo commands=6 reopen=1\n");
    return 0;
}
