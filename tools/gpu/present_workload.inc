#import <IOSurface/IOSurface.h>
#import <CoreGraphics/CGGeometry.h>
#include "present_reference.h"
static void DVMRunPresentedBlur(id<MTLDevice>device,NSData*air,Namespace*ns,void *bundle,double setupStart) {
    id (*create)(id<MTLDevice>,id<MTLLibrary>,uint32_t)=dlsym(bundle,"DVMCreateResidentBlur");
    NSDictionary *(*draw)(id,uint32_t)=dlsym(bundle,"DVMDrawResidentBlur");
    NSDictionary *(*verify)(id)=dlsym(bundle,"DVMVerifyResidentBlur");
    if(!create||!draw||!verify)fail("present-driver-extension");
    void *lib=dlopen("/System/Library/PrivateFrameworks/IOMobileFramebuffer.framework/IOMobileFramebuffer",RTLD_NOW|RTLD_LOCAL);
    if(!lib)fail("present-framework");
    int (*get)(void **)=dlsym(lib,"IOMobileFramebufferGetMainDisplay");
    int (*size)(void *,CGSize *)=dlsym(lib,"IOMobileFramebufferGetDisplaySize");
    int (*power)(void *,uint32_t)=dlsym(lib,"IOMobileFramebufferRequestPowerChange");
    int (*begin)(void *,uint32_t *)=dlsym(lib,"IOMobileFramebufferSwapBegin");
    int (*layer)(void *,uint32_t,IOSurfaceRef,CGRect,CGRect,uint32_t)=dlsym(lib,"IOMobileFramebufferSwapSetLayer");
    int (*end)(void *)=dlsym(lib,"IOMobileFramebufferSwapEnd");
    int (*wait)(void *,uint32_t,uint32_t,double)=dlsym(lib,"IOMobileFramebufferSwapWaitWithTimeout");
    if(!get||!size||!power||!begin||!layer||!end||!wait)fail("present-display-symbol");
    void *fb=NULL;int rc=get(&fb);if(rc||!fb)fail("present-display-open");
    CGSize dimensions={0};if(size(fb,&dimensions)||dimensions.width!=1179||dimensions.height!=2556)fail("present-display-size");
    rc=power(fb,1);if(rc){fprintf(stderr,"GPU_LOAD_ERROR power-on=0x%x\n",rc);fail("present-power-on");}
    CFStringRef *cacheMode=dlsym(RTLD_DEFAULT,"kIOSurfaceCacheMode");if(!cacheMode||!*cacheMode)fail("present-cache-key");
    IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(__bridge id)*cacheMode:@0x700,(id)kIOSurfaceWidth:@DVM_PRESENT_WIDTH,(id)kIOSurfaceHeight:@DVM_PRESENT_HEIGHT,(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@DVM_PRESENT_ROW,(id)kIOSurfaceAllocSize:@DVM_PRESENT_BYTES,(id)kIOSurfacePixelFormat:@(0x42475241)});
    if(!surface||IOSurfaceGetBytesPerRow(surface)!=DVM_PRESENT_ROW||IOSurfaceGetAllocSize(surface)<DVM_PRESENT_BYTES)fail("present-surface-geometry");
    NSError *error=nil;dispatch_data_t data=dispatch_data_create(air.bytes,air.length,dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE,0),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    id<MTLLibrary> library=[device newLibraryWithData:data error:&error];if(!library)fail("present-library");
    uint32_t nonce=arc4random();id resource=create(device,library,nonce);if(!resource)fail("present-resident-create");
    fprintf(stderr,"GPU_LOAD_PRESENT_SETUP us=%.0f nonce=%u surface=%u cache=0x700 width=1179 height=2556 row=4864\n",(now()-setupStart)*1e6,nonce,IOSurfaceGetID(surface));
    struct Sample {double gpu,draw,delivery,swap,total;uint32_t swapID;} samples[33];
    CGRect rect={{0,0},{1179,2556}};double batchStart=now(),steadyStart=0;
    for(uint32_t frame=1;frame<=33;frame++){@autoreleasepool{
        if(frame==2)steadyStart=now();double t0=now();NSDictionary *reply=draw(resource,frame);double t1=now();
        if(IOSurfaceLock(surface,0,NULL))fail("present-surface-lock");
        mmioBarrier();memcpy(IOSurfaceGetBaseAddress(surface),ns.shared+DVM_PRESENT_OFFSET,DVM_PRESENT_BYTES);
        if(IOSurfaceUnlock(surface,0,NULL))fail("present-surface-unlock");double t2=now();
        uint32_t swap=UINT32_MAX;rc=begin(fb,&swap);if(rc)fail("present-begin");
        rc=layer(fb,0,surface,rect,rect,0);if(rc)fail("present-layer");
        rc=end(fb);if(rc){fprintf(stderr,"GPU_LOAD_ERROR frame=%u swap-end=0x%x\n",frame,rc);fail("present-end");}
        // Match the guest's WaitSurface (0x22a3bf7c4): mode 1 waits for
        // pending surface work; mode 0 additionally tracks the current image.
        rc=wait(fb,swap,1,2.0);if(rc){fprintf(stderr,"GPU_LOAD_ERROR frame=%u swap-wait=0x%x\n",frame,rc);fail("present-wait");}
        double t3=now();samples[frame-1]=(struct Sample){[reply[@"gpu_us"] doubleValue],(t1-t0)*1e6,(t2-t1)*1e6,(t3-t2)*1e6,(t3-t0)*1e6,swap};
        if(frame%8==0)fprintf(stderr,"GPU_LOAD_PRESENT_PROGRESS frame=%u\n",frame);
    }}
    double finished=now();fprintf(stderr,"GPU_LOAD_PRESENT_BATCH frames=33 wall_us=%.0f steady_wall_us=%.0f verification_reads=0\n",(finished-batchStart)*1e6,(finished-steadyStart)*1e6);
    for(unsigned i=0;i<33;i++)fprintf(stderr,"GPU_LOAD_PRESENT_FRAME frame=%u gpu_us=%.3f draw_us=%.0f delivery_us=%.0f swap_us=%.0f total_us=%.0f swap=%u\n",i+1,samples[i].gpu,samples[i].draw,samples[i].delivery,samples[i].swap,samples[i].total,samples[i].swapID);
    rc=power(fb,0);fprintf(stderr,"GPU_LOAD_PRESENT_POWER_RESET request=0 rc=0x%x\n",rc);if(rc)fail("present-power-reset");
    // Verification begins only after the final timed swap and batch timestamp.
    if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL))fail("present-final-lock");
    unsigned bad=DVMVerifyPresented(IOSurfaceGetBaseAddress(surface),nonce,33);
    uint8_t sha[32];char text[65];CC_SHA256(IOSurfaceGetBaseAddress(surface),DVM_PRESENT_BYTES,sha);for(unsigned i=0;i<32;i++)snprintf(text+i*2,3,"%02x",sha[i]);
    if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL)||bad)fail("present-final-pixels");
    NSDictionary *checked=verify(resource);if(![checked[@"verified"] boolValue])fail("present-host-final");
    fprintf(stderr,"GPU_LOAD_PRESENT_FINAL verified=1 frame=33 nonce=%u sha=%s bad_pixels=0\n",nonce,text);
    CFRelease(surface);
}
