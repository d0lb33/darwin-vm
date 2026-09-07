// Forwarded private block-write target -> shader read -> shared output.
// Harness-authored arithmetic checks storage/synchronization, not guest AIR.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMFramebufferCompletion
- (NSDictionary *)consumerCompletion;
@end
static void require(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();
    host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\n"
        "fragment float4 paint(float4 p[[position]],constant uint &n[[buffer(0)]]){uint2 q=uint2(p.xy);"
        "return float4(float3((q.x+n)%251u,(q.y+3*n)%251u,(q.x+q.y+7*n)%251u)/255.0f,1);}\n"
        "fragment float4 copyPixel(float4 p[[position]],texture2d<float,access::read> t[[texture(0)]]){return t.read(uint2(p.xy));}";
    id<MTLLibrary> native=[host.device newLibraryWithSource:source options:nil error:&error];
    require(native!=nil,error.description.UTF8String);
    __block uint64_t seq=0;__block unsigned staged=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **outError){
        @synchronized(host){
            NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
            require([NSJSONSerialization dataWithJSONObject:r options:0 error:nil].length<65536,"each transport message fits MMIO");
            if([r[@"op"] isEqual:@"renderStageBegin"])staged++;
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
        for(NSNumber *formatValue in @[@70,@80]){
            MTLPixelFormat format=formatValue.unsignedIntegerValue;
            MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:67 height:39 mipmapped:NO];
            td.storageMode=MTLStorageModePrivate;td.usage=65541;
            id<MTLTexture> intermediate=[device newTextureWithDescriptor:td];require(intermediate!=nil,"private allocation");
            require(intermediate.usage==65541&&intermediate.storageMode==MTLStorageModePrivate,"private metadata");
            td.storageMode=MTLStorageModeShared;td.usage=5;
            id<MTLTexture> target=[device newTextureWithDescriptor:td];require(target!=nil,"output allocation");
            NSMutableArray *pipelines=[NSMutableArray array];
            for(NSString *name in @[@"paint",@"copyPixel"]){
                MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
                pd.vertexFunction=[library newFunctionWithName:@"v"];pd.fragmentFunction=[library newFunctionWithName:name];
                pd.colorAttachments[0].pixelFormat=format;
                id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];
                require(pipeline!=nil,error.description.UTF8String);[pipelines addObject:pipeline];
            }
            for(uint32_t frame=0;frame<8;frame++){
                id<MTLCommandBuffer> command=[queue commandBuffer];
                for(unsigned passIndex=0;passIndex<2;passIndex++){
                    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
                    pass.colorAttachments[0].texture=passIndex?target:intermediate;
                    pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
                    id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pass];
                    [encoder setRenderPipelineState:pipelines[passIndex]];
                    if(!frame&&!passIndex){
                        uint8_t padding[512]={0};
                        for(unsigned repeat=0;repeat<128;repeat++){
                            padding[0]=(uint8_t)repeat;
                            [encoder setFragmentBytes:padding length:sizeof(padding) atIndex:30];
                        }
                    }
                    if(passIndex)[encoder setFragmentTexture:intermediate atIndex:0];
                    else [encoder setFragmentBytes:&frame length:sizeof(frame) atIndex:0];
                    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[encoder endEncoding];
                }
                [command commit];[command waitUntilCompleted];require(command.status==MTLCommandBufferStatusCompleted,command.error.description.UTF8String);
                uint8_t actual[67*39*4];[target getBytes:actual bytesPerRow:67*4 fromRegion:MTLRegionMake2D(0,0,67,39) mipmapLevel:0];
                for(unsigned y=0;y<39;y++)for(unsigned x=0;x<67;x++){
                    uint8_t rgba[]={(x+frame)%251,(y+3*frame)%251,(x+y+7*frame)%251,255};
                    for(unsigned c=0;c<4;c++)require(actual[(y*67+x)*4+c]==rgba[(format==80&&c<3)?2-c:c],"render then read exact pixels");
                }
                fprintf(stderr,"BLOCK_WRITE_PASS format=%lu frame=%u pixels=2613 passes=2\n",(unsigned long)format,frame);
            }
        }
    }
    NSDictionary *stats=[(id<DVMFramebufferCompletion>)device consumerCompletion];
    require([stats[@"live"][@"objects"] isEqual:@0]&&[stats[@"live"][@"resourceBytes"] isEqual:@0],"all resources retired");
    require([stats[@"renderDraws"] isEqual:@32]&&[stats[@"renderPasses"] isEqual:@32],"all planned work executed");
    require(staged==2,"two oversized frontend batches staged without changing GPU pass counts");
    fprintf(stderr,"PASS private block writes: BGRA/RGBA, eight reused frames each, exact pixels, completion and retirement\n");
}}
