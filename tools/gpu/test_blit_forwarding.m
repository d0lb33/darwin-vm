// Native-versus-forwarded host Metal comparison. No exact-guest claim.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMBlitCompletion
- (NSDictionary *)consumerCompletion;
@end
static void check(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static NSArray *scene(id<MTLDevice> device){
    NSMutableArray *outputs=[NSMutableArray array];
    @autoreleasepool {
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(NSNumber *format in @[@70,@80,@115,@10,@30,@554]){
            NSUInteger bpp=DVMFormatBytes(format.unsignedIntegerValue);
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format.unsignedIntegerValue width:17 height:13 mipmapped:NO];
            d.storageMode=MTLStorageModeShared;d.usage=5;
            id<MTLTexture> src=[device newTextureWithDescriptor:d];
            d.storageMode=MTLStorageModePrivate;d.mipmapLevelCount=5;
            id<MTLTexture> a=[device newTextureWithDescriptor:d],b=[device newTextureWithDescriptor:d];
            d.storageMode=MTLStorageModeShared;d.mipmapLevelCount=1;d.width=8;d.height=6;
            id<MTLTexture> out=[device newTextureWithDescriptor:d];
            id<MTLBuffer> buf=[device newBufferWithLength:128 options:0],dst=[device newBufferWithLength:128 options:0];
            check(src&&a&&b&&out&&buf&&dst,"blit allocations");
            for(unsigned frame=0;frame<8;frame++){
                NSMutableData *data=[NSMutableData dataWithLength:17*13*bpp];
                for(unsigned i=0;i<17*13*(bpp==8?4:bpp);i++){
                    if(bpp==8)((_Float16 *)data.mutableBytes)[i]=(_Float16)(((i*7+frame*11)%67)/16.0f-2.0f);
                    else ((uint8_t *)data.mutableBytes)[i]=(i*7+frame*11)%251;
                }
                [src replaceRegion:MTLRegionMake2D(0,0,17,13) mipmapLevel:0 withBytes:data.bytes bytesPerRow:17*bpp];
                memset(buf.contents,frame+1,128);memset(dst.contents,0x91,128);
                id<MTLCommandBuffer> command=[queue commandBuffer];
                if(!(frame%2)){
                    MTLRenderPassDescriptor *p=[MTLRenderPassDescriptor renderPassDescriptor];
                    p.colorAttachments[0].texture=src;p.colorAttachments[0].loadAction=MTLLoadActionClear;p.colorAttachments[0].storeAction=MTLStoreActionStore;
                    p.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
                    [[command renderCommandEncoderWithDescriptor:p] endEncoding];
                }
                id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
                [blit fillBuffer:buf range:NSMakeRange(16,32) value:0x37+frame];
                [blit copyFromBuffer:buf sourceOffset:0 toBuffer:dst destinationOffset:16 size:64];
                [blit copyFromTexture:src sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(17,13,1) toTexture:a destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
                [blit copyFromTexture:a sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(8,6,1) toTexture:a destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(9,7,0)];
                [blit generateMipmapsForTexture:a];
                [blit copyFromTexture:a sourceSlice:0 sourceLevel:1 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(8,6,1) toTexture:b destinationSlice:0 destinationLevel:1 destinationOrigin:MTLOriginMake(0,0,0)];
                [blit copyFromTexture:b sourceSlice:0 sourceLevel:1 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(8,6,1) toTexture:out destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
                [blit endEncoding];[command commit];[command waitUntilCompleted];
                check(command.status==MTLCommandBufferStatusCompleted,command.error.description.UTF8String);
                for(unsigned i=0;i<128;i++){
                    uint8_t expected=i>=16&&i<80?(i>=32&&i<64?0x37+frame:frame+1):0x91;
                    check(((uint8_t *)dst.contents)[i]==expected,"GPU buffer writeback before completion");
                }
                NSMutableData *pixels=[NSMutableData dataWithLength:8*6*bpp];
                [out getBytes:pixels.mutableBytes bytesPerRow:8*bpp fromRegion:MTLRegionMake2D(0,0,8,6) mipmapLevel:0];
                if(!(frame%2)&&format.unsignedIntegerValue!=554)for(unsigned i=0;i<8*6*(bpp==8?4:bpp);i++){
                    unsigned channel=i%(bpp==8?4:bpp);BOOL red=channel==3||channel==(format.unsignedIntegerValue==80?2:0);
                    check(bpp==8?((_Float16 *)pixels.bytes)[i]==(red?1:0):((uint8_t *)pixels.bytes)[i]==(red?255:0),"render-before-blit red pixel oracle");
                }
                [outputs addObject:pixels];[outputs addObject:[NSData dataWithBytes:dst.contents length:128]];
            }
        }
    }
    return outputs;
}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    __block uint64_t seq=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **error){
        @synchronized(host){
            NSData *encoded=[NSJSONSerialization dataWithJSONObject:request options:0 error:nil];
            NSDictionary *r=[NSJSONSerialization JSONObjectWithData:encoded options:0 error:nil];
            NSDictionary *reply=ProcessRequest(host,++seq,r);
            if(![reply[@"ok"] boolValue]){if(error)*error=[NSError errorWithDomain:@"BlitTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply.description}];return nil;}
            return [NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] options:0 error:nil];
        }
    });
    NSArray *native=scene(host.device),*forwarded=scene(device);check([native isEqual:forwarded],"native/forwarded exact mip and buffer bytes");
    NSDictionary *stats=[(id<DVMBlitCompletion>)device consumerCompletion];
    check([stats[@"renderPasses"] isEqual:@24]&&[stats[@"blitPasses"] isEqual:@48],"render and blit counts separated");
    check([stats[@"live"][@"objects"] isEqual:@0],"blit resource retirement");
    NSDictionary *allocation=ProcessRequest(host,++seq,@{@"op":@"buffer",@"length":@128});NSNumber *buffer=allocation[@"handle"];
    NSDictionary *bad=ProcessRequest(host,++seq,@{@"op":@"renderSubmit",@"uploads":@[],@"readbacks":@[],@"commands":@[@{@"kind":@"blit",@"operations":@[@[@"fillBuffer",buffer,@0,@128,@99],@[@"copyTexture",@999,@0,@[@0,@0],@[@1,@1],@998,@0,@[@0,@0]]]}]});
    check(![bad[@"ok"] boolValue],"bad later blit rejected");
    NSDictionary *read=ProcessRequest(host,++seq,@{@"op":@"read",@"buffer":buffer});NSData *bytes=[[NSData alloc] initWithBase64EncodedString:read[@"data"] options:0];
    check([bytes isEqual:[NSData dataWithBytes:(uint8_t[128]){0} length:128]],"invalid batch did not execute earlier fill");
    puts("DVM_BLIT_PASS native_equal=96 outputs formats=RGBA8,BGRA8,RGBA16F,R8,RG8,BGR10_XR frames=48 render_passes=24 blit_passes=48 writeback=verified retirement=verified atomic_rejection=verified");
}}
