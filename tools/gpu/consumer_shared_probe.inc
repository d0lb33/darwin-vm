// Exact-guest CARenderer -> owned IOSurface -> native IOMFB. Frame marker
// pixels are ordinary CALayers, never CPU paint or a driver scene special case.
#import <IOSurface/IOSurface.h>
#import <CommonCrypto/CommonDigest.h>
#include <time.h>
#include "present_layout.h"
static double sharedNow(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void sharedColor(CALayer *layer,CGColorSpaceRef space,uint32_t bgra){
    CGFloat values[]={((bgra>>16)&255)/255.,((bgra>>8)&255)/255.,(bgra&255)/255.,((bgra>>24)&255)/255.};
    CGColorRef color=CGColorCreate(space,values);layer.backgroundColor=color;CGColorRelease(color);
}
static unsigned sharedPixels(IOSurfaceRef surface,unsigned frame){
    uint32_t marker[]={0xff44564d,0xff505253,0xff424c52,0xff000000|frame};
    uint32_t background=frame%3==1?0xffff0000:frame%3==2?0xff00ff00:0xff0000ff;
    const uint8_t *data=IOSurfaceGetBaseAddress(surface);unsigned bad=0;
    for(unsigned y=0;y<DVM_PRESENT_HEIGHT;y++)for(unsigned x=0;x<DVM_PRESENT_WIDTH;x++){
        uint32_t pixel;memcpy(&pixel,data+y*DVM_PRESENT_ROW+x*4,4);
        if(pixel!=(y==0&&x<4?marker[x]:background))bad++;
    }
    return bad;
}
static void DVMRunQuartzCoreConsumer(id<MTLDevice> device){
    double start=sharedNow();const char *stage="mapping";CARenderer *renderer=nil;
    @try {
        NSError *mappingError=nil;id<DVMMetalOwnedMapping> mapping=DVMGetOwnedMetalMapping(device,&mappingError);
        if(!mapping){fprintf(stderr,"GPU_LOAD_MAPPING_ERROR domain=%s code=%ld reason=%.240s\n",mappingError.domain.UTF8String,(long)mappingError.code,mappingError.localizedDescription.UTF8String);fail("shared-provider-mapping");}
        CFStringRef *address=dlsym(RTLD_DEFAULT,"kIOSurfaceClientAddress"),*cache=dlsym(RTLD_DEFAULT,"kIOSurfaceCacheMode");
        if(!address||!*address||!cache||!*cache)fail("shared-surface-symbols");
        NSDictionary *properties=@{(__bridge id)*address:@((uintptr_t)mapping.bytes),(__bridge id)*cache:@0x700,
            (id)kIOSurfaceWidth:@DVM_PRESENT_WIDTH,(id)kIOSurfaceHeight:@DVM_PRESENT_HEIGHT,(id)kIOSurfaceBytesPerElement:@4,
            (id)kIOSurfaceBytesPerRow:@DVM_PRESENT_ROW,(id)kIOSurfaceAllocSize:@DVM_PRESENT_BUFFER_BYTES,(id)kIOSurfacePixelFormat:@(0x42475241)};
        stage="surface";IOSurfaceRef surface=[mapping respondsToSelector:@selector(iosurface)]?mapping.iosurface:NULL;
        if(surface)CFRetain(surface);else surface=IOSurfaceCreate((__bridge CFDictionaryRef)properties);
        if(!surface||IOSurfaceGetBaseAddress(surface)!=mapping.bytes)fail("shared-surface-alias");
        stage="texture";MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:DVM_PRESENT_WIDTH height:DVM_PRESENT_HEIGHT mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
        id<MTLTexture> texture=[device newTextureWithDescriptor:d iosurface:surface plane:0];
        if(!texture||texture.iosurface!=surface)fail("shared-texture-import");
        CFRelease(surface);surface=texture.iosurface;
        CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        stage="renderer";renderer=[CARenderer rendererWithMTLTexture:texture options:@{kCARendererMetalCommandQueue:[device newCommandQueue],kCARendererColorSpace:(__bridge id)space}];
        if(!renderer)fail("shared-renderer");
        [CATransaction begin];[CATransaction setDisableActions:YES];
        CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,DVM_PRESENT_WIDTH,DVM_PRESENT_HEIGHT);
        NSMutableArray *markers=[NSMutableArray array];
        for(unsigned i=0;i<4;i++){CALayer *m=[CALayer layer];m.frame=CGRectMake(i,0,1,1);[root addSublayer:m];[markers addObject:m];}
        renderer.layer=root;renderer.bounds=root.frame;[CATransaction commit];
        void *lib=dlopen("/System/Library/PrivateFrameworks/IOMobileFramebuffer.framework/IOMobileFramebuffer",RTLD_NOW|RTLD_LOCAL);
        int (*get)(void **)=dlsym(lib,"IOMobileFramebufferGetMainDisplay");
        int (*power)(void *,uint32_t)=dlsym(lib,"IOMobileFramebufferRequestPowerChange");
        int (*begin)(void *,uint32_t *)=dlsym(lib,"IOMobileFramebufferSwapBegin");
        int (*layer)(void *,uint32_t,IOSurfaceRef,CGRect,CGRect,uint32_t)=dlsym(lib,"IOMobileFramebufferSwapSetLayer");
        int (*end)(void *)=dlsym(lib,"IOMobileFramebufferSwapEnd");
        int (*wait)(void *,uint32_t,uint32_t,double)=dlsym(lib,"IOMobileFramebufferSwapWaitWithTimeout");
        if(!get||!power||!begin||!layer||!end||!wait)fail("shared-display-symbols");
        void *fb=NULL;if(get(&fb)||!fb||power(fb,1))fail("shared-display-open");
        fprintf(stderr,"GPU_LOAD_CA_SHARED_SETUP us=%.3f width=%u height=%u row=%u surface=%u frames=%u\n",(sharedNow()-start)*1e6,DVM_PRESENT_WIDTH,DVM_PRESENT_HEIGHT,DVM_PRESENT_ROW,IOSurfaceGetID(surface),DVM_CA_FRAMES);
        for(unsigned frame=1;frame<=DVM_CA_FRAMES;frame++){@autoreleasepool {
            double t0=sharedNow();stage="acquire";if(IOSurfaceLock(surface,0,NULL))fail("shared-lock");DVMSharedTextureAcquire(texture,frame);
            stage="layer-update";[CATransaction begin];[CATransaction setDisableActions:YES];
            sharedColor(root,space,frame%3==1?0xffff0000:frame%3==2?0xff00ff00:0xff0000ff);
            uint32_t words[]={0xff44564d,0xff505253,0xff424c52,0xff000000|frame};
            for(unsigned i=0;i<4;i++)sharedColor(markers[i],space,words[i]);[CATransaction commit];
            stage="render";[renderer beginFrameAtTime:frame/60. timeStamp:NULL];[renderer addUpdateRect:root.frame];[renderer render];[renderer endFrame];
            stage="GPU-completion";NSDictionary *completion=DVMSharedTextureSeal(texture,frame);double t1=sharedNow();
            if(IOSurfaceUnlock(surface,0,NULL))fail("shared-unlock");
            stage="display";uint32_t swap=UINT32_MAX;CGRect rect=root.frame;
            if(begin(fb,&swap)||layer(fb,0,surface,rect,rect,0)||end(fb))fail("shared-swap-submit");
            int rc=wait(fb,swap,1,2.0);if(rc){fprintf(stderr,"GPU_LOAD_ERROR shared_wait=0x%x frame=%u swap=%u\n",rc,frame,swap);fail("shared-swap-wait");}
            DVMSharedTextureRetire(texture,frame,swap,rc);double t2=sharedNow();
            fprintf(stderr,"GPU_LOAD_CA_SHARED_FRAME frame=%u swap=%u completed_writes=%llu render_us=%.3f display_us=%.3f total_us=%.3f\n",frame,swap,[completion[@"completedWrites"] unsignedLongLongValue],(t1-t0)*1e6,(t2-t1)*1e6,(t2-t0)*1e6);
        }}
        stage="final-pixels";if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL))fail("shared-final-lock");
        unsigned bad=sharedPixels(surface,DVM_CA_FRAMES);uint8_t digest[32];char sha[65];
        CC_SHA256(IOSurfaceGetBaseAddress(surface),DVM_PRESENT_BYTES,digest);for(unsigned i=0;i<32;i++)snprintf(sha+2*i,3,"%02x",digest[i]);
        if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL))fail("shared-final-unlock");
        fprintf(stderr,"GPU_LOAD_CA_SHARED_FINAL frames=%u bad_pixels=%u sha=%s verification_reads_in_batch=0\n",DVM_CA_FRAMES,bad,sha);
        if(power(fb,0))fail("shared-display-power-reset");
        fprintf(stderr,"GPU_LOAD_CA_SHARED_POWER_RESET rc=0\n");CGColorSpaceRelease(space);
        if(bad)fail("shared-pixels");
    } @catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR shared_stage=%s exception=%s reason=%.300s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
