#import <Foundation/Foundation.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <time.h>
#include "present_host.h"
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
int main(int argc,char **argv){@autoreleasepool{
    if(argc!=3)return 2;double start=now();
    int fd=open(argv[2],O_CREAT|O_EXCL|O_RDWR,0600);if(fd<0||ftruncate(fd,0x1000000))return 3;
    void *memory=mmap(NULL,0x1000000,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);if(memory==MAP_FAILED)return 4;
    NSError *error=nil;id<MTLDevice>device=MTLCreateSystemDefaultDevice();id<MTLLibrary>lib=[device newLibraryWithURL:[NSURL fileURLWithPath:@(argv[1])] error:&error];
    DVMResidentBlur *blur=lib?[[DVMResidentBlur alloc]initWithDevice:device library:lib destination:(uint8_t *)memory+DVM_PRESENT_OFFSET nonce:0x12345678]:nil;if(!blur)return 5;
    printf("SETUP us=%.0f device=%s\n",(now()-start)*1e6,device.name.UTF8String);
    struct {double total,gpu;} samples[33];double steady=0,batch=now();
    for(uint32_t frame=1;frame<=33;frame++){@autoreleasepool{
        if(frame==2)steady=now();double before=now();id<MTLCommandBuffer>cb=[blur encodeFrame:frame];[cb commit];[cb waitUntilCompleted];double done=now();if(cb.status!=MTLCommandBufferStatusCompleted)return 6;
        samples[frame-1].total=(done-before)*1e6;samples[frame-1].gpu=(cb.GPUEndTime-cb.GPUStartTime)*1e6;
    }}
    double finished=now();
    printf("BATCH frames=33 wall_us=%.0f steady_wall_us=%.0f verification_reads=0\n",(finished-batch)*1e6,(finished-steady)*1e6);
    for(unsigned i=0;i<33;i++)printf("FRAME frame=%u total_us=%.0f gpu_us=%.3f\n",i+1,samples[i].total,samples[i].gpu);
    unsigned bad=DVMVerifyPresented((uint8_t *)memory+DVM_PRESENT_OFFSET,0x12345678,33);printf("FINAL bad_pixels=%u\n",bad);
    blur=nil;munmap(memory,0x1000000);close(fd);return bad?7:0;
}}
