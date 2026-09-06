#pragma once
/* Exact AIR compute_simd_blur_5. Uniform offsets and lane/threadgroup mapping
 * are from its unmodified AIR IR; see gpu-blur-feasibility-ios27.md. */
#import <Metal/Metal.h>
#import <Foundation/Foundation.h>
#include "blur_reference.h"
@interface DVMBlurHost : NSObject
@property id<MTLDevice> device;
@property id<MTLCommandQueue> queue;
@property id<MTLComputePipelineState> pipeline;
@property id<MTLTexture> input,temporary,output;
@property unsigned width,height;
- (instancetype)initWithDevice:(id<MTLDevice>)device library:(id<MTLLibrary>)library width:(unsigned)w height:(unsigned)h;
- (id<MTLCommandBuffer>)encode;
@end
@implementation DVMBlurHost
- (instancetype)initWithDevice:(id<MTLDevice>)device library:(id<MTLLibrary>)lib width:(unsigned)w height:(unsigned)h {
    self=[super init];if(!self)return nil;_device=device;_width=w;_height=h;
    NSError *error=nil;id<MTLFunction> f=[lib newFunctionWithName:@"compute_simd_blur_5"];
    _pipeline=[device newComputePipelineStateWithFunction:f error:&error];
    if(!_pipeline){fprintf(stderr,"blur pipeline: %s\n",error.description.UTF8String);return nil;}
    if(_pipeline.threadExecutionWidth!=32||_pipeline.maxTotalThreadsPerThreadgroup<1024)return nil;
    _queue=[device newCommandQueue];
    for(unsigned i=0;i<3;i++) {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:i?w:w+32 height:i==2?h:h+32 mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
        id<MTLTexture> t=[device newTextureWithDescriptor:d];if(!t)return nil;
        if(i==0)_input=t;else if(i==1)_temporary=t;else _output=t;
    }
    return self;
}
- (id<MTLCommandBuffer>)encode {
    id<MTLCommandBuffer> cb=[_queue commandBuffer];
    for(unsigned pass=0;pass<2;pass++) {
        uint16_t u[10]={0,0,0,0,pass?0:32,pass?32:0,32,32,pass?0:1,0x3c00};
        DVMHalf weights[5]={.0625,.25,.375,.25,.0625};
        id<MTLComputeCommandEncoder> e=[cb computeCommandEncoder];[e setComputePipelineState:_pipeline];
        [e setTexture:pass?_temporary:_input atIndex:0];[e setTexture:pass?_output:_temporary atIndex:1];
        [e setBytes:u length:sizeof(u) atIndex:0];[e setBytes:weights length:sizeof(weights) atIndex:1];
        [e setImageblockWidth:32 height:32];
        [e dispatchThreadgroups:MTLSizeMake(_width/32,(_height+(pass?0:32))/32,1) threadsPerThreadgroup:MTLSizeMake(32,32,1)];
        [e endEncoding];
    }
    return cb;
}
@end
