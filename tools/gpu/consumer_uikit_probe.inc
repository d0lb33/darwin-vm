#include "consumer_uikit_support.inc"
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
#ifdef DVM_CA_UIKIT_EFFECT
        DVMUIKitAddEffect(view,DVM_CA_UIKIT_EFFECT);
        fprintf(stderr,"GPU_LOAD_UIKIT_EFFECT kind=%u window=%u\n",DVM_CA_UIKIT_EFFECT,view.window!=nil);
#endif
        UIWindow *window=nil;
#ifdef DVM_CA_UIKIT_WINDOW
        stage="uikit-window";window=DVMUIKitAttachWindow(view,nil);
#endif
        stage="uikit-layout";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        [view layoutIfNeeded];UIKitInspect(view.layer,"before-display",0);UIKitDisplay(view.layer);[CATransaction commit];[CATransaction flush];UIKitInspect(view.layer,"after-display",0);
        if(window)[[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.1]];
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
        renderer.layer=nil;renderer=nil;window.hidden=YES;window.rootViewController=nil;CGColorSpaceRelease(space);
    }@catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR uikit_stage=%s name=%s reason=%.220s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
