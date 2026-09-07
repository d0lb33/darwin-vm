// Mac Catalyst control: host UIKit/QuartzCore, never exact-guest evidence.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <QuartzCore/CARenderer.h>
#import <QuartzCore/CALayer.h>
#import <QuartzCore/CATransaction.h>
#include "consumer_uikit_scene.inc"
#include "consumer_uikit_raster_scene.inc"
#include "consumer_uikit_display_scene.inc"
#include <stdio.h>
#include <unistd.h>
#import <objc/runtime.h>
#include "driver_capabilities.h"
// Test-only capability-matched native control. This does not change the
// production device's advertised profile or backend dispatch implementation.
#define DVM_NATIVE_BOOL(selector,value) static BOOL DVMNative_##selector(id object,SEL command){(void)object;(void)command;return value;}
#define DVM_NATIVE_UINT(selector,value) static NSUInteger DVMNative_##selector(id object,SEL command){(void)object;(void)command;return value;}
DVM_CAPABILITY_QUERIES(DVM_NATIVE_BOOL,DVM_NATIVE_UINT)
#undef DVM_NATIVE_BOOL
#undef DVM_NATIVE_UINT
static BOOL DVMNativeFamily(id object,SEL command,NSUInteger family){(void)object;(void)command;(void)family;return NO;}
static BOOL DVMNativeSamples(id object,SEL command,NSUInteger count){(void)object;(void)command;return count==1;}
static BOOL DVMNativeQueryEnabled(const char *name){
    const char *selected=getenv("DVM_UIKIT_NATIVE_QUERIES");
    return !selected||[[@(selected) componentsSeparatedByString:@","] containsObject:@(name)];
}
static void DVMNativeContract(Class cls){
#define DVM_INSTALL_BOOL(selector,value) if(DVMNativeQueryEnabled(#selector))class_replaceMethod(cls,sel_registerName(#selector),(IMP)DVMNative_##selector,"B@:");
#define DVM_INSTALL_UINT(selector,value) if(DVMNativeQueryEnabled(#selector))class_replaceMethod(cls,sel_registerName(#selector),(IMP)DVMNative_##selector,"Q@:");
    DVM_CAPABILITY_QUERIES(DVM_INSTALL_BOOL,DVM_INSTALL_UINT)
#undef DVM_INSTALL_BOOL
#undef DVM_INSTALL_UINT
    if(DVMNativeQueryEnabled("supportsFamily:"))class_replaceMethod(cls,@selector(supportsFamily:),(IMP)DVMNativeFamily,"B@:Q");
    if(DVMNativeQueryEnabled("supportsFeatureSet:"))class_replaceMethod(cls,@selector(supportsFeatureSet:),(IMP)DVMNativeFamily,"B@:Q");
    if(DVMNativeQueryEnabled("supportsTextureSampleCount:"))class_replaceMethod(cls,@selector(supportsTextureSampleCount:),(IMP)DVMNativeSamples,"B@:Q");
    fprintf(stderr,"UIKIT_HOST_NATIVE_CONTRACT version=%u queries=%s scope=test-process-only\n",DVM_CONTRACT_VERSION,getenv("DVM_UIKIT_NATIVE_QUERIES")?:"all");
}
// Observe the native loader's requested URL. An explicit host-only control can
// replace its FAT archive with the same selected AIR used by the forwarder.
static unsigned nativeLibraryCalls;
@interface NSObject (DVMUIKitLibraryControl)
- (id<MTLLibrary>)newDVMDiagnosticLibraryWithURL:(NSURL *)url error:(NSError **)error;
- (id<MTLRenderPipelineState>)newDVMDiagnosticPipelineWithDescriptor:(MTLRenderPipelineDescriptor *)d error:(NSError **)error;
@end
@implementation NSObject (DVMUIKitLibraryControl)
- (id<MTLRenderPipelineState>)newDVMDiagnosticPipelineWithDescriptor:(MTLRenderPipelineDescriptor *)d error:(NSError **)error {
    BOOL main=[NSThread isMainThread];
    fprintf(stderr,"UIKIT_HOST_NATIVE_PIPELINE vertex=%s fragment=%s main=%u\n",d.vertexFunction.name.UTF8String,d.fragmentFunction.name.UTF8String,main);
    // A bounded delay tests first-use fallback on native Metal; it is never a
    // production workaround and does not delay the forwarded implementation.
    const char *delay=getenv("DVM_UIKIT_NATIVE_PIPELINE_DELAY_US");
    if(delay&&!main)usleep((useconds_t)atoi(delay));
    return [self newDVMDiagnosticPipelineWithDescriptor:d error:error];
}
- (id<MTLLibrary>)newDVMDiagnosticLibraryWithURL:(NSURL *)url error:(NSError **)error {
    nativeLibraryCalls++;
    fprintf(stderr,"UIKIT_HOST_NATIVE_LIBRARY requested=%s substitution=%u\n",url.path.UTF8String,getenv("DVM_UIKIT_NATIVE_AIR")!=NULL);
    const char *path=getenv("DVM_UIKIT_NATIVE_AIR");
    if(!path)return [self newDVMDiagnosticLibraryWithURL:url error:error];
    NSData *bytes=[NSData dataWithContentsOfFile:@(path) options:0 error:error];
    if(!bytes)return nil;
    dispatch_data_t data=dispatch_data_create(bytes.bytes,bytes.length,NULL,^{(void)bytes;});
    return [(id<MTLDevice>)self newLibraryWithData:data error:error];
}
@end
#ifdef DVM_UIKIT_FORWARDED
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#endif
static void require(BOOL ok,const char *why){if(!ok){fprintf(stderr,"UIKIT_HOST_FAIL %s\n",why);exit(1);}}
static void display(CALayer *layer){[layer setNeedsDisplay];[layer displayIfNeeded];for(CALayer *child in layer.sublayers)display(child);}
int main(int argc,const char **argv){@autoreleasepool{
    require(argc==2,"output directory");
    CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();require(device!=nil,"native Metal device");
#ifndef DVM_UIKIT_FORWARDED
    Method replacement=class_getInstanceMethod(NSObject.class,@selector(newDVMDiagnosticLibraryWithURL:error:));
    Class cls=[device class];
    require(class_addMethod(cls,@selector(newDVMDiagnosticLibraryWithURL:error:),method_getImplementation(replacement),method_getTypeEncoding(replacement)),"native library observer");
    method_exchangeImplementations(class_getInstanceMethod(cls,@selector(newLibraryWithURL:error:)),class_getInstanceMethod(cls,@selector(newDVMDiagnosticLibraryWithURL:error:)));
    replacement=class_getInstanceMethod(NSObject.class,@selector(newDVMDiagnosticPipelineWithDescriptor:error:));
    require(class_addMethod(cls,@selector(newDVMDiagnosticPipelineWithDescriptor:error:),method_getImplementation(replacement),method_getTypeEncoding(replacement)),"native pipeline observer");
    method_exchangeImplementations(class_getInstanceMethod(cls,@selector(newRenderPipelineStateWithDescriptor:error:)),class_getInstanceMethod(cls,@selector(newDVMDiagnosticPipelineWithDescriptor:error:)));
#endif
    if(getenv("DVM_UIKIT_NATIVE_CONTRACT"))DVMNativeContract([device class]);
    id<MTLCommandQueue> queue=[device newCommandQueue];
#ifdef DVM_UIKIT_FORWARDED
    DVMHost *host=[DVMHost new];host.device=device;host.queue=queue;host.entries=[NSMutableDictionary dictionary];
    NSString *capture=[@(argv[1]) stringByAppendingPathComponent:@"requests.jsonl"];
    [[NSData data] writeToFile:capture atomically:YES];
    NSFileHandle *log=[NSFileHandle fileHandleForWritingAtPath:capture];
    __block uint64_t sequence=0;
    device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **error){
        @synchronized(host){
            NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
            uint64_t seq=++sequence;
            NSMutableDictionary *recorded=[r mutableCopy];recorded[@"seq"]=@(seq);
            NSDictionary *reply;
            if(getenv("DVM_UIKIT_DIAGNOSTIC_UNSPLIT")&&[r[@"op"] isEqual:@"renderSubmit"]){
                // Host-only diagnosis of the captured trusted UIKit workload.
                // This is not exposed in the driver or an acceptance shortcut.
                NSDictionary *check=RenderSubmitPart(host,seq,r,YES);
                reply=[check[@"ok"] boolValue]?RenderSubmitPart(host,seq,r,NO):check;
            }else reply=ProcessRequest(host,seq,r);
            NSData *line=[NSJSONSerialization dataWithJSONObject:@{@"seq":@(seq),@"op":r[@"op"],@"request":recorded,@"reply":reply,@"wire_encoding":@"json"} options:0 error:nil];
            [log writeData:line];[log writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]];
            if(![reply[@"ok"] boolValue]){if(error)*error=[NSError errorWithDomain:@"UIKitHostTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]?:@"RPC failed"}];return nil;}
            return [NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] options:0 error:nil];
        }
    });
    queue=[device newCommandQueue];
#endif
    [CATransaction begin];[CATransaction setDisableActions:YES];
    UIView *view=DVMUIKitScene(space,320,480);[view layoutIfNeeded];display(view.layer);
    if(getenv("DVM_UIKIT_GUEST_RASTERS")){
        DVMUIKitReplaceGlyphs(view,space,@(getenv("DVM_UIKIT_GUEST_RASTERS")));
        [view layoutIfNeeded];display(view.layer);
    }
    [CATransaction commit];[CATransaction flush];
    BOOL displayed=getenv("DVM_UIKIT_DISPLAY_FRAME")!=NULL;
    unsigned width=displayed?1179:320,height=displayed?2556:480;
    CALayer *root=displayed?DVMUIKitDisplayScene(view,space):view.layer;
    if(getenv("DVM_UIKIT_DISPLAY_ANIMATE"))DVMUIKitDisplayUpdate(view,(unsigned)atoi(getenv("DVM_UIKIT_DISPLAY_FRAME")));
    if(displayed){
        unsigned frame=(unsigned)atoi(getenv("DVM_UIKIT_DISPLAY_FRAME"));
        uint32_t words[]={0xff44564d,0xff505253,0xff424c52,0xff000000|frame};
        for(unsigned i=0;i<4;i++){
            CALayer *marker=[CALayer layer];marker.frame=CGRectMake(i,0,1,1);
            CGFloat c[]={((words[i]>>16)&255)/255.,((words[i]>>8)&255)/255.,(words[i]&255)/255.,1};
            CGColorRef color=CGColorCreate(space,c);marker.backgroundColor=color;CGColorRelease(color);[root addSublayer:marker];
        }
    }
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:width height:height mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    id<MTLTexture> target=[device newTextureWithDescriptor:d];require(target!=nil,"target");
    CARenderer *renderer=[CARenderer rendererWithMTLTexture:target options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(__bridge id)space,@"kCARendererFlags":@2}];
    require(renderer!=nil,"renderer");
    [CATransaction begin];[CATransaction setDisableActions:YES];renderer.layer=root;renderer.bounds=root.bounds;
    [CATransaction commit];[CATransaction flush];
    fprintf(stderr,"UIKIT_HOST_CONTENTS flipped=%u\n",view.layer.contentsAreFlipped);
    unsigned frames=getenv("DVM_UIKIT_HOST_FRAMES")?(unsigned)atoi(getenv("DVM_UIKIT_HOST_FRAMES")):1;
    require(frames==1||frames==3,"frame bound");
    NSMutableData *gpu=[NSMutableData dataWithLength:width*height*4],*cpu=[gpu mutableCopy];
    NSString *out=@(argv[1]);
    CFTimeInterval frameTime=CACurrentMediaTime();
    for(unsigned frame=0;frame<frames;frame++){
    [CATransaction begin];[CATransaction setDisableActions:YES];view.layer.opacity=frame%2?.99:1;
    if(getenv("DVM_UIKIT_HOST_ANIMATE")){
        UIView *card=view.subviews[1];card.frame=CGRectMake(frame%2?28:20,88,280,200);
        card.alpha=frame%2?.7:1;
    }
    [CATransaction commit];[CATransaction flush];
    [renderer beginFrameAtTime:frameTime+frame/60.0 timeStamp:NULL];[renderer addUpdateRect:root.bounds];[renderer render];[renderer endFrame];
    id<MTLCommandBuffer> fence=[queue commandBuffer];[fence commit];[fence waitUntilCompleted];
    require(fence.status==MTLCommandBufferStatusCompleted,"completion");
    [target getBytes:gpu.mutableBytes bytesPerRow:width*4 fromRegion:MTLRegionMake2D(0,0,width,height) mipmapLevel:0];
    require([gpu writeToFile:[out stringByAppendingPathComponent:[NSString stringWithFormat:@"gpu-frame-%u.bgra",frame]] atomically:YES],"frame output");
    fprintf(stderr,"UIKIT_HOST_FRAME index=%u completed=1 model_opacity=%.3f time_offset=%.9f\n",frame,view.layer.opacity,frame/60.0);
    }
    CGContextRef context=CGBitmapContextCreate(cpu.mutableBytes,width,height,8,width*4,space,kCGImageAlphaPremultipliedFirst|kCGBitmapByteOrder32Little);
    CGContextTranslateCTM(context,0,height);CGContextScaleCTM(context,1,-1);[root renderInContext:context];
    require([gpu writeToFile:[out stringByAppendingPathComponent:@"gpu.bgra"] atomically:YES],"GPU output");
    require([cpu writeToFile:[out stringByAppendingPathComponent:@"cpu.bgra"] atomically:YES],"CPU output");
    unsigned different=0,maxError=0;uint64_t totalError=0;const uint8_t *a=gpu.bytes,*b=cpu.bytes;
    for(unsigned i=0;i<width*height*4;i++){unsigned delta=abs((int)a[i]-(int)b[i]);different+=delta>2;maxError=MAX(maxError,delta);totalError+=delta;}
    fprintf(stderr,"UIKIT_HOST_COMPARE different=%u max=%u total=%llu scope=host-catalyst-only\n",different,maxError,(unsigned long long)totalError);
    if(getenv("DVM_UIKIT_NATIVE_AIR"))require(nativeLibraryCalls>0,"native AIR control was exercised");
    renderer.layer=nil;renderer=nil;CGContextRelease(context);CGColorSpaceRelease(space);
}}
