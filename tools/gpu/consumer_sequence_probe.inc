// Test scene only: no scene identity or shader-name branch exists in the driver.
// Retain one renderer/target and move an opaque red layer behind a green layer.
// Verify the first, second and final frames; no readback within the timed batch.
#include <time.h>
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
        if(p[0]!=(green||red?0:255)||p[1]!=(green?255:0)||p[2]!=(red?255:0)||p[3]!=255)bad++;
    }
    fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_PIXELS frame=%u bad_pixels=%u\n",frame,bad);
    if(bad)fail("consumer-sequence-pixels");
}
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device) {
    const char *stage="sequence-init";CARenderer *renderer=nil;
    double setup=DVMConsumerTime(),samples[DVM_CA_FRAMES],sum=0;
    NSUInteger peakObjects=0,peakBytes=0;unsigned misses=0;
    @try {
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        id<MTLTexture> texture=[device newTextureWithDescriptor:d];
        if(!texture)fail("consumer-sequence-target");
        CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        renderer=[CARenderer rendererWithMTLTexture:texture options:@{kCARendererMetalCommandQueue:[device newCommandQueue],kCARendererColorSpace:(__bridge id)space}];
        if(!renderer)fail("consumer-sequence-renderer");
        [CATransaction begin];[CATransaction setDisableActions:YES];
        CALayer *root=[CALayer layer],*moving=[CALayer layer],*front=[CALayer layer];
        root.frame=CGRectMake(0,0,64,64);moving.frame=CGRectMake(0,0,16,64);front.frame=CGRectMake(28,0,8,64);
        DVMConsumerColor(root,space,0,0,1);DVMConsumerColor(moving,space,1,0,0);DVMConsumerColor(front,space,0,1,0);
        CGColorSpaceRelease(space);[root addSublayer:moving];[root addSublayer:front];
        renderer.layer=root;renderer.bounds=root.frame;[CATransaction commit];
        setup=DVMConsumerTime()-setup;
        NSDictionary *last=nil;
        for(unsigned frame=0;frame<DVM_CA_FRAMES;frame++)@autoreleasepool {
            double start=DVMConsumerTime();
            [CATransaction begin];[CATransaction setDisableActions:YES];
            moving.frame=CGRectMake((frame*7)%49,0,16,64);[CATransaction commit];
            stage="sequence-begin";[renderer beginFrameAtTime:frame/60.0 timeStamp:NULL];
            [renderer addUpdateRect:root.frame];
            stage="sequence-render";[renderer render];[renderer endFrame];
            stage="sequence-completion";last=[(id<DVMConsumerCompletion>)device consumerCompletion];
            samples[frame]=(DVMConsumerTime()-start)*1e6;
            if(frame>=2){sum+=samples[frame];if(samples[frame]>1000000.0/60)misses++;}
            peakObjects=MAX(peakObjects,[last[@"live"][@"objects"] unsignedIntegerValue]);
            peakBytes=MAX(peakBytes,[last[@"live"][@"resourceBytes"] unsignedIntegerValue]);
            if(frame<2||frame==DVM_CA_FRAMES-1){stage="sequence-pixels";DVMConsumerCheck(texture,frame);}
        }
        if(DVM_CA_FRAMES<3||[last[@"renderPasses"] unsignedIntegerValue]<DVM_CA_FRAMES)fail("consumer-sequence-submissions");
        double sorted[DVM_CA_FRAMES];memcpy(sorted,samples,sizeof(samples));
        // Small bounded array; keep libc imports unchanged for the exact guest.
        for(unsigned i=3;i<DVM_CA_FRAMES;i++){double value=sorted[i];unsigned j=i;while(j>2&&sorted[j-1]>value){sorted[j]=sorted[j-1];j--;}sorted[j]=value;}
        unsigned n=DVM_CA_FRAMES-2;
        fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_TIMING frames=%u setup_us=%.3f first_us=%.3f steady_us=%.3f p50_us=%.3f p95_us=%.3f max_us=%.3f misses_60=%u peak_objects=%lu peak_bytes=%lu\n",DVM_CA_FRAMES,setup*1e6,samples[0],sum,sorted[2+(n-1)/2],sorted[2+(n-1)*95/100],sorted[DVM_CA_FRAMES-1],misses,(unsigned long)peakObjects,(unsigned long)peakBytes);
        fprintf(stderr,"GPU_LOAD_CA_SEQUENCE_VERIFIED width=64 height=64 frames=%u passes=%lu draws=%lu bad_pixels=0\n",DVM_CA_FRAMES,(unsigned long)[last[@"renderPasses"] unsignedIntegerValue],(unsigned long)[last[@"renderDraws"] unsignedIntegerValue]);
    } @catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR consumer_stage=%s exception=%s reason=%.300s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
