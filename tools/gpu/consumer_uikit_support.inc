#ifndef DVM_CA_RENDERER_FLAGS
#define DVM_CA_RENDERER_FLAGS 0
#endif
// Actual guest UIKit content and diagnostics shared by offscreen/display tests.
// CPU layer rendering is captured separately; it is not a byte-exact GPU oracle.
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
