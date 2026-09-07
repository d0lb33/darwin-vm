// Native macOS QuartzCore rehearsal of the test layer definitions/oracle.
// This does not exercise the guest AIR or the forwarding driver.
#import <Metal/Metal.h>
#import <QuartzCore/QuartzCore.h>
#include <stdio.h>
#include "present_layout.h"
static void fail(const char *s){fprintf(stderr,"FAIL %s\n",s);exit(1);}
static void sharedColor(CALayer *layer,CGColorSpaceRef space,uint32_t bgra){
    CGFloat v[]={((bgra>>16)&255)/255.,((bgra>>8)&255)/255.,(bgra&255)/255.,((bgra>>24)&255)/255.};
    CGColorRef c=CGColorCreate(space,v);layer.backgroundColor=c;CGColorRelease(c);
}
#include "consumer_shared_scene.inc"
int main(int argc,const char **argv){@autoreleasepool {
    if(argc!=2)fail("output directory argument");
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();id<MTLCommandQueue> queue=[device newCommandQueue];
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:DVM_PRESENT_WIDTH height:DVM_PRESENT_HEIGHT mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    id<MTLTexture> texture=[device newTextureWithDescriptor:d];if(!texture)fail("native texture");
    CGColorSpaceRef space=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    CARenderer *renderer=[CARenderer rendererWithMTLTexture:texture options:@{kCARendererMetalCommandQueue:queue,kCARendererColorSpace:(__bridge id)space}];
    [CATransaction begin];[CATransaction setDisableActions:YES];
    CALayer *root=[CALayer layer];root.frame=CGRectMake(0,0,DVM_PRESENT_WIDTH,DVM_PRESENT_HEIGHT);
    CALayer *moving=sharedSceneCreate(root,space);NSMutableArray *markers=[NSMutableArray array];
    for(unsigned i=0;i<4;i++){CALayer *m=[CALayer layer];m.frame=CGRectMake(i,0,1,1);[root addSublayer:m];[markers addObject:m];}
    renderer.layer=root;renderer.bounds=root.frame;[CATransaction commit];
    // Exercise left/right clipping, front-layer overlap and repeated reuse.
    for(NSNumber *number in @[@1,@8,@20,@59,@60,@64,@128]){
        unsigned frame=number.unsignedIntValue;
        [CATransaction begin];[CATransaction setDisableActions:YES];sharedSceneUpdate(root,moving,space,frame);
        uint32_t words[]={0xff44564d,0xff505253,0xff424c52,0xff000000|frame};
        for(unsigned i=0;i<4;i++)sharedColor(markers[i],space,words[i]);[CATransaction commit];
        [renderer beginFrameAtTime:frame/60. timeStamp:NULL];[renderer addUpdateRect:root.frame];[renderer render];[renderer endFrame];
        id<MTLCommandBuffer> fence=[queue commandBuffer];[fence commit];[fence waitUntilCompleted];
        if(fence.status!=MTLCommandBufferStatusCompleted)fail("native GPU completion");
        NSMutableData *pixels=[NSMutableData dataWithLength:DVM_PRESENT_BYTES];
        [texture getBytes:pixels.mutableBytes bytesPerRow:DVM_PRESENT_ROW fromRegion:MTLRegionMake2D(0,0,DVM_PRESENT_WIDTH,DVM_PRESENT_HEIGHT) mipmapLevel:0];
        NSString *path=[NSString stringWithFormat:@"%s/scene-%u-frame-%u.bgra",argv[1],DVM_CA_SCENE,frame];
        if(![pixels writeToFile:path atomically:YES])fail("pixel file");
        fprintf(stderr,"NATIVE_SHARED_SCENE scene=%u frame=%u status=4\n",DVM_CA_SCENE,frame);
    }
    CGColorSpaceRelease(space);
}}
