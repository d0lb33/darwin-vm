// Real frontend/backend execution of programmable color-attachment blending.
// Test shader injection is local to this harness; guest AIR remains separate.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMFramebufferCompletion
- (NSDictionary *)consumerCompletion;
@end
@interface DVMNoFramebufferDevice : NSObject
@end
@implementation DVMNoFramebufferDevice
- (NSUInteger)minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)format{(void)format;return 64;}
- (BOOL)supportsFamily:(MTLGPUFamily)family{(void)family;return NO;}
@end
static void require(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();
    host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\n"
        "fragment float4 f(float4 previous[[color(0)]],constant uint4 &op[[buffer(0)]]){"
        "uint3 x=uint3(round(previous.rgb*255.0f));return float4(float3((x*op.x+op.yzw)%251u)/255.0f,1);}";
    id<MTLLibrary> native=[host.device newLibraryWithSource:source options:nil error:&error];
    require(native!=nil,error.description.UTF8String);
    __block uint64_t seq=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **outError){
        @synchronized(host){
            NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
            NSDictionary *reply;
            if([r[@"op"] isEqual:@"library"]){DVMEntry *entry=nil;require(Add(host,@"library",native,&entry),"fixture library");reply=@{@"ok":@YES,@"handle":@(entry.handle),@"functionNames":native.functionNames};}
            else reply=ProcessRequest(host,++seq,r);
            if(![reply[@"ok"] boolValue]){if(outError)*outError=[NSError errorWithDomain:@"FramebufferReadTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]?:@"RPC"}];return nil;}
            return [NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] options:0 error:nil];
        }
    });
    @autoreleasepool{
        id<MTLLibrary> library=[device newLibraryWithData:dispatch_data_create("fixture",7,NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT) error:&error];
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(unsigned formatIndex=0;formatIndex<2;formatIndex++){
            MTLPixelFormat format=formatIndex?MTLPixelFormatRGBA8Unorm:MTLPixelFormatBGRA8Unorm;
            MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:64 height:64 mipmapped:NO];
            td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;td.storageMode=MTLStorageModeShared;
            id<MTLTexture> texture=[device newTextureWithDescriptor:td];require(texture!=nil,"target");
            MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[library newFunctionWithName:@"v"];
            pd.fragmentFunction=[library newFunctionWithName:@"f"];pd.colorAttachments[0].pixelFormat=format;
            id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];
            require(pipeline!=nil,error.description.UTF8String);
            uint8_t expected[64*64][3]={0},actual[64*64*4];
            for(unsigned frame=0;frame<4;frame++){
                id<MTLCommandBuffer> command=[queue commandBuffer];
                for(unsigned passIndex=0;passIndex<2;passIndex++){
                    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;
                    pass.colorAttachments[0].loadAction=(!frame&&!passIndex)?MTLLoadActionClear:MTLLoadActionLoad;
                    pass.colorAttachments[0].storeAction=MTLStoreActionStore;
                    id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];[encoder setRenderPipelineState:pipeline];
                    for(unsigned draw=0;draw<8;draw++){
                        unsigned n=frame*16+passIndex*8+draw,x=(n*7)%32,y=(n*11)%32;
                        uint32_t op[]={1+n%3,3+n,5+n*2,7+n*3};
                        [encoder setScissorRect:(MTLScissorRect){x,y,64-x,64-y}];[encoder setFragmentBytes:op length:sizeof(op) atIndex:0];
                        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                        for(unsigned py=y;py<64;py++)for(unsigned px=x;px<64;px++)for(unsigned channel=0;channel<3;channel++)
                            expected[py*64+px][channel]=(expected[py*64+px][channel]*op[0]+op[channel+1])%251;
                    }
                    [encoder endEncoding];
                }
                [command commit];[command waitUntilCompleted];require(command.status==MTLCommandBufferStatusCompleted,command.error.description.UTF8String);
                [texture getBytes:actual bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
                for(unsigned pixel=0;pixel<4096;pixel++)for(unsigned channel=0;channel<3;channel++){
                    unsigned stored=formatIndex?channel:2-channel;
                    require(actual[pixel*4+stored]==expected[pixel][channel],"ordered color read/modify/write pixels");
                    require(actual[pixel*4+3]==255,"attachment alpha");
                }
                fprintf(stderr,"FRAMEBUFFER_READ_PASS format=%lu frame=%u pixels=4096 passes=2 draws=16\n",(unsigned long)format,frame);
            }
        }
    }
    NSDictionary *stats=[(id<DVMFramebufferCompletion>)device consumerCompletion];
    require([stats[@"live"][@"objects"] isEqual:@0]&&[stats[@"live"][@"resourceBytes"] isEqual:@0],"all forwarded resources retired");
    require([stats[@"renderDraws"] isEqual:@128]&&[stats[@"renderPasses"] isEqual:@16],"all planned work executed");
    DVMHost *unsupported=[DVMHost new];unsupported.device=(id)[DVMNoFramebufferDevice new];
    NSDictionary *refused=ProcessRequest(unsupported,1,@{@"op":@"capabilities"});
    require([refused[@"ok"] isEqual:@NO]&&[refused[@"description"] isEqual:@"host lacks programmable color-attachment blending"],"unsupported host cannot advertise framebuffer reads");
    fprintf(stderr,"PASS framebuffer read: BGRA/RGBA, ordered overlap, two passes, four reused frames, exact CPU pixels, completion and retirement\n");
}}
