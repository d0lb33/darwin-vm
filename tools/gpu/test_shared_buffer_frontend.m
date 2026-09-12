// Host rehearsal of the guest frontend over the same file-backed pool used by
// mode-3 MMIO.  This proves CPU->GPU and GPU->CPU coherence without upload or
// readback RPCs; exact-guest execution remains a separate acceptance check.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#include <sys/mman.h>
#include <unistd.h>
@interface NSObject (DVMSharedBufferTest)
- (void)setBufferPool:(uint8_t *)bytes;
- (void)setBufferPoolBytes:(NSUInteger)length;
- (NSDictionary *)consumerCompletion;
@end
static void require(BOOL ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
int main(void){@autoreleasepool {
    char path[]="/tmp/dvm-shared-buffer-XXXXXX";int fd=mkstemp(path);require(fd>=0,"temporary pool");
    require(!ftruncate(fd,DVM_SHARED_RAM_BYTES),"pool extent");
    uint8_t *ram=mmap(NULL,DVM_SHARED_RAM_BYTES,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);require(ram!=MAP_FAILED,"pool mapping");
    uint32_t magic=0x44564d31u;memcpy(ram,&magic,4);require(!setenv("DVM_DRIVER_PRESENT_RAM",path,1),"pool environment");
    DVMHost *host=[DVMHost new];host.bufferPoolFD=-1;host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    __block uint64_t seq=0;__block unsigned uploads=0,reads=0;
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *request,NSError **outError){@synchronized(host){
        if([request[@"op"] isEqual:@"writeRenderBuffer"])uploads++;
        if([request[@"op"] hasPrefix:@"readRenderBuffer"])reads++;
        NSDictionary *wire=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
        NSDictionary *reply=ProcessRequest(host,++seq,wire);
        if(![reply[@"ok"] boolValue]){if(outError)*outError=[NSError errorWithDomain:@"SharedBufferTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply.description}];return nil;}
        return reply;
    }};
    id<MTLDevice> device=DVMCreateMetalDevice(rpc);[(id)device setBufferPool:ram+DVM_BUFFER_POOL_OFFSET];[(id)device setBufferPoolBytes:DVM_BUFFER_POOL_BYTES];
    __block NSUInteger firstOffset=0;
    @autoreleasepool {
        id<MTLBuffer> buffer=[device newBufferWithLength:65536 options:MTLResourceStorageModeShared];
        require(buffer&&buffer.length==65536,"shared logical buffer allocation");
        firstOffset=(uint8_t *)buffer.contents-ram;require(firstOffset==DVM_BUFFER_POOL_OFFSET,"first host-assigned range");
        id<MTLBuffer> second=[device newBufferWithLength:262144 options:MTLResourceStorageModeShared];
        require(second&&second.length==262144,"second shared logical buffer allocation");
        NSUInteger secondOffset=(uint8_t *)second.contents-ram;
        require(secondOffset==firstOffset+65536,"simultaneous shared ranges do not overlap");
        memset(buffer.contents,0x21,buffer.length);
        memset(second.contents,0x37,second.length);
        for(NSUInteger i=0;i<buffer.length;i++)require(((uint8_t *)buffer.contents)[i]==0x21,"second allocation preserved first range");
        id<MTLCommandBuffer> command=[[device newCommandQueue] commandBuffer];id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
        [blit fillBuffer:buffer range:NSMakeRange(0,buffer.length) value:0x5a];[blit endEncoding];[command commit];[command waitUntilCompleted];
        require(command.status==MTLCommandBufferStatusCompleted,"shared GPU completion");
        for(NSUInteger i=0;i<buffer.length;i++)require(((uint8_t *)buffer.contents)[i]==0x5a,"GPU result visible in guest mapping");
        require(!uploads&&!reads,"no buffer transfer RPCs");
    }
    [(id)device consumerCompletion];
    @autoreleasepool {
        id<MTLBuffer> reused=[device newBufferWithLength:65536 options:0];
        require((NSUInteger)((uint8_t *)reused.contents-ram)==firstOffset,"released host range reused");
        for(NSUInteger i=0;i<reused.length;i++)require(!((uint8_t *)reused.contents)[i],"reused range zeroed");
    }
    [(id)device consumerCompletion];
    NSDictionary *stats=[(id)device consumerCompletion];require([stats[@"live"][@"sharedBufferPages"] unsignedIntegerValue]==0,"pool pages retired");
    unlink(path);munmap(ram,DVM_SHARED_RAM_BYTES);close(fd);
    puts("PASS shared buffer frontend: nonoverlapping host-assigned ranges, CPU/GPU coherence, zero transfer RPCs, completion, retirement and safe reuse");
}}
