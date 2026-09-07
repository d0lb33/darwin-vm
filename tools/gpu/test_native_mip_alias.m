// Native semantic probe: a render pass reads a lower mip of the allocation
// containing its destination mip. No guest or forwarding-driver involvement.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static void check(BOOL ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
int main(void){@autoreleasepool{
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();id<MTLCommandQueue> queue=[device newCommandQueue];NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
        "vertex float4 v(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}\n"
        "fragment float4 paint(constant uint2 &op[[buffer(0)]]){return float4(float(op.x)/8.0f,op.y?2.0f:0.5f,op.y?-1.0f:1.0f,1);}\n"
        "fragment float4 copyPixel(float4 p[[position]],texture2d<float,access::read> t[[texture(0)]],constant uint2 &op[[buffer(0)]]){"
        "float4 c=t.read(uint2(p.xy)%uint2(t.get_width(op.x),t.get_height(op.x)),op.x);if(op.y)c.gb=float2(c.g/4.0f,-c.b);return c;}";
    id<MTLLibrary> library=[device newLibraryWithSource:source options:nil error:&error];check(library!=nil,error.description.UTF8String);
    for(NSNumber *fmt in @[@80,@115]){@autoreleasepool{
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:fmt.unsignedIntegerValue width:64 height:64 mipmapped:YES];
        td.storageMode=MTLStorageModePrivate;td.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;td.mipmapLevelCount=4;
        id<MTLTexture> chain=[device newTextureWithDescriptor:td];check(chain!=nil,"private mip allocation");
        td.pixelFormat=MTLPixelFormatBGRA8Unorm;td.storageMode=MTLStorageModeShared;td.mipmapLevelCount=1;
        id<MTLTexture> output=[device newTextureWithDescriptor:td];check(output!=nil,"output");
        NSMutableArray *pipelines=[NSMutableArray array];
        for(unsigned step=0;step<3;step++){
            MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[library newFunctionWithName:@"v"];
            pd.fragmentFunction=[library newFunctionWithName:step?@"copyPixel":@"paint"];pd.colorAttachments[0].pixelFormat=step==2?MTLPixelFormatBGRA8Unorm:fmt.unsignedIntegerValue;
            id<MTLRenderPipelineState> pipeline=[device newRenderPipelineStateWithDescriptor:pd error:&error];check(pipeline!=nil,error.description.UTF8String);[pipelines addObject:pipeline];
        }
        for(unsigned frame=0;frame<8;frame++){
            id<MTLCommandBuffer> command=[queue commandBuffer];
            for(unsigned step=0;step<5;step++){
                MTLRenderPassDescriptor *pd=[MTLRenderPassDescriptor renderPassDescriptor];pd.colorAttachments[0].texture=step==4?output:chain;
                pd.colorAttachments[0].level=step==4?0:step;pd.colorAttachments[0].loadAction=MTLLoadActionClear;pd.colorAttachments[0].storeAction=MTLStoreActionStore;
                id<MTLRenderCommandEncoder> encoder=[command renderCommandEncoderWithDescriptor:pd];[encoder setRenderPipelineState:pipelines[step==4?2:step?1:0]];
                uint32_t op[]={step?step-1:frame,(!step||step==4)&&fmt.unsignedIntValue==115};
                [encoder setFragmentBytes:op length:sizeof(op) atIndex:0];if(step)[encoder setFragmentTexture:chain atIndex:0];
                [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[encoder endEncoding];
            }
            [command commit];[command waitUntilCompleted];check(command.status==MTLCommandBufferStatusCompleted,command.error.description.UTF8String);
            uint8_t bytes[64*64*4];[output getBytes:bytes bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
            const uint8_t expected[]={255,128,(255*frame+4)/8,255};
            for(unsigned i=0;i<sizeof(bytes);i++)check(bytes[i]==expected[i%4],"nonoverlapping mip pixels");
            fprintf(stderr,"NATIVE_MIP_ALIAS_PASS format=%u frame=%u levels=4 pixels=4096\n",fmt.unsignedIntValue,frame);
        }
    }}
}}
