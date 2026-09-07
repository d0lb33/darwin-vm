// Experimental Metal forwarding driver. Public selector/structure ABI; only
// the documented subset is implemented. Boot registration is opt-in and
// restricted to backboardd in the isolated system_bootstrap experiment.
#import "driver_api.h"
#import <CommonCrypto/CommonDigest.h>
#import <IOSurface/IOSurface.h>
#include <dlfcn.h>
#include "present_layout.h"
#include "driver_capabilities.h"
#include "dirty_buffer_range.h"
#include "metal_library_slice.h"
#ifdef DVM_SURFACE_PIN_PROBE
static void DVMProbeSurfacePin(IOSurfaceRef surface);
#endif

static NSError *error(NSString *s) {
    return [NSError errorWithDomain:@"DVMMetalDriver"
                               code:1
                           userInfo:@{NSLocalizedDescriptionKey : s}];
}
static void reject(NSString *s) {
    [NSException raise:NSInvalidArgumentException format:@"DVM Metal: %@", s];
}
#include "render_staging_guest.inc"
static void DVMTextureRejection(const char *reason,MTLTextureDescriptor *d,IOSurfaceRef surface,NSUInteger plane){
    // The installed test supervisor exposes its checked audit writer. Driver
    // stderr alone is not retained by that guest launch path. Native clients
    // without the supervisor keep ordinary stderr diagnostics.
    int (*report)(FILE *,const char *,...)=dlsym(RTLD_DEFAULT,"DVMReport");
    if(!report)report=fprintf;
    report(stderr,"GPU_LOAD_TEXTURE_REJECT reason=%s type=%lu width=%lu height=%lu depth=%lu format=%lu storage=%lu usage=%lu options=%lu levels=%lu samples=%lu array=%lu compression=%ld surface=%u plane=%lu\n",
        reason,(unsigned long)d.textureType,(unsigned long)d.width,(unsigned long)d.height,(unsigned long)d.depth,(unsigned long)d.pixelFormat,
        (unsigned long)d.storageMode,(unsigned long)d.usage,(unsigned long)d.resourceOptions,(unsigned long)d.mipmapLevelCount,
        (unsigned long)d.sampleCount,(unsigned long)d.arrayLength,(long)d.compressionType,surface?IOSurfaceGetID(surface):0,(unsigned long)plane);
}
@class DVMDevice, DVMLibrary, DVMFunction, DVMPipeline, DVMTexture, DVMCommand, DVMBuffer;

@interface DVMDevice : NSObject <MTLDevice>
@property(nonatomic, copy) DVMMetalRPC transport;
@property(nonatomic, strong) dispatch_queue_t serial;
@property(nonatomic) NSUInteger pendingSubmissions;
@property(nonatomic,readonly) BOOL submissionInFlight;
@property(nonatomic) BOOL binaryPayloads;
@property(nonatomic,strong) NSDictionary *negotiatedContract;
@property(nonatomic,strong) NSMutableDictionary *linearAlignments;
@property(nonatomic,strong) NSError *lastSubmissionError;
@property(nonatomic,copy) DVMMetalMappingProvider mappingProvider;
@property(nonatomic,strong) id<DVMMetalOwnedMapping> ownedMapping;
@property(nonatomic,copy) DVMMetalSurfaceProvider surfaceProvider;
@property(nonatomic,strong) NSDictionary *importContract;
@property(nonatomic,strong) NSMutableArray *quarantinedImports;
@property(nonatomic,strong) NSError *importFailure;
- (NSDictionary *)contractCapabilities;
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)err;
- (void)retire:(NSNumber *)handle mapping:(id<DVMMetalImportedMapping>)mapping;
@end
@interface DVMObject : NSObject
@property(nonatomic, strong) DVMDevice *owner;
@property(nonatomic, strong) NSNumber *handle;
@property(atomic, copy) NSString *label;
@property(nonatomic,strong) id<DVMMetalImportedMapping> retirementMapping;
@end
@implementation DVMObject
- (id<MTLDevice>)device {
    return _owner;
}
- (void)dealloc {
    if (_handle)
        [_owner retire:_handle mapping:_retirementMapping];
}
@end
@interface DVMLibrary : DVMObject <MTLLibrary>
@property(nonatomic, strong) NSArray<NSString *> *functionNames;
@end
@interface DVMFunction : DVMObject <MTLFunction>
@property(nonatomic, strong) DVMLibrary *library;
@property(nonatomic, strong) NSString *name;
@property(nonatomic) MTLFunctionType functionType;
@end
#include "consumer_resource_guest.inc"
@interface DVMBuffer : DVMResource <MTLBuffer>
@property(nonatomic, strong) NSMutableData *shadow;
@property(nonatomic,strong) NSMutableData *renderUploaded;
@property(nonatomic,copy) void (^clientDeallocator)(void *,NSUInteger);
@property(nonatomic) void *clientBytes;
@property(nonatomic) NSUInteger clientLength;
- (NSData *)cpuData;
@end
static void DVMUploadBufferChanges(DVMBuffer *buffer,DVMMetalRPC rpc){
    NSError *e=nil;
    // contents may be written directly, without didModifyRange.
    // Compare owned bytes; update the cache only after host ACK.
    BOOL initialized=buffer.renderUploaded!=nil;
    NSMutableData *uploaded=buffer.renderUploaded?:[NSMutableData dataWithLength:buffer.length];
    for(NSUInteger offset=0;offset<buffer.length;offset+=32768){
        NSUInteger length=MIN(32768,buffer.length-offset);
        DVMDirtyRange changed=initialized?DVMFindDirtyRange((uint8_t *)buffer.contents+offset,(uint8_t *)uploaded.bytes+offset,length):(DVMDirtyRange){0,length};
        if(!changed.length)continue;
        NSUInteger start=offset+changed.offset;
        NSData *chunk=[NSData dataWithBytes:(uint8_t *)buffer.contents+start length:changed.length];
        if(!rpc(@{@"op":@"writeRenderBuffer",@"buffer":buffer.handle,@"offset":@(start),@"data":[chunk base64EncodedStringWithOptions:0]},&e))reject(e.description?:@"render buffer upload");
        memcpy((uint8_t *)uploaded.mutableBytes+start,chunk.bytes,changed.length);
    }
    buffer.renderUploaded=uploaded;
}
@interface DVMPipeline : DVMObject <MTLComputePipelineState>
@property(nonatomic, strong) DVMFunction *function;
@property(nonatomic) NSUInteger threadExecutionWidth;
@property(nonatomic) NSUInteger maxTotalThreadsPerThreadgroup;
@end
@interface DVMTexture : DVMResource <MTLTexture>
@property(nonatomic, strong) NSData *pendingUpload;
@property(nonatomic, strong) NSData *completedShadow;
@property(nonatomic) NSUInteger width;
@property(nonatomic) NSUInteger height,depth,mipLevels;
@property(nonatomic) MTLTextureType textureType;
@property(nonatomic) MTLPixelFormat pixelFormat;
@property(nonatomic) MTLTextureUsage usage;
@property(nonatomic,strong) DVMBuffer *backingBuffer;
@property(nonatomic) NSUInteger backingOffset,backingRow;
@property(nonatomic,strong) id surfaceObject;
@property(nonatomic,strong) id<DVMMetalOwnedMapping> surfaceMapping;
@property(nonatomic) uint64_t shadowGeneration;
- (NSUInteger)row;
- (NSData *)read;
@end
@interface DVMQueue : NSObject <MTLCommandQueue>
@property(nonatomic, strong) DVMDevice *owner;
@property(atomic, copy) NSString *label;
@property(nonatomic,strong) dispatch_queue_t submissionQueue;
@property(nonatomic,strong) dispatch_queue_t completionQueue;
@property(nonatomic) BOOL configurationFrozen;
@property(nonatomic) NSUInteger maxCommandBufferCount,pendingSubmissions;
@end
static void DVMUploadTextureChunks(DVMTexture *texture,DVMMetalRPC rpc){
    NSData *data=texture.pendingUpload;NSNumber *token=@0;NSError *failure=nil;
    @try{
        for(NSUInteger offset=0;offset<data.length;offset+=DVM_TEXTURE_TRANSFER_CHUNK){
            NSUInteger length=MIN(DVM_TEXTURE_TRANSFER_CHUNK,data.length-offset);
            NSDictionary *r=rpc(@{@"op":@"writeTextureChunk",@"texture":texture.handle,@"token":token,@"offset":@(offset),@"data":[[data subdataWithRange:NSMakeRange(offset,length)] base64EncodedStringWithOptions:0]},&failure);
            if(!r||![r[@"token"] isKindOfClass:NSNumber.class]||![r[@"token"] unsignedLongLongValue]||
               (token.unsignedLongLongValue&&![r[@"token"] isEqual:token])||![r[@"accepted"] isEqual:@(offset+length)]||
               ![r[@"complete"] isEqual:@(offset+length==data.length)])reject(failure.description?:@"texture chunk acknowledgement");
            token=r[@"token"];
        }
    }@catch(NSException *exception){
        if(token.unsignedLongLongValue)rpc(@{@"op":@"abortTextureUpload",@"texture":texture.handle,@"token":token},NULL);
        @throw exception;
    }
}
@interface DVMEncoder : NSObject <MTLComputeCommandEncoder>
@property(nonatomic, strong) DVMCommand *command;
@property(nonatomic, strong) DVMPipeline *pipeline;
@property(nonatomic, strong) NSMutableDictionary *textures;
@property(nonatomic, strong) NSMutableDictionary *constants;
@property(nonatomic, strong) NSMutableDictionary *buffers;
@property(nonatomic, strong) NSMutableDictionary *scratch;
@property(nonatomic) BOOL ended;
@property(nonatomic, strong) NSArray *imageblock;
@property(atomic, copy) NSString *label;
@end
@interface DVMCommand : NSObject <MTLCommandBuffer>
@property(nonatomic, strong) DVMQueue *commandQueue;
@property(nonatomic, strong) NSMutableArray *commands;
@property(nonatomic, strong) NSMutableArray *resources;
@property(nonatomic, strong) NSMutableArray *handlers;
@property(nonatomic,strong) NSMutableArray *scheduledHandlers;
@property(nonatomic,strong) NSArray *responsibleTaskIDs;
@property(nonatomic,strong) NSMutableDictionary *userDictionary;
@property(nonatomic, strong) dispatch_group_t completion;
@property(atomic) MTLCommandBufferStatus status;
@property(atomic, strong) NSError *error;
@property(atomic) CFTimeInterval GPUStartTime,GPUEndTime,kernelStartTime,kernelEndTime;
@property(nonatomic) NSUInteger openEncoders;
@property(atomic, copy) NSString *label;
- (void)encoding;
@end

#include "consumer_state_guest.inc"
#include "consumer_render_guest.inc"
#include "consumer_blit_guest.inc"
#include "consumer_function_guest.inc"
#include "imported_surface_guest.inc"
@implementation DVMDevice
- (BOOL)submissionInFlight {@synchronized(self){return self.pendingSubmissions!=0;}}
- (NSDictionary *)consumerCompletion {
    // EndFrame has submitted on the one queue. The serial barrier observes host
    // completion before the final-only pixel read, and propagates async errors.
    NSDictionary *stats=[self call:@{@"op":@"stats"} error:NULL];
    if(self.lastSubmissionError)reject(self.lastSubmissionError.description);
    return stats;
}
- (id<MTLRenderPipelineState>)newRenderPipelineStateWithDescriptor:(MTLRenderPipelineDescriptor *)d error:(NSError **)e {return DVMNewRenderPipeline(self,d,e);}
- (id<MTLRenderPipelineState>)newRenderPipelineStateWithDescriptor:(MTLRenderPipelineDescriptor *)d options:(MTLPipelineOption)options reflection:(MTLAutoreleasedRenderPipelineReflection *)reflection error:(NSError **)e {
    if(reflection)*reflection=nil;if(options){if(e)*e=error(@"render reflection unsupported");return nil;}return DVMNewRenderPipeline(self,d,e);
}
- (id<MTLSamplerState>)newSamplerStateWithDescriptor:(MTLSamplerDescriptor *)d {return DVMNewSampler(self,d);}
- (id<MTLDepthStencilState>)newDepthStencilStateWithDescriptor:(MTLDepthStencilDescriptor *)d {return DVMNewDepth(self,d);}
- (NSString *)name {
    return @"DVM host Metal (experimental render/compute subset)";
}
- (NSString *)vendorName {return @"Darwin VM";}
- (NSDictionary *)contractCapabilities {
    NSDictionary *contract;
    @synchronized(self){contract=_negotiatedContract;}
    if(!contract) {
        // Never wait for the serial transport while holding the owner
        // monitor: an earlier completion needs it to retire its queue slot.
        NSError *failure=nil;NSDictionary *r=[self call:@{@"op":@"capabilities"} error:&failure];
        NSDictionary *profile=r[@"contract"];
        if(![profile isEqual:DVMContractProfile()])reject(failure.description?:@"guest/host capability contract mismatch");
        @synchronized(self){if(!_negotiatedContract)_negotiatedContract=DVMContractProfile();contract=_negotiatedContract;}
    }
    return contract[@"queries"];
}
#define DVM_BOOL_GETTER(selector,value) - (BOOL)selector {return [[self contractCapabilities][@#selector] boolValue];}
#define DVM_UINT_GETTER(selector,value) - (NSUInteger)selector {return [[self contractCapabilities][@#selector] unsignedIntegerValue];}
DVM_CAPABILITY_QUERIES(DVM_BOOL_GETTER,DVM_UINT_GETTER)
#undef DVM_BOOL_GETTER
#undef DVM_UINT_GETTER
- (id<MTLBinaryArchive>)newBinaryArchiveWithDescriptor:(MTLBinaryArchiveDescriptor *)d error:(NSError **)err {
    (void)d;if(err)*err=error(@"binary archive caching is not supported");return nil;
}
- (BOOL)supportsFamily:(MTLGPUFamily)family {
    (void)family;
    return NO;
}
- (BOOL)supportsFeatureSet:(MTLFeatureSet)set {(void)set;return NO;}
- (BOOL)supportsTextureSampleCount:(NSUInteger)count {return count==1;}
- (NSUInteger)requiredLinearTextureBytesPerRowForDescriptor:(MTLTextureDescriptor *)d {
    if(!DVMTextureDescriptorValid(d)||d.textureType!=MTLTextureType2D)reject(@"linear descriptor outside forwarding contract");
    NSUInteger a=[self minimumLinearTextureAlignmentForPixelFormat:d.pixelFormat];
    return (d.width*DVMFormatBytes(d.pixelFormat)+a-1)&~(a-1);
}
- (NSUInteger)minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)format {
    NSNumber *cached=nil;@synchronized(self){cached=_linearAlignments[@(format)];}
    if(cached)return cached.unsignedIntegerValue;
    // A prior completion needs the owner monitor to retire its queue slot.
    // Never hold that monitor while waiting for the serial transport.
    NSError *e=nil;NSDictionary *r=[self call:@{@"op":@"linearLayout",@"format":@(format)} error:&e];
    NSUInteger a=[r[@"alignment"] unsignedIntegerValue];
    if(!r||!DVMFormatBytes(format)||DVM1DFormat(format)||[r[@"format"] unsignedIntegerValue]!=format||a<16||a>4096||(a&(a-1)))reject(e.description?:@"invalid linear layout contract");
    @synchronized(self){if(!_linearAlignments)_linearAlignments=[NSMutableDictionary dictionary];_linearAlignments[@(format)]=@(a);}
    return a;
}
- (NSUInteger)minimumTextureBufferAlignmentForPixelFormat:(MTLPixelFormat)format {
    (void)format;reject(@"texture-buffer views unsupported by forwarding profile");return 0;
}
- (NSUInteger)deviceLinearReadOnlyTextureAlignmentBytes {
    // Exact QuartzCore update_image +0x1fc tests both pointer and row pitch.
    // All supported format alignments are powers of two: their maximum is
    // sufficient for our existing linear-texture allocation contract.
    NSUInteger alignment=16;
    for(NSNumber *format in DVMContractProfile()[@"textureFormats"])
        if(!DVM1DFormat(format.unsignedIntegerValue))
            alignment=MAX(alignment,[self minimumLinearTextureAlignmentForPixelFormat:format.unsignedIntegerValue]);
    return alignment;
}
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)err {
    __block NSDictionary *reply = nil;
    __block NSError *failure = nil;
    dispatch_sync(_serial, ^{
        reply = self.transport(request, &failure);
    });
    if (!reply && err)
        *err = failure ?: error(@"transport failed");
    return reply;
}
- (void)retire:(NSNumber *)handle mapping:(id<DVMMetalImportedMapping>)mapping {
    // Queued retirement follows earlier submissions. Pending command buffers
    // retain their resources, so deallocation cannot overtake encoded use.
    dispatch_async(_serial, ^{
        NSError *e = nil;
        NSDictionary *reply=self.transport(@{@"op" : @"release", @"handle" : handle}, &e);
        if(mapping&&(!reply||!reply[@"retiredSurface"]||
            ([reply[@"retiredSurface"] unsignedLongLongValue]&&
             ([reply[@"retiredSurface"] unsignedLongLongValue]!=mapping.resourceID||![mapping retire])))){
            if(!self.quarantinedImports)self.quarantinedImports=[NSMutableArray array];
            [self.quarantinedImports addObject:mapping];
            self.lastSubmissionError=e?:error(@"import retirement acknowledgement failed; pages quarantined");
            self.importFailure=self.lastSubmissionError;
        }
        if (!reply||e)
            fprintf(stderr, "GPU_LOAD_DRIVER_RETIRE_ERROR %s\n", e.description.UTF8String);
    });
}
- (id<MTLLibrary>)newLibraryWithData:(dispatch_data_t)data error:(NSError **)err {
    const void *p = NULL;
    size_t n = 0;
    dispatch_data_t map = data ? dispatch_data_create_map(data, &p, &n) : nil;
    if (!map || !p || n > 12 * 1024 * 1024) {
        if (err)
            *err = error(@"invalid library data");
        return nil;
    }
    uint8_t sha[32];
    char hex[65];
    CC_SHA256(p, (CC_LONG)n, sha);
    for (unsigned i = 0; i < 32; i++)
        snprintf(hex + 2 * i, 3, "%02x", sha[i]);
    NSDictionary *r = [self call:@{@"op" : @"library", @"length" : @(n), @"sha256" : @(hex)}
                           error:err];
    if (!r)
        return nil;
    DVMLibrary *o = [DVMLibrary new];
    o.owner = self;
    o.handle = r[@"handle"];
    o.functionNames = r[@"functionNames"];
    return o;
}
- (id<MTLLibrary>)newLibraryWithFile:(NSString *)path error:(NSError **)err {
    if (![path isKindOfClass:NSString.class] || !path.length) {
        if (err) *err = error(@"library file path is empty or invalid");
        return nil;
    }
    return [self newLibraryWithURL:[NSURL fileURLWithPath:path] error:err];
}
- (id<MTLLibrary>)newLibraryWithURL:(NSURL *)url error:(NSError **)err {
    int (*report)(FILE *,const char *,...)=dlsym(RTLD_DEFAULT,"DVMReport");
    if(!report)report=fprintf;
    report(stderr,"GPU_LOAD_LIBRARY_REQUEST path=%.280s\n",url.path.UTF8String?:"(nil)");
#ifdef DVM_CA_REHEARSAL
#if TARGET_OS_IPHONE && !TARGET_OS_MACCATALYST
#error Host rehearsal substitution must never be in an iOS driver
#endif
    const char *rehearsal=getenv("DVM_REHEARSAL_AIR");
    if(!rehearsal)reject(@"host rehearsal requires explicit AIR path");
    fprintf(stderr,"DVM_HOST_REHEARSAL requested=%s substitute=%s scope=host-only\n",url.path.UTF8String,rehearsal);
    url=[NSURL fileURLWithPath:@(rehearsal)];
#endif
    NSData *bytes=[NSData dataWithContentsOfURL:url options:NSDataReadingMappedIfSafe error:err];
    if(!bytes)return nil;
    // Preserve the requested file's unique MTLB slice without rewriting it.
    const uint8_t *p=bytes.bytes;size_t offset=0,length=0;
    const char *failure=DVMSelectLibrarySlice(p,bytes.length,&offset,&length);
    if(failure){if(err)*err=error(@(failure));return nil;}
    dispatch_data_t data=dispatch_data_create(p+offset,length,dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE,0),DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    return [self newLibraryWithData:data error:err];
}
- (id<MTLComputePipelineState>)newComputePipelineStateWithFunction:(id<MTLFunction>)f
                                                             error:(NSError **)err {
    if (![f isKindOfClass:DVMFunction.class] || ((DVMFunction *)f).library.owner != self) {
        if (err)
            *err = error(@"foreign function");
        return nil;
    }
    DVMFunction *function = (DVMFunction *)f;
    NSDictionary *r = [self call:@{
        @"op" : @"pipeline",
        @"library" : function.library.handle,
        @"function" : function.name
    }
                           error:err];
    if (!r)
        return nil;
    DVMPipeline *o = [DVMPipeline new];
    o.owner = self;
    o.handle = r[@"handle"];
    o.function = function;
    o.threadExecutionWidth = [r[@"threadExecutionWidth"] unsignedIntegerValue];
    o.maxTotalThreadsPerThreadgroup = [r[@"maxTotalThreadsPerThreadgroup"] unsignedIntegerValue];
    return o;
}
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor *)d {
    if(!DVMTextureDescriptorValid(d)){
        DVMTextureRejection("descriptor",d,NULL,0);
        return nil;
    }
    NSError *e = nil;
    NSDictionary *r = [self call:@{
        @"op" : @"texture",
        @"width" : @(d.width),
        @"height" : @(d.height), @"depth":@(d.depth), @"type":@(d.textureType),
        @"format" : @(d.pixelFormat),
        @"storage" : @(d.storageMode),
        @"usage" : @(d.usage),@"levels":@(d.mipmapLevelCount)
    }
                           error:&e];
    if (!r)
        return nil;
    DVMTexture *o = [DVMTexture new];
    o.owner = self;
    o.handle = r[@"handle"];
    o.width = d.width;
    o.height = d.height;o.depth=d.depth;o.textureType=d.textureType;o.mipLevels=d.mipmapLevelCount;
    o.pixelFormat = d.pixelFormat;
    o.usage = d.usage;
    o.acceptedOptions=d.resourceOptions;
    o.hostAllocatedSize=[r[@"allocatedSize"] unsignedIntegerValue];
    return o;
}
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor *)d
                                 iosurface:(IOSurfaceRef)s
                                     plane:(NSUInteger)plane {
#ifdef DVM_SURFACE_PIN_PROBE
    DVMProbeSurfacePin(s);
#endif
    if(self.surfaceProvider)return DVMImportSurface(self,d,s,plane);
    if(!s||!d||plane||!self.mappingProvider||d.textureType!=MTLTextureType2D||d.width!=DVM_PRESENT_WIDTH||
       d.height!=DVM_PRESENT_HEIGHT||d.depth!=1||d.arrayLength!=1||d.mipmapLevelCount!=1||d.sampleCount!=1||
       d.pixelFormat!=MTLPixelFormatBGRA8Unorm||d.usage!=(MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead)||
       d.storageMode!=MTLStorageModeShared||d.compressionType!=MTLTextureCompressionTypeLossless||!DVMResourceOptionsValid(d.resourceOptions)||
       ([(id)d respondsToSelector:@selector(protectionOptions)]&&[(id<DVMProtectionMetadata>)d protectionOptions])){DVMTextureRejection("surface-descriptor",d,s,plane);return nil;}
    id<DVMMetalOwnedMapping> mapping=DVMGetOwnedMetalMapping(self,NULL);
    if(!mapping||IOSurfaceGetBaseAddress(s)!=mapping.bytes||IOSurfaceGetAllocSize(s)!=mapping.length||
       IOSurfaceGetWidth(s)!=d.width||IOSurfaceGetHeight(s)!=d.height||IOSurfaceGetBytesPerRow(s)!=DVM_PRESENT_ROW||
       IOSurfaceGetBytesPerElement(s)!=4||IOSurfaceGetPixelFormat(s)!=0x42475241||IOSurfaceGetPlaneCount(s)){DVMTextureRejection("surface-owner",d,s,plane);return nil;}
    NSError *e=nil;NSDictionary *r=[self call:@{@"op":@"sharedRenderCreate",@"width":@(d.width),@"height":@(d.height),
        @"row":@DVM_PRESENT_ROW,@"format":@(d.pixelFormat),@"usage":@(d.usage),@"bytes":@(mapping.length)} error:&e];
    if(!r)return nil;
    DVMTexture *t=[DVMTexture new];t.owner=self;t.handle=r[@"handle"];t.width=d.width;t.height=d.height;t.depth=1;
    t.textureType=d.textureType;t.pixelFormat=d.pixelFormat;t.usage=d.usage;t.acceptedOptions=d.resourceOptions;
    t.hostAllocatedSize=[r[@"allocatedSize"] unsignedIntegerValue];t.surfaceObject=(__bridge id)s;t.surfaceMapping=mapping;
    return t;
}
- (id<MTLCommandQueue>)newCommandQueue {
    DVMQueue *q = [DVMQueue new];
    q.owner = self;
    q.maxCommandBufferCount=DVM_QUEUED_COMMAND_BUFFERS;
    return q;
}
- (id<MTLCommandQueue>)newCommandQueueWithMaxCommandBufferCount:(NSUInteger)count {
    if(!count||count>DVM_QUEUED_COMMAND_BUFFERS)return nil;
    DVMQueue *queue=(id)[self newCommandQueue];queue.maxCommandBufferCount=count;return queue;
}
- (id<MTLBuffer>)newBufferWithLength:(NSUInteger)n options:(MTLResourceOptions)options {
    if (!n || n > DVM_BUFFER_BYTES || !DVMResourceOptionsValid(options))
        return nil;
    NSError *e = nil;
    NSDictionary *r = [self call:@{@"op" : @"buffer", @"length" : @(n)} error:&e];
    if (!r)
        return nil;
    DVMBuffer *b = [DVMBuffer new];
    b.owner = self;
    b.handle = r[@"handle"];
    b.shadow = [NSMutableData dataWithLength:n];
    b.acceptedOptions=options;b.hostAllocatedSize=[r[@"allocatedSize"] unsignedIntegerValue];
    return b;
}
- (id<MTLBuffer>)newBufferWithBytes:(const void *)bytes length:(NSUInteger)length options:(MTLResourceOptions)options {
    if(!bytes)return nil;id<MTLBuffer> b=[self newBufferWithLength:length options:options];
    if(b)memcpy(b.contents,bytes,length);return b;
}
- (id<MTLBuffer>)newBufferWithBytesNoCopy:(void *)bytes length:(NSUInteger)length options:(MTLResourceOptions)options deallocator:(void (^)(void *,NSUInteger))deallocator {
    if(!bytes||(uintptr_t)bytes%DVM_MANAGED_PAGE_BYTES||!length||length%DVM_MANAGED_PAGE_BYTES)return nil;
    DVMBuffer *b=(id)[self newBufferWithLength:length options:options];
    if(!b)return nil;
    // NSMutableData may copy even its bytesNoCopy input on mutable access.
    // Preserve the API's pointer identity explicitly until final retirement.
    b.clientBytes=bytes;b.clientLength=length;b.shadow=nil;
    b.clientDeallocator=deallocator;return b;
}
@end
@implementation DVMLibrary
- (id<MTLFunction>)newFunctionWithDescriptor:(MTLFunctionDescriptor *)d error:(NSError **)e {
    if(!d||!d.name||d.options||d.binaryArchives.count){if(e)*e=error(@"function options/archive unsupported");return nil;}
    NSArray *constants=DVMConstants(d.constantValues);
    NSDictionary *r=[self.owner call:@{@"op":@"function",@"library":self.handle,@"name":d.name,@"specialized":d.specializedName?:@"",@"constants":constants} error:e];
    if(!r)return nil;DVMFunction *f=[DVMFunction new];f.library=self;f.name=d.name;f.owner=self.owner;f.handle=r[@"handle"];f.functionType=[r[@"type"] unsignedIntegerValue];return f;
}
- (id<MTLFunction>)newFunctionWithName:(NSString *)name constantValues:(MTLFunctionConstantValues *)values error:(NSError **)e {
    MTLFunctionDescriptor *d=[MTLFunctionDescriptor functionDescriptor];d.name=name;d.constantValues=values;return [self newFunctionWithDescriptor:d error:e];
}
- (id<MTLFunction>)newFunctionWithName:(NSString *)name {
    if (![_functionNames containsObject:name])
        return nil;
    // Unspecialized functions still need a real stage and owned native handle.
    // A name-only placeholder reports functionType=0 to Metal descriptors.
    MTLFunctionDescriptor *descriptor=[MTLFunctionDescriptor functionDescriptor];descriptor.name=name;
    return [self newFunctionWithDescriptor:descriptor error:nil];
}
@end
@implementation DVMFunction
- (id<MTLDevice>)device {
    return _library.owner;
}
@end
@implementation DVMPipeline
@end
@implementation DVMBuffer
- (void)prepareForPurgeability:(MTLPurgeableState)state {
    if(state==MTLPurgeableStateVolatile&&self.residencyState<=MTLPurgeableStateNonVolatile)
        DVMUploadBufferChanges(self,self.owner.transport);
}
- (void)purgeabilityChanged:(MTLPurgeableState)old current:(MTLPurgeableState)current {
    [super purgeabilityChanged:old current:current];
    if(old==MTLPurgeableStateEmpty||current==MTLPurgeableStateEmpty)self.renderUploaded=nil;
}
- (void)dealloc {if(_clientDeallocator)_clientDeallocator(_clientBytes,_clientLength);}
- (NSData *)cpuData {return [NSData dataWithBytes:self.contents length:self.length];}
- (id<MTLTexture>)newLinearTextureWithDescriptor:(MTLTextureDescriptor *)d offset:(NSUInteger)offset bytesPerRow:(NSUInteger)row bytesPerImage:(NSUInteger)image {
    if(d.textureType!=MTLTextureType2D||(image&&image!=row*d.height))return nil;
    return [self newTextureWithDescriptor:d offset:offset bytesPerRow:row];
}
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor *)d offset:(NSUInteger)offset bytesPerRow:(NSUInteger)row {
    if(!DVMTextureDescriptorValid(d)||d.textureType!=MTLTextureType2D||d.storageMode!=self.storageMode||d.usage!=MTLTextureUsageShaderRead)return nil;
    NSUInteger a=[self.owner minimumLinearTextureAlignmentForPixelFormat:d.pixelFormat];
    if(offset%a||row%a||row<d.width*DVMFormatBytes(d.pixelFormat)||row>DVM_BUFFER_BYTES||offset>self.length||row*d.height>self.length-offset)return nil;
    NSError *e=nil;NSDictionary *r=[self.owner call:@{@"op":@"linearTexture",@"buffer":self.handle,@"width":@(d.width),@"height":@(d.height),@"format":@(d.pixelFormat),@"usage":@(d.usage),@"offset":@(offset),@"row":@(row)} error:&e];
    if(!r)reject(e.description?:@"linear texture allocation");
    DVMTexture *t=[DVMTexture new];t.owner=self.owner;t.handle=r[@"handle"];
    t.width=d.width;t.height=d.height;t.depth=1;t.textureType=MTLTextureType2D;t.pixelFormat=d.pixelFormat;t.usage=d.usage;
    t.acceptedOptions=self.acceptedOptions;t.hostAllocatedSize=[r[@"allocatedSize"] unsignedIntegerValue];
    t.backingBuffer=self;t.backingOffset=offset;t.backingRow=row;return t;
}
- (NSUInteger)length {
    return _clientBytes?_clientLength:_shadow.length;
}
- (void *)contents {
    [self requireResident];
    return _clientBytes?:_shadow.mutableBytes;
}
- (void)didModifyRange:(NSRange)range {
    [self requireResident];
    if(self.owner.submissionInFlight||range.location>self.length||range.length>self.length-range.location)
        reject(@"buffer modification outside owned idle allocation");
}
@end

@implementation DVMTexture
- (DVMResource *)purgeabilityRoot {return self.backingBuffer?:self;}
- (void)prepareForPurgeability:(MTLPurgeableState)state {
    if(self.surfaceMapping&&state>MTLPurgeableStateNonVolatile)
        reject(@"pinned IOSurface volatility requires native surface/pin retirement");
    if(state==MTLPurgeableStateVolatile&&self.pendingUpload){
        DVMUploadTextureChunks(self,self.owner.transport);self.pendingUpload=nil;
    }
}
- (void)purgeabilityChanged:(MTLPurgeableState)old current:(MTLPurgeableState)current {
    [super purgeabilityChanged:old current:current];
    if(old==MTLPurgeableStateEmpty||current==MTLPurgeableStateEmpty){self.completedShadow=nil;self.pendingUpload=nil;}
}
- (NSUInteger)row {
    return _width * DVMFormatBytes(_pixelFormat);
}
- (BOOL)isFramebufferOnly {return NO;}
- (BOOL)isShareable {return NO;}
- (BOOL)isSparse {return NO;}
- (id<MTLTexture>)parentTexture {return nil;}
- (id<MTLResource>)rootResource {return self.backingBuffer?:self;}
- (NSUInteger)parentRelativeLevel {return 0;}
- (NSUInteger)parentRelativeSlice {return 0;}
- (id<MTLBuffer>)buffer {return self.backingBuffer;}
- (NSUInteger)bufferOffset {return self.backingOffset;}
- (NSUInteger)bufferBytesPerRow {return self.backingRow;}
- (IOSurfaceRef)iosurface {return (__bridge IOSurfaceRef)self.surfaceObject;}
- (NSUInteger)iosurfacePlane {return 0;}
- (NSUInteger)mipmapLevelCount {
    return self.mipLevels?:1;
}
- (MTLTextureCompressionType)compressionType {return MTLTextureCompressionTypeLossless;}
- (NSUInteger)arrayLength {
    return 1;
}
- (NSUInteger)sampleCount {
    return 1;
}
- (void)check:(MTLRegion)r level:(NSUInteger)level row:(NSUInteger)row pointer:(const void *)p {
    [self requireResident];
    if(self.surfaceMapping)reject(@"owned IOSurface CPU access uses its lock and lease contract");
    if(self.storageMode==MTLStorageModePrivate)reject(@"private textures have no CPU transfer access");
    if (!p || level || !r.size.width || !r.size.height || !r.size.depth ||
        r.origin.x>_width || r.size.width>_width-r.origin.x ||
        r.origin.y>_height || r.size.height>_height-r.origin.y ||
        r.origin.z>self.depth || r.size.depth>self.depth-r.origin.z ||
        row<r.size.width*DVMFormatBytes(self.pixelFormat) || row>DVM_TEXTURE_BYTES)
        reject(@"texture transfer region/row bounds");
}
- (void)replaceRegion:(MTLRegion)r
          mipmapLevel:(NSUInteger)level
            withBytes:(const void *)p
          bytesPerRow:(NSUInteger)row {
    [self replaceRegion:r mipmapLevel:level slice:0 withBytes:p bytesPerRow:row bytesPerImage:row*r.size.height];
}
- (void)replaceRegion:(MTLRegion)r mipmapLevel:(NSUInteger)level slice:(NSUInteger)slice withBytes:(const void *)p bytesPerRow:(NSUInteger)row bytesPerImage:(NSUInteger)image {
    // Native 1D transfers have no row/image stride. Normalize only our CPU
    // shadow layout; the wire continues to carry the complete tight image.
    if(self.textureType==MTLTextureType1D){row=r.size.width*DVMFormatBytes(self.pixelFormat);image=row;}
    [self check:r level:level row:row pointer:p];
    if(!image)image=row*r.size.height;
    if(slice||image<row*r.size.height||image>DVM_TEXTURE_BYTES)reject(@"texture image pitch/slice");
    NSUInteger bpp=DVMFormatBytes(self.pixelFormat),length=r.size.width*bpp;
    if(self.backingBuffer) {
        if(self.owner.submissionInFlight)reject(@"linear texture upload in flight");
        for(NSUInteger y=0;y<r.size.height;y++)memcpy((uint8_t *)self.backingBuffer.contents+self.backingOffset+(r.origin.y+y)*self.backingRow+r.origin.x*bpp,(const uint8_t *)p+y*row,length);
        return;
    }
    if (self.owner.submissionInFlight) reject(@"texture upload while GPU work is in flight");
    BOOL full=r.size.width==_width&&r.size.height==_height&&r.size.depth==self.depth;
    // A partial update must preserve completed GPU writes outside its region.
    // Reuse an unsubmitted CPU image, otherwise fetch the completed native
    // contents through the existing bounded full-transfer contract. This is
    // correctness-first support, not a claim of efficient partial wire uploads.
    NSMutableData *d=full?[NSMutableData dataWithLength:[self row]*_height*self.depth]:[(self.pendingUpload?:[self read]) mutableCopy];
    for(NSUInteger z=0;z<r.size.depth;z++)for(NSUInteger y=0;y<r.size.height;y++)
        memcpy((uint8_t *)d.mutableBytes+((z+r.origin.z)*_height+y+r.origin.y)*[self row]+r.origin.x*bpp,
               (const uint8_t *)p+z*image+y*row,length);
    self.pendingUpload = d;
    self.completedShadow=nil;
}
- (NSData *)read {
    [self requireResident];
    uint64_t generation=self.purgeabilityRoot.storageGeneration;
    if(self.shadowGeneration!=generation){self.completedShadow=nil;self.shadowGeneration=generation;}
    if(self.surfaceMapping)reject(@"owned IOSurface has no copied readback path");
    if(self.storageMode==MTLStorageModePrivate)reject(@"private textures have no CPU transfer access");
    if(self.owner.submissionInFlight)reject(@"texture read while GPU work is in flight");
    if(self.completedShadow)return self.completedShadow;
    NSError *e = nil;
    if(self.backingBuffer) {
        if(![self.owner call:@{@"op":@"upload",@"buffer":self.backingBuffer.handle,@"data":[[self.backingBuffer cpuData] base64EncodedStringWithOptions:0]} error:&e])reject(e.description?:@"linear upload");
    }
    if (self.pendingUpload) {
        if(self.pendingUpload.length>DVM_TEXTURE_TRANSFER_CHUNK){
            __block NSException *caught=nil;
            dispatch_sync(self.owner.serial,^{@try{DVMUploadTextureChunks(self,self.owner.transport);}@catch(NSException *exception){caught=exception;}});
            if(caught)@throw caught;
        }else if (![self.owner call:@{@"op":@"upload", @"texture":self.handle,
              @"row":@([self row]), @"data":[self.pendingUpload base64EncodedStringWithOptions:0]} error:&e])
            reject(e.description ?: @"texture upload failed");
        self.pendingUpload = nil;
    }
    NSUInteger total=[self row]*_height*self.depth;
    if(total>DVM_TEXTURE_TRANSFER_CHUNK){
        NSMutableData *data=[NSMutableData dataWithCapacity:total];
        for(NSUInteger offset=0;offset<total;offset+=DVM_TEXTURE_TRANSFER_CHUNK){
            NSUInteger length=MIN(DVM_TEXTURE_TRANSFER_CHUNK,total-offset);
            NSDictionary *part=[self.owner call:@{@"op":@"read",@"texture":self.handle,@"offset":@(offset),@"length":@(length)} error:&e];
            NSData *bytes=part?[[NSData alloc] initWithBase64EncodedString:part[@"data"] options:0]:nil;
            if(bytes.length!=length||![part[@"row"] isEqual:@([self row])])reject(e.description?:@"texture read chunk");
            [data appendData:bytes];
        }
        return data;
    }
    NSDictionary *r = [self.owner call:@{@"op" : @"read", @"texture" : self.handle} error:&e];
    NSData *d = r ? [[NSData alloc] initWithBase64EncodedString:r[@"data"] options:0] : nil;
    if (!d || d.length != [self row] * _height * self.depth || [r[@"row"] unsignedIntegerValue] != [self row])
        reject(e.description ?: @"bad texture readback");
    return d;
}
- (void)getBytes:(void *)p
     bytesPerRow:(NSUInteger)row
      fromRegion:(MTLRegion)r
     mipmapLevel:(NSUInteger)level {
    [self getBytes:p bytesPerRow:row bytesPerImage:row*r.size.height fromRegion:r mipmapLevel:level slice:0];
}
- (void)getBytes:(void *)p bytesPerRow:(NSUInteger)row bytesPerImage:(NSUInteger)image fromRegion:(MTLRegion)r mipmapLevel:(NSUInteger)level slice:(NSUInteger)slice {
    if(self.textureType==MTLTextureType1D){row=r.size.width*DVMFormatBytes(self.pixelFormat);image=row;}
    [self check:r level:level row:row pointer:p];
    if(!image)image=row*r.size.height;
    if(slice||image<row*r.size.height||image>DVM_TEXTURE_BYTES)reject(@"texture image pitch/slice");
    NSData *d = [self read];
    NSUInteger bpp=DVMFormatBytes(self.pixelFormat);
    for(NSUInteger z=0;z<r.size.depth;z++)for(NSUInteger y=0;y<r.size.height;y++)
        memcpy((uint8_t *)p+z*image+y*row,(const uint8_t *)d.bytes+((z+r.origin.z)*_height+y+r.origin.y)*[self row]+r.origin.x*bpp,r.size.width*bpp);
}

@end

@implementation DVMQueue
// The one physical execution queue has no guest priority classes. These
// private selectors return whether the requested policy was accepted; return
// NO instead of pretending to change GPU scheduling. Exact QuartzCore's
// constructor ignores both results (0x184588da0 and 0x1845891c4).
- (BOOL)setGPUPriority:(NSUInteger)priority {(void)priority;return NO;}
- (BOOL)setBackgroundGPUPriority:(NSUInteger)priority {(void)priority;return NO;}
- (void)setSubmissionQueue:(dispatch_queue_t)queue {
    @synchronized(self) {
        if(!queue||_configurationFrozen)reject(@"submission queue must be configured before command-buffer creation");
        _submissionQueue=queue;
    }
}
- (void)setCompletionQueue:(dispatch_queue_t)queue {
    @synchronized(self) {
        if(!queue||_configurationFrozen)reject(@"completion queue must be configured before command-buffer creation");
        _completionQueue=queue;
    }
}
- (id<MTLDevice>)device {
    return _owner;
}
- (id<MTLCommandBuffer>)commandBuffer {
    @synchronized(self){_configurationFrozen=YES;}
    DVMCommand *b = [DVMCommand new];
    b.commandQueue = self;
    b.commands = [NSMutableArray array];
    b.resources = [NSMutableArray array];
    b.handlers = [NSMutableArray array];
    b.scheduledHandlers=[NSMutableArray array];
    b.responsibleTaskIDs=@[];b.userDictionary=[NSMutableDictionary dictionary];
    b.completion = dispatch_group_create();
    b.status = MTLCommandBufferStatusNotEnqueued;
    return b;
}
@end
@implementation DVMCommand
- (id<MTLBlitCommandEncoder>)blitCommandEncoder {return DVMNewBlitEncoder(self);}
- (void)setResponsibleTaskIDs:(const uint32_t *)ids count:(uint32_t)count {
    // Exact MTLIOAccelCommandBuffer at 0x1a55a1334 copies count x 4 bytes
    // (ldr/str w at +0x80/+0x84). Preserve guest attribution, never use it
    // as a host Mach task identity. No encoder may be open during this update.
    [self encoding];if(_openEncoders||count>16||(!ids&&count))reject(@"responsible guest task ID extent/state");
    NSMutableArray *values=[NSMutableArray array];for(uint32_t i=0;i<count;i++)[values addObject:@(ids[i])];
    self.responsibleTaskIDs=values;
}
- (BOOL)isCommitted {return self.status>=MTLCommandBufferStatusCommitted;}
- (BOOL)synchronousDebugMode {return NO;}
- (id<MTLRenderCommandEncoder>)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d {return DVMNewRenderEncoder(self,d);}
- (void)setProtectionOptions:(NSUInteger)options {if(options)reject(@"protected commands unsupported");}
- (NSUInteger)protectionOptions {return 0;}
- (void)pushDebugGroup:(NSString *)s {(void)s;}
- (void)popDebugGroup {}
- (id<MTLDevice>)device {
    return _commandQueue.owner;
}
- (BOOL)retainedReferences {
    return YES;
}
- (void)encoding {
    if (self.status > MTLCommandBufferStatusEnqueued)
        reject(@"command already committed");
}
- (void)enqueue {[self encoding];self.status=MTLCommandBufferStatusEnqueued;}
- (void)addScheduledHandler:(MTLCommandBufferHandler)handler {
    [self encoding];if(!handler)reject(@"nil scheduled handler");[_scheduledHandlers addObject:[handler copy]];
}
- (void)waitUntilScheduled {[self waitUntilCompleted];}
- (BOOL)commitAndWaitUntilSubmitted {
    // Exact Metal 0x1a54fcb64 commits, then synchronously drains the queue's
    // submission executor. Our wire reply currently includes GPU completion,
    // so this conservative boundary waits longer, never acknowledges early.
    [self commit];[self waitUntilCompleted];
    return self.status==MTLCommandBufferStatusCompleted&&!self.error;
}
- (id<MTLComputeCommandEncoder>)computeCommandEncoder {
    [self encoding];
    if (_openEncoders)
        reject(@"overlapping encoders");
    _openEncoders++;
    DVMEncoder *e = [DVMEncoder new];
    e.command = self;
    e.textures = [NSMutableDictionary dictionary];
    e.constants = [NSMutableDictionary dictionary];
    e.buffers = [NSMutableDictionary dictionary];
    e.scratch = [NSMutableDictionary dictionary];
    return e;
}
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler {
    @synchronized(self) {
        if (!handler || self.status > MTLCommandBufferStatusEnqueued)
            reject(@"register completion before commit");
        [_handlers addObject:[handler copy]];
    }
}
- (void)commit {
    // Commit order and dispatch order must agree, including concurrent callers.
    @synchronized(_commandQueue.owner) {
    @synchronized(self) {
        if(_commandQueue.owner.importFailure)reject(@"import ownership failed; device reuse quarantined");
        [self encoding];
        if (_openEncoders)
            reject(@"unclosed command buffer");
        @synchronized(_commandQueue.owner) {
            if (_commandQueue.owner.pendingSubmissions>=DVM_QUEUED_COMMAND_BUFFERS)
                reject(@"bounded command buffer queue is full");
            if(_commandQueue.pendingSubmissions>=_commandQueue.maxCommandBufferCount)
                reject(@"command queue's configured command-buffer bound is full");
            if(!_commandQueue.owner.pendingSubmissions)_commandQueue.owner.lastSubmissionError=nil;
            _commandQueue.owner.pendingSubmissions++;
            _commandQueue.pendingSubmissions++;
        }
        self.status = MTLCommandBufferStatusCommitted;
        dispatch_group_enter(_completion);
    }
    // Encoding is finished; arrays cannot be mutated after commit. Resources
    // remain strongly owned until completion even if the caller releases them.
    NSMutableArray *uploads = [NSMutableArray array], *buffers = [NSMutableArray array],
                   *textures = [NSMutableArray array], *readbacks = [NSMutableArray array];
    BOOL render=!_commands.count||_commands[0][@"kind"]!=nil;
    for(id o in [_resources copy])if([o isKindOfClass:DVMTexture.class]&&[(DVMTexture *)o backingBuffer])
        [_resources addObject:[(DVMTexture *)o backingBuffer]];
    for (id o in _resources)
        if ([o isKindOfClass:DVMBuffer.class] && ![buffers containsObject:o]) {
            DVMBuffer *b = o;
            [buffers addObject:b];
            if(!render)[readbacks addObject:b.handle];
        }
    for (id o in _resources)
        if ([o isKindOfClass:DVMTexture.class] && ![textures containsObject:o]) {
            DVMTexture *t = o;
            [textures addObject:t];
        }
    dispatch_async(_commandQueue.owner.serial, ^{
        // Keep the device FIFO reservation in place while the caller's
        // submission queue executes. Resource RPCs cannot overtake this work.
        // As with native private dispatch queues, callers must not block the
        // supplied serial target waiting for work scheduled onto that target.
        void (^execute)(void)=^{
        @autoreleasepool {
            @try {
                NSError *e = nil;
                @synchronized(self.commandQueue.owner){
                    if(self.commandQueue.owner.lastSubmissionError)reject(@"earlier queued command failed; dependent work cancelled");
                }
                // Resolve uploads only after preceding GPU completion/writeback.
                // Capturing pending textures at commit would re-upload stale
                // CPU contents over a predecessor's GPU-produced image.
                for(DVMTexture *t in textures)[t requireResident];
                if(!render)for(DVMBuffer *b in buffers)
                    [uploads addObject:@{@"op":@"upload",@"buffer":b.handle,
                        @"data":self.commandQueue.owner.binaryPayloads?[b cpuData]:[[b cpuData] base64EncodedStringWithOptions:0]}];
                for(DVMTexture *t in textures)if(t.pendingUpload){
                    if(t.pendingUpload.length>DVM_TEXTURE_TRANSFER_CHUNK)DVMUploadTextureChunks(t,self.commandQueue.owner.transport);
                    else [uploads addObject:@{@"texture":t.handle,@"row":@([t row]),
                        @"data":self.commandQueue.owner.binaryPayloads&&!render?t.pendingUpload:[t.pendingUpload base64EncodedStringWithOptions:0]}];
                }
                if(render)for(DVMBuffer *buffer in buffers)DVMUploadBufferChanges(buffer,self.commandQueue.owner.transport);
                BOOL blur=self.commands.count&&self.commands[0][@"imageblock"]!=nil;
                DVMTexture *output=nil;
                if(blur)for(DVMTexture *t in textures)if([t.handle isEqual:[self.commands.lastObject[@"textures"] lastObject]])output=t;
                if(blur && (!output||!self.commandQueue.owner.binaryPayloads))reject(@"blur requires binary transport and output");
                NSMutableDictionary *request=[@{@"op":render?@"renderSubmit":blur?@"blurSubmit":@"submit",@"commands":self.commands,@"uploads":uploads,@"readbacks":readbacks} mutableCopy];
                if(render)request[@"guestTaskIDs"]=self.responsibleTaskIDs;
                if(blur){request[@"w"]=@(output.width);request[@"h"]=@(output.height);}
                NSDictionary *r=render?DVMRenderTransport(self.commandQueue.owner.transport,request,&e):self.commandQueue.owner.transport(request,&e);
                if (!r || [r[@"status"] integerValue] != MTLCommandBufferStatusCompleted)
                    self.error = e ?: error(@"GPU did not complete");
                else {
                    if(render){self.GPUStartTime=[r[@"gpu_start"] doubleValue];self.GPUEndTime=[r[@"gpu_end"] doubleValue];self.kernelStartTime=[r[@"kernel_start"] doubleValue];self.kernelEndTime=[r[@"kernel_end"] doubleValue];}
                    NSDictionary *returned = r[@"buffers"];
                    if (![returned isKindOfClass:NSDictionary.class] || returned.count != (render?0:buffers.count))
                        reject(@"bad batched readback table");
                    NSMutableArray *decoded = [NSMutableArray array];
                    NSMutableArray *writtenObjects = [NSMutableArray array], *writtenData = [NSMutableArray array];
                    if(render){
                        NSArray *written=r[@"writtenBuffers"]?:@[];
                        if(![written isKindOfClass:NSArray.class]||written.count>buffers.count)reject(@"render writeback table");
                        NSMutableArray *seen=[NSMutableArray array];NSUInteger total=0;
                        for(id handle in written){
                            DVMBuffer *owned=nil;
                            for(DVMBuffer *b in buffers)if([b.handle isEqual:handle])owned=b;
                            if(!owned||[seen containsObject:handle]||owned.length>DVM_BUFFER_BYTES-total)reject(@"render writeback ownership/budget");
                            [seen addObject:handle];total+=owned.length;owned.renderUploaded=nil;
                            NSMutableData *data=[NSMutableData dataWithLength:owned.length];
                            for(NSUInteger offset=0;offset<owned.length;offset+=32768){
                                NSUInteger length=MIN(32768,owned.length-offset);
                                NSDictionary *part=self.commandQueue.owner.transport(@{@"op":@"readRenderBuffer",@"buffer":handle,@"offset":@(offset),@"length":@(length)},&e);
                                id encoded=part[@"data"];
                                NSData *chunk=[encoded isKindOfClass:NSString.class]?[[NSData alloc] initWithBase64EncodedString:encoded options:0]:nil;
                                if(![part[@"buffer"] isEqual:handle]||![part[@"offset"] isEqual:@(offset)]||chunk.length!=length)reject(e.description?:@"render writeback chunk");
                                memcpy((uint8_t *)data.mutableBytes+offset,chunk.bytes,length);
                            }
                            [writtenObjects addObject:owned];[writtenData addObject:data];
                        }
                    }
                    for (DVMBuffer *b in render?@[]:buffers) {
                        id encoded = returned[b.handle.stringValue];
                        NSData *d = self.commandQueue.owner.binaryPayloads&&!render
                            ? ([encoded isKindOfClass:NSData.class] ? encoded : nil)
                            : ([encoded isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:encoded options:0] : nil);
                        if (d.length != b.length) reject(@"buffer readback size");
                        [decoded addObject:d];
                    }
                    if(blur){
                        NSData *pixels=r[@"texture"];
                        if(![pixels isKindOfClass:NSData.class]||pixels.length!=[output row]*output.height||![r[@"output"] isEqual:output.handle]||[r[@"w"] unsignedIntegerValue]!=output.width||[r[@"h"] unsignedIntegerValue]!=output.height)reject(@"blur texture reply");
                        output.completedShadow=pixels;
                    }
                    // Validate all readbacks before publishing any CPU shadow.
                    for(NSUInteger i=0;i<writtenObjects.count;i++){
                        DVMBuffer *b=writtenObjects[i];NSData *data=writtenData[i];
                        memcpy(b.contents,data.bytes,data.length);b.renderUploaded=[data mutableCopy];
                    }
                    for (NSUInteger i = 0; !render && i < buffers.count; i++) {
                        DVMBuffer *b = buffers[i]; NSData *d = decoded[i];
                        memcpy(b.contents, d.bytes, d.length);b.renderUploaded=nil;
                    }
                    for (DVMTexture *t in textures) {t.pendingUpload = nil;if(render)t.completedShadow=nil;}
                }
            } @catch (NSException *e) {
                self.error = error(e.reason);
            }
            // Any ambiguous failure may have changed native storage. Never
            // skip a later upload using an unconfirmed cached CPU image.
            if(self.error)for(DVMBuffer *buffer in buffers)buffer.renderUploaded=nil;
            NSArray *handlers = nil;
            @synchronized(self) {
                self.status =
                    self.error ? MTLCommandBufferStatusError : MTLCommandBufferStatusCompleted;
                handlers = [self.scheduledHandlers arrayByAddingObjectsFromArray:self.handlers];
                [self.scheduledHandlers removeAllObjects];
                [self.handlers removeAllObjects];
                [self.resources removeAllObjects];
            }
            @synchronized(self.commandQueue.owner) {
                self.commandQueue.owner.lastSubmissionError=self.error;
                self.commandQueue.owner.pendingSubmissions--;
                self.commandQueue.pendingSubmissions--;
            }
            dispatch_group_leave(self.completion);
            dispatch_queue_t callbacks=self.commandQueue.completionQueue?:dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE,0);
            dispatch_async(callbacks, ^{for(MTLCommandBufferHandler h in handlers)h(self);});
        }
        };
        if(self.commandQueue.submissionQueue)dispatch_sync(self.commandQueue.submissionQueue,execute);
        else execute();
    });
    }
}
- (void)waitUntilCompleted {
    if (self.status < MTLCommandBufferStatusCommitted)
        reject(@"wait before commit");
    dispatch_group_wait(_completion, DISPATCH_TIME_FOREVER);
}
@end
@implementation DVMEncoder
- (id<MTLDevice>)device {
    return _command.device;
}
- (void)check {
    if (_ended || !_command)
        reject(@"ended encoder");
    [_command encoding];
}
- (void)setComputePipelineState:(id<MTLComputePipelineState>)p {
    [self check];
    if (![p isKindOfClass:DVMPipeline.class] || p.device != self.device)
        reject(@"foreign pipeline");
    _pipeline = (DVMPipeline *)p;
}
- (void)setTexture:(id<MTLTexture>)t atIndex:(NSUInteger)i {
    [self check];
    if (i >= DVM_COMPUTE_BINDINGS || ![t isKindOfClass:DVMTexture.class] || t.device != self.device)
        reject(@"foreign texture or index");
    _textures[@(i)] = t;
}
- (void)setBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i {
    [self check];
    if (!p || !n || n > DVM_INLINE_BYTES || i >= DVM_COMPUTE_BINDINGS)
        reject(@"invalid inline bytes");
    _constants[@(i)] = [NSData dataWithBytes:p length:n];
    [_buffers removeObjectForKey:@(i)];
}
- (void)setBuffer:(id<MTLBuffer>)b offset:(NSUInteger)offset atIndex:(NSUInteger)i {
    [self check];
    if (i >= DVM_COMPUTE_BINDINGS || ![b isKindOfClass:DVMBuffer.class] || b.device != self.device ||
        offset >= b.length || offset % DVM_BUFFER_BINDING_ALIGNMENT)
        reject(@"invalid buffer binding");
    _buffers[@(i)] = @{@"object" : b, @"offset" : @(offset)};
    [_constants removeObjectForKey:@(i)];
}
- (void)setImageblockWidth:(NSUInteger)w height:(NSUInteger)h {
    [self check];if(w!=32||h!=32)reject(@"only audited 32x32 imageblock supported");_imageblock=@[@(w),@(h)];
}
- (void)setThreadgroupMemoryLength:(NSUInteger)n atIndex:(NSUInteger)i {
    [self check];
    if (i >= DVM_COMPUTE_BINDINGS || !n || n > 16384 || n % 16)
        reject(@"invalid threadgroup scratch");
    _scratch[@(i)] = @(n);
}
- (void)dispatchThreadgroups:(MTLSize)g threadsPerThreadgroup:(MTLSize)t {
    [self check];
    if (!_pipeline || _command.commands.count >= 32 || !g.width || !g.height || g.depth != 1 ||
        g.width > 512 || g.height > 512 || !t.width || !t.height || t.depth != 1 || t.width > 32 ||
        t.height > 32 || t.width * t.height > _pipeline.maxTotalThreadsPerThreadgroup)
        reject(@"unsupported dispatch geometry");
    NSMutableArray *textures = [NSMutableArray array], *bytes = [NSMutableArray array],
                   *buffers = [NSMutableArray array], *scratch = [NSMutableArray array];
    for (NSUInteger i = 0; i < _textures.count; i++) {
        DVMTexture *o = _textures[@(i)];
        if (!o)
            reject(@"sparse texture bindings unsupported");
        [textures addObject:o.handle];
        [_command.resources addObject:o];
    }
    for (NSNumber *i in _constants)
        [bytes
            addObject:@{@"index" : i, @"data" : ((DVMDevice *)self.device).binaryPayloads ? _constants[i] : [_constants[i] base64EncodedStringWithOptions:0]}];
    for (NSNumber *i in _buffers) {
        DVMBuffer *b = _buffers[i][@"object"];
        [buffers
            addObject:@{@"index" : i, @"buffer" : b.handle, @"offset" : _buffers[i][@"offset"]}];
        [_command.resources addObject:b];
    }
    for (NSNumber *i in _scratch)
        [scratch addObject:@{@"index" : i, @"length" : _scratch[i]}];
    [_command.resources addObject:_pipeline];
    NSMutableDictionary *encoded=[@{
        @"pipeline" : _pipeline.handle,
        @"textures" : textures,
        @"bytes" : bytes,
        @"buffers" : buffers,
        @"threadgroupMemory" : scratch,
        @"groups" : @[ @(g.width), @(g.height), @(g.depth) ],
        @"threads" : @[ @(t.width), @(t.height), @(t.depth) ]
    } mutableCopy];
    if(_imageblock)encoded[@"imageblock"]=_imageblock;
    [_command.commands addObject:encoded];
}
- (void)endEncoding {
    [self check];
    _ended = YES;
    _command.openEncoders--;
    _command = nil;
}
@end

id<MTLDevice> DVMCreateMetalDevice(DVMMetalRPC rpc) {
    if (!rpc)
        return nil;
    DVMDevice *d = [DVMDevice new];
    d.transport = rpc;
    dispatch_queue_attr_t attr=dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_USER_INTERACTIVE,0);
    d.serial = dispatch_queue_create("org.darwin-vm.metal.transport", attr);
    return d;
}

id<MTLDevice> DVMCreateBinaryMetalDevice(DVMMetalRPC rpc) {
    DVMDevice *d=(DVMDevice *)DVMCreateMetalDevice(rpc);
    d.binaryPayloads=YES;return d;
}

id<MTLDevice> DVMCreateSharedMetalDevice(DVMMetalRPC rpc,DVMMetalMappingProvider provider) {
    if(!provider)return nil;
    DVMDevice *d=(id)DVMCreateBinaryMetalDevice(rpc);d.mappingProvider=provider;return d;
}
void DVMEnableSurfaceImports(id<MTLDevice> device,DVMMetalSurfaceProvider provider){
    if(![(id)device isKindOfClass:DVMDevice.class]||!provider)reject(@"import provider/device");
    DVMDevice *d=(id)device;d.surfaceProvider=provider;
}
id<DVMMetalOwnedMapping> DVMGetOwnedMetalMapping(id<MTLDevice> device,NSError **outError) {
    if(outError)*outError=nil;
    if(![(id)device isKindOfClass:DVMDevice.class]){if(outError)*outError=error(@"foreign mapping device");return nil;}
    DVMDevice *d=(id)device;
    @synchronized(d){
        if(d.ownedMapping)return d.ownedMapping;
        NSError *e=nil;id<DVMMetalOwnedMapping> mapping=d.mappingProvider?d.mappingProvider(&e):nil;
        if(!mapping||![mapping conformsToProtocol:@protocol(DVMMetalOwnedMapping)]||mapping.mappingVersion!=1||
           !mapping.bytes||(uintptr_t)mapping.bytes%DVM_MANAGED_PAGE_BYTES||mapping.length!=DVM_PRESENT_BUFFER_BYTES){
            if(outError)*outError=e?:error(@"mapping provider/version/extent contract");return nil;
        }
        d.ownedMapping=mapping;return mapping;
    }
}
static NSDictionary *DVMSharedTextureOperation(id<MTLTexture> texture,uint64_t epoch,NSString *op,NSDictionary *fields) {
    if(![(id)texture isKindOfClass:DVMTexture.class]||!((DVMTexture *)texture).surfaceMapping)reject(@"shared texture ownership");
    DVMTexture *t=(id)texture;NSError *e=nil;
    NSMutableDictionary *request=[fields mutableCopy];request[@"op"]=op;request[@"handle"]=t.handle;request[@"epoch"]=@(epoch);
    NSDictionary *reply=[t.owner call:request error:&e];
    if(!reply||t.owner.lastSubmissionError)reject((e?:t.owner.lastSubmissionError).description?:@"shared texture state");
    // Serial RPC completion precedes this CPU-visible boundary. The actual
    // IOSurface lock/unlock and native display wait belong to its consumer.
    __atomic_thread_fence(__ATOMIC_SEQ_CST);return reply;
}
NSDictionary *DVMSharedTextureAcquire(id<MTLTexture> texture,uint64_t epoch) {
    return DVMSharedTextureOperation(texture,epoch,@"sharedRenderAcquire",[NSDictionary dictionary]);
}
NSDictionary *DVMSharedTextureSeal(id<MTLTexture> texture,uint64_t epoch) {
    return DVMSharedTextureOperation(texture,epoch,@"sharedRenderSeal",[NSDictionary dictionary]);
}
NSDictionary *DVMSharedTextureRetire(id<MTLTexture> texture,uint64_t epoch,uint32_t swap,int waitResult) {
    return DVMSharedTextureOperation(texture,epoch,@"sharedRenderRetire",@{@"swap":@(swap),@"waitMode":@1,@"waitResult":@(waitResult)});
}

// Narrow resident-workload extension. This is not an implementation of general
// MTLTexture shared backing; explicit entry points keep that limitation visible.
@interface DVMResidentTask : DVMObject
@property(nonatomic,strong) DVMLibrary *library;
@property(nonatomic) uint32_t lastFrame,frameCount;
@end
@implementation DVMResidentTask
@end
id DVMCreateResidentBlurBatch(id<MTLDevice> device,id<MTLLibrary> library,uint32_t nonce,uint32_t frames) {
    if(frames<2||frames>DVM_PRESENT_MAX_FRAMES)reject(@"resident frame budget");
    if(![(id)device isKindOfClass:DVMDevice.class]||![(id)library isKindOfClass:DVMLibrary.class])reject(@"resident object type");
    DVMDevice *d=(id)device;DVMLibrary *l=(id)library;
    if(l.owner!=d||!d.binaryPayloads||d.submissionInFlight)reject(@"resident device ownership/state");
    NSError *e=nil;NSDictionary *r=[d call:@{@"op":@"residentCreate",@"library":l.handle,@"nonce":@(nonce),@"frames":@(frames)} error:&e];
    if(!r)reject(e.description);
    DVMResidentTask *task=[DVMResidentTask new];task.owner=d;task.library=l;task.handle=r[@"handle"];task.frameCount=frames;return task;
}
id DVMCreateResidentBlur(id<MTLDevice> device,id<MTLLibrary> library,uint32_t nonce) {
    return DVMCreateResidentBlurBatch(device,library,nonce,33);
}
NSDictionary *DVMDrawResidentBlur(id object,uint32_t frame) {
    if(![object isKindOfClass:DVMResidentTask.class])reject(@"resident object type");
    DVMResidentTask *task=object;
    if(frame!=task.lastFrame+1||frame>task.frameCount||task.owner.submissionInFlight)reject(@"resident frame sequence");
    NSError *e=nil;NSDictionary*r=[task.owner call:@{@"op":@"residentDraw",@"handle":task.handle,@"frame":@(frame)} error:&e];
    if(!r||[r[@"frame"] unsignedIntValue]!=frame||[r[@"status"] unsignedIntValue]!=4)reject(e.description?:@"resident completion");
    task.lastFrame=frame;return r;
}
NSDictionary *DVMVerifyResidentBlur(id object) {
    if(![object isKindOfClass:DVMResidentTask.class]||((DVMResidentTask *)object).lastFrame!=((DVMResidentTask *)object).frameCount)reject(@"resident final verification state");
    DVMResidentTask *task=object;NSError *e=nil;
    NSDictionary*r=[task.owner call:@{@"op":@"residentVerify",@"handle":task.handle} error:&e];if(!r)reject(e.description);return r;
}
void DVMRetireResidentBlur(id object,uint32_t frame) {
    if(![object isKindOfClass:DVMResidentTask.class])reject(@"resident retirement object");
    DVMResidentTask *task=object;NSError *e=nil;
    if(frame!=task.lastFrame)reject(@"resident retirement sequence");
    if(![task.owner call:@{@"op":@"residentRetire",@"handle":task.handle,@"frame":@(frame)} error:&e])reject(e.description);
}
