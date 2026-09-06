/* Host-only prerequisite: can Metal access one virtual buffer assembled from
 * noncontiguous file-backed guest-sized pages? This is not guest GPU proof. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

int main(int argc,const char **argv) { @autoreleasepool {
    if(argc!=2)return 2;
    const size_t page=16384,pages=759,length=page*pages;
    int fd=open(argv[1],O_RDWR|O_CREAT|O_EXCL|O_NOFOLLOW,0600);
    if(fd<0||ftruncate(fd,length))return 3;
    uint8_t *original=mmap(NULL,length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    uint8_t *alias=mmap(NULL,length,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(original==MAP_FAILED||alias==MAP_FAILED)return 4;
    memset(original,0x39,length);
    for(size_t i=0;i<pages;i++) {
        size_t source=pages-1-i;
        if(mmap(alias+i*page,page,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,fd,source*page)!=alias+i*page)return 5;
    }
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    id<MTLBuffer> buffer=[device newBufferWithBytesNoCopy:alias length:length options:MTLResourceStorageModeShared deallocator:nil];
    fprintf(stderr,"MANAGED_MAPPING buffer=%d bytes=%zu pages=%zu device=%s\n",buffer!=nil,length,pages,device.name.UTF8String);
    if(!buffer)return 6;
    id<MTLCommandQueue> queue=[device newCommandQueue];
    id<MTLCommandBuffer> command=[queue commandBuffer];
    id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
    for(size_t i=0;i<pages;i++)[blit fillBuffer:buffer range:NSMakeRange(i*page,page) value:(uint8_t)(i%251)];
    [blit endEncoding];[command commit];[command waitUntilCompleted];
    if(command.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"%s\n",command.error.description.UTF8String);return 7;}
    for(size_t i=0;i<pages;i++)for(size_t j=0;j<page;j++)
        if(original[(pages-1-i)*page+j]!=(uint8_t)(i%251))return 8;
    fprintf(stderr,"MANAGED_MAPPING pass=1 verified_bytes=%zu gpu_us=%.3f scope=host-scattered-file-backing-only\n",length,(command.GPUEndTime-command.GPUStartTime)*1e6);
    command=nil;buffer=nil;queue=nil;
    munmap(alias,length);munmap(original,length);close(fd);return 0;
}}
