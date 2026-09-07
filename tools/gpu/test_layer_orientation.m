#ifndef DVM_CA_RENDERER_FLAGS
#define DVM_CA_RENDERER_FLAGS 0
#endif
// Native macOS control for image orientation in CARenderer versus CALayer's
// independent CPU renderer. Does not constitute exact-guest driver evidence.
#import <Metal/Metal.h>
#import <QuartzCore/CARenderer.h>
#import <QuartzCore/CALayer.h>
#import <QuartzCore/CATransaction.h>
#include <stdio.h>
#ifdef DVM_ORIENTATION_FORWARDED
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#endif
#ifdef DVM_ORIENTATION_GUEST
static void require(BOOL ok,const char *why){if(!ok)fail(why);}
#else
static void require(BOOL ok,const char *why){if(!ok){fprintf(stderr,"GPU_LOAD_ERROR orientation=%s\n",why);exit(1);}}
#endif
#ifdef DVM_ORIENTATION_GUEST
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device){
#else
int main(int argc,const char **argv){@autoreleasepool{
    require(argc==2,"output directory");
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
#endif
    fprintf(stderr,"GPU_LOAD_RENDERER_FLAGS value=%u\n",(unsigned)DVM_CA_RENDERER_FLAGS);
    CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    uint32_t source[]={0xffff0000,0xff00ff00,0xff0000ff,0xffffffff};
    CGContextRef bitmap=CGBitmapContextCreate(source,2,2,8,8,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
    CGImageRef image=CGBitmapContextCreateImage(bitmap);
    id<MTLCommandQueue> queue=[device newCommandQueue];
#ifdef DVM_ORIENTATION_FORWARDED
    DVMHost *host=[DVMHost new];host.device=device;host.queue=queue;host.entries=[NSMutableDictionary dictionary];
    __block uint64_t sequence=0;
    device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **error){
        @synchronized(host){
            NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
            NSDictionary *reply=ProcessRequest(host,++sequence,r);
            if(![reply[@"ok"] boolValue]){if(error)*error=[NSError errorWithDomain:@"OrientationTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]?:@"RPC failed"}];return nil;}
            return [NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] options:0 error:nil];
        }
    });
    queue=[device newCommandQueue];
#endif
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    id<MTLTexture> target=[device newTextureWithDescriptor:d];require(target!=nil,"target");
    CARenderer *renderer=[CARenderer rendererWithMTLTexture:target options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(__bridge id)space,@"kCARendererFlags":@DVM_CA_RENDERER_FLAGS}];
    [CATransaction begin];[CATransaction setDisableActions:YES];
    CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,64,64);
    CALayer *picture=[CALayer layer];picture.frame=CGRectMake(8,12,24,32);picture.contents=(__bridge id)image;
    picture.minificationFilter=kCAFilterNearest;picture.magnificationFilter=kCAFilterNearest;
    [root addSublayer:picture];renderer.layer=root;renderer.bounds=root.bounds;
    fprintf(stderr,"GPU_LOAD_ORIENTATION_CONTENTS root=%u image=%u\n",root.contentsAreFlipped,picture.contentsAreFlipped);
    [CATransaction commit];[CATransaction flush];
    NSMutableData *cpu=[NSMutableData dataWithLength:64*64*4],*gpu=[cpu mutableCopy];
    CGContextRef context=CGBitmapContextCreate(cpu.mutableBytes,64,64,8,256,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
    // Match the layer's explicit nearest-neighbor sampling. CoreGraphics
    // otherwise interpolates even though CALayer magnificationFilter is nearest.
    CGContextSetInterpolationQuality(context,kCGInterpolationNone);
    CGContextTranslateCTM(context,0,64);CGContextScaleCTM(context,1,-1);[root renderInContext:context];
    [renderer beginFrameAtTime:CACurrentMediaTime() timeStamp:NULL];[renderer addUpdateRect:root.bounds];[renderer render];[renderer endFrame];
    id<MTLCommandBuffer> fence=[queue commandBuffer];[fence commit];[fence waitUntilCompleted];require(fence.status==MTLCommandBufferStatusCompleted,"completion");
    [target getBytes:gpu.mutableBytes bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
    #ifndef DVM_ORIENTATION_GUEST
    require([cpu writeToFile:[@(argv[1]) stringByAppendingPathComponent:@"native-cpu.bgra"] atomically:YES],"CPU output");
    require([gpu writeToFile:[@(argv[1]) stringByAppendingPathComponent:@"native-gpu.bgra"] atomically:YES],"GPU output");
    #else
    id<MTLTexture> reference=[device newTextureWithDescriptor:d];
    [reference replaceRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0 withBytes:cpu.bytes bytesPerRow:256];
    NSMutableData *roundtrip=[cpu mutableCopy];
    [reference getBytes:roundtrip.mutableBytes bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
    require([roundtrip isEqual:cpu],"CPU reference capture");
    #endif
    const uint32_t *c=cpu.bytes,*g=gpu.bytes;
    for(unsigned y=16;y<=36;y+=20)for(unsigned x=12;x<=28;x+=16)
        fprintf(stderr,"GPU_LOAD_ORIENTATION_SAMPLE x=%u y=%u cpu=%08x gpu=%08x\n",x,y,c[y*64+x],g[y*64+x]);
    unsigned differences=0;for(unsigned i=0;i<64*64;i++)differences+=c[i]!=g[i];
    fprintf(stderr,"GPU_LOAD_ORIENTATION pixels=4096 differing_pixels=%u\n",differences);
    renderer.layer=nil;renderer=nil;
    CGImageRelease(image);CGContextRelease(bitmap);CGContextRelease(context);CGColorSpaceRelease(space);
    require(!differences,"pixel reference");
#ifdef DVM_ORIENTATION_GUEST
}
#else
}}
#endif
