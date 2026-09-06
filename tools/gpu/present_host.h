#pragma once
#include "blur_host.h"
#include <sys/mman.h>
#include <unistd.h>
#include "present_layout.h"
#include "managed_host.h"
@interface DVMResidentBlur : NSObject
@property DVMBlurHost *blur;
@property void *ownedMap;
@property int ownedFD;
@property size_t ownedLength;
@property void *pixels;
@property BOOL managed,displayPending;
@property unsigned completionDelayUS;
@property uint32_t nonce,lastFrame;
@property id<MTLComputePipelineState> convert;
@property id<MTLBuffer> destination;
- (instancetype)initWithDevice:(id<MTLDevice>)device library:(id<MTLLibrary>)library destination:(void *)pointer nonce:(uint32_t)nonce;
- (id<MTLCommandBuffer>)encodeFrame:(uint32_t)frame;
@end
@implementation DVMResidentBlur
- (void)dealloc {
    // GPU calls are synchronous; destroy buffer aliases before their mapping.
    _destination=nil;_blur=nil;
    if(_ownedMap){munmap(_ownedMap,_ownedLength?:0x1000000);close(_ownedFD);}
}
- (instancetype)initWithDevice:(id<MTLDevice>)device library:(id<MTLLibrary>)library destination:(void *)pointer nonce:(uint32_t)nonce {
    self=[super init];if(!self)return nil;
    _pixels=pointer;
    if((uintptr_t)pointer%16384)return nil;
    _blur=[[DVMBlurHost alloc]initWithDevice:device library:library width:1184 height:2560];if(!_blur)return nil;
    // This host-authored kernel only converts the exact guest blur's output.
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
    "kernel void dvm_bgra(texture2d<half, access::read> src [[texture(0)]], device uint *dst [[buffer(0)]], constant uint &frame [[buffer(1)]], uint2 p [[thread_position_in_grid]]) {"
    "if(p.x>=1179||p.y>=2556)return; float4 c=float4(src.read(p)); uint4 u=uint4(c*255.0f+0.5f);"
    "uint v=u.z|(u.y<<8)|(u.x<<16)|(u.w<<24);"
    "if(p.y==0&&p.x<4){v=p.x==0?0xff44564d:p.x==1?0xff505253:p.x==2?0xff424c52:(0xff000000|frame);}"
    "dst[p.y*1216+p.x]=v;}";
    NSError *error=nil;id<MTLLibrary> conversion=[device newLibraryWithSource:source options:nil error:&error];
    _convert=conversion?[device newComputePipelineStateWithFunction:[conversion newFunctionWithName:@"dvm_bgra"] error:&error]:nil;
    if(!_convert){fprintf(stderr,"convert: %s\n",error.description.UTF8String);return nil;}
    _destination=[device newBufferWithBytesNoCopy:pointer length:DVM_PRESENT_BUFFER_BYTES options:MTLResourceStorageModeShared deallocator:nil];
    if(!_destination)return nil;
    size_t n=(size_t)1216*2592*8;DVMHalf *input=malloc(n);if(!input)return nil;
    DVMBlurInput(input,1216,2592,nonce);
    [_blur.input replaceRegion:MTLRegionMake2D(0,0,1216,2592) mipmapLevel:0 withBytes:input bytesPerRow:1216*8];
    free(input);return self;
}
- (id<MTLCommandBuffer>)encodeFrame:(uint32_t)frame {
    id<MTLCommandBuffer> cb=[_blur encode];
    if(_completionDelayUS&&frame==1) {
        // Host-only fault injection: hold the GPU before it writes the shared
        // output. The ordinary command completion must remain blocked.
        id<MTLSharedEvent> event=[_blur.output.device newSharedEvent];
        if(!event)return nil;
        [cb encodeWaitForEvent:event value:1];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)_completionDelayUS*1000),dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE,0),^{event.signaledValue=1;});
    }
    id<MTLComputeCommandEncoder> e=[cb computeCommandEncoder];[e setComputePipelineState:_convert];[e setTexture:_blur.output atIndex:0];[e setBuffer:_destination offset:0 atIndex:0];[e setBytes:&frame length:4 atIndex:1];
    [e dispatchThreadgroups:MTLSizeMake((1179+15)/16,(2556+15)/16,1) threadsPerThreadgroup:MTLSizeMake(16,16,1)];[e endEncoding];return cb;
}
@end
#include "present_reference.h"
