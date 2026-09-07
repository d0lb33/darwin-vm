// Host contract test: native and forwarded large copied textures, no guest claim.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
static void check(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static NSArray *exercise(id<MTLDevice> device,NSUInteger w,NSUInteger h,NSUInteger fmt){
    NSMutableArray *outputs=[NSMutableArray array];
    @autoreleasepool{
        // The exact 2.39 MiB profile also exercises padded caller rows. Keep
        // the allocation-boundary case within the separate 4 MiB CPU span.
        NSUInteger bpp=DVMFormatBytes(fmt),row=w*bpp+(fmt==30?64:0);
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt width:w height:h mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
        id<MTLTexture> src=[device newTextureWithDescriptor:d],dst=[device newTextureWithDescriptor:d];
        check(src&&dst,"large sampled allocations");
        NSMutableData *source=[NSMutableData dataWithLength:row*h];uint8_t *bytes=source.mutableBytes;
        for(NSUInteger i=0;i<source.length;i++)bytes[i]=(uint8_t)(i%251);
        [src replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:bytes bytesPerRow:row];
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(unsigned frame=0;frame<3;frame++){@autoreleasepool{
            // A partial update before first submit and after completed GPU use.
            NSUInteger x=17+frame*13,y=29+frame*7;
            for(NSUInteger j=y;j<y+5;j++)memset(bytes+j*row+x*bpp,113+frame,19*bpp);
            [src replaceRegion:MTLRegionMake2D(x,y,19,5) mipmapLevel:0 withBytes:bytes+y*row+x*bpp bytesPerRow:row];
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLBlitCommandEncoder> e=[cb blitCommandEncoder];
            [e copyFromTexture:src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1) toTexture:dst destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
            [e endEncoding];[cb commit];[cb waitUntilCompleted];check(cb.status==MTLCommandBufferStatusCompleted,"large GPU copy completion");
            NSMutableData *read=[NSMutableData dataWithLength:row*h];memset(read.mutableBytes,0xa5,read.length);
            [dst getBytes:read.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
            for(NSUInteger j=0;j<h;j++){
                check(!memcmp((const uint8_t *)read.bytes+j*row,bytes+j*row,w*bpp),"large copied pixels and partial preservation");
                for(NSUInteger i=w*bpp;i<row;i++)check(((const uint8_t *)read.bytes)[j*row+i]==0xa5,"caller row padding preserved");
            }
            [outputs addObject:read];
        }}
    }
    return outputs;
}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    __block uint64_t seq=0;
    id<MTLDevice> forwarded=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **error){@synchronized(host){
        NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
        NSDictionary *reply=ProcessRequest(host,++seq,r);
        if(![reply[@"ok"] boolValue]){if(error)*error=[NSError errorWithDomain:@"LargeTextureTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply.description}];return nil;}return reply;
    }});
    for(NSArray *s in @[@[@1280,@932,@30],@[@1024,@1024,@80]]){
        NSUInteger w=[s[0] unsignedIntegerValue],h=[s[1] unsignedIntegerValue],fmt=[s[2] unsignedIntegerValue];
        NSArray *native=exercise(host.device,w,h,fmt),*proxy=exercise(forwarded,w,h,fmt);
        check([native isEqual:proxy],"native versus forwarded pixels");
        check(host.entries.count==0&&host.textureBytes==0,"completed resources retired");
    }
    puts("DVM_LARGE_TEXTURE_PASS profiles=2 frames=6 padded_rows=1 partial_updates=1 native_equal=1 gpu_completion=1 retirement=1");
}}
