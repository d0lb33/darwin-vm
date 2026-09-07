// Test scene only: no scene identity or shader-name branch exists in the driver.
// Retain one renderer/target and move an opaque red layer behind a green layer.
// Verify the first, second and final frames; no readback within the timed batch.
#include <time.h>
#include <unistd.h>
#ifndef DVM_CA_HZ
#define DVM_CA_HZ 0
#endif
#ifndef DVM_CA_SCENE
#define DVM_CA_SCENE 0
#endif
static double DVMConsumerTime(void) {
    struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);
    return t.tv_sec+t.tv_nsec*1e-9;
}
static void DVMConsumerColor(CALayer *layer,CGColorSpaceRef space,double r,double g,double b) {
    const CGFloat values[]={r,g,b,1};CGColorRef color=CGColorCreate(space,values);
    layer.backgroundColor=color;CGColorRelease(color);
}
static void DVMConsumerCheck(id<MTLTexture> texture,unsigned frame) {
    uint8_t pixels[64*64*4];
    [texture getBytes:pixels bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
    unsigned bad=0,x0=(frame*7)%49;
    for(unsigned y=0;y<64;y++)for(unsigned x=0;x<64;x++){
        const uint8_t *p=pixels+(y*64+x)*4;
        BOOL green=x>=28&&x<36,red=!green&&x>=x0&&x<x0+16;
        if(DVM_CA_SCENE==2)red=red&&x>=8&&x<56;
        if(DVM_CA_SCENE==3&&red&&x>=x0+8){red=NO;green=YES;}
        unsigned b=green||red?0:255,g=green?255:0,r=red?255:0;
        if(DVM_CA_SCENE==1&&red){b=128;r=128;}
        int tolerance=DVM_CA_SCENE==1?1:0;
        if(abs((int)p[0]-(int)b)>tolerance||abs((int)p[1]-(int)g)>tolerance||abs((int)p[2]-(int)r)>tolerance||p[3]!=255)bad++;
    }
    fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_PIXELS frame=%u bad_pixels=%u\n",frame,bad);
    if(bad){fprintf(stderr,"GPU_LOAD_CA_PIXEL_DETAIL scene=%u sample=%u,%u,%u,%u\n",DVM_CA_SCENE,pixels[0],pixels[1],pixels[2],pixels[3]);fail("consumer-sequence-pixels");}
}
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device) {
    const char *stage="sequence-init";CARenderer *renderer=nil;
    double setup=DVMConsumerTime(),samples[DVM_CA_FRAMES],sum=0;
    NSUInteger peakObjects=0,peakBytes=0;unsigned misses=0;
    double pacingStart=0,pacingEnd=0,maxLate=0;unsigned late=0;
    @try {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> texture=[device newTextureWithDescriptor:d];
        if(!texture)fail("consumer-sequence-target");
        CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        id<MTLCommandQueue> queue=[device newCommandQueue];
        renderer=[CARenderer rendererWithMTLTexture:texture options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(__bridge id)space}];
        if(!renderer)fail("consumer-sequence-renderer");
        [CATransaction begin];[CATransaction setDisableActions:YES];
        CALayer *root=[CALayer layer],*moving=[CALayer layer],*front=[CALayer layer];
        root.frame=CGRectMake(0,0,64,64);moving.frame=CGRectMake(0,0,16,64);front.frame=CGRectMake(28,0,8,64);
        DVMConsumerColor(root,space,0,0,1);DVMConsumerColor(moving,space,1,0,0);DVMConsumerColor(front,space,0,1,0);
        CALayer *clip=nil;
        if(DVM_CA_SCENE==1)moving.opacity=.5;
        if(DVM_CA_SCENE==2){
            clip=[CALayer layer];clip.frame=CGRectMake(8,0,48,64);clip.masksToBounds=YES;
            [root addSublayer:clip];[clip addSublayer:moving];
            moving.anchorPoint=CGPointZero;moving.bounds=CGRectMake(0,0,8,64);
            moving.transform=CATransform3DMakeScale(2,1,1);
        }else [root addSublayer:moving];
        if(DVM_CA_SCENE==3){
            const uint8_t imageBytes[]={255,0,0,255,0,255,0,255};
            CFDataRef data=CFDataCreate(NULL,imageBytes,sizeof(imageBytes));
            CGDataProviderRef provider=CGDataProviderCreateWithCFData(data);
            CGImageRef image=CGImageCreate(2,1,8,32,8,space,kCGBitmapByteOrder32Big|kCGImageAlphaPremultipliedLast,provider,NULL,false,kCGRenderingIntentDefault);
            if(!image)fail("consumer-image");moving.backgroundColor=NULL;moving.contents=(__bridge id)image;
            moving.magnificationFilter=kCAFilterNearest;moving.minificationFilter=kCAFilterNearest;
            CGImageRelease(image);CGDataProviderRelease(provider);CFRelease(data);
        }
        CGColorSpaceRelease(space);[root addSublayer:front];
        fprintf(stderr,"GPU_LOAD_CA_SCENE id=%u\n",DVM_CA_SCENE);
        renderer.layer=root;renderer.bounds=root.frame;[CATransaction commit];
        setup=DVMConsumerTime()-setup;
        NSDictionary *last=nil;
        for(unsigned frame=0;frame<DVM_CA_FRAMES;frame++)@autoreleasepool {
            double target=0;
            if(DVM_CA_HZ&&frame>=2){
                if(frame==2)pacingStart=DVMConsumerTime();
                target=pacingStart+(frame-2)/(double)(DVM_CA_HZ?DVM_CA_HZ:1);
                double delay=target-DVMConsumerTime();if(delay>0)usleep((useconds_t)(delay*1e6));
            }
            double start=DVMConsumerTime();
            [CATransaction begin];[CATransaction setDisableActions:YES];
            if(DVM_CA_SCENE==2)moving.position=CGPointMake((int)((frame*7)%49)-8,0);
            else moving.frame=CGRectMake((frame*7)%49,0,16,64);
            [CATransaction commit];
            stage="sequence-begin";[renderer beginFrameAtTime:frame/60.0 timeStamp:NULL];
            [renderer addUpdateRect:root.frame];
            stage="sequence-render";[renderer render];[renderer endFrame];
            stage="sequence-completion";
#ifdef DVM_CA_NATIVE
            id<MTLCommandBuffer> fence=[queue commandBuffer];[fence commit];[fence waitUntilCompleted];
            if(fence.status!=MTLCommandBufferStatusCompleted)fail("native-sequence-completion");
            last=@{}; // Native resource/pass accounting is unavailable here.
#else
            last=[(id<DVMConsumerCompletion>)device consumerCompletion];
#endif
            pacingEnd=DVMConsumerTime();samples[frame]=(pacingEnd-start)*1e6;
            if(DVM_CA_HZ&&frame>=2){double behind=(pacingEnd-target)*1e6-1e6/(DVM_CA_HZ?DVM_CA_HZ:1);if(behind>0){late++;maxLate=MAX(maxLate,behind);}}
            if(frame>=2){sum+=samples[frame];if(samples[frame]>1000000.0/60)misses++;}
            peakObjects=MAX(peakObjects,[last[@"live"][@"objects"] unsignedIntegerValue]);
            peakBytes=MAX(peakBytes,[last[@"live"][@"resourceBytes"] unsignedIntegerValue]);
            if(frame<2||frame==DVM_CA_FRAMES-1){stage="sequence-pixels";DVMConsumerCheck(texture,frame);}
        }
#ifndef DVM_CA_NATIVE
        if(DVM_CA_FRAMES<3||[last[@"renderPasses"] unsignedIntegerValue]<DVM_CA_FRAMES)fail("consumer-sequence-submissions");
#endif
        double sorted[DVM_CA_FRAMES];memcpy(sorted,samples,sizeof(samples));
        // Small bounded array; keep libc imports unchanged for the exact guest.
        for(unsigned i=3;i<DVM_CA_FRAMES;i++){double value=sorted[i];unsigned j=i;while(j>2&&sorted[j-1]>value){sorted[j]=sorted[j-1];j--;}sorted[j]=value;}
        unsigned n=DVM_CA_FRAMES-2;
        if(DVM_CA_HZ)fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_PACING hz=%u frames=%u elapsed_us=%.3f late_frames=%u max_late_us=%.3f\n",DVM_CA_HZ,n,(pacingEnd-pacingStart)*1e6,late,maxLate);
        fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_TIMING frames=%u setup_us=%.3f first_us=%.3f steady_us=%.3f p50_us=%.3f p95_us=%.3f max_us=%.3f misses_60=%u peak_objects=%lu peak_bytes=%lu\n",DVM_CA_FRAMES,setup*1e6,samples[0],sum,sorted[2+(n-1)/2],sorted[2+(n*95+99)/100-1],sorted[DVM_CA_FRAMES-1],misses,(unsigned long)peakObjects,(unsigned long)peakBytes);
#ifdef DVM_CA_NATIVE
        fprintf(stderr,"NATIVE_CA_SEQUENCE_VERIFIED width=64 height=64 frames=%u bad_pixels=0 resource_accounting=unavailable\n",DVM_CA_FRAMES);
#else
        fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_VERIFIED width=64 height=64 frames=%u passes=%lu draws=%lu bad_pixels=0\n",DVM_CA_FRAMES,(unsigned long)[last[@"renderPasses"] unsignedIntegerValue],(unsigned long)[last[@"renderDraws"] unsignedIntegerValue]);
#endif
    } @catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR consumer_stage=%s exception=%s reason=%.300s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
