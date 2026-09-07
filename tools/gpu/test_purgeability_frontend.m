// Host-native allocation and forwarding behavior, not exact guest evidence.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#include <assert.h>
static BOOL refused(void (^block)(void)){@try{block();}@catch(NSException *e){(void)e;return YES;}return NO;}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    __block uint64_t seq=0;__block unsigned bufferWrites=0;
    __block BOOL dropPurgeabilityReply=NO;
    __block BOOL throwPurgeabilityReply=NO;
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *request,NSError **outError){
        NSDictionary *reply=nil;@autoreleasepool{@synchronized(host){
            if([request[@"op"] isEqual:@"writeRenderBuffer"])bufferWrites++;
            reply=ProcessRequest(host,++seq,request);
        }}
        if(throwPurgeabilityReply&&[request[@"op"] isEqual:@"resourcePurgeable"]){
            throwPurgeabilityReply=NO;[NSException raise:@"InjectedTransportException" format:@"native state changed before exception"];
        }
        if(dropPurgeabilityReply&&[request[@"op"] isEqual:@"resourcePurgeable"]){
            dropPurgeabilityReply=NO;if(outError)*outError=[NSError errorWithDomain:@"lost-purgeability-reply" code:5 userInfo:nil];return nil;
        }
        if(![reply[@"ok"] boolValue]){if(outError)*outError=[NSError errorWithDomain:@"host-test" code:[reply[@"code"] integerValue] userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]}];return nil;}
        return reply;
    };
    id<MTLDevice> device=DVMCreateMetalDevice(rpc);id<MTLBuffer> buffer=[device newBufferWithLength:65536 options:0];assert(buffer);
    memset(buffer.contents,0x37,buffer.length);
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:80 width:16 height:16 mipmapped:NO];d.storageMode=0;d.usage=1;
    id<MTLTexture> alias=[buffer newTextureWithDescriptor:d offset:0 bytesPerRow:64];assert(alias);
    assert([alias setPurgeableState:MTLPurgeableStateVolatile]==MTLPurgeableStateNonVolatile&&bufferWrites==2);
    assert(refused(^{(void)buffer.contents;}));
    uint32_t pixelStorage[256];uint32_t *pixels=pixelStorage;assert(refused(^{[alias getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];}));
    MTLPurgeableState old=[buffer setPurgeableState:MTLPurgeableStateNonVolatile];assert(old==3||old==4);
    if(old==3){[alias getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];for(unsigned i=0;i<256;i++)assert(pixels[i]==0x37373737);}
    unsigned before=bufferWrites;assert([buffer setPurgeableState:3]==2);
    if(old==3)assert(bufferWrites==before); // No repeated full upload for retained bytes.
    assert([alias setPurgeableState:2]>=3);
    assert([alias setPurgeableState:4]==2);assert([buffer setPurgeableState:1]==4);assert([buffer setPurgeableState:2]==4);
    memset(buffer.contents,0x6a,buffer.length);before=bufferWrites;
    assert([buffer setPurgeableState:3]==2&&bufferWrites==before+2);assert([alias setPurgeableState:2]>=3);
    [alias getBytes:pixels bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];for(unsigned i=0;i<256;i++)assert(pixels[i]==0x6a6a6a6a);
    for(unsigned type=0;type<3;type++){
        MTLTextureDescriptor *td=[MTLTextureDescriptor new];td.textureType=type==0?MTLTextureType2D:type==1?MTLTextureType1D:MTLTextureType3D;
        td.width=16;td.height=type==1?1:4;td.depth=type==2?2:1;td.pixelFormat=type==1?23:80;td.storageMode=0;td.usage=1;
        id<MTLTexture> texture=[device newTextureWithDescriptor:td];assert(texture);
        NSUInteger row=16*(type==1?2:4),bytes=row*td.height*td.depth;NSMutableData *initial=[NSMutableData dataWithLength:bytes];memset(initial.mutableBytes,0x49,bytes);
        MTLRegion region=MTLRegionMake3D(0,0,0,td.width,td.height,td.depth);
        [texture replaceRegion:region mipmapLevel:0 slice:0 withBytes:initial.bytes bytesPerRow:row bytesPerImage:row*td.height];
        assert([texture setPurgeableState:3]==2);MTLPurgeableState current=[texture setPurgeableState:1];
        if(current>2)assert(refused(^{[texture getBytes:pixels bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];}));
        old=[texture setPurgeableState:2];assert(old>=2&&old<=4);
        NSMutableData *actual=[NSMutableData dataWithLength:bytes];
        if(old!=4){[texture getBytes:actual.mutableBytes bytesPerRow:row bytesPerImage:row*td.height fromRegion:region mipmapLevel:0 slice:0];assert([actual isEqual:initial]);}
        assert([texture setPurgeableState:4]==2);current=[texture setPurgeableState:1];assert(current==2||current==4);
        assert([texture setPurgeableState:2]==current);
        fprintf(stderr,"NATIVE_PURGEABILITY_PROFILE type=%lu bytes=%lu after_empty=%lu\n",(unsigned long)td.textureType,(unsigned long)bytes,(unsigned long)current);
        memset(initial.mutableBytes,0xa7,bytes);[texture replaceRegion:region mipmapLevel:0 slice:0 withBytes:initial.bytes bytesPerRow:row bytesPerImage:row*td.height];
        [texture getBytes:actual.mutableBytes bytesPerRow:row bytesPerImage:row*td.height fromRegion:region mipmapLevel:0 slice:0];assert([actual isEqual:initial]);
    }
    // Native views may report NonVolatile while the private root is Volatile.
    // The root, not that view hint, must gate both frontend and backend reuse.
    @autoreleasepool{
        MTLTextureDescriptor *vd=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:115 width:64 height:48 mipmapped:YES];vd.mipmapLevelCount=4;vd.storageMode=MTLStorageModePrivate;vd.usage=5;
        id<MTLTexture> root=[device newTextureWithDescriptor:vd],view=[root newTextureViewWithPixelFormat:115];assert(view);
        assert([root setPurgeableState:3]==2);
        assert([view setPurgeableState:1]==2);
        assert(refused(^{(void)[view newTextureViewWithPixelFormat:115];}));
        NSNumber *handle=[(id)view valueForKey:@"handle"];
        assert(![rpc(@{@"op":@"textureView",@"texture":handle,@"format":@115,@"type":@2,@"level":@0,@"levels":@1,@"slice":@0,@"slices":@1},NULL)[@"ok"] boolValue]);
        MTLPurgeableState prior=[root setPurgeableState:2];assert(prior==3||prior==4);
        assert([view newTextureViewWithPixelFormat:115]);
        throwPurgeabilityReply=YES;assert(refused(^{[view setPurgeableState:3];}));
        uint64_t before=seq;
        assert(refused(^{[root setPurgeableState:2];}));
        assert(refused(^{(void)[view newTextureViewWithPixelFormat:115];}));
        assert(seq==before); // Uncertain root blocks even an attempted recovery RPC.
    }
    dropPurgeabilityReply=YES;assert(refused(^{[buffer setPurgeableState:4];}));
    assert(refused(^{(void)buffer.contents;}));assert(refused(^{[alias setPurgeableState:2];}));
    MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:80 width:64 height:64 mipmapped:NO];td.storageMode=0;td.usage=5;
    id<MTLTexture> target=[device newTextureWithDescriptor:td];assert(target);
    dropPurgeabilityReply=YES;assert(refused(^{[target setPurgeableState:4];}));
    MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor new];pass.colorAttachments[0].texture=target;pass.colorAttachments[0].loadAction=2;pass.colorAttachments[0].storeAction=1;
    id<MTLCommandBuffer> cb=[[device newCommandQueue] commandBuffer];[[cb renderCommandEncoderWithDescriptor:pass] endEncoding];
    uint64_t beforeSubmit=host.submissions;[cb commit];[cb waitUntilCompleted];
    assert(cb.status==MTLCommandBufferStatusError&&cb.error&&host.submissions==beforeSubmit);
    // A resource RPC may precede a command awaiting client-queue admission.
    // Reject that command before native execution if its resource became volatile.
    id<MTLDevice> gatedDevice=DVMCreateMetalDevice(rpc);
    id<MTLTexture> gatedTexture=[gatedDevice newTextureWithDescriptor:td];assert(gatedTexture);
    dispatch_queue_t gate=dispatch_queue_create("test.purge.admission",DISPATCH_QUEUE_SERIAL);
    id<MTLCommandQueue> gatedQueue=[gatedDevice newCommandQueue];
    [(id)gatedQueue setValue:gate forKey:@"submissionQueue"];
    MTLRenderPassDescriptor *gatedPass=[MTLRenderPassDescriptor new];
    gatedPass.colorAttachments[0].texture=gatedTexture;gatedPass.colorAttachments[0].loadAction=2;gatedPass.colorAttachments[0].storeAction=1;
    id<MTLCommandBuffer> gated=[gatedQueue commandBuffer];[[gated renderCommandEncoderWithDescriptor:gatedPass] endEncoding];
    dispatch_semaphore_t gateDone=dispatch_semaphore_create(0);
    [gated addCompletedHandler:^(id<MTLCommandBuffer> c){(void)c;dispatch_semaphore_signal(gateDone);}];
    id<MTLTexture> unrelated=[gatedDevice newTextureWithDescriptor:td];assert(unrelated);
    dispatch_suspend(gate);[gated commit];
    uint32_t texel=0xff123456;
    [unrelated replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&texel bytesPerRow:4];
    uint32_t copied=0;[unrelated getBytes:&copied bytesPerRow:4 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];assert(copied==texel);
    assert(refused(^{[gatedTexture replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&texel bytesPerRow:4];}));
    uint64_t nativeBefore=host.submissions;
    [gatedTexture setPurgeableState:MTLPurgeableStateVolatile];
    dispatch_resume(gate);
    assert(!dispatch_semaphore_wait(gateDone,dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC)));
    assert(gated.status==MTLCommandBufferStatusError&&gated.error&&host.submissions==nativeBefore);
    [gatedTexture setPurgeableState:MTLPurgeableStateNonVolatile];
    [gatedTexture replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&texel bytesPerRow:4];
    [gatedTexture getBytes:&copied bytesPerRow:4 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];assert(copied==texel);
    id<MTLBuffer> copySource=[gatedDevice newBufferWithLength:256 options:0],copyTarget=[gatedDevice newBufferWithLength:256 options:0];
    MTLTextureDescriptor *aliasDescriptor=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:80 width:16 height:1 mipmapped:NO];aliasDescriptor.storageMode=0;aliasDescriptor.usage=1;
    id<MTLTexture> copyAlias=[copySource newTextureWithDescriptor:aliasDescriptor offset:0 bytesPerRow:64];assert(copyAlias);
    memset(copySource.contents,0x6b,256);
    id<MTLCommandBuffer> copy=[gatedQueue commandBuffer];id<MTLBlitCommandEncoder> blit=[copy blitCommandEncoder];
    [blit copyFromBuffer:copySource sourceOffset:0 toBuffer:copyTarget destinationOffset:0 size:256];[blit endEncoding];
    [copy addCompletedHandler:^(id<MTLCommandBuffer> c){(void)c;dispatch_semaphore_signal(gateDone);}];
    dispatch_suspend(gate);[copy commit];
    assert(refused(^{[copyAlias replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&texel bytesPerRow:4];}));
    dispatch_resume(gate);assert(!dispatch_semaphore_wait(gateDone,dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC)));
    assert(copy.status==MTLCommandBufferStatusCompleted&&!memcmp(copySource.contents,copyTarget.contents,256));
    [copyAlias replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&texel bytesPerRow:4];
    puts("RESOURCE_USE_PASS unrelated_upload_read=1 pending_target_rejected=1 completion_releases_use=1 volatile_preflight_cancels=1 buffer_alias_inflight_rejected=1 blit_pixels=1");
    puts("PASS purgeability frontend: pending CPU writes retained, native Empty reacquisition, buffer/linear alias state, stale upload cache invalidation, no redundant retained upload, 1D/2D/3D texture refill, volatile CPU-access rejection and lost-ack CPU/GPU quarantine");
}}
