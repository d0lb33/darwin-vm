// Host execution test of the real frontend/backend and framed JSON values.
// Runtime-compiled test shaders are injected only into this test's library table;
// production library loading and exact-guest shader evidence remain separate.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"

static void require(BOOL ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
int main(void){@autoreleasepool {
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();
    host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\n"
      "struct O{float4 p[[position]];};\n"
      "vertex O v(uint i[[vertex_id]],device uint *s[[buffer(30)]],constant uint &step[[buffer(8)]]){"
      "if(i%3==0){s[0]+=step;s[16384]+=7;}float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return O{float4(p[i%3],0,1)};}\n"
      "fragment float4 f(constant float4 &color[[buffer(30)]]){return color;}\n"
      "fragment float4 fw(device uint *s[[buffer(0)]]){s[0]=1;return float4(1,0,0,1);}";
    id<MTLLibrary> native=[host.device newLibraryWithSource:source options:nil error:&error];
    require(native!=nil,error.description.UTF8String);
    __block uint64_t seq=0;__block BOOL corrupt=NO;__block unsigned chunks=0;
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *request,NSError **outError){
        @synchronized(host) {
        // Round-trip every request/reply through JSON, including writeback IDs.
        NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
        NSDictionary *reply;
        if([r[@"op"] isEqual:@"library"]){DVMEntry *e=nil;require(Add(host,@"library",native,&e),"test library allocation");reply=@{@"ok":@YES,@"handle":@(e.handle),@"functionNames":native.functionNames};}
        else reply=ProcessRequest(host,++seq,r);
        if([r[@"op"] isEqual:@"readRenderBuffer"]){chunks++;if(corrupt&&[r[@"offset"] unsignedIntegerValue]>=32768){NSMutableDictionary *bad=[reply mutableCopy];bad[@"offset"]=@0;reply=bad;}}
        reply=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:reply options:0 error:nil] options:0 error:nil];
        if(![reply[@"ok"] boolValue]){if(outError)*outError=[NSError errorWithDomain:@"HostTest" code:[reply[@"code"] integerValue] userInfo:@{NSLocalizedDescriptionKey:reply[@"description"]?:@"failure"}];return nil;}
        return reply;
        }
    };
    @autoreleasepool {
        id<MTLDevice> d=DVMCreateMetalDevice(rpc);id<MTLCommandQueue> q=[d newCommandQueue];
        id<MTLLibrary> lib=[d newLibraryWithData:dispatch_data_create("test",4,NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT) error:&error];
        MTLRenderPipelineDescriptor *desc=[MTLRenderPipelineDescriptor new];
        desc.vertexFunction=[lib newFunctionWithName:@"v"];desc.fragmentFunction=[lib newFunctionWithName:@"f"];desc.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
        id<MTLRenderPipelineState> pipeline=[d newRenderPipelineStateWithDescriptor:desc error:&error];require(pipeline!=nil,error.description.UTF8String);
        desc.fragmentFunction=[lib newFunctionWithName:@"fw"];
        require([d newRenderPipelineStateWithDescriptor:desc error:&error]==nil,"unverified fragment writes rejected");
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];td.usage=MTLTextureUsageRenderTarget;td.storageMode=MTLStorageModeShared;
        id<MTLTexture> t=[d newTextureWithDescriptor:td];id<MTLBuffer> b=[d newBufferWithLength:65552 options:MTLResourceStorageModeShared];
        uint32_t step=3;float red[4]={1,0,0,1};
        uint32_t *words=b.contents;words[0]=100;words[16384]=200;
        for(unsigned iteration=0;iteration<3;iteration++){@autoreleasepool {
            if(iteration==1){words[0]=900;[b didModifyRange:NSMakeRange(0,4)];}
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=t;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
            id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:pass];[e setRenderPipelineState:pipeline];[e setVertexBuffer:b offset:0 atIndex:30];[e setVertexBytes:&step length:4 atIndex:8];[e setFragmentBytes:red length:16 atIndex:30];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];if(iteration==0)[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:3 vertexCount:3];[e endEncoding];
            dispatch_semaphore_t done=dispatch_semaphore_create(0);__block BOOL observed=NO;
            [cb addCompletedHandler:^(id<MTLCommandBuffer> completed){observed=completed.status==MTLCommandBufferStatusCompleted&&words[16384]==200+(iteration+2)*7;dispatch_semaphore_signal(done);}];
            [cb commit];[cb waitUntilCompleted];long wait=dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,NSEC_PER_SEC));fprintf(stderr,"WRITEBACK iteration=%u first=%u last=%u status=%lu callback=%u\n",iteration,words[0],words[16384],(unsigned long)cb.status,observed);require(wait==0&&observed,"completion publishes all chunks");
            require(words[0]==(iteration?900+iteration*3:106)&&words[16384]==200+(iteration+2)*7,"CPU/GPU reuse coherence");
        }}
        uint8_t pixels[16384];[t getBytes:pixels bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];for(unsigned i=0;i<4096;i++)require(!pixels[i*4]&&!pixels[i*4+1]&&pixels[i*4+2]==255&&pixels[i*4+3]==255,"render pixels");
        require(chunks==9,"three chunks per written buffer");
        MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=t;pass.colorAttachments[0].storeAction=MTLStoreActionStore;
        uint64_t before=host.submissions;
        id<MTLCommandBuffer> bad=[q commandBuffer];id<MTLRenderCommandEncoder> e=[bad renderCommandEncoderWithDescriptor:pass];[e setRenderPipelineState:pipeline];[e setVertexBytes:words length:4 atIndex:30];[e setVertexBytes:&step length:4 atIndex:8];[e setFragmentBytes:red length:16 atIndex:30];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[bad commit];[bad waitUntilCompleted];
        require(bad.status==MTLCommandBufferStatusError&&host.submissions==before,"writable inline rejected before GPU submit");
        id<MTLCommandBuffer> boundary=[q commandBuffer];e=[boundary renderCommandEncoderWithDescriptor:pass];
        BOOL rejected=NO;@try{[e setVertexBuffer:b offset:0 atIndex:31];}@catch(NSException *ex){rejected=YES;}
        require(rejected,"vertex slot 31 rejected");rejected=NO;
        @try{[e setFragmentBytes:red length:16 atIndex:31];}@catch(NSException *ex){rejected=YES;}
        require(rejected,"fragment slot 31 rejected");[e endEncoding];
        @synchronized(host){
            require(![ProcessRequest(host,++seq,@{@"op":@"readRenderBuffer",@"buffer":@UINT64_MAX,@"offset":@0,@"length":@4})[@"ok"] boolValue],"foreign readback rejected");
        }
        corrupt=YES;uint32_t old0=words[0],oldLast=words[16384];
        id<MTLCommandBuffer> damaged=[q commandBuffer];e=[damaged renderCommandEncoderWithDescriptor:pass];[e setRenderPipelineState:pipeline];[e setVertexBuffer:b offset:0 atIndex:30];[e setVertexBytes:&step length:4 atIndex:8];[e setFragmentBytes:red length:16 atIndex:30];[e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];[damaged commit];[damaged waitUntilCompleted];
        require(damaged.status==MTLCommandBufferStatusError&&words[0]==old0&&words[16384]==oldLast,"bad later chunk cannot partially publish CPU shadow");
        fprintf(stderr,"PASS render writes: GPU pixels, 3-chunk CPU coherence, ordered draws, reuse, completion, slots 8/30, boundary/ownership/inline rejection, atomic error publication\n");
    }
    // Drain queued retirements through a fresh device-independent host check.
    NSUInteger remaining=1;
    for(unsigned i=0;i<5000&&remaining;i++){@synchronized(host){remaining=host.entries.count;}if(remaining)usleep(1000);}
    require(remaining==0,"all resources retired");
    return 0;
}}
