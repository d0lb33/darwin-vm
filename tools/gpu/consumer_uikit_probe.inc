#ifndef DVM_CA_RENDERER_FLAGS
#define DVM_CA_RENDERER_FLAGS 0
#endif
// Actual guest UIKit content. Its CPU layer rendering is an independent pixel
// reference; CARenderer must submit the same layer tree through the proxy.
#include "consumer_uikit_scene.inc"
#ifdef DVM_CA_UIKIT_TRACE
#import <objc/runtime.h>
extern CFTypeID CABackingStoreGetTypeID(void);
static IMP UIKitOriginalCopyRenderValue;
static void *UIKitCopyRenderValue(id value,SEL selector){
    // Diagnostic only: preserve the exact guest implementation and its owned
    // return value. The fixed offset is from 24A5430a's getter at 0x1844da684.
    BOOL backing=CFGetTypeID((__bridge CFTypeRef)value)==CABackingStoreGetTypeID();
    unsigned flags=backing?*(const uint16_t *)((const uint8_t *)(__bridge const void *)value+0x1ec):0;
    void *result=((void *(*)(id,SEL))UIKitOriginalCopyRenderValue)(value,selector);
    if(backing){
        const uint8_t *front=*(const uint8_t **)((const uint8_t *)(__bridge const void *)value+0x190);
        const uint8_t *shmem=front?*(const uint8_t **)(front+0x10):NULL;
        uint64_t length=shmem?*(const uint64_t *)(shmem+0x10):0;
        const uint8_t *header=shmem?*(const uint8_t **)(shmem+0x18):NULL;
        fprintf(stderr,"GPU_LOAD_UIKIT_BACKING flags=%x result=%u front=%u shmem=%u drawable=%u bytes=%llu shmem_flags=%x format=%u levels=%u\n",
            flags,result!=NULL,front!=NULL,shmem!=NULL,front&&*(void **)(front+0x18)!=NULL,
            (unsigned long long)length,shmem?shmem[0xd]:0,header&&length>=36?*(const uint32_t *)(header+0xc):0,
            header&&length>=36?*(const uint32_t *)(header+0x20):0);
    }
    return result;
}
#endif
static void UIKitInspect(CALayer *layer, const char *stage, unsigned depth){
    fprintf(stderr,"GPU_LOAD_UIKIT_LAYER stage=%s depth=%u class=%s delegate=%s width=%.0f height=%.0f contents=%s needs=%u flipped=%u contentflip=%u scale=%.1f\n",
        stage,depth,NSStringFromClass(layer.class).UTF8String,NSStringFromClass([(id)layer.delegate class]).UTF8String,
        layer.bounds.size.width,layer.bounds.size.height,layer.contents?NSStringFromClass([layer.contents class]).UTF8String:"nil",
        layer.needsDisplay,layer.geometryFlipped,layer.contentsAreFlipped,layer.contentsScale);
    CGRect frame=layer.frame;
    fprintf(stderr,"GPU_LOAD_UIKIT_GEOMETRY stage=%s depth=%u frame=%.3f,%.3f,%.3f,%.3f corner_radius=%.3f corner_curve=%s masks=%u edge_mask=%u edge_aa=%u\n",
        stage,depth,frame.origin.x,frame.origin.y,frame.size.width,frame.size.height,
        layer.cornerRadius,layer.cornerCurve.UTF8String,layer.masksToBounds,(unsigned)layer.edgeAntialiasingMask,layer.allowsEdgeAntialiasing);
    if(layer.contents){
        CFStringRef description=CFCopyDescription((__bridge CFTypeRef)layer.contents);
        NSString *singleLine=[(__bridge NSString *)description stringByReplacingOccurrencesOfString:@"\n" withString:@" "];
        singleLine=[singleLine stringByReplacingOccurrencesOfString:@"\r" withString:@" "];
        fprintf(stderr,"GPU_LOAD_UIKIT_CONTENT stage=%s depth=%u type=%lu description=%.240s\n",stage,depth,
            (unsigned long)CFGetTypeID((__bridge CFTypeRef)layer.contents),singleLine.UTF8String);
        CFRelease(description);
    }
    for(CALayer *child in layer.sublayers)UIKitInspect(child,stage,depth+1);
}
static void UIKitDisplay(CALayer *layer){
    [layer setNeedsDisplay];[layer displayIfNeeded];for(CALayer *child in layer.sublayers)UIKitDisplay(child);
}
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device){
    const char *stage="uikit-view";CARenderer *renderer=nil;
    @try {
#ifdef DVM_CA_UIKIT_TRACE
        Method method=class_getInstanceMethod(NSObject.class,sel_registerName("CA_copyRenderValue"));
        if(!method)fail("uikit-copy-render-method");
        UIKitOriginalCopyRenderValue=method_setImplementation(method,(IMP)UIKitCopyRenderValue);
#endif
        fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s main=%u\n",stage,[NSThread isMainThread]);
        [CATransaction begin];[CATransaction setDisableActions:YES];
        const unsigned width=320,height=480,row=width*4;
        fprintf(stderr,"GPU_LOAD_RENDERER_FLAGS value=%u\n",(unsigned)DVM_CA_RENDERER_FLAGS);
        CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        UIView *view=DVMUIKitScene(space,width,height);
        stage="uikit-layout";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        [view layoutIfNeeded];UIKitInspect(view.layer,"before-display",0);UIKitDisplay(view.layer);[CATransaction commit];[CATransaction flush];UIKitInspect(view.layer,"after-display",0);
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:height mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> target=[device newTextureWithDescriptor:d];if(!target)fail("uikit-target");
        id<MTLCommandQueue> queue=[device newCommandQueue];
        stage="uikit-renderer";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        renderer=[CARenderer rendererWithMTLTexture:target options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(__bridge id)space,@"kCARendererFlags":@DVM_CA_RENDERER_FLAGS}];
        if(!renderer)fail("uikit-renderer-nil");
        [CATransaction begin];[CATransaction setDisableActions:YES];
        renderer.layer=view.layer;renderer.bounds=view.bounds;
        [CATransaction commit];[CATransaction flush];
        fprintf(stderr,"GPU_LOAD_UIKIT_ROOT width=%.0f height=%.0f hidden=%u opacity=%.3f children=%lu\n",view.bounds.size.width,view.bounds.size.height,view.layer.hidden,view.layer.opacity,(unsigned long)view.layer.sublayers.count);
        stage="uikit-render";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        [renderer beginFrameAtTime:CACurrentMediaTime() timeStamp:NULL];[renderer addUpdateRect:view.bounds];
        CGRect update=renderer.updateBounds;fprintf(stderr,"GPU_LOAD_UIKIT_UPDATE width=%.0f height=%.0f\n",update.size.width,update.size.height);
        [renderer render];[renderer endFrame];
        NSDictionary *stats=[(id<DVMConsumerCompletion>)device consumerCompletion];
        if(![stats[@"renderPasses"] unsignedIntegerValue]||![stats[@"renderDraws"] unsignedIntegerValue])fail("uikit-no-gpu-submission");
        NSMutableData *actual=[NSMutableData dataWithLength:row*height];
        [target getBytes:actual.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
        // CPU reference comes after GPU capture so any private UIKit drawing
        // side effects of renderInContext cannot prepare the GPU input for us.
        stage="uikit-cpu-reference";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        NSMutableData *reference=[NSMutableData dataWithLength:row*height];
        CGContextRef context=CGBitmapContextCreate(reference.mutableBytes,width,height,8,row,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
        if(!context)fail("uikit-cpu-context");
        CGContextTranslateCTM(context,0,height);CGContextScaleCTM(context,1,-1);
        [view.layer renderInContext:context];CGContextRelease(context);UIKitInspect(view.layer,"after-cpu",0);
        // Capture the independent CPU reference through ordinary texture I/O so
        // the host verifier can compare both byte arrays, not trust our verdict.
        id<MTLTexture> referenceTexture=[device newTextureWithDescriptor:d];
        [referenceTexture replaceRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0 withBytes:reference.bytes bytesPerRow:row];
        NSMutableData *roundtrip=[NSMutableData dataWithLength:row*height];
        [referenceTexture getBytes:roundtrip.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
        if(![reference isEqual:roundtrip])fail("uikit-reference-capture");
        NSUInteger different=0;const uint8_t *a=actual.bytes,*b=reference.bytes;
        for(unsigned i=0;i<row*height;i++)if(abs((int)a[i]-(int)b[i])>2)different++;
        fprintf(stderr,"GPU_LOAD_UIKIT_PIXELS width=%u height=%u differing_channels=%lu passes=%lu draws=%lu\n",width,height,(unsigned long)different,(unsigned long)[stats[@"renderPasses"] unsignedIntegerValue],(unsigned long)[stats[@"renderDraws"] unsignedIntegerValue]);
#ifdef DVM_CA_UIKIT_EXTERNAL_REFERENCE
        // The raw CPU result stays captured and its mismatch stays reported.
        // Return resources normally so the external native-composition oracle
        // can require clean retirement as well as pixels. This marker is not
        // pixel acceptance; the runner job must be observed, then analyzed.
        fprintf(stderr,"GPU_LOAD_UIKIT_REFERENCE mode=external-native status=pending\n");
#else
        if(different)fail("uikit-pixel-reference");
#endif
        renderer.layer=nil;renderer=nil;CGColorSpaceRelease(space);
    }@catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR uikit_stage=%s name=%s reason=%.220s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
