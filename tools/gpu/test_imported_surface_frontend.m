// Native-host rehearsal of the complete forwarding path over synthetic page
// registrations. This supplies no evidence about XNU or DCP completion.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#import <IOSurface/IOSurface.h>
#include <dlfcn.h>
#include <assert.h>
static unsigned retiredMappings;
@interface ImportedTestMapping : NSObject <DVMMetalImportedMapping>
@property(nonatomic,strong) id surface;
@property(nonatomic,strong) NSString *ack;
@property(nonatomic) BOOL retired;
@end
@implementation ImportedTestMapping
- (NSUInteger)mappingVersion{return 2;}
- (uint64_t)resourceID{return 1;}
- (void *)bytes{return IOSurfaceGetBaseAddress((__bridge IOSurfaceRef)_surface);}
- (NSUInteger)length{return IOSurfaceGetAllocSize((__bridge IOSurfaceRef)_surface);}
- (BOOL)retire{assert(!_retired&&[[NSFileManager defaultManager] fileExistsAtPath:_ack]);_retired=YES;retiredMappings++;return YES;}
@end
int main(void){@autoreleasepool{
    char root[]="/tmp/dvm-import-test-XXXXXX";assert(mkdtemp(root));NSString *dir=@(root);
    NSString *control=[dir stringByAppendingPathComponent:@"control"],*ram=[dir stringByAppendingPathComponent:@"ram"],*pages=[dir stringByAppendingPathComponent:@"pages"],*imports=[pages stringByAppendingString:@".imports"];
    assert(!mkdir(imports.fileSystemRepresentation,0700));
    setenv("DVM_DRIVER_PRESENT_RAM",control.fileSystemRepresentation,1);setenv("DVM_DRIVER_MANAGED_RAM",ram.fileSystemRepresentation,1);setenv("DVM_DRIVER_MANAGED_PAGES",pages.fileSystemRepresentation,1);
    uint8_t header[32]={0};DVMPagePut(header,UINT64_C(0x144564d31));memset(header+16,0x37,16);
    int c=open(control.fileSystemRepresentation,O_RDWR|O_CREAT|O_EXCL,0600);assert(c>=0&&!ftruncate(c,0x1000000)&&pwrite(c,header,32,0)==32);close(c);
    int f=open(ram.fileSystemRepresentation,O_RDWR|O_CREAT|O_EXCL,0600);assert(f>=0&&!ftruncate(f,DVM_SURFACE_DRAM_BYTES));
    void *alias=mmap(NULL,32768,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(alias!=MAP_FAILED);
    assert(mmap(alias,16384,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,f,16384)==alias);
    assert(mmap((uint8_t *)alias+16384,16384,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_FIXED,f,0)==(uint8_t *)alias+16384);
    memset(alias,0,32768);
    uint8_t record[80]={0};DVMPagePut(record,DVM_SURFACE_MAGIC);DVMPagePut(record+8,1);memcpy(record+16,header+16,16);
    DVMPagePut(record+32,1);DVMPagePut(record+40,32768);DVMPagePut(record+56,2);DVMPagePut(record+64,16384);
    NSString *manifest=[imports stringByAppendingPathComponent:@"0000000000000001.pages"],*ack=[imports stringByAppendingPathComponent:@"0000000000000001.retired"];
    int p=open(manifest.fileSystemRepresentation,O_RDWR|O_CREAT|O_EXCL,0600);assert(p>=0&&write(p,record,80)==80);close(p);
    CFStringRef *address=dlsym(RTLD_DEFAULT,"kIOSurfaceClientAddress");assert(address&&*address);
    IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(__bridge id)*address:@((uintptr_t)alias),
        (id)kIOSurfaceWidth:@64,(id)kIOSurfaceHeight:@64,(id)kIOSurfaceBytesPerElement:@8,
        (id)kIOSurfaceBytesPerRow:@512,(id)kIOSurfaceAllocSize:@32768,(id)kIOSurfacePixelFormat:@0x52476841});
    assert(surface&&IOSurfaceGetBaseAddress(surface)==alias&&IOSurfaceGetAllocSize(surface)==32768);
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    __block uint64_t seq=0;__block unsigned registrations=0;NSMapTable *cache=[NSMapTable strongToWeakObjectsMapTable];
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *r,NSError **e){
        NSDictionary *reply=nil;@autoreleasepool{@synchronized(host){reply=ProcessRequest(host,++seq,r);}}
        // Create NSError after the native-resource pool drains: an autoreleasing
        // out parameter must remain alive through the caller's ARC writeback.
        if(getenv("DVM_IMPORT_DROP_RETIRE_REPLY")&&[reply[@"retiredSurface"] unsignedLongLongValue]){
            if(e)*e=[NSError errorWithDomain:@"injected-lost-retirement-reply" code:1 userInfo:nil];return nil;
        }
        if(![reply[@"ok"] boolValue]){if(e)*e=[NSError errorWithDomain:@"test" code:[reply[@"code"] integerValue] userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]}];return nil;}return reply;
    };
    id<MTLDevice> device=DVMCreateBinaryMetalDevice(rpc);
    DVMEnableSurfaceImports(device,^id<DVMMetalImportedMapping>(IOSurfaceRef s,NSError **e){
        (void)e;ImportedTestMapping *m=[cache objectForKey:@1];if(m&&!m.retired)return m;
        registrations++;m=[ImportedTestMapping new];m.surface=(__bridge id)s;m.ack=ack;[cache setObject:m forKey:@1];return m;
    });
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:115 width:64 height:64 mipmapped:NO];d.storageMode=0;d.usage=5;
    assert(![device newTextureWithDescriptor:d iosurface:surface plane:1]&&!registrations);
    d.usage=2;assert(![device newTextureWithDescriptor:d iosurface:surface plane:0]&&!registrations);d.usage=5;
    @autoreleasepool{
        id<MTLTexture> a=[device newTextureWithDescriptor:d iosurface:surface plane:0];
        id<MTLTexture> b=[device newTextureWithDescriptor:d iosurface:surface plane:0];
        assert(a&&b&&registrations==1&&host.imports.count==1&&host.importedBytes==32768);
        assert([a setPurgeableState:MTLPurgeableStateKeepCurrent]==MTLPurgeableStateNonVolatile);
        BOOL refused=NO;@try{[a setPurgeableState:MTLPurgeableStateVolatile];}@catch(NSException *e){refused=[e.reason containsString:@"pinned IOSurface volatility"];}
        assert(refused&&[b setPurgeableState:MTLPurgeableStateKeepCurrent]==MTLPurgeableStateNonVolatile);
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(unsigned frame=0;frame<8;frame++){@autoreleasepool{
            if(getenv("DVM_IMPORT_BACKEND_ONLY")){
                // Native validation assumes Apple resource subclasses in the
                // host descriptor setters. Exercise backend native resources
                // separately; do not call this branch a frontend render test.
                NSNumber *handle=[(id)(frame&1?a:b) valueForKey:@"handle"];
                NSDictionary *reply=rpc(@{@"op":@"renderSubmit",@"commands":@[@{@"kind":@"render",@"target":handle,
                    @"load":@2,@"store":@1,@"clear":@[@(frame==7?2:0),@(-.5),@.25,@1],@"operations":@[]}],@"uploads":@[],@"readbacks":@[]},NULL);
                assert(reply&&[reply[@"renderPasses"] unsignedIntValue]==1);continue;
            }
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=frame&1?a:b;
            pass.colorAttachments[0].loadAction=2;pass.colorAttachments[0].storeAction=1;
            pass.colorAttachments[0].clearColor=MTLClearColorMake(frame==7?2:0,-.5,.25,1);
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];assert(encoder);[encoder endEncoding];[cb commit];[cb waitUntilCompleted];
            assert(cb.status==MTLCommandBufferStatusCompleted&&!cb.error&&!retiredMappings);
        }}
        const uint16_t *pixel=alias;unsigned bad=0;for(unsigned i=0;i<4096;i++)if(pixel[i*4]!=0x4000||pixel[i*4+1]!=0xb800||pixel[i*4+2]!=0x3400||pixel[i*4+3]!=0x3c00)bad++;
        assert(!bad);a=nil;
        // A synchronous allocation drains the preceding asynchronous release.
        id<MTLBuffer> barrier=[device newBufferWithLength:16 options:0];assert(barrier&&!retiredMappings&&host.imports.count==1);barrier=nil;
        assert(![[NSFileManager defaultManager] fileExistsAtPath:ack]);
        fprintf(stderr,"HOST_IMPORT frames=8 pixels=4096 bad=%u registrations=%u live_bytes=%lu aliases=1\n",bad,registrations,(unsigned long)host.importedBytes);
    }
    id<MTLBuffer> barrier=[device newBufferWithLength:16 options:0];assert(barrier&&!host.imports.count&&!host.importedBytes);
    if(getenv("DVM_IMPORT_DROP_RETIRE_REPLY")){
        assert(!retiredMappings);BOOL rejected=NO;
        @try{[[[device newCommandQueue] commandBuffer] commit];}@catch(NSException *e){rejected=[e.reason containsString:@"import ownership failed"];}
        assert(rejected);puts("PASS lost retirement reply: no provider unpin, later GPU reuse rejected despite empty queue");
    }else assert(retiredMappings==1);
    NSString *why=nil;assert(!DVMOpenImportedPages(1,&why)&&[why isEqual:@"retired surface ID"]);
    CFRelease(surface);assert(!munmap(alias,32768));close(f);
    assert([[NSFileManager defaultManager] removeItemAtPath:dir error:nil]);
    printf("PASS host imported IOSurface: render_path=%s shared alias reuse, native signed/HDR pixels, no readback RPC, last-alias acknowledgement before provider retirement, replay rejected\n",getenv("DVM_IMPORT_BACKEND_ONLY")?"backend-native":"frontend-forwarded");
}}
