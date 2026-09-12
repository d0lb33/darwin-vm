// Persistent host-only Metal executor for the framed guest driver protocol.
// Stdout carries only length-prefixed protocol replies; diagnostics never share it.
#import <CommonCrypto/CommonDigest.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "driver_binary.h"
#include "driver_capabilities.h"
#include "blur_wire.h"
#include "present_host.h"
#include "shared_render_host.h"
#include "imported_pages_host.h"
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum {
    // A staged 1.875 MiB payload arrives from the peer as base64 inside JSON.
    kMaxFrame = 4 * 1024 * 1024,
    kMaxObjects = DVM_OBJECTS,
    kMaxTextures = DVM_ORDINARY_RESOURCE_BYTES,
    kMaxDispatches = 32,
    kMaxDimension = DVM_TEXTURE_DIMENSION,
    kMaxLibrary = 16 * 1024 * 1024
};

@interface DVMEntry : NSObject
@property(nonatomic) uint64_t handle;
@property(nonatomic, copy) NSString *kind;
@property(nonatomic, strong) id object;
@property(nonatomic) NSUInteger textureBytes;
@property(nonatomic) NSUInteger width, height, row;
@property(nonatomic) MTLPixelFormat format;
@property(nonatomic,strong) DVMEntry *parent;
@property(nonatomic) BOOL textureView;
@property(nonatomic) NSUInteger viewBaseLevel;
@property(nonatomic,strong) DVMSharedRender *sharedRender;
@property(nonatomic,strong) DVMImportedPages *importedPages;
@property(nonatomic) uint32_t guestProcessBits;
@property(nonatomic,strong) NSMutableData *textureUpload;
@property(nonatomic) uint64_t textureUploadToken;
@property(nonatomic) MTLPurgeableState purgeableState;
@property(nonatomic) NSUInteger poolOffset,poolSpan;
@end
@implementation DVMEntry
- (void)dealloc {_object=nil;_sharedRender=nil;}
@end

// View parents form a tree of owned handles; no guest pointer is followed.
static DVMEntry *ResourceRoot(DVMEntry *entry){while(entry.parent)entry=entry.parent;return entry;}
static DVMEntry *TextureRoot(DVMEntry *entry){while(entry.textureView)entry=entry.parent;return entry;}
static NSUInteger TextureBaseLevel(DVMEntry *entry){NSUInteger level=0;while(entry.textureView){level+=entry.viewBaseLevel;entry=entry.parent;}return level;}
static BOOL TextureLevelsOverlap(DVMEntry *a,DVMEntry *b){
    if(TextureRoot(a)!=TextureRoot(b))return NO;
    NSUInteger al=TextureBaseLevel(a),bl=TextureBaseLevel(b);
    return al<bl+[(id<MTLTexture>)b.object mipmapLevelCount]&&bl<al+[(id<MTLTexture>)a.object mipmapLevelCount];
}
@interface DVMHost : NSObject
@property(nonatomic, strong) id<MTLDevice> device;
@property(nonatomic, strong) id<MTLCommandQueue> queue;
@property(nonatomic, strong) NSMutableDictionary<NSNumber *, DVMEntry *> *entries;
@property(nonatomic) uint64_t nextHandle, lastSeq, creations, submissions;
@property(nonatomic) NSUInteger renderPasses,renderDraws,blitPasses,computePasses;
@property(nonatomic) NSUInteger textureBytes,residentBytes;
@property(nonatomic,strong) NSMutableDictionary<NSNumber *,DVMImportedPages *> *imports;
@property(nonatomic) NSUInteger importedBytes;
@property(nonatomic,strong) NSMutableData *renderStage;
@property(nonatomic,copy) NSString *renderStageSHA;
@property(nonatomic) uint64_t renderStageToken,renderStageLength;
@property(nonatomic) void *bufferPoolMap;
@property(nonatomic) int bufferPoolFD;
@property(nonatomic,strong) NSMutableIndexSet *bufferPoolPages;
@end
@implementation DVMHost
- (void)dealloc {
    if(_bufferPoolMap&&_bufferPoolMap!=MAP_FAILED)munmap(_bufferPoolMap,DVM_SHARED_RAM_BYTES);
    if(_bufferPoolFD>=0)close(_bufferPoolFD);
}
@end

static NSDictionary *ErrorReply(uint64_t seq, NSString *domain, NSInteger code,
                                NSString *description) {
    return @{
        @"seq" : @(seq),
        @"ok" : @NO,
        @"domain" : domain ?: @"NSError",
        @"code" : @(code),
        @"description" : description ?: @"error"
    };
}
static NSDictionary *HostError(uint64_t seq, NSInteger code, NSString *description) {
    return ErrorReply(seq, @"DVMDriverHost", code, description);
}
static NSDictionary *NSErrorReply(uint64_t seq, NSError *error, NSString *fallback) {
    return ErrorReply(seq, error.domain ?: @"NSError", error.code,
                      error.localizedDescription ?: fallback);
}
static BOOL WriteAll(const void *bytes, size_t length) {
    const uint8_t *p = bytes;
    while (length) {
        size_t n = fwrite(p, 1, length, stdout);
        if (!n)
            return NO;
        p += n;
        length -= n;
    }
    return fflush(stdout) == 0;
}
static BOOL ReadAll(void *bytes, size_t length) {
    uint8_t *p = bytes;
    while (length) {
        size_t n = fread(p, 1, length, stdin);
        if (!n)
            return NO;
        p += n;
        length -= n;
    }
    return YES;
}
static BOOL Reply(NSDictionary *reply) {
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:reply options:0 error:&error];
    if (!json || json.length > kMaxFrame)
        return NO;
    uint32_t length = CFSwapInt32HostToLittle((uint32_t)json.length);
    return WriteAll(&length, sizeof(length)) && WriteAll(json.bytes, json.length);
}
static BOOL Number(id value, uint64_t *out) {
    if (![value isKindOfClass:NSNumber.class])
        return NO;
    if (CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID())
        return NO;
    const char *type = [(NSNumber *)value objCType];
    if (strcmp(type, @encode(char)) && strcmp(type, @encode(unsigned char)) &&
        strcmp(type, @encode(short)) && strcmp(type, @encode(unsigned short)) &&
        strcmp(type, @encode(int)) && strcmp(type, @encode(unsigned int)) &&
        strcmp(type, @encode(long)) && strcmp(type, @encode(unsigned long)) &&
        strcmp(type, @encode(long long)) && strcmp(type, @encode(unsigned long long)))
        return NO;
    if ([(NSNumber *)value longLongValue] < 0)
        return NO;
    *out = [(NSNumber *)value unsignedLongLongValue];
    return YES;
}
static NSString *HexDigest(NSData *data) {
    unsigned char raw[CC_SHA256_DIGEST_LENGTH];
    char text[CC_SHA256_DIGEST_LENGTH * 2 + 1];
    CC_SHA256(data.bytes, (CC_LONG)data.length, raw);
    for (unsigned i = 0; i < sizeof(raw); i++)
        snprintf(text + i * 2, 3, "%02x", raw[i]);
    return @(text);
}
static DVMEntry *Entry(DVMHost *host, id raw, NSString *kind) {
    uint64_t handle;
    if (!Number(raw, &handle) || handle == 0)
        return nil;
    DVMEntry *entry = host.entries[@(handle)];
    if(entry.purgeableState>MTLPurgeableStateNonVolatile||ResourceRoot(entry).purgeableState>MTLPurgeableStateNonVolatile)return nil;
    return entry && [entry.kind isEqualToString:kind] ? entry : nil;
}
static BOOL Add(DVMHost *host, NSString *kind, id object, DVMEntry **out) {
    if (host.entries.count >= kMaxObjects || host.nextHandle == UINT64_MAX)
        return NO;
    DVMEntry *entry = [DVMEntry new];
    entry.handle = ++host.nextHandle;
    entry.kind = kind;
    entry.object = object;
    entry.purgeableState=MTLPurgeableStateNonVolatile;
    host.entries[@(entry.handle)] = entry;
    host.creations++;
    *out = entry;
    return YES;
}
static BOOL Usage(id value, MTLTextureUsage *usage) {
    uint64_t u;
    if (!Number(value, &u) || !u || (u & ~(DVM_TEXTURE_USAGE_MASK|DVM_TEXTURE_BLOCK_WRITES_ONLY)))
        return NO;
    *usage = (MTLTextureUsage)u;
    return YES;
}
static NSSet<NSNumber *> *TextureIndices(MTLComputePipelineReflection *reflection) {
    NSMutableSet *out = [NSMutableSet set];
    for (MTLArgument *arg in reflection.arguments)
        if (arg.type == MTLArgumentTypeTexture)
            [out addObject:@(arg.index)];
    return out;
}
static NSSet<NSNumber *> *BufferIndices(MTLComputePipelineReflection *reflection) {
    NSMutableSet *out = [NSMutableSet set];
    for (MTLArgument *arg in reflection.arguments)
        if (arg.type == MTLArgumentTypeBuffer)
            [out addObject:@(arg.index)];
    return out;
}
static NSDictionary *Library(DVMHost *host, uint64_t seq, NSDictionary *request) {
    NSString *wanted = request[@"sha256"];
    uint64_t wantedLength;
    if (![wanted isKindOfClass:NSString.class] || wanted.length != 64 ||
        !Number(request[@"length"], &wantedLength))
        return HostError(seq, EINVAL, @"library requires sha256 and length");
    NSCharacterSet *hex = [NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"];
    if ([wanted rangeOfCharacterFromSet:[hex invertedSet]].location != NSNotFound)
        return HostError(seq, EINVAL, @"sha256 must be lowercase hexadecimal");
    if (!wantedLength || wantedLength > kMaxLibrary)
        return HostError(seq, EINVAL, @"library length exceeds contract");
    // Guest paths never select host files. Additional exact-guest AIR is
    // explicitly provisioned by digest; still rehash bytes before native load.
    const char *cache = getenv("DVM_DRIVER_LIBRARY_CACHE");
    const char *path = getenv("DVM_DRIVER_LIBRARY");
    NSData *bytes = nil;
    if (cache && *cache) {
        NSString *candidate = [@(cache) stringByAppendingPathComponent:[wanted stringByAppendingString:@".metallib"]];
        bytes = [NSData dataWithContentsOfFile:candidate options:NSDataReadingMappedIfSafe error:NULL];
    }
    if (!bytes && path && *path)
        bytes = [NSData dataWithContentsOfFile:@(path) options:NSDataReadingMappedIfSafe error:NULL];
    if (!bytes)
        return HostError(seq, ENOENT, @"requested AIR unavailable in configured host libraries");
    if (bytes.length > kMaxLibrary || bytes.length != wantedLength ||
        ![[HexDigest(bytes) lowercaseString] isEqualToString:wanted])
        return HostError(seq, EILSEQ, @"configured host library does not match requested AIR length/SHA256");
    NSError *error = nil;
    dispatch_data_t data =
        dispatch_data_create(bytes.bytes, bytes.length, nil, DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    id<MTLLibrary> library = [host.device newLibraryWithData:data error:&error];
    if (!library)
        return NSErrorReply(seq, error, @"newLibraryWithData failed");
    DVMEntry *entry;
    if (!Add(host, @"library", library, &entry))
        return HostError(seq, ENOSPC, @"object table is full");
    return @{
        @"seq" : @(seq),
        @"ok" : @YES,
        @"handle" : @(entry.handle),
        @"functionNames" : library.functionNames ?: @[]
    };
}
static NSDictionary *Pipeline(DVMHost *host, uint64_t seq, NSDictionary *request) {
    DVMEntry *library = Entry(host, request[@"library"], @"library");
    NSString *name = request[@"function"];
    if (!library || ![name isKindOfClass:NSString.class] || !name.length)
        return HostError(seq, EINVAL, @"unknown library or invalid function");
    if (![@[ @"compute_average_luma", @"compute_sum_luma", @"compute_simd_blur_5" ] containsObject:name])
        return HostError(seq, ENOTSUP, @"kernel execution contract has not been audited");
    id<MTLFunction> function = [(id<MTLLibrary>)library.object newFunctionWithName:name];
    if (!function || function.functionType != MTLFunctionTypeKernel)
        return HostError(seq, ENOENT, @"compute function not found");
    NSError *error = nil;
    MTLComputePipelineReflection *reflection = nil;
    id<MTLComputePipelineState> state =
        [host.device newComputePipelineStateWithFunction:function
                                                 options:MTLPipelineOptionArgumentInfo
                                              reflection:&reflection
                                                   error:&error];
    if (!state)
        return NSErrorReply(seq, error, @"newComputePipelineState failed");
    DVMEntry *entry;
    if (!Add(host, @"pipeline", @{
            @"state" : state,
            @"function" : name,
            @"textures" : TextureIndices(reflection),
            @"buffers" : BufferIndices(reflection)
        },
             &entry))
        return HostError(seq, ENOSPC, @"object table is full");
    return @{
        @"seq" : @(seq),
        @"ok" : @YES,
        @"handle" : @(entry.handle),
        @"threadExecutionWidth" : @(state.threadExecutionWidth),
        @"maxTotalThreadsPerThreadgroup" : @(state.maxTotalThreadsPerThreadgroup)
    };
}
static NSDictionary *Texture(DVMHost *host, uint64_t seq, NSDictionary *request) {
    uint64_t width, height, format,depth,type,storage,levels;
    MTLTextureUsage usage;
    if (!Number(request[@"width"], &width) || !Number(request[@"height"], &height) || !width ||
        !height || width > kMaxDimension || height > kMaxDimension ||
        !Usage(request[@"usage"], &usage))
        return HostError(seq, EINVAL, @"invalid texture descriptor");
    if(!Number(request[@"storage"]?:@0,&storage)||storage>MTLStorageModePrivate)
        return HostError(seq,EINVAL,@"unsupported texture storage mode");
    if(!Number(request[@"depth"]?:@1,&depth)||!depth||depth>kMaxDimension||!Number(request[@"type"]?:@2,&type)||
       (type!=MTLTextureType1D&&type!=MTLTextureType2D&&type!=MTLTextureType3D)||(type!=MTLTextureType3D&&depth!=1)||
       (type==MTLTextureType1D&&height!=1)||
       (type==MTLTextureType3D&&usage!=MTLTextureUsageShaderRead))return HostError(seq,EINVAL,@"texture type/depth/usage contract");
    MTLPixelFormat pixel;
    NSUInteger bpp;
    if (!Number(request[@"format"], &format))
        return HostError(seq, EINVAL, @"invalid format");
    bpp=DVMFormatBytes(format);pixel=format;
    if(!bpp)return HostError(seq, EINVAL, @"unsupported texture format");
    if(!DVMTextureUsageValid(format,storage,type,usage))return HostError(seq,EINVAL,@"texture format usage contract");
    if(!Number(request[@"levels"]?:@1,&levels)||!DVMTextureLevelsValid(width,height,format,storage,type,levels))
        return HostError(seq,EINVAL,@"texture mip level contract");
    NSUInteger bytes = DVMTextureAllocationBytes(width,height,depth,format,levels);
    if (bytes > (storage==MTLStorageModePrivate?DVM_PRIVATE_TEXTURE_BYTES:DVM_TEXTURE_BYTES))
        return HostError(seq, EINVAL, @"texture exceeds storage-mode byte limit");
    if (bytes > kMaxTextures - host.textureBytes) {
        NSMutableDictionary *reply=[HostError(seq, ENOSPC, @"texture memory cap exceeded") mutableCopy];
        reply[@"requestedLogicalBytes"]=@(bytes);
        reply[@"liveLogicalBytes"]=@(host.textureBytes);
        reply[@"ordinaryResourceBudgetBytes"]=@(kMaxTextures);
        return reply;
    }
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pixel
                                                           width:(NSUInteger)width
                                                          height:(NSUInteger)height
                                                       mipmapped:NO];
    descriptor.textureType=type;descriptor.depth=depth;
    descriptor.storageMode = storage==MTLStorageModePrivate?MTLStorageModePrivate:MTLStorageModeShared;
    descriptor.usage = usage;
    descriptor.mipmapLevelCount=levels;
    id<MTLTexture> texture = [host.device newTextureWithDescriptor:descriptor];
    if (!texture)
        return HostError(seq, ENOMEM, @"Metal texture allocation failed");
    if(texture.usage!=usage||texture.mipmapLevelCount!=levels)return HostError(seq,ENOTSUP,@"native texture usage differs from requested contract");
    DVMEntry *entry;
    if (!Add(host, @"texture", texture, &entry))
        return HostError(seq, ENOSPC, @"object table is full");
    entry.textureBytes = bytes;
    entry.width = width;
    entry.height = height;
    entry.row = (NSUInteger)width * bpp;
    entry.format = pixel;
    host.textureBytes += bytes;
    return @{@"seq" : @(seq), @"ok" : @YES, @"handle" : @(entry.handle), @"row" : @(entry.row), @"allocatedSize":@(texture.allocatedSize),@"nativeStorageMode":@(texture.storageMode)};
}
static void ReplaceTextureBytes(id<MTLTexture> texture,const void *bytes,NSUInteger row) {
    BOOL oneD=texture.textureType==MTLTextureType1D;
    [texture replaceRegion:MTLRegionMake3D(0,0,0,texture.width,texture.height,texture.depth)
        mipmapLevel:0 slice:0 withBytes:bytes bytesPerRow:oneD?0:row bytesPerImage:oneD?0:row*texture.height];
}
static NSDictionary *Upload(DVMHost *host, uint64_t seq, NSDictionary *request) {
    DVMEntry *entry = Entry(host, request[@"texture"], @"texture");
    uint64_t row;
    NSString *base64 = request[@"data"];
    NSData *data = [base64 isKindOfClass:NSString.class]
                       ? [[NSData alloc] initWithBase64EncodedString:base64 options:0]
                       : nil;
    if (request[@"buffer"]) {
        DVMEntry *b = Entry(host, request[@"buffer"], @"buffer");
        if (!b || !data || data.length != b.textureBytes || request[@"texture"])
            return HostError(seq, EINVAL, @"invalid buffer upload");
        memcpy([(id<MTLBuffer>)b.object contents], data.bytes, data.length);
        return @{@"seq" : @(seq), @"ok" : @YES};
    }
    if (!entry || entry.sharedRender || entry.importedPages || entry.textureUpload || [(id<MTLTexture>)entry.object storageMode]==MTLStorageModePrivate || !Number(request[@"row"], &row) || row < entry.row || row > UINT32_MAX || !data ||
        data.length != row * entry.height * [(id<MTLTexture>)entry.object depth])
        return HostError(seq, EINVAL, @"upload must contain one complete texture");
    ReplaceTextureBytes(entry.object,data.bytes,row);
    return @{@"seq" : @(seq), @"ok" : @YES};
}
static NSDictionary *ReadTexture(DVMHost *host, uint64_t seq, NSDictionary *request) {
    if (request[@"buffer"]) {
        DVMEntry *b = Entry(host, request[@"buffer"], @"buffer");
        if (!b || request[@"texture"])
            return HostError(seq, ENOENT, @"unknown buffer");
        NSData *d = [NSData dataWithBytes:[(id<MTLBuffer>)b.object contents] length:b.textureBytes];
        return @{@"seq" : @(seq), @"ok" : @YES, @"data" : [d base64EncodedStringWithOptions:0]};
    }
    DVMEntry *entry = Entry(host, request[@"texture"], @"texture");
    if (!entry)
        return HostError(seq, ENOENT, @"unknown texture handle");
    if(entry.sharedRender||entry.importedPages)return HostError(seq,ENOTSUP,@"shared render pixels use owned mapping, not readback RPC");
    if(entry.textureUpload)return HostError(seq,EBUSY,@"texture upload not committed");
    if([(id<MTLTexture>)entry.object storageMode]==MTLStorageModePrivate)return HostError(seq,ENOTSUP,@"private texture has no CPU read access");
    uint64_t offset=0,length=entry.textureBytes;
    BOOL ranged=request[@"offset"]!=nil||request[@"length"]!=nil;
    if(!ranged&&length>DVM_TEXTURE_DIRECT_READ_BYTES)
        return HostError(seq,EINVAL,@"large texture read requires bounded chunks");
    if(ranged&&(!Number(request[@"offset"],&offset)||!Number(request[@"length"],&length)||!length||length>DVM_TEXTURE_TRANSFER_CHUNK||offset>entry.textureBytes||length>entry.textureBytes-offset))
        return HostError(seq,EINVAL,@"texture read range");
    NSMutableData *data = [NSMutableData dataWithLength:entry.textureBytes];
    MTLRegion region = MTLRegionMake3D(0,0,0,entry.width,entry.height,[(id<MTLTexture>)entry.object depth]);
    BOOL oneD=[(id<MTLTexture>)entry.object textureType]==MTLTextureType1D;
    [(id<MTLTexture>)entry.object getBytes:data.mutableBytes
                               bytesPerRow:oneD?0:entry.row bytesPerImage:oneD?0:entry.row*entry.height
                                fromRegion:region
                               mipmapLevel:0 slice:0];
    return @{
        @"seq" : @(seq),
        @"ok" : @YES,
        @"data" : [[data subdataWithRange:NSMakeRange(offset,length)] base64EncodedStringWithOptions:0],
        @"row" : @(entry.row)
    };
}
static NSDictionary *TextureChunk(DVMHost *host,uint64_t seq,NSDictionary *r){
    DVMEntry *e=Entry(host,r[@"texture"],@"texture");uint64_t token,offset;
    if(!e||e.sharedRender||e.importedPages||e.parent||[(id<MTLTexture>)e.object storageMode]==MTLStorageModePrivate||!Number(r[@"token"],&token))
        return HostError(seq,EINVAL,@"texture upload ownership/storage/token");
    if([r[@"op"] isEqual:@"abortTextureUpload"]){
        if(!e.textureUpload||token!=e.textureUploadToken)return HostError(seq,EINVAL,@"texture upload abort token");
        e.textureUpload=nil;return @{@"seq":@(seq),@"ok":@YES};
    }
    NSData *data=DVMBPayload(r[@"data"]);
    if(!data.length||data.length>DVM_STAGING_BYTES||!Number(r[@"offset"],&offset)||offset>e.textureBytes||data.length>e.textureBytes-offset)
        return HostError(seq,EINVAL,@"texture upload chunk extent");
    if(!token){
        if(offset)return HostError(seq,EINVAL,@"texture upload must start at zero");
        // One bounded CPU staging allocation; never expose partially uploaded
        // content to GPU commands. The existing native image changes only once.
        for(DVMEntry *other in host.entries.allValues)if(other.textureUpload)return HostError(seq,EBUSY,@"texture upload already active");
        e.textureUpload=[NSMutableData dataWithCapacity:e.textureBytes];e.textureUploadToken=seq;
    }else if(!e.textureUpload||token!=e.textureUploadToken||offset!=e.textureUpload.length)
        return HostError(seq,EINVAL,@"texture upload chunk order/token");
    [e.textureUpload appendData:data];BOOL complete=e.textureUpload.length==e.textureBytes;
    if(complete){
        id<MTLTexture> texture=e.object;
        ReplaceTextureBytes(texture,e.textureUpload.bytes,e.row);
        e.textureUpload=nil;
    }
    return @{@"seq":@(seq),@"ok":@YES,@"token":@(e.textureUploadToken),@"accepted":@(offset+data.length),@"complete":@(complete)};
}
static NSDictionary *Release(DVMHost *host, uint64_t seq, NSDictionary *request) {
    uint64_t handle,retiredID=0;
    if (!Number(request[@"handle"], &handle) || !host.entries[@(handle)])
        return HostError(seq, ENOENT, @"unknown handle");
    DVMEntry *entry = host.entries[@(handle)];
    if(entry.sharedRender&&entry.sharedRender.state!=DVMSharedIdle)
        return HostError(seq,EBUSY,@"shared render release before retirement");
    for(DVMEntry *other in host.entries.allValues)if(other.parent==entry)
        return HostError(seq,EBUSY,@"parent retained by texture view");
    if([entry.kind isEqual:@"resident"]&&[(DVMResidentBlur *)entry.object displayPending])
        return HostError(seq,EBUSY,@"managed release before display retirement");
    if(entry.importedPages){
        DVMImportedPages *mapping=entry.importedPages;
        if(mapping.failed)return HostError(seq,EBUSY,@"failed imported allocation is quarantined");
        entry.object=nil;entry.importedPages=nil;
        BOOL last=YES;for(DVMEntry *other in host.entries.allValues)if(other.importedPages==mapping){last=NO;break;}
        if(last){
            if(![mapping retire]){mapping.failed=YES;entry.importedPages=mapping;return HostError(seq,EIO,@"import aliases/retirement acknowledgment failed; quarantine");}
            host.importedBytes-=mapping.span;[host.imports removeObjectForKey:@(mapping.resourceID)];
            retiredID=mapping.resourceID;
        }
    }
    if(entry.poolSpan){
        entry.object=nil;
        NSRange pages=NSMakeRange((entry.poolOffset-DVM_BUFFER_POOL_OFFSET)/DVM_MANAGED_PAGE_BYTES,
                                  entry.poolSpan/DVM_MANAGED_PAGE_BYTES);
        [host.bufferPoolPages removeIndexesInRange:pages];
    }
    if(!entry.textureView)host.textureBytes -= entry.textureBytes;
    if([entry.kind isEqual:@"resident"])host.residentBytes=0;
    [host.entries removeObjectForKey:@(handle)];
    return @{@"seq" : @(seq), @"ok" : @YES,@"retiredSurface":@(retiredID)};
}
static NSDictionary *Stats(DVMHost *host, uint64_t seq) {
    NSUInteger libraries = 0, pipelines = 0, textures = 0, buffers = 0;
    for (DVMEntry *entry in host.entries.allValues) {
        if ([entry.kind isEqual:@"library"])
            libraries++;
        else if ([entry.kind isEqual:@"pipeline"]||[entry.kind isEqual:@"renderPipeline"]||[entry.kind isEqual:@"computePipeline"])
            pipelines++;
        else if ([entry.kind isEqual:@"texture"])
            textures++;
        else if ([entry.kind isEqual:@"buffer"])
            buffers++;
    }
    return @{
        @"seq" : @(seq),
        @"ok" : @YES,
        @"live" : @{
            @"libraries" : @(libraries),
            @"pipelines" : @(pipelines),
            @"textures" : @(textures),
            @"buffers" : @(buffers),
            @"objects" : @(host.entries.count),
            @"resourceBytes" : @(host.textureBytes+host.residentBytes+host.importedBytes),
            @"ordinaryLogicalBytes" : @(host.textureBytes),
            @"residentWorkloads":@(host.residentBytes?1:0),
            @"stagedRenderBytes":@(host.renderStage.length),@"stagedRenderTransactions":@(host.renderStage?1:0),
            @"importedBytes":@(host.importedBytes),@"importedAllocations":@(host.imports.count),
            @"sharedBufferBytes":@(host.bufferPoolPages.count*DVM_MANAGED_PAGE_BYTES),@"sharedBufferPages":@(host.bufferPoolPages.count)
        },
        @"creations" : @(host.creations),
        @"submissions" : @(host.submissions),@"renderPasses":@(host.renderPasses),@"renderDraws":@(host.renderDraws),@"blitPasses":@(host.blitPasses),@"computePasses":@(host.computePasses)
    };
}
static BOOL MapBufferPool(DVMHost *host) {
    if(host.bufferPoolMap)return host.bufferPoolMap!=MAP_FAILED;
    host.bufferPoolFD=-1;
    const char *path=getenv("DVM_DRIVER_PRESENT_RAM");
    if(!path||!*path){host.bufferPoolMap=MAP_FAILED;return NO;}
    int fd=open(path,O_RDWR);struct stat st;
    if(fd<0||fstat(fd,&st)||st.st_size!=DVM_SHARED_RAM_BYTES){if(fd>=0)close(fd);host.bufferPoolMap=MAP_FAILED;return NO;}
    void *map=mmap(NULL,DVM_SHARED_RAM_BYTES,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    uint32_t magic=0;if(map!=MAP_FAILED)memcpy(&magic,map,4);
    if(map==MAP_FAILED||magic!=0x44564d31u){if(map!=MAP_FAILED)munmap(map,DVM_SHARED_RAM_BYTES);close(fd);host.bufferPoolMap=MAP_FAILED;return NO;}
    host.bufferPoolFD=fd;host.bufferPoolMap=map;host.bufferPoolPages=[NSMutableIndexSet indexSet];return YES;
}
static NSRange AllocateBufferPool(DVMHost *host,NSUInteger length) {
    NSUInteger pages=(length+DVM_MANAGED_PAGE_BYTES-1)/DVM_MANAGED_PAGE_BYTES;
    NSUInteger capacity=DVM_BUFFER_POOL_BYTES/DVM_MANAGED_PAGE_BYTES;
    for(NSUInteger start=0;start+pages<=capacity;start++){
        NSRange range=NSMakeRange(start,pages);
        if(![host.bufferPoolPages intersectsIndexesInRange:range]){[host.bufferPoolPages addIndexesInRange:range];return range;}
    }
    return NSMakeRange(NSNotFound,0);
}
static NSDictionary *Buffer(DVMHost *host, uint64_t seq, NSDictionary *r) {
    uint64_t n;
    if (!Number(r[@"length"], &n) || !n || n > DVM_BUFFER_BYTES || n > kMaxTextures - host.textureBytes)
        return HostError(seq, EINVAL, @"invalid buffer length");
    BOOL requested=[r[@"sharedPool"] isEqual:@YES];NSRange pages=NSMakeRange(NSNotFound,0);
    if(requested&&!MapBufferPool(host))return HostError(seq,EIO,@"shared buffer pool mapping");
    if(requested)pages=AllocateBufferPool(host,(NSUInteger)n);
    NSUInteger span=pages.location==NSNotFound?0:pages.length*DVM_MANAGED_PAGE_BYTES;
    NSUInteger offset=pages.location==NSNotFound?0:DVM_BUFFER_POOL_OFFSET+pages.location*DVM_MANAGED_PAGE_BYTES;
    id<MTLBuffer> b=span?[host.device newBufferWithBytesNoCopy:(uint8_t *)host.bufferPoolMap+offset length:span options:MTLResourceStorageModeShared deallocator:nil]:
                         [host.device newBufferWithLength:n options:MTLResourceStorageModeShared];
    DVMEntry *e;
    if (!b || !Add(host, @"buffer", b, &e)) {
        if(span)[host.bufferPoolPages removeIndexesInRange:pages];
        return HostError(seq, ENOMEM, @"buffer allocation");
    }
    e.textureBytes = n;
    e.poolOffset=offset;e.poolSpan=span;
    host.textureBytes += n;
    memset(b.contents, 0, span?:n);
    NSMutableDictionary *reply=[@{@"seq" : @(seq), @"ok" : @YES, @"handle" : @(e.handle), @"allocatedSize":@(b.allocatedSize)} mutableCopy];
    if(span){reply[@"sharedOffset"]=@(offset);reply[@"sharedSpan"]=@(span);}
    return reply;
}

#include "blur_host_submit.inc"

// Safety envelope derived from the exact AIR, not inferred from reflection's
// minimum element size. General arbitrary dispatch is intentionally not exposed.
// compute_average_luma writes one float4 per group; compute_sum_luma consumes
// min(iterations*(lid+1),thread_group_count) partials and writes one float4.
static NSDictionary *Submit(DVMHost *host, uint64_t seq, NSDictionary *r) {
    NSArray *items = r[@"commands"];
    if (![items isKindOfClass:NSArray.class] || !items.count || items.count > 32)
        return HostError(seq, EINVAL, @"invalid command count");
    NSMutableArray *validated = [NSMutableArray array];
    for (id raw in items) {
        if (![raw isKindOfClass:NSDictionary.class])
            return HostError(seq, EINVAL, @"invalid command");
        NSDictionary *c = raw;
        DVMEntry *p = Entry(host, c[@"pipeline"], @"pipeline");
        if (!p)
            return HostError(seq, ENOENT, @"unknown pipeline");
        NSString *name = p.object[@"function"];
        BOOL average = [name isEqual:@"compute_average_luma"];
        NSArray *ts = c[@"textures"], *bs = c[@"buffers"], *inlineBytes = c[@"bytes"],
                *scratch = c[@"threadgroupMemory"];
        if (![ts isKindOfClass:NSArray.class] || ![bs isKindOfClass:NSArray.class] ||
            ![inlineBytes isKindOfClass:NSArray.class] || ![scratch isKindOfClass:NSArray.class] ||
            ts.count != (average ? 1 : 0) || bs.count != (average ? 1 : 2) ||
            inlineBytes.count != 1 || scratch.count != 1 ||
            ![c[@"groups"] isEqual:(average ? @[ @1, @6, @1 ] : @[ @1, @1, @1 ])] ||
            ![c[@"threads"] isEqual:(average ? @[ @8, @8, @1 ] : @[ @8, @1, @1 ])])
            return HostError(seq, EINVAL, @"dispatch outside audited luma envelope");
        NSDictionary *u = inlineBytes[0], *tg = scratch[0];
        uint64_t index, n;
        if (![u isKindOfClass:NSDictionary.class] || !Number(u[@"index"], &index) || index != 0 ||
            !DVMBPayload(u[@"data"]) || ![tg isKindOfClass:NSDictionary.class] ||
            !Number(tg[@"index"], &index) || index != 0 || !Number(tg[@"length"], &n) ||
            n != (average ? 1024 : 128))
            return HostError(seq, EINVAL, @"invalid uniform or scratch binding");
        NSData *uniform = DVMBPayload(u[@"data"]);
        const uint8_t expected[20] = {0, 0, 0, 0, 64, 0, 48, 0, 1, 0, 0, 0, 6, 0, 0, 0, 1, 0, 0, 0};
        if (uniform.length != 20 || memcmp(uniform.bytes, expected, 20))
            return HostError(seq, EINVAL, @"luma uniform does not match audited bounds");
        NSMutableDictionary *buffers = [NSMutableDictionary dictionary];
        for (id binding in bs) {
            uint64_t off;
            if (![binding isKindOfClass:NSDictionary.class] || !Number(binding[@"index"], &index) ||
                (index != 2 && (average || index != 1)) || buffers[@(index)] ||
                !Number(binding[@"offset"], &off) || off)
                return HostError(seq, EINVAL, @"invalid buffer index or offset");
            DVMEntry *b = Entry(host, binding[@"buffer"], @"buffer");
            NSUInteger needed = average || index == 1 ? 96 : 16;
            if (!b || b.textureBytes < needed)
                return HostError(seq, EINVAL, @"buffer shorter than shader footprint");
            buffers[@(index)] = b;
        }
        if (!buffers[@2] || (!average && (!buffers[@1] || buffers[@1] == buffers[@2])))
            return HostError(seq, EINVAL, @"missing or aliased buffer");
        DVMEntry *texture = average ? Entry(host, ts[0], @"texture") : nil;
        if (average && (!texture || texture.importedPages || texture.width != 64 || texture.height != 48 || [(id<MTLTexture>)texture.object textureType]!=MTLTextureType2D ||
                        texture.format != MTLPixelFormatRGBA16Float ||
                        !([(id<MTLTexture>)texture.object usage] & MTLTextureUsageShaderRead)))
            return HostError(seq, EINVAL, @"luma requires 64x48 RGBA16Float input");
        [validated addObject:@{
            @"pipeline" : p,
            @"buffers" : buffers,
            @"uniform" : uniform,
            @"scratch" : @(n),
            @"average" : @(average),
            @"texture" : texture ?: NSNull.null
        }];
    }
    // Validate every transfer and dispatch before changing resources or committing.
    NSArray *uploads = r[@"uploads"] ?: @[], *readbacks = r[@"readbacks"] ?: @[];
    if (![uploads isKindOfClass:NSArray.class] || uploads.count > kMaxObjects ||
        ![readbacks isKindOfClass:NSArray.class] || readbacks.count > kMaxObjects)
        return HostError(seq, EINVAL, @"invalid batch transfers");
    NSMutableArray *transfers = [NSMutableArray array], *reads = [NSMutableArray array];
    NSMutableSet *written = [NSMutableSet set], *readHandles = [NSMutableSet set];
    NSUInteger totalRead = 0;
    for (id raw in uploads) {
        if (![raw isKindOfClass:NSDictionary.class]) return HostError(seq, EINVAL, @"invalid batch upload");
        NSDictionary *u = raw;
        BOOL buffer = u[@"buffer"] != nil;
        DVMEntry *entry = Entry(host, u[buffer ? @"buffer" : @"texture"], buffer ? @"buffer" : @"texture");
        id encoded = u[@"data"];
        NSData *data = DVMBPayload(encoded);
        uint64_t row = 0;
        if (!entry || entry.sharedRender || entry.importedPages || (!buffer&&[(id<MTLTexture>)entry.object storageMode]==MTLStorageModePrivate) || (buffer && u[@"texture"]) || !data || data.length != entry.textureBytes ||
            [written containsObject:@(entry.handle)] ||
            (!buffer && (!Number(u[@"row"], &row) || row != entry.row)))
            return HostError(seq, EINVAL, @"invalid or duplicate batch upload");
        [written addObject:@(entry.handle)];
        [transfers addObject:@{@"entry":entry, @"data":data}];
    }
    for (id handle in readbacks) {
        DVMEntry *entry = Entry(host, handle, @"buffer");
        if (!entry || [readHandles containsObject:handle] || entry.textureBytes > 1024*1024-totalRead)
            return HostError(seq, EINVAL, @"invalid, duplicate or oversized batch readback");
        totalRead += entry.textureBytes;
        [readHandles addObject:handle]; [reads addObject:entry];
    }
    for (NSDictionary *transfer in transfers) {
        DVMEntry *entry = transfer[@"entry"]; NSData *data = transfer[@"data"];
        if ([entry.kind isEqual:@"buffer"])
            memcpy([(id<MTLBuffer>)entry.object contents], data.bytes, data.length);
        else
            ReplaceTextureBytes(entry.object,data.bytes,entry.row);
    }
    id<MTLCommandBuffer> cb = [host.queue commandBuffer];
    for (NSDictionary *v in validated) {
        DVMEntry *p = v[@"pipeline"];
        id<MTLComputeCommandEncoder> e = [cb computeCommandEncoder];
        [e setComputePipelineState:p.object[@"state"]];
        BOOL average = [v[@"average"] boolValue];
        if (average)
            [e setTexture:((DVMEntry *)v[@"texture"]).object atIndex:0];
        NSData *u = v[@"uniform"];
        [e setBytes:u.bytes length:u.length atIndex:0];
        NSDictionary *bs = v[@"buffers"];
        for (NSNumber *i in bs)
            [e setBuffer:((DVMEntry *)bs[i]).object offset:0 atIndex:i.unsignedIntegerValue];
        [e setThreadgroupMemoryLength:[v[@"scratch"] unsignedIntegerValue] atIndex:0];
        [e dispatchThreadgroups:MTLSizeMake(1, average ? 6 : 1, 1)
            threadsPerThreadgroup:MTLSizeMake(8, average ? 8 : 1, 1)];
        [e endEncoding];
    }
    [cb commit];
    [cb waitUntilCompleted];
    host.submissions++;
    if (cb.status != MTLCommandBufferStatusCompleted)
        return NSErrorReply(seq, cb.error, @"GPU completion failed");
    uint64_t us = (uint64_t)((cb.GPUEndTime - cb.GPUStartTime) * 1e6);
    fprintf(stderr, "DVM_DRIVER_GPU seq=%llu dispatches=%lu status=%lu gpu_us=%llu\n",
            (unsigned long long)seq, (unsigned long)items.count, (unsigned long)cb.status,
            (unsigned long long)us);
    fflush(stderr);
    NSMutableDictionary *results = [NSMutableDictionary dictionary];
    for (DVMEntry *entry in reads) {
        NSData *data = [NSData dataWithBytes:[(id<MTLBuffer>)entry.object contents] length:entry.textureBytes];
        results[@(entry.handle).stringValue] = [r[@"_binary"] boolValue] ? data : [data base64EncodedStringWithOptions:0];
    }
    return @{
        @"buffers" : results,
        @"seq" : @(seq),
        @"ok" : @YES,
        @"status" : @(cb.status),
        @"gpu_us" : @(us),
        @"dispatches" : @(items.count)
    };
}
#include "present_host_submit.inc"
#include "consumer_state_host.inc"
#include "consumer_resource_host.inc"
#include "consumer_render_host.inc"
#include "consumer_function_host.inc"
#include "shared_render_host.inc"
#include "imported_surface_host.inc"

static NSDictionary *ProcessRequest(DVMHost *host, uint64_t seq, NSDictionary *request);
#include "render_staging_host.inc"
static NSDictionary *ProcessRequest(DVMHost *host, uint64_t seq, NSDictionary *request) {
    NSString *op = request[@"op"];
    if (![op isKindOfClass:NSString.class])
        return HostError(seq, EINVAL, @"request lacks op");
    if([op hasPrefix:@"renderStage"])return RenderStage(host,seq,request);
    if(host.renderStage&&![op isEqual:@"stats"])return HostError(seq,EBUSY,@"incomplete render request transaction");
    if([op isEqual:@"renderSubmitJSON"]){
        NSString *encoded=request[@"data"];
        NSData *data=[encoded isKindOfClass:NSString.class]
            ?[[NSData alloc] initWithBase64EncodedString:encoded options:0]:nil;
        if(!data.length||data.length>DVM_RENDER_REQUEST_BYTES)
            return HostError(seq,EINVAL,@"serialized render request extent");
        NSError *failure=nil;
        id decoded=[NSJSONSerialization JSONObjectWithData:data options:0 error:&failure];
        if(![decoded isKindOfClass:NSDictionary.class]||![decoded[@"op"] isEqual:@"renderSubmit"]||decoded[@"seq"]||decoded[@"client"])
            return HostError(seq,EINVAL,@"serialized render request contract");
        return ProcessRequest(host,seq,decoded);
    }
    if([op isEqual:@"textureView"])return TextureView(host,seq,request);
    if([op isEqual:@"computePipeline"])return ComputePipeline(host,seq,request);
    if([op isEqual:@"writeTextureChunk"]||[op isEqual:@"abortTextureUpload"])return TextureChunk(host,seq,request);
    if([op isEqual:@"renderSubmit"]||[op isEqual:@"submit"]||[op isEqual:@"blurSubmit"])
        for(DVMEntry *e in host.entries.allValues)if(e.textureUpload)return HostError(seq,EBUSY,@"GPU submission during incomplete texture upload");
    if([op isEqual:@"capabilities"]){
        // Apple feature tables list programmable blending from Apple2. This
        // backend forwards current-fragment color reads to native Metal;
        // test_framebuffer_read verifies ordered overlapping draws/reuse.
        // Do not advertise this profile on an IMR/non-Apple backend.
        if(![host.device supportsFamily:MTLGPUFamilyApple2])
            return HostError(seq,ENOTSUP,@"host lacks programmable color-attachment blending");
        // Our only IOSurface texture import is owned BGRA storage. Verify the
        // native implementation accepts the advertised row/base granularity;
        // kernel registration still requires 16 KiB pages independently.
        NSUInteger alignment=[host.device minimumLinearTextureAlignmentForPixelFormat:MTLPixelFormatBGRA8Unorm];
        if(!alignment||DVM_SHARED_TEXTURE_ALIGNMENT%alignment||DVM_PRESENT_ROW%DVM_SHARED_TEXTURE_ALIGNMENT||
           DVM_MANAGED_PAGE_BYTES%DVM_SHARED_TEXTURE_ALIGNMENT)
            return HostError(seq,ENOTSUP,@"host cannot honor owned IOSurface alignment profile");
        for(NSNumber *format in @[@70,@80,@115]){
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format.unsignedIntegerValue width:4 height:4 mipmapped:NO];
            d.storageMode=MTLStorageModePrivate;d.usage=DVM_TEXTURE_BLOCK_WRITES_ONLY|5u;
            id<MTLTexture> probe=[host.device newTextureWithDescriptor:d];
            if(!probe||probe.usage!=d.usage||probe.storageMode!=d.storageMode)
                return HostError(seq,ENOTSUP,@"host cannot honor private block-write color textures");
        }
        for(NSNumber *format in @[@10,@30,@554]){
            MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format.unsignedIntegerValue width:16 height:16 mipmapped:YES];
            d.storageMode=MTLStorageModePrivate;d.usage=5;
            id<MTLTexture> probe=[host.device newTextureWithDescriptor:d];
            if(!probe||probe.pixelFormat!=d.pixelFormat||probe.mipmapLevelCount!=d.mipmapLevelCount)
                return HostError(seq,ENOTSUP,@"host cannot honor QuartzCore warmup color/mipmap formats");
        }
        for(NSNumber *format in @[@23,@25,@55,@105]){
            MTLTextureDescriptor *lut=[MTLTextureDescriptor new];lut.textureType=MTLTextureType1D;
            lut.pixelFormat=format.unsignedIntegerValue;lut.width=DVM_TEXTURE_DIMENSION;lut.height=lut.depth=1;
            lut.storageMode=MTLStorageModeShared;lut.usage=MTLTextureUsageShaderRead;
            id<MTLTexture> lutProbe=[host.device newTextureWithDescriptor:lut];
            if(!lutProbe||lutProbe.textureType!=lut.textureType||lutProbe.pixelFormat!=lut.pixelFormat)
                return HostError(seq,ENOTSUP,@"host cannot honor sampled 1D LUT profile");
        }
        return @{@"seq":@(seq),@"ok":@YES,@"contract":DVMContractProfile()};
    }
    if([op hasPrefix:@"resident"])return ResidentRequest(host,seq,request);
    if([op hasPrefix:@"sharedRender"])return SharedRenderRequest(host,seq,request);
    if([op isEqual:@"surfaceImport"]||[op isEqual:@"surfaceImportCaps"])return ImportedSurfaceRequest(host,seq,request);
    if([op isEqual:@"depthState"])return DepthState(host,seq,request);
    if([op isEqual:@"linearLayout"])return LinearLayout(host,seq,request);
    if([op isEqual:@"linearTexture"])return LinearTexture(host,seq,request);
    if([op isEqual:@"resourcePurgeable"])return ResourcePurgeable(host,seq,request);
    if([op isEqual:@"resourceProcess"]){
        DVMEntry *entry=Entry(host,request[@"handle"],@"texture")?:Entry(host,request[@"handle"],@"buffer");uint64_t bits;
        if(!entry||!Number(request[@"processBits"],&bits)||bits>UINT32_MAX)return HostError(seq,EINVAL,@"resource process ownership/value");
        // Guest pid_t is opaque attribution; never charge or impersonate a
        // host PID using a numerically equal identifier from the VM.
        entry.guestProcessBits=(uint32_t)bits;
        return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"processBits":@(entry.guestProcessBits)};
    }
    if([op isEqual:@"renderPipeline"])return RenderPipeline(host,seq,request);
    if([op isEqual:@"sampler"])return Sampler(host,seq,request);
    if([op isEqual:@"renderSubmit"])return RenderSubmit(host,seq,request);
    if([op isEqual:@"writeRenderBuffer"])return WriteRenderBuffer(host,seq,request);
    if([op isEqual:@"readRenderBuffer"])return ReadRenderBuffer(host,seq,request);
    if([op isEqual:@"readRenderBufferStaged"])return ReadRenderBufferStaged(host,seq,request);
    if([op isEqual:@"function"])return SpecializedFunction(host,seq,request);
    if ([op isEqual:@"buffer"])
        return Buffer(host, seq, request);
    if ([op isEqual:@"library"])
        return Library(host, seq, request);
    if ([op isEqual:@"pipeline"])
        return Pipeline(host, seq, request);
    if ([op isEqual:@"texture"])
        return Texture(host, seq, request);
    if ([op isEqual:@"upload"])
        return Upload(host, seq, request);
    if ([op isEqual:@"submit"])
        return Submit(host, seq, request);
    if ([op isEqual:@"read"])
        return ReadTexture(host, seq, request);
    if ([op isEqual:@"release"])
        return Release(host, seq, request);
    if ([op isEqual:@"stats"])
        return Stats(host, seq);
    return HostError(seq, ENOSYS, @"unknown operation");
}
int main(void) {
    @autoreleasepool {
        DVMHost *host = [DVMHost new];
        host.bufferPoolFD=-1;
        host.device = MTLCreateSystemDefaultDevice();
        if (!host.device)
            return 2;
        host.queue = [host.device newCommandQueue];
        if (!host.queue)
            return 2;
        host.entries = [NSMutableDictionary dictionary];
        fprintf(stderr, "DVM_DRIVER_HOST device=%s os=%s\n", host.device.name.UTF8String,
                NSProcessInfo.processInfo.operatingSystemVersionString.UTF8String);
        fflush(stderr);
        // Bootstrap is outside the guest command sequence. Publish readiness
        // only after an actual Metal device and command queue exist.
        if (getenv("DVM_DRIVER_BOOTSTRAP") &&
            !Reply(@{@"bootstrap":@1, @"protocol":@"DVM-METAL-DRIVER-v1",
                     @"device":host.device.name, @"queue":@YES}))
            return 4;
        for (;;) {
            @autoreleasepool {
                uint32_t little;
                size_t got = fread(&little, 1, sizeof(little), stdin);
                if (!got)
                    return 0;
                if (got != sizeof(little))
                    return 3;
                uint32_t length = CFSwapInt32LittleToHost(little);
                if (!length || length > kMaxFrame)
                    return 3;
                NSMutableData *data = [NSMutableData dataWithLength:length];
                if (!ReadAll(data.mutableBytes, length))
                    return 3;
                NSError *error = nil;
                BOOL blur=DVMBMagic(data)==DVM_BLUR_REQUEST;
                BOOL binary=DVMBMagic(data)==DVM_BIN_REQUEST;
                id object = blur?DVMBlurDecode(data):binary?DVMBDecodeRequest(data):[NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
                if (![object isKindOfClass:NSDictionary.class])
                    return 3;
                NSDictionary *request = object;
                if(!binary && request[@"_binary"])return 3;
                uint64_t seq;
                if (!Number(request[@"seq"], &seq) || !seq || seq <= host.lastSeq) {
                    if (Number(request[@"seq"], &seq))
                        Reply(HostError(seq, EALREADY, @"seq must be strictly monotonic"));
                    return 3;
                }
                host.lastSeq = seq;
                NSDictionary *response=blur?BlurSubmit(host,seq,request):ProcessRequest(host,seq,request);
                if((binary||blur) && [response[@"ok"] boolValue]) {
                    NSData *encoded=blur?DVMBlurReplyEncode(response):DVMBEncodeReply(response);
                    if(!encoded)return 4;
                    uint32_t n=(uint32_t)encoded.length;
                    if(!WriteAll(&n,4)||!WriteAll(encoded.bytes,encoded.length))return 4;
                } else if(!Reply(response))return 4;
            }
        }
    }
}
