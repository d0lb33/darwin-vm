// Full frontend/backend host test. External IOSurface address support on this
// host is checked explicitly; this is not evidence about the exact guest.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#import <IOSurface/IOSurface.h>
#include <dlfcn.h>
static void check(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static unsigned mappingReleased;
@interface TestOwnedMapping : NSObject <DVMMetalOwnedMapping>
@property void *bytes;
@property NSUInteger length;
@property int fd;
@end
@implementation TestOwnedMapping
- (NSUInteger)mappingVersion {return 1;}
- (void)dealloc {if(_bytes){munmap(_bytes,_length);close(_fd);mappingReleased++;}}
@end
int main(void){@autoreleasepool {
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    int transport=open(getenv("DVM_DRIVER_PRESENT_RAM"),O_RDONLY);uint8_t session[16];
    check(transport>=0&&pread(transport,session,16,16)==16,"test session");close(transport);
    NSData *sessionData=[NSData dataWithBytes:session length:16];
    __block unsigned providerCalls=0;__block uint64_t seq=0;
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *r,NSError **e){@synchronized(host){
        NSDictionary *reply=ProcessRequest(host,++seq,r);
        if(![reply[@"ok"] boolValue]){if(e)*e=[NSError errorWithDomain:reply[@"domain"] code:[reply[@"code"] integerValue] userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]}];return nil;}return reply;
    }};
    @autoreleasepool {
        id<MTLDevice> device=DVMCreateSharedMetalDevice(rpc,^id<DVMMetalOwnedMapping>(NSError **e){
            (void)e;providerCalls++;TestOwnedMapping *mapping=[TestOwnedMapping new];int fd=-1;
            mapping.bytes=DVMMapManaged(sessionData.bytes,&fd);mapping.fd=fd;mapping.length=DVM_PRESENT_BUFFER_BYTES;return mapping;
        });
        id<DVMMetalOwnedMapping> mapping=DVMGetOwnedMetalMapping(device,NULL);
        check(mapping!=nil&&DVMGetOwnedMetalMapping(device,NULL)==mapping&&providerCalls==1,"retained provider mapping");
        CFStringRef *address=dlsym(RTLD_DEFAULT,"kIOSurfaceClientAddress"),*cache=dlsym(RTLD_DEFAULT,"kIOSurfaceCacheMode");
        check(address&&*address&&cache&&*cache,"host IOSurface address/cache symbols");
        NSDictionary *props=@{(__bridge id)*address:@((uintptr_t)mapping.bytes),(__bridge id)*cache:@0x700,
            (id)kIOSurfaceWidth:@DVM_PRESENT_WIDTH,(id)kIOSurfaceHeight:@DVM_PRESENT_HEIGHT,(id)kIOSurfaceBytesPerElement:@4,
            (id)kIOSurfaceBytesPerRow:@DVM_PRESENT_ROW,(id)kIOSurfaceAllocSize:@DVM_PRESENT_BUFFER_BYTES,(id)kIOSurfacePixelFormat:@(0x42475241)};
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)props);
        fprintf(stderr,"HOST_SURFACE surface=%p requested=%p returned=%p bytes=%zu planes=%zu\n",surface,mapping.bytes,surface?IOSurfaceGetBaseAddress(surface):NULL,surface?IOSurfaceGetAllocSize(surface):0,surface?IOSurfaceGetPlaneCount(surface):0);
        check(surface&&IOSurfaceGetBaseAddress(surface)==mapping.bytes,"host IOSurface original mapping alias");
        MTLTextureDescriptor *desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:DVM_PRESENT_WIDTH height:DVM_PRESENT_HEIGHT mipmapped:NO];
        desc.storageMode=MTLStorageModeShared;desc.usage=MTLTextureUsageShaderRead|MTLTextureUsageRenderTarget;
        NSMutableDictionary *foreignProps=[props mutableCopy];[foreignProps removeObjectForKey:(__bridge id)*address];
        IOSurfaceRef foreign=IOSurfaceCreate((__bridge CFDictionaryRef)foreignProps);
        check(foreign!=NULL&&IOSurfaceGetBaseAddress(foreign)!=mapping.bytes,"foreign surface fixture");
        check(![device newTextureWithDescriptor:desc iosurface:foreign plane:0],"reject foreign backing");CFRelease(foreign);
        check(![device newTextureWithDescriptor:desc iosurface:surface plane:1],"reject plane");
        desc.usage=MTLTextureUsageShaderRead;check(![device newTextureWithDescriptor:desc iosurface:surface plane:0],"reject unsupported usage");desc.usage|=MTLTextureUsageRenderTarget;
        id<MTLTexture> texture=[device newTextureWithDescriptor:desc iosurface:surface plane:0];
        check(texture&&texture.iosurface==surface&&texture.iosurfacePlane==0&&texture.allocatedSize==DVM_PRESENT_BUFFER_BYTES,"owned surface metadata");
        CFRelease(surface);surface=texture.iosurface;
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(unsigned epoch=1;epoch<=3;epoch++){@autoreleasepool {
            check(IOSurfaceLock(surface,0,NULL)==0,"lock owned surface");
            DVMSharedTextureAcquire(texture,epoch);
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;
            pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            pass.colorAttachments[0].clearColor=MTLClearColorMake(epoch==3,epoch==2,epoch==1,1);
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];[encoder endEncoding];
            [cb commit];[cb waitUntilCompleted];check(cb.status==MTLCommandBufferStatusCompleted,"native completion through frontend");
            DVMSharedTextureSeal(texture,epoch);check(!mappingReleased,"mapping retained through GPU and handoff");
            check(!IOSurfaceUnlock(surface,0,NULL),"unlock owned surface");
            // Synthetic host wait report; no DCP is involved in this test.
            DVMSharedTextureRetire(texture,epoch,epoch,0);
        }}
        check(!IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL),"final lock");
        const uint8_t *bytes=IOSurfaceGetBaseAddress(surface);unsigned bad=0;
        for(unsigned y=0;y<DVM_PRESENT_HEIGHT;y++)for(unsigned x=0;x<DVM_PRESENT_WIDTH;x++){
            const uint8_t *p=bytes+y*DVM_PRESENT_ROW+x*4;if(p[0]||p[1]||p[2]!=255||p[3]!=255)bad++;
        }
        check(!IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL)&&!bad,"final shared pixels");
        fprintf(stderr,"HOST_SURFACE verified=1 frames=3 bad_pixels=%u bytes=%u\n",bad,DVM_PRESENT_BUFFER_BYTES);
    }
    NSUInteger live=1;
    for(unsigned i=0;i<1000&&live;i++){@synchronized(host){live=host.entries.count;}if(live)usleep(1000);}
    for(unsigned i=0;i<1000&&!mappingReleased;i++)usleep(1000);
    check(!live&&mappingReleased==1,"texture and mapping retirement");
    fprintf(stderr,"PASS owned IOSurface frontend: alias, GPU completion, lease reuse, metadata and mapping retirement\n");
    return 0;
}}
