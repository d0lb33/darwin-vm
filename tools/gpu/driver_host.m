// Persistent host-only Metal executor for the framed guest driver protocol.
// Stdout carries only length-prefixed protocol replies; diagnostics never share it.
#import <CommonCrypto/CommonDigest.h>
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include "driver_binary.h"
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

enum {
    kMaxFrame = 2 * 1024 * 1024,
    kMaxObjects = 128,
    kMaxTextures = 32 * 1024 * 1024,
    kMaxDispatches = 32,
    kMaxDimension = 512,
    kMaxLibrary = 16 * 1024 * 1024
};

@interface DVMEntry : NSObject
@property(nonatomic) uint64_t handle;
@property(nonatomic, copy) NSString *kind;
@property(nonatomic, strong) id object;
@property(nonatomic) NSUInteger textureBytes;
@property(nonatomic) NSUInteger width, height, row;
@property(nonatomic) MTLPixelFormat format;
@end
@implementation DVMEntry
@end

@interface DVMHost : NSObject
@property(nonatomic, strong) id<MTLDevice> device;
@property(nonatomic, strong) id<MTLCommandQueue> queue;
@property(nonatomic, strong) NSMutableDictionary<NSNumber *, DVMEntry *> *entries;
@property(nonatomic) uint64_t nextHandle, lastSeq, creations, submissions;
@property(nonatomic) NSUInteger textureBytes;
@end
@implementation DVMHost
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
    return entry && [entry.kind isEqualToString:kind] ? entry : nil;
}
static BOOL Add(DVMHost *host, NSString *kind, id object, DVMEntry **out) {
    if (host.entries.count >= kMaxObjects || host.nextHandle == UINT64_MAX)
        return NO;
    DVMEntry *entry = [DVMEntry new];
    entry.handle = ++host.nextHandle;
    entry.kind = kind;
    entry.object = object;
    host.entries[@(entry.handle)] = entry;
    host.creations++;
    *out = entry;
    return YES;
}
static BOOL Usage(id value, MTLTextureUsage *usage) {
    uint64_t u;
    if (!Number(value, &u) || !u || (u & ~3u))
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
    if ([[wanted lowercaseString] rangeOfCharacterFromSet:[hex invertedSet]].location != NSNotFound)
        return HostError(seq, EINVAL, @"sha256 must be lowercase hexadecimal");
    const char *path = getenv("DVM_DRIVER_LIBRARY");
    if (!path || !*path)
        return HostError(seq, ENOENT, @"DVM_DRIVER_LIBRARY is unset");
    NSData *bytes = [NSData dataWithContentsOfFile:@(path)
                                           options:NSDataReadingMappedIfSafe
                                             error:NULL];
    if (!bytes)
        return HostError(seq, ENOENT, @"DVM_DRIVER_LIBRARY cannot be read");
    if (bytes.length > kMaxLibrary || bytes.length != wantedLength ||
        ![[HexDigest(bytes) lowercaseString] isEqualToString:wanted])
        return HostError(seq, EILSEQ, @"DVM_DRIVER_LIBRARY does not match requested AIR");
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
    if (![@[ @"compute_average_luma", @"compute_sum_luma" ] containsObject:name])
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
    uint64_t width, height, format;
    MTLTextureUsage usage;
    if (!Number(request[@"width"], &width) || !Number(request[@"height"], &height) || !width ||
        !height || width > kMaxDimension || height > kMaxDimension ||
        !Usage(request[@"usage"], &usage))
        return HostError(seq, EINVAL, @"invalid texture descriptor");
    MTLPixelFormat pixel;
    NSUInteger bpp;
    if (!Number(request[@"format"], &format))
        return HostError(seq, EINVAL, @"invalid format");
    if (format == MTLPixelFormatBGRA8Unorm) {
        pixel = MTLPixelFormatBGRA8Unorm;
        bpp = 4;
    } else if (format == MTLPixelFormatRGBA16Float) {
        pixel = MTLPixelFormatRGBA16Float;
        bpp = 8;
    } else
        return HostError(seq, EINVAL, @"unsupported texture format");
    NSUInteger bytes = (NSUInteger)width * (NSUInteger)height * bpp;
    if (bytes > 1024 * 1024)
        return HostError(seq, EINVAL, @"texture exceeds framed transfer limit");
    if (bytes > kMaxTextures - host.textureBytes)
        return HostError(seq, ENOSPC, @"texture memory cap exceeded");
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:pixel
                                                           width:(NSUInteger)width
                                                          height:(NSUInteger)height
                                                       mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = usage;
    id<MTLTexture> texture = [host.device newTextureWithDescriptor:descriptor];
    if (!texture)
        return HostError(seq, ENOMEM, @"Metal texture allocation failed");
    DVMEntry *entry;
    if (!Add(host, @"texture", texture, &entry))
        return HostError(seq, ENOSPC, @"object table is full");
    entry.textureBytes = bytes;
    entry.width = width;
    entry.height = height;
    entry.row = (NSUInteger)width * bpp;
    entry.format = pixel;
    host.textureBytes += bytes;
    return @{@"seq" : @(seq), @"ok" : @YES, @"handle" : @(entry.handle), @"row" : @(entry.row)};
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
    if (!entry || !Number(request[@"row"], &row) || row < entry.row || row > UINT32_MAX || !data ||
        data.length != row * entry.height)
        return HostError(seq, EINVAL, @"upload must contain one complete texture");
    MTLRegion region = MTLRegionMake2D(0, 0, entry.width, entry.height);
    [(id<MTLTexture>)entry.object replaceRegion:region
                                    mipmapLevel:0
                                      withBytes:data.bytes
                                    bytesPerRow:(NSUInteger)row];
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
    NSMutableData *data = [NSMutableData dataWithLength:entry.textureBytes];
    MTLRegion region = MTLRegionMake2D(0, 0, entry.width, entry.height);
    [(id<MTLTexture>)entry.object getBytes:data.mutableBytes
                               bytesPerRow:entry.row
                                fromRegion:region
                               mipmapLevel:0];
    return @{
        @"seq" : @(seq),
        @"ok" : @YES,
        @"data" : [data base64EncodedStringWithOptions:0],
        @"row" : @(entry.row)
    };
}
static NSDictionary *Release(DVMHost *host, uint64_t seq, NSDictionary *request) {
    uint64_t handle;
    if (!Number(request[@"handle"], &handle) || !host.entries[@(handle)])
        return HostError(seq, ENOENT, @"unknown handle");
    DVMEntry *entry = host.entries[@(handle)];
    host.textureBytes -= entry.textureBytes;
    [host.entries removeObjectForKey:@(handle)];
    return @{@"seq" : @(seq), @"ok" : @YES};
}
static NSDictionary *Stats(DVMHost *host, uint64_t seq) {
    NSUInteger libraries = 0, pipelines = 0, textures = 0, buffers = 0;
    for (DVMEntry *entry in host.entries.allValues) {
        if ([entry.kind isEqual:@"library"])
            libraries++;
        else if ([entry.kind isEqual:@"pipeline"])
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
            @"resourceBytes" : @(host.textureBytes)
        },
        @"creations" : @(host.creations),
        @"submissions" : @(host.submissions)
    };
}
static NSDictionary *Buffer(DVMHost *host, uint64_t seq, NSDictionary *r) {
    uint64_t n;
    if (!Number(r[@"length"], &n) || !n || n > 1024 * 1024 || n > kMaxTextures - host.textureBytes)
        return HostError(seq, EINVAL, @"invalid buffer length");
    id<MTLBuffer> b = [host.device newBufferWithLength:n options:MTLResourceStorageModeShared];
    DVMEntry *e;
    if (!b || !Add(host, @"buffer", b, &e))
        return HostError(seq, ENOMEM, @"buffer allocation");
    e.textureBytes = n;
    host.textureBytes += n;
    memset(b.contents, 0, n);
    return @{@"seq" : @(seq), @"ok" : @YES, @"handle" : @(e.handle)};
}

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
        if (average && (!texture || texture.width != 64 || texture.height != 48 ||
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
        if (!entry || (buffer && u[@"texture"]) || !data || data.length != entry.textureBytes ||
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
            [(id<MTLTexture>)entry.object replaceRegion:MTLRegionMake2D(0,0,entry.width,entry.height)
                mipmapLevel:0 withBytes:data.bytes bytesPerRow:entry.row];
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
static NSDictionary *ProcessRequest(DVMHost *host, uint64_t seq, NSDictionary *request) {
    NSString *op = request[@"op"];
    if (![op isKindOfClass:NSString.class])
        return HostError(seq, EINVAL, @"request lacks op");
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
                BOOL binary=DVMBMagic(data)==DVM_BIN_REQUEST;
                id object = binary?DVMBDecodeRequest(data):[NSJSONSerialization JSONObjectWithData:data options:0 error:&error];
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
                NSDictionary *response=ProcessRequest(host,seq,request);
                if(binary && [response[@"ok"] boolValue]) {
                    NSData *encoded=DVMBEncodeReply(response);
                    if(!encoded)return 4;
                    uint32_t n=(uint32_t)encoded.length;
                    if(!WriteAll(&n,4)||!WriteAll(encoded.bytes,encoded.length))return 4;
                } else if(!Reply(response))return 4;
            }
        }
    }
}
