// Forwarded nested mip views: render alias -> sample alias -> shared output.
// Harness-authored arithmetic checks storage/synchronization, not guest AIR.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMFramebufferCompletion
- (NSDictionary *)consumerCompletion;
@end
static void require(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static void sharedAliases(id<MTLDevice> device){@autoreleasepool{
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:16 height:16 mipmapped:NO];td.storageMode=MTLStorageModeShared;td.usage=5;
    id<MTLTexture> root=[device newTextureWithDescriptor:td],view=[root newTextureViewWithPixelFormat:td.pixelFormat],child=[view newTextureViewWithPixelFormat:td.pixelFormat];
    require(root&&view&&child,"shared nested aliases");
    require(![root newTextureViewWithPixelFormat:MTLPixelFormatRGBA8Unorm],"reinterpretation rejected");
    require(![root newTextureViewWithPixelFormat:td.pixelFormat textureType:MTLTextureType2D levels:NSMakeRange(NSUIntegerMax,1) slices:NSMakeRange(0,1)],"overflow level rejected");
    uint32_t pixels[256];for(unsigned i=0;i<256;i++)pixels[i]=0xff0000ff;
    [child replaceRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0 withBytes:pixels bytesPerRow:64];
    [root getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
    for(unsigned i=0;i<256;i++)require(pixels[i]==0xff0000ff,"view CPU writes visible through parent");
    id<MTLCommandBuffer> cb=[[device newCommandQueue] commandBuffer];MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=child;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
    id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:pass];[e endEncoding];[cb commit];[cb waitUntilCompleted];require(cb.status==MTLCommandBufferStatusCompleted,"view clear completion");
    uint32_t green=0xff00ff00;[view replaceRegion:MTLRegionMake2D(3,2,1,1) mipmapLevel:0 withBytes:&green bytesPerRow:4];
    [child getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
    for(unsigned i=0;i<256;i++)require(pixels[i]==(i==35?green:0xffff0000),"partial view update preserves completed GPU writes");
    require([child setPurgeableState:MTLPurgeableStateVolatile]==MTLPurgeableStateNonVolatile,"native view hint returns prior nonvolatile state");
    MTLPurgeableState rootState=[root setPurgeableState:MTLPurgeableStateKeepCurrent],childState=[child setPurgeableState:MTLPurgeableStateKeepCurrent];require(rootState==childState,"parent and nested view report the same native residency");
    MTLPurgeableState previous=[root setPurgeableState:MTLPurgeableStateNonVolatile];require(previous==rootState||previous==MTLPurgeableStateEmpty,"parent reacquires reported native residency");
    require([child setPurgeableState:MTLPurgeableStateKeepCurrent]==MTLPurgeableStateNonVolatile,"nested view sees completed reacquisition");
    fprintf(stderr,"SHARED_VIEW_PASS cpu_alias=1 gpu_write_preserved=1 parent_residency=1\n");
}}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();
    host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\n"
        "fragment float4 paint(float4 p[[position]],constant uint2 &op[[buffer(0)]]){uint n=op.x;uint2 q=uint2(p.xy);"
        "float3 rgb=float3((q.x+n)%251u,(q.y+3*n)%251u,(q.x+q.y+7*n)%251u);return float4(op.y?(rgb-128.0f)/16.0f:rgb/255.0f,1);}\n"
        "fragment float4 copyPixel(float4 p[[position]],texture2d<float,access::read> t[[texture(0)]],constant uint4 &opts[[buffer(0)]]){uint lod=opts.y;uint2 extent=uint2(t.get_width(lod),t.get_height(lod));float4 c=t.read((uint2(p.xy)+opts.zw)%extent,lod);if(opts.x)c.rgb=(c.rgb*16.0f+128.0f)/255.0f;return c;}";
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
            if([r[@"op"] isEqual:@"resourcePurgeable"])fprintf(stderr,"VIEW_PURGE %s\n",reply.description.UTF8String);
            if(![reply[@"ok"] boolValue]){if(outError)*outError=[NSError errorWithDomain:@"FramebufferReadTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]?:@"RPC"}];return nil;}
            return [NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] options:0 error:nil];
        }
    });
    @autoreleasepool{
        id<MTLLibrary> library=[device newLibraryWithData:dispatch_data_create("fixture",7,NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT) error:&error];
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(NSNumber *formatValue in @[@70,@80,@115]){
            MTLPixelFormat format=formatValue.unsignedIntegerValue;
            MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:67 height:39 mipmapped:NO];
            if(format==115){td.width=1216;td.height=2560;}
            td.storageMode=MTLStorageModePrivate;td.usage=65541;td.mipmapLevelCount=4;
            id<MTLTexture> intermediate=[device newTextureWithDescriptor:td];require(intermediate!=nil,"private allocation");
            id<MTLTexture> parent=[intermediate newTextureViewWithPixelFormat:format];require(parent!=nil,"whole mip view");
            NSMutableArray *views=[NSMutableArray array];for(unsigned level=0;level<4;level++){
                id<MTLTexture> view=[parent newTextureViewWithPixelFormat:format textureType:MTLTextureType2D levels:NSMakeRange(level,1) slices:NSMakeRange(0,1)];
                require(view!=nil&&view.parentTexture==parent&&parent.parentTexture==intermediate&&view.rootResource==intermediate&&view.parentRelativeLevel==level&&view.mipmapLevelCount==1&&view.allocatedSize==intermediate.allocatedSize,"nested parent/level/allocation metadata");
                [views addObject:view];
            }
            require(intermediate.usage==65541&&intermediate.storageMode==MTLStorageModePrivate&&intermediate.mipmapLevelCount==4,"private metadata");
            MTLPixelFormat outputFormat=format==115?MTLPixelFormatBGRA8Unorm:format;
            td.width=67;td.height=39;td.storageMode=MTLStorageModeShared;td.usage=5;td.pixelFormat=outputFormat;td.mipmapLevelCount=1;
            id<MTLTexture> target=[device newTextureWithDescriptor:td];require(target!=nil,"output allocation");
            NSMutableArray *pipelines=[NSMutableArray array];
            for(NSString *name in @[@"paint",@"copyPixel"]){
                MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
                pd.vertexFunction=[library newFunctionWithName:@"v"];pd.fragmentFunction=[library newFunctionWithName:name];
                pd.colorAttachments[0].pixelFormat=[name isEqual:@"paint"]?format:outputFormat;
                id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];
                require(pipeline!=nil,error.description.UTF8String);[pipelines addObject:pipeline];
            }
            for(uint32_t frame=0;frame<8;frame++){
                id<MTLCommandBuffer> command=[queue commandBuffer];
                for(unsigned passIndex=0;passIndex<2;passIndex++){
                    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];
                    pass.colorAttachments[0].texture=passIndex?target:views[frame%4];pass.colorAttachments[0].level=0;
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
                    uint32_t hdr=format==115,values[]={frame,hdr},readOptions[]={hdr,0,format==115&&(frame&1)?(uint32_t)(intermediate.width>>(frame%4))-67:0,format==115&&(frame&2)?(uint32_t)(intermediate.height>>(frame%4))-39:0};
                    if(passIndex){[encoder setFragmentTexture:views[frame%4] atIndex:0];[encoder setFragmentBytes:readOptions length:sizeof(readOptions) atIndex:0];}
                    else [encoder setFragmentBytes:values length:sizeof(values) atIndex:0];
                    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[encoder endEncoding];
                }
                [command commit];[command waitUntilCompleted];require(command.status==MTLCommandBufferStatusCompleted,command.error.description.UTF8String);
                uint8_t actual[67*39*4];[target getBytes:actual bytesPerRow:67*4 fromRegion:MTLRegionMake2D(0,0,67,39) mipmapLevel:0];
                for(unsigned y=0;y<39;y++)for(unsigned x=0;x<67;x++){
                    unsigned level=frame%4;unsigned px=x+(format==115&&(frame&1)?(intermediate.width>>level)-67:0),py=y+(format==115&&(frame&2)?(intermediate.height>>level)-39:0);px%=intermediate.width>>level;py%=intermediate.height>>level;
                    uint8_t rgba[]={(px+frame)%251,(py+3*frame)%251,(px+py+7*frame)%251,255};
                    for(unsigned c=0;c<4;c++)require(actual[(y*67+x)*4+c]==rgba[(outputFormat==80&&c<3)?2-c:c],"render then read exact pixels");
                }
                fprintf(stderr,"TEXTURE_VIEW_PASS format=%lu frame=%u pixels=2613 passes=2\n",(unsigned long)format,frame);
            }
        }
    }
    sharedAliases(device);
    NSDictionary *stats=[(id<DVMFramebufferCompletion>)device consumerCompletion];
    require([stats[@"live"][@"objects"] isEqual:@0]&&[stats[@"live"][@"resourceBytes"] isEqual:@0],"all resources retired");
    require([stats[@"renderDraws"] isEqual:@48]&&[stats[@"renderPasses"] isEqual:@49],"all planned work executed");
    require(staged==3,"three oversized frontend batches staged without changing GPU pass counts");
    fprintf(stderr,"PASS nested texture views: BGRA/RGBA/RGBA16F HDR, 1216x2560 RGBA16F with edge samples, four rendered mip levels, eight reused frames each, exact pixels, completion and retirement\n");
}}
