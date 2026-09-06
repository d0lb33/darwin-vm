// Narrow, process-local Metal-shaped wrappers backed by the host proxy protocol.
// These classes intentionally do not declare MTL protocol conformance.
// Diagnostic contract: one serial caller holds the device and borrowed FDs
// alive through all operations. Any transport error requires session disposal.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <CommonCrypto/CommonDigest.h>
#include <dispatch/dispatch.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <unistd.h>

NSString * const DVMForwardingErrorDomain = @"DVMForwardingErrorDomain";
enum { DVMForwardingProtocol = 1, DVMForwardingState = 2, DVMForwardingIO = 3,
       DVMForwardingUnsupported = 4, DVMForwardingValidation = 5 };
enum { DVMWidth = 64, DVMHeight = 48, DVMRow = 256, DVMBytes = 12288,
       DVMMaxLibrary = 12 * 1024 * 1024 };

@class DVMForwardingDevice, DVMForwardingTexture, DVMForwardingCommandBuffer;

static NSError *DVMError(NSInteger code, NSString *detail) {
    return [NSError errorWithDomain:DVMForwardingErrorDomain code:code
                            userInfo:@{NSLocalizedDescriptionKey: detail}];
}
static BOOL WriteAll(int fd, const void *pointer, size_t length) {
    const uint8_t *p = pointer;
    while (length) {
        ssize_t count = write(fd, p, length);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return NO;
        p += (size_t)count; length -= (size_t)count;
    }
    return YES;
}
static BOOL ReadAll(int fd, void *pointer, size_t length) {
    uint8_t *p = pointer;
    while (length) {
        ssize_t count = read(fd, p, length);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return NO;
        p += (size_t)count; length -= (size_t)count;
    }
    return YES;
}
static NSString *ReadLine(int fd) {
    NSMutableData *line = [NSMutableData data];
    for (NSUInteger i = 0; i != 255; ++i) {
        char c = 0;
        if (!ReadAll(fd, &c, 1)) return nil;
        if (c == '\n') return [[NSString alloc] initWithData:line encoding:NSASCIIStringEncoding];
        if (c == '\0') return nil;
        [line appendBytes:&c length:1];
    }
    return nil;
}
static BOOL ExactOK(NSString *line, uint64_t ident) {
    return [line isEqualToString:[NSString stringWithFormat:@"OK %" PRIu64, ident]];
}

@interface DVMForwardingLibrary : NSObject
@property(nonatomic, weak) DVMForwardingDevice *device;
@property(nonatomic, strong) NSData *data;
@end

@interface DVMForwardingFunction : NSObject
@property(nonatomic, strong) DVMForwardingLibrary *library;
@end

@interface DVMForwardingPipeline : NSObject
@property(nonatomic, strong) DVMForwardingFunction *function;
@end

@interface DVMForwardingTexture : NSObject
@property(nonatomic, weak) DVMForwardingDevice *device;
@property(nonatomic, strong) NSMutableData *sourceBytes;
@property(nonatomic) IOSurfaceRef surface;
@property(nonatomic) uint64_t generation;
@property(nonatomic) BOOL destination;
@property(nonatomic) BOOL complete;
@end
@implementation DVMForwardingTexture
- (void)dealloc { if (_surface) CFRelease(_surface); }
- (NSUInteger)width { return DVMWidth; }
- (NSUInteger)height { return DVMHeight; }
- (MTLPixelFormat)pixelFormat { return MTLPixelFormatBGRA8Unorm; }
- (void)replaceRegion:(MTLRegion)region mipmapLevel:(NSUInteger)level
             withBytes:(const void *)bytes bytesPerRow:(NSUInteger)row {
    if (_destination || level || region.origin.x || region.origin.y || region.origin.z || region.size.depth != 1 ||
        region.size.width != DVMWidth || region.size.height != DVMHeight || row < DVMRow || !bytes) {
        [NSException raise:NSInvalidArgumentException format:@"unsupported forwarding texture write"];
    }
    const uint8_t *in = bytes; uint8_t *out = _sourceBytes.mutableBytes;
    for (NSUInteger y = 0; y < DVMHeight; ++y) memcpy(out + y * DVMRow, in + y * row, DVMRow);
}
- (void)getBytes:(void *)bytes bytesPerRow:(NSUInteger)row fromRegion:(MTLRegion)region
     mipmapLevel:(NSUInteger)level {
    if (!_destination || !_complete || level || region.origin.x || region.origin.y || region.origin.z || region.size.depth != 1 ||
        region.size.width != DVMWidth || region.size.height != DVMHeight || row < DVMRow || !bytes) {
        [NSException raise:NSInvalidArgumentException format:@"destination data is not complete or region is unsupported"];
    }
    if (IOSurfaceLock(_surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess) {
        [NSException raise:NSInternalInconsistencyException format:@"IOSurface read lock failed"];
    }
    const uint8_t *in = IOSurfaceGetBaseAddress(_surface); uint8_t *out = bytes;
    if (!in) { IOSurfaceUnlock(_surface,kIOSurfaceLockReadOnly,NULL); [NSException raise:NSInternalInconsistencyException format:@"surface has no CPU address"]; }
    for (NSUInteger y = 0; y < DVMHeight; ++y) memcpy(out + y * row, in + y * DVMRow, DVMRow);
    if (IOSurfaceUnlock(_surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess)
        [NSException raise:NSInternalInconsistencyException format:@"surface read unlock failed"];
}
@end

@interface DVMForwardingCommandEncoder : NSObject
@property(nonatomic, weak) DVMForwardingCommandBuffer *commandBuffer;
@property(nonatomic, strong) DVMForwardingPipeline *pipeline;
@property(nonatomic, strong) DVMForwardingTexture *source;
@property(nonatomic, strong) DVMForwardingTexture *destination;
@property(nonatomic) BOOL dispatched;
@property(nonatomic) BOOL ended;
@end

@interface DVMForwardingCommandBuffer : NSObject
@property(nonatomic, weak) DVMForwardingDevice *device;
@property(nonatomic, strong) DVMForwardingCommandEncoder *encoder;
@property(nonatomic, strong) DVMForwardingPipeline *pipeline;
@property(nonatomic, strong) DVMForwardingTexture *source;
@property(nonatomic, strong) DVMForwardingTexture *destination;
@property(nonatomic, strong) NSError *failure;
@property(nonatomic) MTLCommandBufferStatus commandStatus;
@property(nonatomic) uint64_t ident;
- (void)fail:(NSError *)error;
@end

@interface DVMForwardingCommandQueue : NSObject
@property(nonatomic, weak) DVMForwardingDevice *device;
@end

@interface DVMForwardingDevice : NSObject
@property(nonatomic) int readFD;
@property(nonatomic) int writeFD;
@property(nonatomic) uint64_t nextID;
@property(nonatomic) uint64_t nextGeneration;
@property(nonatomic) BOOL sentFirstRun;
@property(nonatomic, strong) DVMForwardingCommandBuffer *active;
- (instancetype)initWithReadFD:(int)readFD writeFD:(int)writeFD;
- (uint64_t)reserveID;
- (BOOL)sendHeader:(NSString *)header error:(NSError **)error;
- (BOOL)expectOK:(uint64_t)ident error:(NSError **)error;
- (void)clearActive:(DVMForwardingCommandBuffer *)buffer;
@end

@implementation DVMForwardingDevice
- (instancetype)initWithReadFD:(int)readFD writeFD:(int)writeFD {
    if (!(self = [super init])) return nil;
    _readFD = readFD; _writeFD = writeFD; _nextID = 1; _nextGeneration = 1;
    return self;
}
- (uint64_t)reserveID { return _nextID++; }
- (BOOL)sendHeader:(NSString *)header error:(NSError **)error {
    NSData *data = [header dataUsingEncoding:NSASCIIStringEncoding];
    if (!WriteAll(_writeFD, data.bytes, data.length)) {
        if (error) *error = DVMError(DVMForwardingIO, @"proxy header write failed");
        return NO;
    }
    return YES;
}
- (BOOL)expectOK:(uint64_t)ident error:(NSError **)error {
    NSString *line = ReadLine(_readFD);
    if (ExactOK(line, ident)) return YES;
    if (error) *error = DVMError(DVMForwardingProtocol,
                                 [NSString stringWithFormat:@"unexpected proxy reply: %@", line ?: @"EOF"]);
    return NO;
}
- (id)newLibraryWithData:(dispatch_data_t)wireData error:(NSError **)error {
    const void *mapped = NULL; size_t length = 0;
    dispatch_data_t map = wireData ? dispatch_data_create_map(wireData, &mapped, &length) : nil;
    if (!map || !mapped || length > DVMMaxLibrary) {
        if (error) *error = DVMError(DVMForwardingValidation, @"library must be non-nil and at most 12 MiB");
        return nil;
    }
    NSData *data = [NSData dataWithBytes:mapped length:length];
    uint64_t ident = [self reserveID]; NSError *local = nil;
    if(getenv("DVM_PROXY_LIBRARY_REF")) {
        unsigned char digest[CC_SHA256_DIGEST_LENGTH];char hex[65];
        CC_SHA256(data.bytes,(CC_LONG)data.length,digest);
        for(unsigned i=0;i<sizeof(digest);i++)snprintf(hex+2*i,3,"%02x",digest[i]);
        if(![self sendHeader:[NSString stringWithFormat:@"LIBREF %" PRIu64 " %lu %s\n",ident,(unsigned long)data.length,hex] error:&local] || ![self expectOK:ident error:&local]) {
            if(error)*error=local;return nil;
        }
        DVMForwardingLibrary *library=[DVMForwardingLibrary new];library.device=self;library.data=[data copy];return library;
    }
    if (![self sendHeader:[NSString stringWithFormat:@"LIB %" PRIu64 " %lu\n", ident, (unsigned long)data.length]
                  error:&local] || !WriteAll(_writeFD, data.bytes, data.length) || ![self expectOK:ident error:&local]) {
        if (!local) local = DVMError(DVMForwardingIO, @"proxy library payload write failed");
        if (error) *error = local; return nil;
    }
    DVMForwardingLibrary *library = [DVMForwardingLibrary new]; library.device = self; library.data = [data copy];
    return library;
}
- (id)libraryWithData:(NSData *)data error:(NSError **)error {
    dispatch_data_t wireData = data ? dispatch_data_create(data.bytes, data.length, NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT) : nil;
    return [self newLibraryWithData:wireData error:error];
}
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)descriptor {
    if (descriptor.pixelFormat != MTLPixelFormatBGRA8Unorm || descriptor.width != DVMWidth ||
        descriptor.height != DVMHeight || descriptor.textureType != MTLTextureType2D || descriptor.depth != 1 ||
        descriptor.mipmapLevelCount != 1 || descriptor.sampleCount != 1 || descriptor.arrayLength != 1) return nil;
    DVMForwardingTexture *texture = [DVMForwardingTexture new]; texture.device = self;
    texture.sourceBytes = [NSMutableData dataWithLength:DVMBytes]; return texture;
}
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)descriptor iosurface:(IOSurfaceRef)surface plane:(NSUInteger)plane {
    if (!surface || plane || descriptor.pixelFormat != MTLPixelFormatBGRA8Unorm || descriptor.width != DVMWidth ||
        descriptor.height != DVMHeight || IOSurfaceGetWidth(surface) != DVMWidth || IOSurfaceGetHeight(surface) != DVMHeight ||
        IOSurfaceGetBytesPerRow(surface) != DVMRow || IOSurfaceGetPixelFormat(surface) != (uint32_t)'BGRA' ||
        IOSurfaceGetBytesPerElement(surface) != 4 || IOSurfaceGetAllocSize(surface) < DVMBytes || IOSurfaceGetPlaneCount(surface) != 0 ||
        descriptor.textureType != MTLTextureType2D || descriptor.depth != 1 || descriptor.mipmapLevelCount != 1 ||
        descriptor.sampleCount != 1 || descriptor.arrayLength != 1) return nil;
    DVMForwardingTexture *texture = [DVMForwardingTexture new]; texture.device = self; texture.destination = YES;
    texture.surface = (IOSurfaceRef)CFRetain(surface); texture.generation = _nextGeneration++; return texture;
}
- (id)newCommandQueue { DVMForwardingCommandQueue *queue = [DVMForwardingCommandQueue new]; queue.device = self; return queue; }
- (void)clearActive:(DVMForwardingCommandBuffer *)buffer { if (_active == buffer) _active = nil; }
- (BOOL)reportVerification:(NSData *)data error:(NSError **)error {
    if(_active || data.length>16384)return NO;
    uint64_t ident=[self reserveID];
    return [self sendHeader:[NSString stringWithFormat:@"REPORT %" PRIu64 " %lu\n",ident,(unsigned long)data.length] error:error] &&
        WriteAll(_writeFD,data.bytes,data.length) && [self expectOK:ident error:error];
}
@end

@implementation DVMForwardingLibrary
- (id)newFunctionWithName:(NSString *)name {
    if (![name isEqualToString:@"read_write_surf_compute"]) return nil;
    DVMForwardingFunction *function = [DVMForwardingFunction new]; function.library = self; return function;
}
@end

@implementation DVMForwardingFunction
- (NSString *)name { return @"read_write_surf_compute"; }
@end

@implementation DVMForwardingPipeline @end

@implementation DVMForwardingCommandQueue
- (id)commandBuffer {
    DVMForwardingCommandBuffer *buffer = [DVMForwardingCommandBuffer new];
    buffer.device = _device; buffer.commandStatus = MTLCommandBufferStatusNotEnqueued; return buffer;
}
@end

@implementation DVMForwardingCommandEncoder
- (void)setComputePipelineState:(id)pipeline {
    if (_ended || ![pipeline isKindOfClass:DVMForwardingPipeline.class] ||
        ((DVMForwardingPipeline *)pipeline).function.library.device != _commandBuffer.device) {
        [_commandBuffer fail:DVMError(DVMForwardingState, @"invalid pipeline encoding")]; return;
    }
    _pipeline = pipeline;
}
- (void)setTexture:(id)texture atIndex:(NSUInteger)index {
    if (_ended || ![texture isKindOfClass:DVMForwardingTexture.class] || index > 1 ||
        ((DVMForwardingTexture *)texture).device != _commandBuffer.device) {
        [_commandBuffer fail:DVMError(DVMForwardingState, @"invalid texture encoding")]; return;
    }
    if (index == 0) _source = texture; else _destination = texture;
}
- (void)dispatchThreadgroups:(MTLSize)groups threadsPerThreadgroup:(MTLSize)threads {
    if (_ended || groups.width != 9 || groups.height != 7 || groups.depth != 1 ||
        threads.width != 8 || threads.height != 8 || threads.depth != 1) {
        [_commandBuffer fail:DVMError(DVMForwardingUnsupported, @"only 9x7 groups of 8x8 threads are supported")]; return;
    }
    _dispatched = YES;
}
- (void)endEncoding { _ended = YES; }
@end

@implementation DVMForwardingCommandBuffer
- (void)fail:(NSError *)error { if (_commandStatus < MTLCommandBufferStatusCompleted) { _failure = error; _commandStatus = MTLCommandBufferStatusError; } }
- (id)computeCommandEncoder {
    if (_encoder || _commandStatus != MTLCommandBufferStatusNotEnqueued) { [self fail:DVMError(DVMForwardingState, @"encoder is stale or command already committed")]; return nil; }
    _encoder = [DVMForwardingCommandEncoder new]; _encoder.commandBuffer = self; return _encoder;
}
- (void)commit {
    if (_commandStatus != MTLCommandBufferStatusNotEnqueued || !_encoder.ended || !_encoder.dispatched ||
        !_encoder.pipeline || !_encoder.source || !_encoder.destination || _encoder.source.destination || !_encoder.destination.destination ||
        !_device || _device.active) { [self fail:DVMError(DVMForwardingState, @"misordered commit or another command is active")]; return; }
    _pipeline = _encoder.pipeline; _source = _encoder.source; _destination = _encoder.destination;
    _destination.complete = NO; _ident = [_device reserveID];
    const char *raw = getenv("DVM_PROXY_FIRST_DELAY_MS"); uint64_t delay = 0;
    if (!_device.sentFirstRun && raw && !strcmp(raw, "200")) delay = 200;
    _device.sentFirstRun = YES;
    NSData *snapshot = [NSData dataWithBytes:_source.sourceBytes.bytes length:DVMBytes]; NSError *error = nil;
    NSString *header = [NSString stringWithFormat:@"RUN %" PRIu64 " %" PRIu64 " 64 48 %" PRIu64 " 12288\n", _ident, _destination.generation, delay];
    if (![_device sendHeader:header error:&error] || !WriteAll(_device.writeFD, snapshot.bytes, snapshot.length)) {
        [self fail:error ?: DVMError(DVMForwardingIO, @"proxy RUN payload write failed")]; return;
    }
    _device.active = self; _commandStatus = MTLCommandBufferStatusCommitted;
}
- (void)waitUntilCompleted {
    if (_commandStatus != MTLCommandBufferStatusCommitted || _device.active != self) {
        [self fail:DVMError(DVMForwardingState, @"wait requires one committed active command")]; return;
    }
    NSString *line = ReadLine(_device.readFD);
    NSString *expected = [NSString stringWithFormat:@"DATA %" PRIu64 " 12288", _ident];
    if (![line isEqualToString:expected]) { [self fail:DVMError(DVMForwardingProtocol, @"proxy DATA id or length mismatch")]; [_device clearActive:self]; return; }
    NSMutableData *output = [NSMutableData dataWithLength:DVMBytes];
    if (!ReadAll(_device.readFD, output.mutableBytes, output.length) || IOSurfaceLock(_destination.surface, 0, NULL) != kIOReturnSuccess) {
        [self fail:DVMError(DVMForwardingIO, @"proxy output read or IOSurface lock failed")]; [_device clearActive:self]; return;
    }
    void *base=IOSurfaceGetBaseAddress(_destination.surface);
    if (!base) { IOSurfaceUnlock(_destination.surface,0,NULL); [self fail:DVMError(DVMForwardingIO,@"surface has no CPU address")]; [_device clearActive:self]; return; }
    memcpy(base, output.bytes, DVMBytes);
    if (IOSurfaceUnlock(_destination.surface, 0, NULL) != kIOReturnSuccess) { [self fail:DVMError(DVMForwardingIO, @"IOSurface unlock failed")]; [_device clearActive:self]; return; }
    _destination.complete = YES; _commandStatus = MTLCommandBufferStatusCompleted; [_device clearActive:self];
    _pipeline = nil; _source = nil; _destination = nil; _encoder = nil;
}
- (MTLCommandBufferStatus)status { return _commandStatus; }
- (NSError *)error { return _failure; }
@end

@implementation DVMForwardingDevice (Pipeline)
- (id)newComputePipelineStateWithFunction:(id)function error:(NSError **)error {
    if (![function isKindOfClass:DVMForwardingFunction.class] || ((DVMForwardingFunction *)function).library.device != self) { if (error) *error = DVMError(DVMForwardingValidation, @"foreign function"); return nil; }
    uint64_t ident = [self reserveID]; NSError *local = nil;
    if (![self sendHeader:[NSString stringWithFormat:@"PIPE %" PRIu64 " read_write_surf_compute\n", ident] error:&local] || ![self expectOK:ident error:&local]) {
        if (error) *error = local; return nil;
    }
    DVMForwardingPipeline *pipeline = [DVMForwardingPipeline new]; pipeline.function = function; return pipeline;
}
@end

id DVMCreateForwardingDevice(int readFD, int writeFD) {
    if (readFD < 0 || writeFD < 0) return nil;
    return [[DVMForwardingDevice alloc] initWithReadFD:readFD writeFD:writeFD];
}
