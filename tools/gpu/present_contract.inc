/* Exact 24A5430a IOMobileFramebuffer wrappers, extracted image:
 * GetMainDisplay 0x22a395f2c; GetDisplaySize 0x22a3bf234 (CGSize*),
 * SwapBegin 0x22a3911b0 (uint32_t*), SwapEnd 0x22a391750;
 * kern_SwapSetLayer 0x22a3918f4 embeds its full CGRect-by-value signature.
 * kern_SwapWaitWithTimeout 0x22a3c2314 takes two uint32_t plus double seconds.
 * This small prerequisite uses CPU pixels, not the GPU acceptance workload. */
#import <IOSurface/IOSurface.h>
#import <CoreGraphics/CGGeometry.h>
static void DVMDisplayContract(void) {
    void *lib=dlopen("/System/Library/PrivateFrameworks/IOMobileFramebuffer.framework/IOMobileFramebuffer",RTLD_NOW|RTLD_LOCAL);
    if(!lib)fail("present-framework-load");
    int (*get)(void **)=dlsym(lib,"IOMobileFramebufferGetMainDisplay");
    int (*size)(void *,CGSize *)=dlsym(lib,"IOMobileFramebufferGetDisplaySize");
    int (*power)(void *,uint32_t)=dlsym(lib,"IOMobileFramebufferRequestPowerChange");
    int (*begin)(void *,uint32_t *)=dlsym(lib,"IOMobileFramebufferSwapBegin");
    int (*layer)(void *,uint32_t,IOSurfaceRef,CGRect,CGRect,uint32_t)=dlsym(lib,"IOMobileFramebufferSwapSetLayer");
    int (*end)(void *)=dlsym(lib,"IOMobileFramebufferSwapEnd");
    int (*wait)(void *,uint32_t,uint32_t,double)=dlsym(lib,"IOMobileFramebufferSwapWaitWithTimeout");
    if(!get||!size||!power||!begin||!layer||!end||!wait)fail("present-symbol");
    double start=now();void *fb=NULL;
    fprintf(stderr,"GPU_LOAD_PRESENT_ENTER op=get-main\n");
    int rc=get(&fb);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN op=get-main rc=0x%x fb=%p us=%.0f\n",rc,fb,(now()-start)*1e6);
    if(rc||!fb)fail("present-get-main");
    CGSize dimensions={0};rc=size(fb,&dimensions);
    fprintf(stderr,"GPU_LOAD_PRESENT_RETURN op=size rc=0x%x width=%.0f height=%.0f\n",rc,dimensions.width,dimensions.height);
    if(rc||dimensions.width!=1179||dimensions.height!=2556)fail("present-display-size");
    rc=power(fb,1);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN op=power-on rc=0x%x\n",rc);if(rc)fail("present-power-on");
    CFStringRef *cacheMode=dlsym(RTLD_DEFAULT,"kIOSurfaceCacheMode");
    if(!cacheMode||!*cacheMode)fail("present-cache-mode-symbol");
    fprintf(stderr,"GPU_LOAD_PRESENT_CACHE requested=0x700\n");
    const unsigned w=1179,h=2556,row=4864,bytes=row*h;
    IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(__bridge id)*cacheMode:@0x700,(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@(row),(id)kIOSurfaceAllocSize:@(bytes),(id)kIOSurfacePixelFormat:@(0x42475241)});
    if(!surface||IOSurfaceGetBytesPerRow(surface)!=row||IOSurfaceGetAllocSize(surface)<bytes)fail("present-surface");
    CGRect rect={{0,0},{w,h}};
    for(unsigned frame=1;frame<=3;frame++) {
        if(IOSurfaceLock(surface,0,NULL))fail("present-lock");
        uint32_t *pixels=IOSurfaceGetBaseAddress(surface);
        for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)pixels[(size_t)y*(row/4)+x]=0xff000000u|((x*255/w)<<16)|((y*255/h)<<8)|(frame*70);
        pixels[0]=0xff44564d;pixels[1]=0xff505253;pixels[2]=0xff424c52;pixels[3]=0xff000000|frame;
        if(IOSurfaceUnlock(surface,0,NULL))fail("present-unlock");
        uint32_t swap=UINT32_MAX;
        rc=begin(fb,&swap);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN frame=%u op=begin rc=0x%x swap=%u surface=%u\n",frame,rc,swap,IOSurfaceGetID(surface));if(rc)fail("present-begin");
        rc=layer(fb,0,surface,rect,rect,0);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN frame=%u op=layer rc=0x%x\n",frame,rc);if(rc)fail("present-layer");
        rc=end(fb);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN frame=%u op=end rc=0x%x\n",frame,rc);if(rc)fail("present-end");
        rc=wait(fb,swap,0,2.0);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN frame=%u op=wait rc=0x%x\n",frame,rc);if(rc)fail("present-wait");
    }
    rc=power(fb,0);fprintf(stderr,"GPU_LOAD_PRESENT_RETURN op=power-off rc=0x%x\n",rc);if(rc)fail("present-power-off");
    // Keep the final surface alive until the short-lived diagnostic exits.
    fprintf(stderr,"GPU_LOAD_PRESENT_CONTRACT pass=1 frames=3 scope=swap-api-returns-not-pixel-proof\n");
}
