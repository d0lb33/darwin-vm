// Exact-guest CARenderer -> owned IOSurface -> native IOMFB. Frame marker
// pixels are ordinary CALayers, never CPU paint or a driver scene special case.
#import <IOSurface/IOSurfaceRef.h>
#import <CommonCrypto/CommonDigest.h>
#include <time.h>
#include <mach/mach.h>
#include <unistd.h>
#include "present_layout.h"
#ifndef DVM_CA_HZ
#define DVM_CA_HZ 0
#endif
static double sharedNow(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static void sharedColor(CALayer *layer,CGColorSpaceRef space,uint32_t bgra){
    CGFloat values[]={((bgra>>16)&255)/255.,((bgra>>8)&255)/255.,(bgra&255)/255.,((bgra>>24)&255)/255.};
    CGColorRef color=CGColorCreate(space,values);layer.backgroundColor=color;CGColorRelease(color);
}
#ifdef DVM_CA_UIKIT
#include "consumer_uikit_support.inc"
#include "consumer_uikit_display_scene.inc"
#else
#include "consumer_shared_scene.inc"
static unsigned sharedPixels(IOSurfaceRef surface,unsigned frame){
    uint32_t marker[]={0xff44564d,0xff505253,0xff424c52,0xff000000|frame};
    uint32_t background=frame%3==1?0xffff0000:frame%3==2?0xff00ff00:0xff0000ff;
    const uint8_t *data=IOSurfaceGetBaseAddress(surface);unsigned bad=0;
    for(unsigned y=0;y<DVM_PRESENT_HEIGHT;y++)for(unsigned x=0;x<DVM_PRESENT_WIDTH;x++){
        uint32_t pixel;memcpy(&pixel,data+y*DVM_PRESENT_ROW+x*4,4);
        uint32_t expected=background;int tolerance=0;
        if(DVM_CA_SCENE){
            unsigned left=(frame*17)%(DVM_PRESENT_WIDTH-159);
            BOOL green=x>=280&&x<360,red=!green&&x>=left&&x<left+160;
            if(DVM_CA_SCENE==2)red=red&&x>=80&&x<DVM_PRESENT_WIDTH-80;
            if(DVM_CA_SCENE==3&&red&&x>=left+80){red=NO;green=YES;}
            expected=green?0xff00ff00:red?0xffff0000:0xff0000ff;
            if(DVM_CA_SCENE==1&&red){expected=0xff800080;tolerance=1;}
            if(DVM_CA_SCENE==4&&(red||green)){expected=green?0xff008080:0xff800080;tolerance=1;}
        }
        if(y==0&&x<4){expected=marker[x];tolerance=0;}
        if((pixel>>24)!=255)bad++;
        else for(unsigned c=0;c<3;c++)if(abs((int)((pixel>>(c*8))&255)-(int)((expected>>(c*8))&255))>tolerance){bad++;break;}
    }
    return bad;
}
#endif
static void sharedMemory(id<MTLDevice> device,unsigned frame){
    task_vm_info_data_t info={0};mach_msg_type_number_t count=TASK_VM_INFO_COUNT;
    kern_return_t kr=task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&info,&count);
    if(kr||count<TASK_VM_INFO_REV1_COUNT)fail("shared-memory-query");
    NSDictionary *stats=[(id<DVMConsumerCompletion>)device consumerCompletion];
    if(!stats[@"live"])fail("shared-resource-query");
    fprintf(stderr,"GPU_LOAD_CA_SHARED_MEMORY frame=%u resident=%llu footprint=%llu objects=%llu resource_bytes=%llu\n",frame,
        (unsigned long long)info.resident_size,(unsigned long long)info.phys_footprint,
        [stats[@"live"][@"objects"] unsignedLongLongValue],[stats[@"live"][@"resourceBytes"] unsignedLongLongValue]);
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
        #ifdef DVM_CA_UIKIT
        NSDictionary *rendererOptions=@{kCARendererMetalCommandQueue:[device newCommandQueue],kCARendererColorSpace:(__bridge id)space,@"kCARendererFlags":@2};
#else
        NSDictionary *rendererOptions=@{kCARendererMetalCommandQueue:[device newCommandQueue],kCARendererColorSpace:(__bridge id)space};
#endif
        stage="renderer";renderer=[CARenderer rendererWithMTLTexture:texture options:rendererOptions];
        if(!renderer)fail("shared-renderer");
        [CATransaction begin];[CATransaction setDisableActions:YES];
        #ifdef DVM_CA_UIKIT
        UIView *view=DVMUIKitScene(space,320,480);[view layoutIfNeeded];UIKitDisplay(view.layer);
        UIKitInspect(view.layer,"after-display",0);
        CALayer *root=DVMUIKitDisplayScene(view,space);
#else
        CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,DVM_PRESENT_WIDTH,DVM_PRESENT_HEIGHT);
        CALayer *moving=sharedSceneCreate(root,space);
#endif
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
        fprintf(stderr,"GPU_LOAD_CA_SHARED_SETUP us=%.3f width=%u height=%u row=%u surface=%u frames=%u hz=%u warmup=2 profile=1 scene=%u\n",(sharedNow()-start)*1e6,DVM_PRESENT_WIDTH,DVM_PRESENT_HEIGHT,DVM_PRESENT_ROW,IOSurfaceGetID(surface),DVM_CA_FRAMES,DVM_CA_HZ,DVM_CA_SCENE);
        double origin=sharedNow(),pacingStart=0;
        for(unsigned frame=1;frame<=DVM_CA_FRAMES;frame++){@autoreleasepool {
            double target=-1;
            if(frame>=3){
                if(frame==3)pacingStart=sharedNow();
                if(DVM_CA_HZ){
                    target=pacingStart+(frame-3)/(double)(DVM_CA_HZ?DVM_CA_HZ:1);
                    double remaining;
                    while((remaining=target-sharedNow())>0)usleep((useconds_t)(remaining*1e6)+1);
                }
            }
            double t0=sharedNow();stage="acquire";if(IOSurfaceLock(surface,0,NULL))fail("shared-lock");DVMSharedTextureAcquire(texture,frame);
            double acquired=sharedNow();
            stage="layer-update";[CATransaction begin];[CATransaction setDisableActions:YES];
#ifndef DVM_CA_UIKIT
            sharedSceneUpdate(root,moving,space,frame);
#elif defined(DVM_CA_UIKIT_ANIMATE)
            DVMUIKitDisplayUpdate(view,frame);
#endif
            uint32_t words[]={0xff44564d,0xff505253,0xff424c52,0xff000000|frame};
            for(unsigned i=0;i<4;i++)sharedColor(markers[i],space,words[i]);[CATransaction commit];
            double updated=sharedNow();
            stage="render";[renderer beginFrameAtTime:frame/60. timeStamp:NULL];[renderer addUpdateRect:root.frame];[renderer render];[renderer endFrame];
            double encoded=sharedNow();
            stage="GPU-completion";NSDictionary *completion=DVMSharedTextureSeal(texture,frame);double t1=sharedNow();
            if(IOSurfaceUnlock(surface,0,NULL))fail("shared-unlock");
            stage="display";uint32_t swap=UINT32_MAX;CGRect rect=root.frame;
            if(begin(fb,&swap)||layer(fb,0,surface,rect,rect,0)||end(fb))fail("shared-swap-submit");
            double enqueued=sharedNow();
            int rc=wait(fb,swap,1,2.0);if(rc){fprintf(stderr,"GPU_LOAD_ERROR shared_wait=0x%x frame=%u swap=%u\n",rc,frame,swap);fail("shared-swap-wait");}
            double waited=sharedNow();
            DVMSharedTextureRetire(texture,frame,swap,rc);double t2=sharedNow();
            fprintf(stderr,"GPU_LOAD_CA_SHARED_FRAME frame=%u swap=%u completed_writes=%llu render_us=%.3f display_us=%.3f total_us=%.3f start_us=%.3f finish_us=%.3f target_us=%.3f acquire_us=%.3f update_us=%.3f encode_us=%.3f seal_us=%.3f enqueue_us=%.3f wait_us=%.3f retire_us=%.3f\n",frame,swap,[completion[@"completedWrites"] unsignedLongLongValue],(t1-t0)*1e6,(t2-t1)*1e6,(t2-t0)*1e6,(t0-origin)*1e6,(t2-origin)*1e6,target<0?-1:(target-origin)*1e6,(acquired-t0)*1e6,(updated-acquired)*1e6,(encoded-updated)*1e6,(t1-encoded)*1e6,(enqueued-t1)*1e6,(waited-enqueued)*1e6,(t2-waited)*1e6);
            // Sampling is outside the measured work interval. Its cost still
            // contributes to the next paced frame's schedule lateness.
            if(frame==2||frame%128==0||frame==DVM_CA_FRAMES)sharedMemory(device,frame);
        }}
        stage="final-pixels";if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL))fail("shared-final-lock");
        #ifdef DVM_CA_UIKIT
        // Full-frame acceptance belongs to the independently produced native
        // reference. Hash and ownership alone are not pixel correctness.
        uint8_t digest[32];char sha[65];
#else
        unsigned bad=sharedPixels(surface,DVM_CA_FRAMES);uint8_t digest[32];char sha[65];
#endif
        CC_SHA256(IOSurfaceGetBaseAddress(surface),DVM_PRESENT_BYTES,digest);for(unsigned i=0;i<32;i++)snprintf(sha+2*i,3,"%02x",digest[i]);
        if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL))fail("shared-final-unlock");
        #ifdef DVM_CA_UIKIT
        fprintf(stderr,"GPU_LOAD_CA_SHARED_FINAL frames=%u reference=external-native status=pending sha=%s verification_reads_in_batch=0\n",DVM_CA_FRAMES,sha);
#else
        fprintf(stderr,"GPU_LOAD_CA_SHARED_FINAL frames=%u bad_pixels=%u sha=%s verification_reads_in_batch=0\n",DVM_CA_FRAMES,bad,sha);
        #endif
        if(power(fb,0))fail("shared-display-power-reset");
        fprintf(stderr,"GPU_LOAD_CA_SHARED_POWER_RESET rc=0\n");CGColorSpaceRelease(space);
        #ifndef DVM_CA_UIKIT
        if(bad)fail("shared-pixels");
#endif
    } @catch(NSException *e){fprintf(stderr,"GPU_LOAD_ERROR shared_stage=%s exception=%s reason=%.300s\n",stage,e.name.UTF8String,e.reason.UTF8String);exit(1);}
}
