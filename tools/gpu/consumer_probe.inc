// A real QuartzCore client, deliberately process-local. No shader replay here.
#import <QuartzCore/CARenderer.h>
#import <QuartzCore/CALayer.h>
#import <QuartzCore/CATransaction.h>
#include "driver_capabilities.h"
@protocol DVMConsumerCompletion
- (NSDictionary *)consumerCompletion;
@end
#ifndef DVM_CA_FRAMES
#define DVM_CA_FRAMES 1
#endif
#if defined(DVM_CA_SHARED)
#include "consumer_shared_probe.inc"
#elif DVM_CA_FRAMES > 1
#include "consumer_sequence_probe.inc"
#else
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device) {
    const char *stage="capability-batch";
    // Keep the renderer alive through the catch: its dealloc calls endFrame,
    // which can otherwise hide the first unsupported selector with a second
    // exception while unwinding. The bounded probe exits on the first failure.
    CARenderer *renderer=nil;
    CGColorSpaceRef colorSpace=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    @try {
        id<DVMCapabilityQueries> caps=(id)device;
#define DVM_CHECK_BOOL(selector,value) if([caps selector]!=(BOOL)(value))fail("capability-" #selector); fprintf(stderr,"GPU_LOAD_CA_CAP selector=" #selector " value=%u\n",(unsigned)[caps selector]);
#define DVM_CHECK_UINT(selector,value) if([caps selector]!=(NSUInteger)(value))fail("capability-" #selector); fprintf(stderr,"GPU_LOAD_CA_CAP selector=" #selector " value=%lu\n",(unsigned long)[caps selector]);
        DVM_CAPABILITY_QUERIES(DVM_CHECK_BOOL,DVM_CHECK_UINT)
#undef DVM_CHECK_BOOL
#undef DVM_CHECK_UINT
        fprintf(stderr,"GPU_LOAD_CA_CAPABILITIES verified=1 version=%u render_encoders=1 generic_iosurface_import=0\n",DVM_CONTRACT_VERSION);
        stage="texture";
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];
        d.storageMode=MTLStorageModeShared;
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> texture=[device newTextureWithDescriptor:d];
        if(!texture)fail("consumer-target-allocation");
        fprintf(stderr,"GPU_LOAD_CA_STAGE texture=1 width=64 height=64\n");
        stage="renderer-init";
        renderer=[CARenderer rendererWithMTLTexture:texture options:@{kCARendererMetalCommandQueue:[device newCommandQueue],kCARendererColorSpace:(__bridge id)colorSpace}];
        if(!renderer)fail("consumer-renderer-nil");
        fprintf(stderr,"GPU_LOAD_CA_STAGE renderer=1\n");
        stage="layer-tree";
        [CATransaction begin];[CATransaction setDisableActions:YES];
        CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,64,64);
        const CGFloat components[]={1,0,0,1};CGColorRef color=CGColorCreate(colorSpace,components);
        root.backgroundColor=color;CGColorRelease(color);
        CGColorSpaceRelease(colorSpace);colorSpace=NULL;
        renderer.layer=root;renderer.bounds=root.frame;
        [CATransaction commit];
        stage="begin-frame";
        [renderer beginFrameAtTime:0 timeStamp:NULL];
        [renderer addUpdateRect:root.frame];
        fprintf(stderr,"GPU_LOAD_CA_STAGE begin=1\n");
        stage="render";[renderer render];
        stage="end-frame";[renderer endFrame];
        stage="completion";NSDictionary *completion=[(id<DVMConsumerCompletion>)device consumerCompletion];
        if(![completion[@"renderPasses"] unsignedIntegerValue])fail("consumer-no-render-submission");
        stage="pixels";uint8_t pixels[64*64*4];
        [texture getBytes:pixels bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
        unsigned bad=0;for(unsigned i=0;i<64*64;i++)if(pixels[i*4]||pixels[i*4+1]||pixels[i*4+2]!=255||pixels[i*4+3]!=255)bad++;
        if(bad){fprintf(stderr,"GPU_LOAD_CA_PIXELS bad=%u first=%02x%02x%02x%02x\n",bad,pixels[0],pixels[1],pixels[2],pixels[3]);fail("consumer-pixels");}
        fprintf(stderr,"GPU_LOAD_CA_VERIFIED width=64 height=64 passes=%lu draws=%lu bad_pixels=0\n",(unsigned long)[completion[@"renderPasses"] unsignedIntegerValue],(unsigned long)[completion[@"renderDraws"] unsignedIntegerValue]);
    } @catch(NSException *exception) {
        fprintf(stderr,"GPU_LOAD_ERROR consumer_stage=%s exception=%s reason=%.300s\n",stage,exception.name.UTF8String,exception.reason.UTF8String);
        exit(1);
    }
}
#endif
