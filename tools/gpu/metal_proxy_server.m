// Framed stdin/stdout host Metal worker for the guest GPU proxy.
// Stdout is protocol only; all diagnostics, including Metal errors, go to stderr.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <CommonCrypto/CommonDigest.h>
#include <dispatch/dispatch.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

enum { kWidth = 64, kHeight = 48, kBytesPerPixel = 4,
       kBytesPerRow = kWidth * kBytesPerPixel, kImageBytes = kBytesPerRow * kHeight,
       kMaxLibraryBytes = 12 * 1024 * 1024, kMaxLineBytes = 256 };

static void diag(NSString *event, uint64_t ident, uint64_t generation,
                 NSString *detail) {
    fprintf(stderr, "DIAG event=%s id=%" PRIu64 " generation=%" PRIu64 " detail=%s\n",
            event.UTF8String, ident, generation, detail.UTF8String);
    fflush(stderr);
}

static void protocol_error(uint64_t ident, const char *domain, int code, NSString *detail) {
    fprintf(stdout, "ERR %" PRIu64 " %s %d\n", ident, domain, code);
    fflush(stdout);
    diag(@"error", ident, 0, detail);
}

// NSError domains used by Metal are already single protocol tokens. Keep the
// precise domain/code on stdout; only the descriptive text belongs on stderr.
static void nserror_protocol_error(uint64_t ident, NSError *error, NSString *fallback) {
    NSString *domain = error.domain ?: @"NSError";
    fprintf(stdout, "ERR %" PRIu64 " %s %ld\n", ident, domain.UTF8String, (long)error.code);
    fflush(stdout);
    diag(@"metal-error", ident, 0, error.description ?: fallback);
}

static bool write_all(const void *bytes, size_t length) {
    const uint8_t *cursor = bytes;
    while (length != 0) {
        const size_t n = fwrite(cursor, 1, length, stdout);
        if (n == 0) {
            return false;
        }
        cursor += n;
        length -= n;
    }
    return fflush(stdout) == 0;
}

// Returns true only when exactly length bytes were consumed.
static bool read_exact(void *bytes, size_t length) {
    uint8_t *cursor = bytes;
    while (length != 0) {
        const size_t n = fread(cursor, 1, length, stdin);
        if (n == 0) {
            return false;
        }
        cursor += n;
        length -= n;
    }
    return true;
}

// 1 = line, 0 = EOF before a byte, -1 = oversized/unterminated line.
static int read_line(char line[kMaxLineBytes]) {
    size_t used = 0;
    for (;;) {
        const int c = fgetc(stdin);
        if (c == EOF) {
            return used == 0 ? 0 : -1;
        }
        if (c == '\n') {
            line[used] = '\0';
            return 1;
        }
        if (used + 1 >= kMaxLineBytes || c == '\0') {
            int discard;
            while ((discard = fgetc(stdin)) != EOF && discard != '\n') {
            }
            return -1;
        }
        line[used++] = (char)c;
    }
}

static bool parse_u64(const char *text, uint64_t *value) {
    if (!text || !*text) {
        return false;
    }
    uint64_t parsed = 0;
    for (const unsigned char *cursor = (const unsigned char *)text; *cursor; cursor++) {
        if (*cursor < '0' || *cursor > '9') {
            return false;
        }
        const uint64_t digit = (uint64_t)(*cursor - '0');
        if (parsed > (UINT64_MAX - digit) / 10) {
            return false;
        }
        parsed = parsed * 10 + digit;
    }
    *value = parsed;
    return true;
}

static NSUInteger split_words(char *line, char *words[8]) {
    NSUInteger count = 0;
    char *cursor = line;
    while (cursor && *cursor) {
        if (count == 8 || *cursor == ' ') {
            return 0;
        }
        words[count++] = cursor;
        char *space = strchr(cursor, ' ');
        if (!space) {
            break;
        }
        *space = '\0';
        cursor = space + 1;
        if (!*cursor) {
            return 0;
        }
    }
    return count;
}

@interface Server : NSObject
@property(nonatomic, strong) id<MTLDevice> device;
@property(nonatomic, strong) id<MTLCommandQueue> queue;
@property(nonatomic, strong) id<MTLLibrary> library;
@property(nonatomic, strong) id<MTLComputePipelineState> pipeline;
@property(nonatomic, strong) id<MTLTexture> source;
@property(nonatomic, strong) id<MTLTexture> destination;
@property(nonatomic) IOSurfaceRef surface;
@property(nonatomic) uint64_t textureGeneration;
@property(nonatomic) bool hasTextureGeneration;
@property(nonatomic) NSUInteger completedRuns;
@end

@implementation Server

- (void)dealloc {
    [self discardTextures];
}

- (void)discardTextures {
    _destination = nil;
    _source = nil;
    if (_surface) {
        CFRelease(_surface);
        _surface = NULL;
    }
    _hasTextureGeneration = false;
}

- (bool)ensureTexturesForGeneration:(uint64_t)generation recreated:(bool *)recreated
                               error:(NSString **)detail {
    if (_hasTextureGeneration && _textureGeneration == generation) {
        *recreated = false;
        return true;
    }
    [self discardTextures];
    NSDictionary *properties = @{
        (id)kIOSurfaceWidth: @(kWidth),
        (id)kIOSurfaceHeight: @(kHeight),
        (id)kIOSurfaceBytesPerElement: @(kBytesPerPixel),
        (id)kIOSurfaceBytesPerRow: @(kBytesPerRow),
        (id)kIOSurfaceAllocSize: @(kImageBytes),
        (id)kIOSurfacePixelFormat: @((uint32_t)'BGRA'),
    };
    _surface = IOSurfaceCreate((__bridge CFDictionaryRef)properties);
    if (!_surface) {
        *detail = @"IOSurfaceCreate failed";
        return false;
    }
    MTLTextureDescriptor *descriptor =
        [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                            width:kWidth height:kHeight mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    _source = [_device newTextureWithDescriptor:descriptor];
    _destination = [_device newTextureWithDescriptor:descriptor iosurface:_surface plane:0];
    if (!_source || !_destination) {
        *detail = @"Metal texture creation failed";
        [self discardTextures];
        return false;
    }
    _textureGeneration = generation;
    _hasTextureGeneration = true;
    *recreated = true;
    return true;
}

@end

static bool check_id(uint64_t ident, uint64_t *lastID) {
    if (ident <= *lastID) {
        protocol_error(ident, "PROTO", EALREADY, @"command id is not strictly monotonic");
        return false;
    }
    *lastID = ident;
    return true;
}

static bool handle_library_data(Server *server,uint64_t ident,NSData *contents) {
    uint64_t bytes=contents.length;
    NSError *error = nil;
    dispatch_data_t data = dispatch_data_create(contents.bytes, contents.length, NULL,
                                                DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    id<MTLLibrary> library = [server.device newLibraryWithData:data error:&error];
    if (!library) {
        nserror_protocol_error(ident, error, @"newLibraryWithData failed");
        return false;
    }
    server.library = library;
    server.pipeline = nil;
    [server discardTextures];
    fprintf(stdout, "OK %" PRIu64 "\n", ident);
    fflush(stdout);
    diag(@"library", ident, 0, [NSString stringWithFormat:@"bytes=%" PRIu64, bytes]);
    return true;
}

static bool handle_library(Server *server, uint64_t ident, uint64_t bytes) {
    if (bytes > kMaxLibraryBytes) {
        protocol_error(ident, "PROTO", EFBIG, @"library byte count exceeds 12 MiB");
        return false;
    }
    NSMutableData *contents = [NSMutableData dataWithLength:(NSUInteger)bytes];
    if (!read_exact(contents.mutableBytes, contents.length)) {
        protocol_error(ident, "IO", EPIPE, @"short library payload read");
        return false;
    }
    return handle_library_data(server,ident,contents);
}

static bool handle_library_reference(Server *server,uint64_t ident,uint64_t bytes,const char *digest) {
    const char *path=getenv("DVM_PROXY_LIBRARY_CACHE");
    NSData *contents=path?[NSData dataWithContentsOfFile:@(path)]:nil;
    if(!contents || contents.length!=bytes || bytes>kMaxLibraryBytes || strlen(digest)!=64) {
        protocol_error(ident,"LIBCACHE",ENOENT,@"no cached library with requested length");return false;
    }
    unsigned char raw[CC_SHA256_DIGEST_LENGTH];char hex[65];
    CC_SHA256(contents.bytes,(CC_LONG)contents.length,raw);
    for(unsigned i=0;i<sizeof(raw);i++)snprintf(hex+2*i,3,"%02x",raw[i]);
    if(strcmp(hex,digest)) {protocol_error(ident,"LIBCACHE",EILSEQ,@"cached library SHA256 differs from guest");return false;}
    diag(@"library-reference",ident,0,[NSString stringWithFormat:@"sha256=%s bytes=%" PRIu64,hex,bytes]);
    return handle_library_data(server,ident,contents);
}

static bool handle_report(Server *server,uint64_t ident,uint64_t bytes) {
    if(bytes>16384 || server.completedRuns!=9) {protocol_error(ident,"REPORT",EINVAL,@"report requires nine completed GPU operations");return false;}
    NSMutableData *data=[NSMutableData dataWithLength:(NSUInteger)bytes];
    if(!read_exact(data.mutableBytes,data.length))return false;
    id object=[NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
    if(![object isKindOfClass:NSDictionary.class] || ![object[@"passed"] isEqual:@YES] ||
       ![object[@"runs"] isKindOfClass:NSArray.class] || [object[@"runs"] count]!=9) {
        protocol_error(ident,"REPORT",EINVAL,@"malformed guest verification");return false;
    }
    diag(@"guest-verification",ident,0,[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]);
    fprintf(stdout,"OK %" PRIu64 "\n",ident);fflush(stdout);return true;
}

static bool handle_pipeline(Server *server, uint64_t ident) {
    if (!server.library) {
        protocol_error(ident, "STATE", ENOENT, @"PIPE before a successful LIB");
        return false;
    }
    id<MTLFunction> function = [server.library newFunctionWithName:@"read_write_surf_compute"];
    if (!function) {
        protocol_error(ident, "METAL", ENOENT, @"read_write_surf_compute is absent");
        return false;
    }
    NSError *error = nil;
    id<MTLComputePipelineState> pipeline =
        [server.device newComputePipelineStateWithFunction:function error:&error];
    if (!pipeline) {
        nserror_protocol_error(ident, error, @"pipeline creation failed");
        return false;
    }
    server.pipeline = pipeline;
    fprintf(stdout, "OK %" PRIu64 "\n", ident);
    fflush(stdout);
    diag(@"pipeline", ident, 0, @"function=read_write_surf_compute");
    return true;
}

static bool handle_run(Server *server, uint64_t ident, uint64_t generation,
                       uint64_t width, uint64_t height, uint64_t delay,
                       uint64_t byteCount) {
    if (width != kWidth || height != kHeight || byteCount != kImageBytes || delay > 500) {
        protocol_error(ident, "PROTO", EINVAL, @"RUN dimensions, byte count, or delay are invalid");
        return false;
    }
    NSMutableData *input = [NSMutableData dataWithLength:kImageBytes];
    if (!read_exact(input.mutableBytes, input.length)) {
        protocol_error(ident, "IO", EPIPE, @"short RUN payload read");
        return false;
    }
    if (!server.pipeline) {
        protocol_error(ident, "STATE", ENOENT, @"RUN before a successful PIPE");
        return false;
    }
    NSString *detail = nil;
    bool recreated = false;
    if (![server ensureTexturesForGeneration:generation recreated:&recreated error:&detail]) {
        protocol_error(ident, "METAL", ENOMEM, detail);
        return false;
    }
    const CFTimeInterval began = NSDate.timeIntervalSinceReferenceDate;
    [server.source replaceRegion:MTLRegionMake2D(0, 0, kWidth, kHeight)
                     mipmapLevel:0 withBytes:input.bytes bytesPerRow:kBytesPerRow];
    if (IOSurfaceLock(server.surface, 0, NULL) != kIOReturnSuccess) {
        protocol_error(ident, "IOSURFACE", EIO, @"destination lock for fill failed");
        return false;
    }
    memset(IOSurfaceGetBaseAddress(server.surface), 0xa5, kImageBytes);
    const bool preDispatchDifferent =
        memcmp(input.bytes, IOSurfaceGetBaseAddress(server.surface), kImageBytes) != 0;
    if (IOSurfaceUnlock(server.surface, 0, NULL) != kIOReturnSuccess) {
        protocol_error(ident, "IOSURFACE", EIO, @"destination unlock after fill failed");
        return false;
    }
    if (!preDispatchDifferent) {
        protocol_error(ident, "ORACLE", EILSEQ,
                       @"0xa5 negative-control destination equals supplied input before dispatch");
        return false;
    }
    id<MTLCommandBuffer> commandBuffer = [server.queue commandBuffer];
    id<MTLComputeCommandEncoder> encoder = [commandBuffer computeCommandEncoder];
    [encoder setComputePipelineState:server.pipeline];
    [encoder setTexture:server.source atIndex:0];
    [encoder setTexture:server.destination atIndex:1];
    [encoder dispatchThreadgroups:MTLSizeMake(9, 7, 1)
            threadsPerThreadgroup:MTLSizeMake(8, 8, 1)];
    [encoder endEncoding];
    [commandBuffer commit];
    [commandBuffer waitUntilCompleted];
    if (commandBuffer.status != MTLCommandBufferStatusCompleted) {
        diag(@"metal-error", ident, generation,
             commandBuffer.error.description ?: @"command buffer did not complete");
        nserror_protocol_error(ident, commandBuffer.error,
                               @"command buffer did not complete");
        return false;
    }
    NSMutableData *textureBytes = [NSMutableData dataWithLength:kImageBytes];
    [server.destination getBytes:textureBytes.mutableBytes bytesPerRow:kBytesPerRow
                      fromRegion:MTLRegionMake2D(0, 0, kWidth, kHeight) mipmapLevel:0];
    NSMutableData *surfaceBytes = [NSMutableData dataWithLength:kImageBytes];
    if (IOSurfaceLock(server.surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess) {
        protocol_error(ident, "IOSURFACE", EIO, @"destination read lock failed");
        return false;
    }
    memcpy(surfaceBytes.mutableBytes, IOSurfaceGetBaseAddress(server.surface), kImageBytes);
    if (IOSurfaceUnlock(server.surface, kIOSurfaceLockReadOnly, NULL) != kIOReturnSuccess) {
        protocol_error(ident, "IOSURFACE", EIO, @"destination read unlock failed");
        return false;
    }
    if (memcmp(input.bytes, textureBytes.bytes, kImageBytes) != 0 ||
        memcmp(input.bytes, surfaceBytes.bytes, kImageBytes) != 0 ||
        memcmp(textureBytes.bytes, surfaceBytes.bytes, kImageBytes) != 0) {
        protocol_error(ident, "ORACLE", EILSEQ, @"texture and IOSurface bytes differ from GPU result");
        return false;
    }
    const CFTimeInterval completed = NSDate.timeIntervalSinceReferenceDate;
    diag(@"run", ident, generation,
         [NSString stringWithFormat:@"status=%ld wall_ms=%.3f gpu_ms=%.3f delay_ms=%" PRIu64
                                    @" textures=%s pre_dispatch_differs=%d",
                                    (long)commandBuffer.status, (completed - began) * 1000.0,
                                    (commandBuffer.GPUEndTime - commandBuffer.GPUStartTime) * 1000.0,
                                    delay, recreated ? "recreated" : "reused", preDispatchDifferent]);
    if (delay != 0) {
        usleep((useconds_t)(delay * 1000));
    }
    fprintf(stdout, "DATA %" PRIu64 " %d\n", ident, kImageBytes);
    if (!write_all(textureBytes.bytes, kImageBytes)) {
        diag(@"io-error", ident, generation, @"short stdout write of GPU output");
        return false;
    }
    server.completedRuns++;
    return true;
}

int main(void) {
    @autoreleasepool {
        setvbuf(stdout, NULL, _IONBF, 0);
        Server *server = [Server new];
        server.device = MTLCreateSystemDefaultDevice();
        if (!server.device) {
            fprintf(stderr, "DIAG event=startup detail=no_metal_device\n");
            return 1;
        }
        server.queue = [server.device newCommandQueue];
        if (!server.queue) {
            fprintf(stderr, "DIAG event=startup detail=no_command_queue\n");
            return 1;
        }
        uint64_t lastID = 0;
        for (;;) {
            @autoreleasepool {
            char line[kMaxLineBytes];
            const int lineStatus = read_line(line);
            if (lineStatus == 0) {
                return 0;
            }
            if (lineStatus < 0) {
                protocol_error(0, "PROTO", EMSGSIZE, @"invalid or oversized command line");
                return 1;
            }
            char *words[8] = {0};
            const NSUInteger count = split_words(line, words);
            if (count == 1 && strcmp(words[0], "END") == 0) {
                return 0;
            }
            uint64_t ident = 0;
            if (count < 2 || !parse_u64(words[1], &ident)) {
                protocol_error(0, "PROTO", EINVAL, @"command has no valid id");
                return 1;
            }
            if (!check_id(ident, &lastID)) {
                return 1;
            }
            bool okay = false;
            if (count == 3 && strcmp(words[0], "LIB") == 0) {
                uint64_t bytes = 0;
                if (!parse_u64(words[2], &bytes)) {
                    protocol_error(ident, "PROTO", EINVAL, @"LIB byte count is invalid");
                } else {
                    okay = handle_library(server, ident, bytes);
                }
            } else if(count==4 && strcmp(words[0],"LIBREF")==0) {
                uint64_t bytes=0;
                if(parse_u64(words[2],&bytes))okay=handle_library_reference(server,ident,bytes,words[3]);
                else protocol_error(ident,"PROTO",EINVAL,@"invalid library reference length");
            } else if(count==3 && strcmp(words[0],"REPORT")==0) {
                uint64_t bytes=0;
                if(parse_u64(words[2],&bytes))okay=handle_report(server,ident,bytes);
                else protocol_error(ident,"PROTO",EINVAL,@"invalid report length");
            } else if (count == 3 && strcmp(words[0], "PIPE") == 0 &&
                       strcmp(words[2], "read_write_surf_compute") == 0) {
                okay = handle_pipeline(server, ident);
            } else if (count == 7 && strcmp(words[0], "RUN") == 0) {
                uint64_t generation = 0, width = 0, height = 0, delay = 0, bytes = 0;
                if (!parse_u64(words[2], &generation) || !parse_u64(words[3], &width) ||
                    !parse_u64(words[4], &height) || !parse_u64(words[5], &delay) ||
                    !parse_u64(words[6], &bytes)) {
                    protocol_error(ident, "PROTO", EINVAL, @"RUN numeric field is invalid");
                } else {
                    okay = handle_run(server, ident, generation, width, height, delay, bytes);
                }
            } else {
                protocol_error(ident, "PROTO", EINVAL, @"command grammar is invalid");
            }
            if (!okay) {
                return 1;
            }
            }
        }
    }
}
