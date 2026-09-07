// Native/forwarded host contract comparison; the fixture is not guest evidence.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
@protocol DVMComputeStats
- (NSDictionary *)consumerCompletion;
@end
static void check(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static NSArray *exercise(id<MTLDevice> device,id<MTLLibrary> library){
    NSMutableArray *results=[NSMutableArray array];
    @autoreleasepool{
        NSError *error=nil;uint32_t bias=37;MTLFunctionConstantValues *constants=[MTLFunctionConstantValues new];[constants setConstantValue:&bias type:MTLDataTypeUInt atIndex:3];
        id<MTLFunction> function=[library newFunctionWithName:@"k" constantValues:constants error:&error];check(function!=nil,error.description.UTF8String);
        MTLComputePipelineDescriptor *cd=[MTLComputePipelineDescriptor new];cd.computeFunction=function;cd.maxTotalThreadsPerThreadgroup=64;cd.threadGroupSizeIsMultipleOfThreadExecutionWidth=YES;cd.requiredThreadsPerThreadgroup=MTLSizeMake(8,8,1);
        if([device respondsToSelector:@selector(consumerCompletion)]){
            MTLComputePipelineDescriptor *bad=[cd copy];bad.stageInputDescriptor.attributes[0].format=MTLAttributeFormatFloat;
            check(![device newComputePipelineStateWithDescriptor:bad options:0 reflection:NULL error:&error]&&error,"active stage input rejected");
            bad=[cd copy];bad.buffers[1].mutability=MTLMutabilityImmutable;
            check(![device newComputePipelineStateWithDescriptor:bad options:0 reflection:NULL error:&error],"nondefault mutability rejected");
            bad=[cd copy];bad.linkedFunctions.functions=@[function];
            check(![device newComputePipelineStateWithDescriptor:bad options:0 reflection:NULL error:&error],"linking rejected");
            MTLComputePipelineReflection *reflection=nil;
            check(![device newComputePipelineStateWithDescriptor:cd options:MTLPipelineOptionArgumentInfo reflection:&reflection error:&error]&&!reflection,"guest reflection unsupported");
        }
        id<MTLComputePipelineState> compute=[device newComputePipelineStateWithDescriptor:cd options:0 reflection:NULL error:&error];check(compute!=nil,error.description.UTF8String);check(compute.maxTotalThreadsPerThreadgroup==64,"descriptor max threads preserved");
        MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:16 height:16 mipmapped:NO];td.storageMode=MTLStorageModeShared;td.usage=5;
        id<MTLTexture> source=[device newTextureWithDescriptor:td];td.usage=3;id<MTLTexture> intermediate=[device newTextureWithDescriptor:td];td.pixelFormat=MTLPixelFormatBGRA8Unorm;td.usage=5;id<MTLTexture> output=[device newTextureWithDescriptor:td];
        id<MTLBuffer> generated=[device newBufferWithLength:1024 options:0],copy=[device newBufferWithLength:1024 options:0];memset(generated.contents,0,1024);memset(copy.contents,0,1024);
        MTLRenderPipelineDescriptor *rd=[MTLRenderPipelineDescriptor new];rd.vertexFunction=[library newFunctionWithName:@"v"];rd.fragmentFunction=[library newFunctionWithName:@"f"];rd.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
        id<MTLRenderPipelineState> render=[device newRenderPipelineStateWithDescriptor:rd error:&error];check(render!=nil,error.description.UTF8String);
        MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;id<MTLSamplerState> sampler=[device newSamplerStateWithDescriptor:sd];
        id<MTLCommandQueue> queue=[device newCommandQueue];
        for(uint32_t frame=0;frame<6;frame++){@autoreleasepool{
            MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=source;pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,.5,1);
            id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLRenderCommandEncoder> re=[cb renderCommandEncoderWithDescriptor:pass];[re endEncoding];
            id<MTLComputeCommandEncoder> ce=frame%2?[cb computeCommandEncoderWithDispatchType:MTLDispatchTypeSerial]:[cb computeCommandEncoder];check(ce!=nil,"serial compute encoder");[ce setComputePipelineState:compute];[ce setTexture:source atIndex:2];[ce setTexture:intermediate atIndex:5];[ce setSamplerState:sampler atIndex:4];[ce setBuffer:generated offset:0 atIndex:1];[ce setBytes:&frame length:4 atIndex:3];[ce dispatchThreadgroups:MTLSizeMake(2,2,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];[ce endEncoding];
            pass.colorAttachments[0].texture=output;pass.colorAttachments[0].clearColor=MTLClearColorMake(0,0,0,0);
            re=[cb renderCommandEncoderWithDescriptor:pass];[re setRenderPipelineState:render];[re setFragmentTexture:intermediate atIndex:2];[re drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[re endEncoding];
            id<MTLBlitCommandEncoder> be=[cb blitCommandEncoder];[be copyFromBuffer:generated sourceOffset:0 toBuffer:copy destinationOffset:0 size:1024];[be endEncoding];
            if(!frame){pass.colorAttachments[0].texture=source;for(unsigned extra=0;extra<60;extra++){re=[cb renderCommandEncoderWithDescriptor:pass];[re endEncoding];}}
            [cb commit];[cb waitUntilCompleted];check(cb.status==MTLCommandBufferStatusCompleted,cb.error.description.UTF8String);
            NSMutableData *pixels=[NSMutableData dataWithLength:1024];[output getBytes:pixels.mutableBytes bytesPerRow:64 fromRegion:MTLRegionMake2D(0,0,16,16) mipmapLevel:0];
            const uint8_t *p=pixels.bytes;for(unsigned i=0;i<256;i++){uint32_t expected=i+37+frame;check(((uint32_t *)generated.contents)[i]==expected&&((uint32_t *)copy.contents)[i]==expected,"compute and blit completion writeback");check(p[4*i]==128&&p[4*i+1]==(expected&255)&&p[4*i+2]==255&&p[4*i+3]==255,"independent mixed-stage pixels");}
            [results addObject:pixels];
        }}
    }
    return results;
}
int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];NSError *error=nil;
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;constant uint addend[[function_constant(3)]];vertex float4 v(uint i[[vertex_id]]){float2 p[3]={float2(-1,-1),float2(3,-1),float2(-1,3)};return float4(p[i],0,1);}fragment float4 f(float4 p[[position]],texture2d<float,access::read> t[[texture(2)]]){return t.read(uint2(p.xy));}kernel void k(texture2d<float> src[[texture(2)]],texture2d<float,access::write> dst[[texture(5)]],sampler s[[sampler(4)]],device uint *out[[buffer(1)]],constant uint &frame[[buffer(3)]],uint2 p[[thread_position_in_grid]]){uint value=p.y*16+p.x+addend+frame;float4 c=src.sample(s,(float2(p)+0.5f)/16.0f,level(0));dst.write(float4(c.r,float(value&255u)/255.0f,c.b,1),p);out[p.y*16+p.x]=value;}";
    id<MTLLibrary> library=[host.device newLibraryWithSource:source options:nil error:&error];check(library!=nil,error.description.UTF8String);
    __block uint64_t seq=0;__block BOOL rejectedBatch=NO;
    id<MTLDevice> forwarded=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **e){@synchronized(host){
        NSDictionary *r=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:request options:0 error:nil] options:0 error:nil],*reply;
        if([r[@"op"] isEqual:@"library"]){DVMEntry *entry=nil;check(Add(host,@"library",library,&entry),"fixture library");reply=@{@"ok":@YES,@"handle":@(entry.handle),@"functionNames":library.functionNames};}
        else {
            if([r[@"op"] isEqual:@"renderSubmit"]&&!rejectedBatch){
                rejectedBatch=YES;uint64_t submissions=host.submissions;
                // Reject a bad later dispatch before the earlier render clear
                // executes. Native reflection supplies sparse access contracts.
                for(unsigned variant=0;variant<6;variant++){
                    NSMutableDictionary *bad=[NSJSONSerialization JSONObjectWithData:[NSJSONSerialization dataWithJSONObject:r options:0 error:nil] options:NSJSONReadingMutableContainers error:nil];
                    NSMutableDictionary *compute=bad[@"commands"][1];
                    if(variant==0)compute[@"samplers"]=@[];
                    if(variant==1)compute[@"threads"]=@[@4,@8,@1];
                    if(variant==2){id sourceHandle=nil;for(NSArray *binding in compute[@"textures"])if([binding[0] isEqual:@2])sourceHandle=binding[1];check(sourceHandle!=nil,"sparse source fixture");compute[@"textures"]=@[@[@2,sourceHandle],@[@5,sourceHandle]];}
                    if(variant==3)compute[@"buffers"]=@[];
                    if(variant==4)compute[@"bytes"]=@[@{@"index":@3,@"data":@"AA=="}];
                    if(variant==5)while([bad[@"commands"] count]<=DVM_ORDERED_COMMANDS)[bad[@"commands"] addObject:bad[@"commands"][0]];
                    NSDictionary *failure=ProcessRequest(host,++seq,bad);
                    check(![failure[@"ok"] boolValue]&&host.submissions==submissions,"mixed preflight atomic rejection");
                    fprintf(stderr,"DVM_COMPUTE_NEGATIVE variant=%u failure=%s\n",variant,[failure[@"description"] UTF8String]);
                }
            }
            reply=ProcessRequest(host,++seq,r);
        }
        if(![reply[@"ok"] boolValue]){if(e)*e=[NSError errorWithDomain:@"ComputeTest" code:1 userInfo:@{NSLocalizedDescriptionKey:reply.description}];return nil;}return reply;
    }});
    NSArray *native=exercise(host.device,library),*actual;
    @autoreleasepool{dispatch_data_t data=dispatch_data_create("fixture",7,NULL,DISPATCH_DATA_DESTRUCTOR_DEFAULT);id<MTLLibrary> proxy=[forwarded newLibraryWithData:data error:&error];actual=exercise(forwarded,proxy);}
    check([native isEqual:actual],"native versus forwarded mixed frames");
    // Drain asynchronous retirements through the real device RPC serial queue.
    [(id<DVMComputeStats>)forwarded consumerCompletion];check(host.entries.count==0&&host.textureBytes==0,"mixed resource retirement");
    puts("DVM_COMPUTE_RENDER_PASS frames=6 specialization=1 sparse_bindings=1 sampled_and_written_textures=1 render_compute_render_blit=1 native_equal=1 writeback=1 retirement=1 commands64=1 reject_over_contract_limit=1");
}}
