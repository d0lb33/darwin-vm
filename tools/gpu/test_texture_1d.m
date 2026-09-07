// Host-authored LUT arithmetic validates the transport resource contract.
// It is not evidence of guest HDRProcessing shader execution.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMLUTCompletion
- (NSDictionary *)consumerCompletion;
@end
static void check(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static void setLUT(void *bytes,NSUInteger format,unsigned i,unsigned delta){
    if(format==23){((uint16_t *)bytes)[i]=(uint16_t)((i*17)^delta);return;}
    unsigned channels=(format==25||format==55)?1:2;
    for(unsigned c=0;c<channels;c++){
        float value=(float)(((i*17+c*53)^delta)&255)/256.0f;
        if((format==55||format==105))((float *)bytes)[i*channels+c]=value;
        else ((__fp16 *)bytes)[i*channels+c]=(__fp16)value;
    }
}
static NSArray *drawLUT(id<MTLDevice> device,id<MTLLibrary> library,NSUInteger format,NSUInteger width){
    NSMutableArray *outputs=[NSMutableArray array];
    @autoreleasepool{
        MTLTextureDescriptor *d=[MTLTextureDescriptor new];d.textureType=MTLTextureType1D;
        d.pixelFormat=format;d.width=width;d.height=d.depth=1;d.storageMode=MTLStorageModeShared;d.usage=1;
        id<MTLTexture> lut=[device newTextureWithDescriptor:d];check(lut!=nil,"1D allocation");
        check(lut.textureType==MTLTextureType1D&&lut.pixelFormat==format&&lut.height==1&&lut.depth==1,"1D metadata");
        d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:1 mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=5;
        id<MTLTexture> target=[device newTextureWithDescriptor:d];check(target!=nil,"output allocation");
        MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[library newFunctionWithName:@"v"];pd.fragmentFunction=[library newFunctionWithName:format==23?@"f":@"f_float"];pd.colorAttachments[0].pixelFormat=80;
        NSError *error=nil;id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];check(pipeline!=nil,error.description.UTF8String);
        id<MTLCommandQueue> queue=[device newCommandQueue];NSUInteger bpp=DVMFormatBytes(format);
        NSMutableData *values=[NSMutableData dataWithLength:width*bpp];
        for(unsigned i=0;i<width;i++)setLUT(values.mutableBytes,format,i,0);
        [lut replaceRegion:MTLRegionMake1D(0,width) mipmapLevel:0 withBytes:values.bytes bytesPerRow:0];
        for(unsigned frame=0;frame<8;frame++){@autoreleasepool{
            unsigned start=frame*71;for(unsigned i=start;i<start+17;i++)setLUT(values.mutableBytes,format,i,0x35d+frame*129);
            [lut replaceRegion:MTLRegionMake1D(start,17) mipmapLevel:0 slice:0 withBytes:(const uint8_t *)values.bytes+start*bpp bytesPerRow:0 bytesPerImage:0];
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:pass];
            [e setRenderPipelineState:pipeline];[e setFragmentTexture:lut atIndex:0];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[cb commit];[cb waitUntilCompleted];
            check(cb.status==MTLCommandBufferStatusCompleted,"LUT GPU completion");
            NSMutableData *pixels=[NSMutableData dataWithLength:width*4];[target getBytes:pixels.mutableBytes bytesPerRow:width*4 fromRegion:MTLRegionMake2D(0,0,width,1) mipmapLevel:0];
            const uint8_t *p=pixels.bytes;
            for(unsigned i=0;i<width;i++){
                unsigned red,green,blue;
                if(format==23){uint16_t v=((const uint16_t *)values.bytes)[i];red=v&255;green=v>>8;blue=(v^0x5a)&255;}
                else{
                    unsigned channels=(format==25||format==55)?1:2;
                    float r=(format==55||format==105)?((const float *)values.bytes)[i*channels]:(float)((const __fp16 *)values.bytes)[i*channels];
                    float g=channels==1?0:(format==55||format==105)?((const float *)values.bytes)[i*channels+1]:(float)((const __fp16 *)values.bytes)[i*channels+1];
                    red=(unsigned)lrintf(r*255);green=(unsigned)lrintf(g*255);blue=64;
                }
                check(p[i*4]==blue&&p[i*4+1]==green&&p[i*4+2]==red&&p[i*4+3]==255,"independent LUT pixel oracle");
            }
            uint8_t read[17*8];[lut getBytes:read bytesPerRow:0 fromRegion:MTLRegionMake1D(start,17) mipmapLevel:0];
            check(!memcmp(read,(const uint8_t *)values.bytes+start*bpp,17*bpp),"1D partial read");
            [outputs addObject:pixels];
        }}
    }
    return outputs;
}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    NSError *error=nil;id<MTLLibrary> library=[host.device newLibraryWithSource:@"#include <metal_stdlib>\nusing namespace metal;\nvertex float4 v(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\nfragment float4 f(float4 p[[position]],texture1d<uint,access::read> lut[[texture(0)]]){uint v=lut.read(uint(p.x)).r;return float4(v&255u,v>>8,(v^0x5au)&255u,255u)/255.0f;}\nfragment float4 f_float(float4 p[[position]],texture1d<float,access::read> lut[[texture(0)]]){float4 c=lut.read(uint(p.x));return float4(c.r,c.g,0.25f,1.0f);}" options:nil error:&error];check(library!=nil,error.description.UTF8String);
    __block uint64_t seq=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **outError){@synchronized(host){
        NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
        NSDictionary *reply;
        if([r[@"op"] isEqual:@"library"]){DVMEntry *entry=nil;check(Add(host,@"library",library,&entry),"fixture library");reply=@{@"ok":@YES,@"handle":@(entry.handle),@"functionNames":library.functionNames};}
        else reply=ProcessRequest(host,++seq,r);
        if(![reply[@"ok"] boolValue]){if(outError)*outError=[NSError errorWithDomain:@"LUTTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply.description}];return nil;}return reply;
    }});
    NSMutableArray *native=[NSMutableArray array],*forwarded=[NSMutableArray array];
    for(NSArray *spec in @[@[@23,@3072],@[@25,@4096],@[@55,@1024],@[@55,@4096],@[@105,@1024]]){
        NSUInteger format=[spec[0] unsignedIntegerValue],width=[spec[1] unsignedIntegerValue];
        [native addObjectsFromArray:drawLUT(host.device,library,format,width)];
        @autoreleasepool{
            id<MTLLibrary> proxy=[device newLibraryWithData:dispatch_data_create("fixture",7,NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT) error:&error];
            [forwarded addObjectsFromArray:drawLUT(device,proxy,format,width)];
        }
    }
    check([native isEqual:forwarded],"native versus forwarded LUT pixels");
    NSDictionary *stats=[(id<DVMLUTCompletion>)device consumerCompletion];check([stats[@"live"][@"objects"] isEqual:@0],"LUT ownership retirement");
    puts("DVM_LUT_PASS formats=R16Uint,R16Float,R32Float,RG32Float frames=40 native_equal=40 pixel_oracle=verified partial_updates=verified partial_reads=verified retirement=verified");
}}
