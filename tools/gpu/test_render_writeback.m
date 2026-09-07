// Host execution test of the real frontend/backend and framed JSON values.
// Runtime-compiled test shaders are injected only into this test's library table;
// production library loading and exact-guest shader evidence remain separate.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMLinearTest
- (id<MTLTexture>)newLinearTextureWithDescriptor:(MTLTextureDescriptor *)d offset:(NSUInteger)o bytesPerRow:(NSUInteger)r bytesPerImage:(NSUInteger)i;
@end

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
      "fragment float4 fw(device uint *s[[buffer(0)]]){s[0]=1;return float4(1,0,0,1);}"
      "vertex O vg(uint i[[vertex_id]],device uint *s[[buffer(30)]],constant uint &step[[buffer(8)]]){if(i==0){s[0]+=1;s[4]=step;s[5]=1;s[6]=0;}float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return O{float4(p[i%3],0,1)};}"
      "fragment float4 ft(texture2d<float> image[[texture(8)]],texture3d<float> lut[[texture(15)]],sampler sam[[sampler(15)]]){return image.sample(sam,float2(.5))*lut.sample(sam,float3(.5,.5,.75));}";
    id<MTLLibrary> native=[host.device newLibraryWithSource:source options:nil error:&error];
    require(native!=nil,error.description.UTF8String);
    __block uint64_t seq=0;__block BOOL corrupt=NO;__block unsigned chunks=0;__block unsigned uploads=0;
    __block DVMSharedRender *partialLease=nil;
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *request,NSError **outError){
        @synchronized(host) {
        // Round-trip every request/reply through JSON, including writeback IDs.
        NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil];
        if([r[@"op"] isEqual:@"writeRenderBuffer"])uploads++;
        NSDictionary *reply;
        if([r[@"op"] isEqual:@"library"]){DVMEntry *e=nil;require(Add(host,@"library",native,&e),"test library allocation");reply=@{@"ok":@YES,@"handle":@(e.handle),@"functionNames":native.functionNames};}
        else {
            DVMEntry *sharedTarget=nil;
            if(partialLease&&[r[@"op"] isEqual:@"renderSubmit"]){
                // State-only fixture over this test's real GPU render target.
                // Separate scattered-page tests validate the physical mapping.
                sharedTarget=Entry(host,r[@"commands"][0][@"target"],@"texture");
                require(sharedTarget!=nil,"partial frame target");sharedTarget.sharedRender=partialLease;
            }
            reply=ProcessRequest(host,++seq,r);
            if(sharedTarget){
                require(partialLease.state==DVMSharedFailed,"partial GPU frame poisons shared lease");
                require(![SharedRenderRequest(host,++seq,@{@"op":@"sharedRenderSeal",@"handle":@(sharedTarget.handle),@"epoch":@1})[@"ok"] boolValue],"partial frame cannot be presented");
                sharedTarget.sharedRender=nil;
            }
        }
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
            require(uploads==(iteration?4:3),"skip unchanged chunks after GPU writeback and detect direct CPU writes");
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
        corrupt=NO;
        desc.fragmentFunction=[lib newFunctionWithName:@"ft"];
        id<MTLRenderPipelineState> textured=[d newRenderPipelineStateWithDescriptor:desc error:&error];require(textured!=nil,"3D sampling pipeline");
        MTLTextureDescriptor *volume=[MTLTextureDescriptor new];volume.textureType=MTLTextureType3D;volume.pixelFormat=MTLPixelFormatRGBA8Unorm;volume.width=2;volume.height=2;volume.depth=2;volume.usage=MTLTextureUsageShaderRead;volume.storageMode=MTLStorageModeShared;
        id<MTLTexture> lut=[d newTextureWithDescriptor:volume];require(lut&&lut.depth==2&&lut.textureType==MTLTextureType3D,"3D allocation metadata");
        uint8_t padded[96],returned[96];memset(padded,0xa5,sizeof(padded));memset(returned,0xa5,sizeof(returned));
        for(unsigned z=0;z<2;z++)for(unsigned y=0;y<2;y++)for(unsigned x=0;x<2;x++){
            uint8_t *v=padded+z*48+y*16+x*4;v[0]=0;v[1]=z?255:0;v[2]=z?0:255;v[3]=255;
        }
        MTLRegion cube=MTLRegionMake3D(0,0,0,2,2,2);
        [lut replaceRegion:cube mipmapLevel:0 slice:0 withBytes:padded bytesPerRow:16 bytesPerImage:48];
        [lut getBytes:returned bytesPerRow:16 bytesPerImage:48 fromRegion:cube mipmapLevel:0 slice:0];
        require(!memcmp(padded,returned,sizeof(padded)),"3D pitched transfer with intact padding");
        MTLTextureDescriptor *imageDesc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:1 height:1 mipmapped:NO];imageDesc.usage=MTLTextureUsageShaderRead;
        id<MTLTexture> image=[d newTextureWithDescriptor:imageDesc];uint32_t white=UINT32_MAX;[image replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:&white bytesPerRow:4];
        id<MTLSamplerState> sampler=[d newSamplerStateWithDescriptor:[MTLSamplerDescriptor new]];
        for(unsigned wrong=0;wrong<2;wrong++){
            id<MTLCommandBuffer> tc=[q commandBuffer];id<MTLRenderCommandEncoder> te=[tc renderCommandEncoderWithDescriptor:pass];
            [te setRenderPipelineState:textured];[te setVertexBuffer:b offset:0 atIndex:30];[te setVertexBytes:&step length:4 atIndex:8];
            id<MTLTexture> inputs[16]={nil};inputs[8]=image;inputs[15]=wrong?image:lut;
            id<MTLSamplerState> samplers[16]={nil};samplers[15]=sampler;
            [te setFragmentTextures:inputs withRange:NSMakeRange(0,16)];[te setFragmentSamplerStates:samplers withRange:NSMakeRange(0,16)];
            BOOL refused=NO;@try{[te setFragmentTexture:image atIndex:16];}@catch(NSException *ex){refused=YES;}require(refused,"texture boundary 16");
            refused=NO;@try{[te setFragmentSamplerState:sampler atIndex:16];}@catch(NSException *ex){refused=YES;}require(refused,"sampler boundary 16");
            [te drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[te endEncoding];uint64_t prior=host.submissions;
            [tc commit];[tc waitUntilCompleted];
            if(wrong)require(tc.status==MTLCommandBufferStatusError&&host.submissions==prior,"wrong texture dimension rejected before GPU");
            else {require(uploads==7&&words[0]==old0+step&&words[16384]==oldLast+7,"failed writeback invalidates upload cache before reuse");require(tc.status==MTLCommandBufferStatusCompleted,"3D sampling completion");[t getBytes:pixels bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];for(unsigned i=0;i<4096;i++)require(pixels[i*4]==0&&pixels[i*4+1]==255&&pixels[i*4+2]==0&&pixels[i*4+3]==255,"3D sample produces green output");}
        }
        fprintf(stderr,"PASS texture sampling: 3D pitch/padding, slots 8/15, sampler 15, bulk unbinding, correct GPU pixels, dimension/boundary rejection\n");
        fprintf(stderr,"PASS render writes: GPU pixels, 3-chunk CPU coherence, ordered draws, reuse, completion, slots 8/30, boundary/ownership/inline rejection, atomic error publication\n");
        void *client=NULL;require(!posix_memalign(&client,16384,16384),"client aligned allocation");
        memset(client,0,16384);__block unsigned freed=0;
        void (^deallocator)(void *,NSUInteger)=^(void *pointer,NSUInteger length){require(pointer==client&&length==16384,"client deallocator identity");freed++;free(pointer);};
        require(![d newBufferWithBytesNoCopy:(char *)client+1 length:16384 options:0 deallocator:deallocator],"unaligned client pointer rejected");
        require(![d newBufferWithBytesNoCopy:client length:16383 options:0 deallocator:deallocator],"unaligned client extent rejected");
        require(freed==0,"failed allocation does not take ownership");
        @autoreleasepool {
            id<MTLBuffer> clientBuffer=[d newBufferWithBytesNoCopy:client length:16384 options:0 deallocator:deallocator];
            fprintf(stderr,"CLIENT_BUFFER pointer=%p contents=%p object=%p length=%lu\n",client,clientBuffer.contents,(__bridge void *)clientBuffer,(unsigned long)clientBuffer.length);require(clientBuffer.contents==client,"client buffer retains original CPU storage");
            MTLTextureDescriptor *linearDesc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:1 height:1 mipmapped:NO];linearDesc.storageMode=MTLStorageModeShared;linearDesc.usage=MTLTextureUsageShaderRead;
            NSUInteger row=[d minimumLinearTextureAlignmentForPixelFormat:MTLPixelFormatBGRA8Unorm];
            id<MTLTexture> linear=[(id<DVMLinearTest>)clientBuffer newLinearTextureWithDescriptor:linearDesc offset:0 bytesPerRow:row bytesPerImage:row];
            require(linear!=nil&&linear.buffer==clientBuffer,"private linear view retains client buffer");
            require(![(id<DVMLinearTest>)clientBuffer newLinearTextureWithDescriptor:linearDesc offset:0 bytesPerRow:row bytesPerImage:row+1],"inconsistent image pitch rejected");
            ((uint32_t *)client)[0]=0xff00ff00;uint32_t sample=0;
            [linear getBytes:&sample bytesPerRow:4 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
            require(sample==0xff00ff00,"client CPU update reaches native linear texture");
            desc.vertexFunction=[lib newFunctionWithName:@"vg"];desc.fragmentFunction=[lib newFunctionWithName:@"f"];
            id<MTLRenderPipelineState> generated=[d newRenderPipelineStateWithDescriptor:desc error:&error];require(generated!=nil,"generated index pipeline");
            @autoreleasepool {
                uint32_t next=2;id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLRenderCommandEncoder> re=[cb renderCommandEncoderWithDescriptor:pass];
                [re setRenderPipelineState:generated];[re setVertexBuffer:clientBuffer offset:0 atIndex:30];[re setVertexBytes:&next length:4 atIndex:8];[re setFragmentBytes:red length:16 atIndex:30];
                [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                [re setVertexBytes:&next length:4 atIndex:30];
                [re drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:3 indexType:MTLIndexTypeUInt32 indexBuffer:clientBuffer indexBufferOffset:16];[re endEncoding];
                uint64_t before=host.submissions;[cb commit];[cb waitUntilCompleted];
                require(cb.status==MTLCommandBufferStatusError&&host.submissions==before,"bad later alias binding rejected before any GPU draw");
            }
            for(unsigned invalid=0;invalid<2;invalid++)@autoreleasepool {
                if(invalid){partialLease=[DVMSharedRender new];partialLease.state=DVMSharedAcquired;partialLease.epoch=1;}
                memset(client,0,16384);uint32_t next=invalid?5000:2;
                id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLRenderCommandEncoder> re=[cb renderCommandEncoderWithDescriptor:pass];
                [re setRenderPipelineState:generated];[re setVertexBuffer:clientBuffer offset:0 atIndex:30];[re setVertexBytes:&next length:4 atIndex:8];[re setFragmentBytes:red length:16 atIndex:30];
                [re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                [re drawIndexedPrimitives:MTLPrimitiveTypeTriangle indexCount:3 indexType:MTLIndexTypeUInt32 indexBuffer:clientBuffer indexBufferOffset:16];[re endEncoding];
                uint64_t before=host.submissions;[cb commit];[cb waitUntilCompleted];
                require(freed==0,"in-flight and retained client lifetime");
                if(invalid){require(cb.status==MTLCommandBufferStatusError&&host.submissions==before+1,"GPU-generated invalid indices rejected before dependent draw");require(partialLease.state==DVMSharedFailed,"failed shared frame retained as failed");partialLease=nil;}
                else {
                    require(cb.status==MTLCommandBufferStatusCompleted&&host.submissions==before+2,"dependent indexed draws split at completed GPU boundary");
                    require(((uint32_t *)client)[0]==2&&((uint32_t *)client)[4]==2,"GPU writes published directly to client storage");
                    [t getBytes:pixels bytesPerRow:256 fromRegion:MTLRegionMake2D(0,0,64,64) mipmapLevel:0];
                    for(unsigned i=0;i<4096;i++)require(pixels[i*4]==0&&pixels[i*4+1]==0&&pixels[i*4+2]==255&&pixels[i*4+3]==255,"GPU-generated indices retain correct render pixels");
                }
            }
        }
        for(unsigned i=0;i<1000&&!freed;i++)usleep(1000);
        require(freed==1,"client deallocator exactly once after final reference");
        fprintf(stderr,"PASS client storage and index dependencies: original pointer, GPU writeback, valid generated indices, invalid-index stop, final deallocator\n");
    }
    // Drain queued retirements through a fresh device-independent host check.
    NSUInteger remaining=1;
    for(unsigned i=0;i<5000&&remaining;i++){@synchronized(host){remaining=host.entries.count;}if(remaining)usleep(1000);}
    require(remaining==0,"all resources retired");
    return 0;
}}
