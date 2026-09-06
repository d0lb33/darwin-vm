// A real QuartzCore client, deliberately process-local. No shader replay here.
#import <QuartzCore/CARenderer.h>
#import <QuartzCore/CALayer.h>
#import <QuartzCore/CATransaction.h>
#include "driver_capabilities.h"
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device) {
    const char *stage="capability-batch";
    @try {
        id<DVMCapabilityQueries> caps=(id)device;
#define DVM_CHECK_BOOL(selector,value) if([caps selector]!=(BOOL)(value))fail("capability-" #selector); fprintf(stderr,"GPU_LOAD_CA_CAP selector=" #selector " value=%u\n",(unsigned)[caps selector]);
#define DVM_CHECK_UINT(selector,value) if([caps selector]!=(NSUInteger)(value))fail("capability-" #selector); fprintf(stderr,"GPU_LOAD_CA_CAP selector=" #selector " value=%lu\n",(unsigned long)[caps selector]);
        DVM_CAPABILITY_QUERIES(DVM_CHECK_BOOL,DVM_CHECK_UINT)
#undef DVM_CHECK_BOOL
#undef DVM_CHECK_UINT
        fprintf(stderr,"GPU_LOAD_CA_CAPABILITIES verified=1 version=%u render_encoders=0 generic_iosurface_import=0\n",DVM_CONTRACT_VERSION);
        stage="texture";
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];
        d.storageMode=MTLStorageModeShared;
        d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> texture=[device newTextureWithDescriptor:d];
        if(!texture)fail("consumer-target-allocation");
        fprintf(stderr,"GPU_LOAD_CA_STAGE texture=1 width=64 height=64\n");
        stage="renderer-init";
        CARenderer *renderer=[CARenderer rendererWithMTLTexture:texture options:@{kCARendererMetalCommandQueue:[device newCommandQueue]}];
        if(!renderer)fail("consumer-renderer-nil");
        fprintf(stderr,"GPU_LOAD_CA_STAGE renderer=1\n");
        stage="layer-tree";
        [CATransaction begin];[CATransaction setDisableActions:YES];
        CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,64,64);
        CGColorRef color=CGColorCreateGenericRGB(1,0,0,1);
        root.backgroundColor=color;CGColorRelease(color);
        renderer.layer=root;renderer.bounds=root.frame;
        [CATransaction commit];
        stage="begin-frame";
        [renderer beginFrameAtTime:0 timeStamp:NULL];
        [renderer addUpdateRect:root.frame];
        fprintf(stderr,"GPU_LOAD_CA_STAGE begin=1\n");
        stage="render";[renderer render];
        stage="end-frame";[renderer endFrame];
        // Reaching this point alone is not pixel or GPU execution proof.
        fprintf(stderr,"GPU_LOAD_CA_STAGE render_returned=1 unverified=1\n");
        fail("consumer-verification-not-implemented");
    } @catch(NSException *exception) {
        fprintf(stderr,"GPU_LOAD_ERROR consumer_stage=%s exception=%s reason=%.300s\n",stage,exception.name.UTF8String,exception.reason.UTF8String);
        exit(1);
    }
}
