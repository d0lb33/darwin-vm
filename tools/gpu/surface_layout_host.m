// Host-only contract probe for the exact guest's observed RGhA IOSurface
// layout. No guest mapping, QuartzCore shader or compositor submission claim.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <sys/mman.h>
#include <unistd.h>
#include <time.h>
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void require(BOOL b,NSString *s){if(!b){fprintf(stderr,"FAIL %s\n",s.UTF8String);exit(1);}}
int main(int argc,const char **argv){@autoreleasepool{
    require(argc==2,@"usage: surface_layout_host pin-verification.json");
    NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:@(argv[1])] options:0 error:nil];
    NSDictionary *m=r[@"metadata"];NSUInteger width=[m[@"width"] integerValue],height=[m[@"height"] integerValue],row=[m[@"row"] integerValue],length=[m[@"bytes"] integerValue];
    require([r[@"verified"] boolValue]&&[m[@"format"] isEqual:@"52476841"]&&[m[@"element"] integerValue]==8&&[m[@"planes"] integerValue]==0&&[m[@"base_offset"] integerValue]==0,@"requires measured nonplanar RGhA metadata");
    require(width&&height&&width<=4096&&height<=4096&&row>=width*8&&row<=65536&&length>=height*row&&length<=64*1024*1024,@"range bounds");
    double start=now();NSUInteger page=16384,count=(length+page-1)/page,span=count*page;
    require((NSUInteger)getpagesize()==page,@"host mapping page size");
    char name[]="/tmp/dvm-surface-layout-XXXXXX";int fd=mkstemp(name);require(fd>=0,@"owned backing file");unlink(name);
    require(!ftruncate(fd,span),@"backing extent");
    uint8_t *file=mmap(NULL,span,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    uint8_t *alias=mmap(NULL,span,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);
    require(file!=MAP_FAILED&&alias!=MAP_FAILED,@"VA reservation");memset(file,0xa5,span);
    // Reverse physical page order. Native buffer bytes remain logically
    // contiguous, as they must for a descriptor-derived guest page export.
    for(NSUInteger i=0;i<count;i++)require(mmap(alias+i*page,page,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,fd,(count-1-i)*page)==alias+i*page,@"page alias");
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();require(device!=nil,@"native device");
    NSUInteger alignment=[device minimumLinearTextureAlignmentForPixelFormat:MTLPixelFormatRGBA16Float];
    require(alignment&&row%alignment==0,@"native linear texture row alignment");
    id<MTLBuffer> buffer=[device newBufferWithBytesNoCopy:alias length:span options:MTLResourceStorageModeShared deallocator:nil];
    require(buffer&&buffer.contents==alias,@"no-copy buffer identity");
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:width height:height mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
    id<MTLTexture> texture=[buffer newTextureWithDescriptor:d offset:0 bytesPerRow:row];
    require(texture!=nil,@"native buffer-backed RGBA16Float texture");
    id<MTLCommandQueue> queue=[device newCommandQueue];require(queue!=nil,@"queue");
    double setup=now()-start;start=now();
    for(unsigned frame=0;frame<8;frame++){@autoreleasepool{
        MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
        pass.colorAttachments[0].texture=texture;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        pass.colorAttachments[0].clearColor=MTLClearColorMake(frame==7?.25:0,-.5,2,1);
        id<MTLCommandBuffer> command=[queue commandBuffer];
        id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];require(encoder!=nil,@"encoder");
        [encoder endEncoding];dispatch_semaphore_t done=dispatch_semaphore_create(0);
        [command addCompletedHandler:^(id<MTLCommandBuffer> c){(void)c;dispatch_semaphore_signal(done);}];
        [command commit];require(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0,@"GPU completion deadline");
        require(command.status==MTLCommandBufferStatusCompleted,command.error.description?:@"completion");
    }}
    double batch=now()-start;uint16_t expected[4]={0x3400,0xb800,0x4000,0x3c00};
    // Check via the original file mapping, NOT Metal readback or the pointer
    // used to create the buffer. Only the final batch output is inspected.
    for(NSUInteger y=0;y<height;y++)for(NSUInteger x=0;x<width;x++)for(NSUInteger c=0;c<8;c++){
        NSUInteger at=y*row+x*8+c,physical=(count-1-at/page)*page+at%page;
        require(file[physical]==((uint8_t *)expected)[c],@"file alias exact half-float pixel");
    }
    for(NSUInteger at=length;at<span;at++)require(file[(count-1-at/page)*page+at%page]==0xa5,@"bytes outside measured allocation unchanged");
    texture=nil;buffer=nil;queue=nil;require(!munmap(alias,span)&&!munmap(file,span),@"retire aliases");close(fd);
    printf("PASS scope=host-only-native-layout frames=8 pixels=%lu row=%lu allocation=%lu mapped=%lu pages=%lu alignment=%lu setup_ms=%.3f batch_ms=%.3f guest_import=unproven\n",(unsigned long)(width*height),(unsigned long)row,(unsigned long)length,(unsigned long)span,(unsigned long)count,(unsigned long)alignment,setup*1000,batch*1000);
}}
