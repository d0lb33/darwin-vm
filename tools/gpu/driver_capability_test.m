#import "driver_api.h"
#include "driver_capabilities.h"
static void check(BOOL value,const char *what){if(!value){fprintf(stderr,"FAIL %s\n",what);exit(1);}}
int main(void){@autoreleasepool{
    __block unsigned requests=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *r,NSError **e){
        (void)e;check([r[@"op"] isEqual:@"capabilities"],"unexpected call");requests++;
        return @{@"contract":DVMContractProfile()};
    });
    id<DVMCapabilityQueries> caps=(id)device;
#define CHECK_BOOL(selector,value) check([caps selector]==(BOOL)(value),#selector);
#define CHECK_UINT(selector,value) check([caps selector]==(NSUInteger)(value),#selector);
    DVM_CAPABILITY_QUERIES(CHECK_BOOL,CHECK_UINT)
#undef CHECK_BOOL
#undef CHECK_UINT
    check(requests==1,"one negotiation for the complete batch");
    check([caps maxFragmentTextures]==DVM_FRAGMENT_TEXTURES&&[caps maxFragmentSamplers]==DVM_FRAGMENT_SAMPLERS&&[caps maxColorAttachments]==1,"bounded implemented render bindings");
    check(![device supportsFamily:MTLGPUFamilyApple1]&&![device supportsTextureSampleCount:4],"no unimplemented families/MSAA");
    MTLTextureDescriptor *texture=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:DVM_TEXTURE_DIMENSION+1 height:1 mipmapped:NO];
    texture.storageMode=MTLStorageModeShared;texture.usage=MTLTextureUsageShaderRead;
    check(![device newTextureWithDescriptor:texture],"advertised texture extent enforced");
    check(![device newBufferWithLength:DVM_BUFFER_BYTES+1 options:MTLResourceStorageModeShared],"advertised buffer extent enforced");
    check(requests==1,"out of bounds rejected before transport");
    for(NSString *key in @[@"version",@"queries"]){
        id<MTLDevice> bad=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *r,NSError **e){
            (void)r;(void)e;NSMutableDictionary *profile=[DVMContractProfile() mutableCopy];
            if([key isEqual:@"version"])profile[key]=@(DVM_CONTRACT_VERSION+1);
            else {NSMutableDictionary *queries=[profile[key] mutableCopy];queries[@"maxFragmentTextures"]=@128;profile[key]=queries;}
            return @{@"contract":profile};
        });
        BOOL refused=NO;@try{[(id<DVMCapabilityQueries>)bad maxFragmentTextures];}@catch(NSException *e){refused=[e.reason containsString:@"capability contract mismatch"];}
        check(refused,"mismatched host contract must fail closed");
    }
    fprintf(stderr,"DVM_CAPABILITIES_PASS batch=1 cached=1 bounds=1 mismatch_rejected=1 bounded_render_stages=1\n");
}}
