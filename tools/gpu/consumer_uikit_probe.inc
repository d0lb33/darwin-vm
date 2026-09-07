// Actual guest UIKit content. Its CPU layer rendering is an independent pixel
// reference; CARenderer must submit the same layer tree through the proxy.
#import <UIKit/UIView.h>
#import <UIKit/UILabel.h>
#import <UIKit/UIButton.h>
#import <UIKit/UIImageView.h>
#import <UIKit/UIColor.h>
#import <UIKit/UIFont.h>
#import <UIKit/UIImage.h>
static void UIKitInspect(CALayer *layer, const char *stage, unsigned depth){
    fprintf(stderr,"GPU_LOAD_UIKIT_LAYER stage=%s depth=%u class=%s delegate=%s width=%.0f height=%.0f contents=%s needs=%u flipped=%u scale=%.1f\n",
        stage,depth,NSStringFromClass(layer.class).UTF8String,NSStringFromClass([(id)layer.delegate class]).UTF8String,
        layer.bounds.size.width,layer.bounds.size.height,layer.contents?NSStringFromClass([layer.contents class]).UTF8String:"nil",
        layer.needsDisplay,layer.geometryFlipped,layer.contentsScale);
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
        fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s main=%u\n",stage,[NSThread isMainThread]);
        [CATransaction begin];[CATransaction setDisableActions:YES];
        const unsigned width=320,height=480,row=width*4;
        CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        UIView *view=[[UIView alloc] initWithFrame:CGRectMake(0,0,width,height)];
        view.backgroundColor=[UIColor colorWithRed:.08 green:.12 blue:.20 alpha:1];
        UILabel *title=[[UILabel alloc] initWithFrame:CGRectMake(20,22,280,46)];
        title.text=@"UIKit on host Metal";title.font=[UIFont boldSystemFontOfSize:24];
        title.textColor=[UIColor whiteColor];[view addSubview:title];
        UIView *card=[[UIView alloc] initWithFrame:CGRectMake(20,88,280,200)];
        card.backgroundColor=[UIColor colorWithRed:.15 green:.30 blue:.65 alpha:1];
        card.layer.cornerRadius=24;card.clipsToBounds=YES;[view addSubview:card];
        uint32_t imagePixels[16*16];for(unsigned y=0;y<16;y++)for(unsigned x=0;x<16;x++)
            imagePixels[y*16+x]=((x/4+y/4)%2)?0xff20d0f0:0xfff08020;
        CGContextRef imageContext=CGBitmapContextCreate(imagePixels,16,16,8,64,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
        CGImageRef image=CGBitmapContextCreateImage(imageContext);
        UIImageView *picture=[[UIImageView alloc] initWithFrame:CGRectMake(-12,20,304,128)];
        picture.image=[UIImage imageWithCGImage:image];[card addSubview:picture];
        CGImageRelease(image);CGContextRelease(imageContext);
        UILabel *caption=[[UILabel alloc] initWithFrame:CGRectMake(16,154,252,30)];
        caption.text=@"Text, image and rounded clipping";caption.font=[UIFont systemFontOfSize:14];
        caption.textColor=[UIColor whiteColor];[card addSubview:caption];
        UIButton *button=[UIButton buttonWithType:UIButtonTypeSystem];
        button.frame=CGRectMake(40,326,240,56);[button setTitle:@"Render another frame" forState:UIControlStateNormal];
        button.backgroundColor=[UIColor colorWithRed:.1 green:.6 blue:.4 alpha:1];
        button.layer.cornerRadius=16;[view addSubview:button];
        stage="uikit-layout";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        [view layoutIfNeeded];UIKitInspect(view.layer,"before-display",0);UIKitDisplay(view.layer);[CATransaction commit];[CATransaction flush];UIKitInspect(view.layer,"after-display",0);
        stage="uikit-cpu-reference";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        NSMutableData *reference=[NSMutableData dataWithLength:row*height];
        CGContextRef context=CGBitmapContextCreate(reference.mutableBytes,width,height,8,row,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
        if(!context)fail("uikit-cpu-context");
        CGContextTranslateCTM(context,0,height);CGContextScaleCTM(context,1,-1);
        [view.layer renderInContext:context];CGContextRelease(context);UIKitInspect(view.layer,"after-cpu",0);
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:height mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> target=[device newTextureWithDescriptor:d];if(!target)fail("uikit-target");
        id<MTLCommandQueue> queue=[device newCommandQueue];
        stage="uikit-renderer";fprintf(stderr,"GPU_LOAD_UIKIT_STAGE name=%s\n",stage);
        renderer=[CARenderer rendererWithMTLTexture:target options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(__bridge id)space}];
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
        if(different)fail("uikit-pixel-reference");
        renderer.layer=nil;renderer=nil;CGColorSpaceRelease(space);
    }@catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR uikit_stage=%s name=%s reason=%.220s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
