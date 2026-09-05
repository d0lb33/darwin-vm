// Exact-guest worker; --spawn also permits independent host-only verification.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#import <CommonCrypto/CommonDigest.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <sys/wait.h>
#include <unistd.h>

typedef id (*CreateDevice)(int readFD, int writeFD);
enum { Width = 64, Height = 48, Row = 256, Bytes = 12288 };
@protocol NarrowDevice
- (id)newLibraryWithData:(dispatch_data_t)data error:(NSError **)error;
- (id)newComputePipelineStateWithFunction:(id)function error:(NSError **)error;
- (id)newCommandQueue;
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)descriptor;
- (id)newTextureWithDescriptor:(MTLTextureDescriptor *)descriptor iosurface:(IOSurfaceRef)surface plane:(NSUInteger)plane;
@end
@protocol NarrowLibrary
- (id)newFunctionWithName:(NSString *)name;
@end
@protocol NarrowQueue
- (id)commandBuffer;
@end
static void Fail(NSString *message) { fprintf(stderr, "HARNESS_FAIL %s\n", message.UTF8String); exit(1); }
static void Fill(uint8_t *p, NSUInteger generation, NSUInteger pass) {
    for (NSUInteger y = 0; y < Height; ++y) for (NSUInteger x = 0; x < Width; ++x) {
        NSUInteger i = y * Row + x * 4;
        p[i] = (x * 17 + y * 31 + generation * 43 + pass * 59) & 255;
        p[i + 1] = (x * 7 + y * 13 + generation * 19 + pass * 29) & 255;
        p[i + 2] = (x ^ (y * 3) ^ (generation * 97) ^ (pass * 71)) & 255;
        p[i + 3] = (255 - x - y - generation * 11 - pass * 5) & 255;
    }
}
static uint32_t BE32(const uint8_t *p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }
static NSData *SelectExactAIR(NSString *path, char digest[CC_SHA256_DIGEST_LENGTH * 2 + 1]) {
    static const char expected[] = "8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364";
    NSData *fat = [NSData dataWithContentsOfFile:path]; const uint8_t *p = fat.bytes;
    if (fat.length < 8 || BE32(p) != 0xcafebabe) Fail(@"expected big-endian fat metallib");
    uint32_t count = BE32(p + 4); if (!count || count > 128 || fat.length < 8 + (NSUInteger)count * 20) Fail(@"bad fat architecture table");
    for (uint32_t i = 0; i < count; ++i) {
        const uint8_t *arch = p + 8 + (NSUInteger)i * 20; uint32_t offset = BE32(arch + 8), length = BE32(arch + 12);
        if ((uint64_t)offset + length > fat.length || length < 4 || memcmp(p + offset, "MTLB", 4)) continue;
        uint8_t raw[CC_SHA256_DIGEST_LENGTH]; CC_SHA256(p + offset, (CC_LONG)length, raw);
        for (NSUInteger j = 0; j < sizeof(raw); ++j) sprintf(digest + j * 2, "%02x", raw[j]);
        digest[64] = '\0';
        if (length == 2705796 && !strcmp(digest, expected)) return [fat subdataWithRange:NSMakeRange(offset, length)];
    }
    Fail(@"fat library has no exact known read_write_surf_compute MTLB slice"); return nil;
}
static void CheckLocalSurfaces(void) {
    uint8_t expected[Bytes];
    for(NSUInteger generation=0;generation<3;generation++) {
        IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(Width),(id)kIOSurfaceHeight:@(Height),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@(Row),(id)kIOSurfaceAllocSize:@(Bytes),(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        if(!surface) Fail(@"local IOSurfaceCreate returned NULL");
        if(IOSurfaceGetWidth(surface)!=Width || IOSurfaceGetHeight(surface)!=Height || IOSurfaceGetBytesPerRow(surface)!=Row || IOSurfaceGetAllocSize(surface)<Bytes) Fail(@"local IOSurface geometry mismatch");
        for(NSUInteger pass=0;pass<3;pass++) {
            Fill(expected,generation,pass);
            if(IOSurfaceLock(surface,0,NULL)) Fail(@"local IOSurface write lock failed");
            void *base=IOSurfaceGetBaseAddress(surface);
            if(!base) Fail(@"local IOSurface has no CPU address");
            memcpy(base,expected,Bytes);
            if(IOSurfaceUnlock(surface,0,NULL)) Fail(@"local IOSurface write unlock failed");
            if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)) Fail(@"local IOSurface read lock failed");
            base=IOSurfaceGetBaseAddress(surface);
            BOOL equal=base && memcmp(base,expected,Bytes)==0;
            if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL) || !equal) Fail(@"local IOSurface readback mismatch");
        }
        fprintf(stderr,"HARNESS_SURFACE generation=%lu bytes=%d passes=3 verified=1 id=%u\n",(unsigned long)generation,Bytes,IOSurfaceGetID(surface));
        CFRelease(surface);
    }
}
int main(int argc, const char *argv[]) { @autoreleasepool {
    fprintf(stderr,"HARNESS_START version=1\n");
    BOOL hostOnly = NO; int readFD = -1, writeFD = -1; pid_t child = -1; NSString *bundlePath = nil, *libraryPath = nil;
    if (argc == 4 && !strcmp(argv[1], "--stdio")) {
        bundlePath = @(argv[2]); libraryPath = @(argv[3]); readFD = STDIN_FILENO; writeFD = STDOUT_FILENO;
    } else if (argc == 5 && !strcmp(argv[1], "--spawn")) {
        hostOnly = YES; bundlePath = @(argv[2]); libraryPath = @(argv[4]);
    } else return 2;
    void *bundle = dlopen(bundlePath.UTF8String, RTLD_NOW | RTLD_LOCAL); if (!bundle) Fail(@(dlerror()));
    CreateDevice create = (CreateDevice)dlsym(bundle, "DVMCreateForwardingDevice"); if (!create) Fail(@"factory missing");
    if(getenv("DVM_PROXY_SURFACE_ONLY")) {
        CheckLocalSurfaces();
        fprintf(stderr,"HARNESS_SURFACE_COMPLETE result=pass scope=guest-local-cpu-access-only\n");
        return 0;
    }
    if (hostOnly) {
        int toChild[2], fromChild[2]; if (pipe(toChild) || pipe(fromChild)) Fail(@"pipe");
        child = fork(); if (child < 0) Fail(@"fork");
        if (child == 0) {
            dup2(toChild[0], STDIN_FILENO); dup2(fromChild[1], STDOUT_FILENO);
            close(toChild[0]); close(toChild[1]); close(fromChild[0]); close(fromChild[1]);
            execl(argv[3], argv[3], (char *)NULL); _exit(127);
        }
        close(toChild[0]); close(fromChild[1]); readFD = fromChild[0]; writeFD = toChild[1]; setenv("DVM_PROXY_FIRST_DELAY_MS", "200", 1);
    }
    id<NarrowDevice> device = create(readFD, writeFD); if (!device) Fail(@"factory returned nil");
    char airDigest[65]; NSData *air = SelectExactAIR(libraryPath, airDigest); NSError *error = nil;
    fprintf(stderr,"HARNESS_AIR sha256=%s bytes=%lu\n",airDigest,(unsigned long)air.length);
    dispatch_data_t airData = dispatch_data_create(air.bytes, air.length, NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT);
    id library = [device newLibraryWithData:airData error:&error]; if (!library) Fail(error.description);
    id function = [(id<NarrowLibrary>)library newFunctionWithName:@"read_write_surf_compute"]; if (!function) Fail(@"function");
    id pipeline = [device newComputePipelineStateWithFunction:function error:&error]; if (!pipeline) Fail(error.description);
    fprintf(stderr,"HARNESS_PIPELINE created=1\n");
    id<NarrowQueue> queue = [device newCommandQueue]; if (!queue) Fail(@"queue");
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:Width height:Height mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    NSMutableArray *runs = [NSMutableArray array];
    for (NSUInteger generation = 0; generation < 3; ++generation) {
        IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(Width), (id)kIOSurfaceHeight:@(Height), (id)kIOSurfaceBytesPerElement:@4, (id)kIOSurfaceBytesPerRow:@(Row), (id)kIOSurfaceAllocSize:@(Bytes), (id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
        if (!surface) Fail(@"surface");
        id destination = [device newTextureWithDescriptor:descriptor iosurface:surface plane:0]; if (!destination) Fail(@"destination");
        for (NSUInteger pass = 0; pass < 3; ++pass) {
            id source = [device newTextureWithDescriptor:descriptor]; if (!source) Fail(@"source");
            NSMutableData *input = [NSMutableData dataWithLength:Bytes]; Fill(input.mutableBytes, generation, pass);
            [(id<MTLTexture>)source replaceRegion:MTLRegionMake2D(0, 0, Width, Height) mipmapLevel:0 withBytes:input.bytes bytesPerRow:Row];
            id command = [queue commandBuffer]; id encoder = [command computeCommandEncoder];
            id<MTLComputeCommandEncoder> metalEncoder = (id<MTLComputeCommandEncoder>)encoder;
            [metalEncoder setComputePipelineState:(id<MTLComputePipelineState>)pipeline]; [metalEncoder setTexture:(id<MTLTexture>)source atIndex:0]; [metalEncoder setTexture:(id<MTLTexture>)destination atIndex:1];
            [metalEncoder dispatchThreadgroups:MTLSizeMake(9, 7, 1) threadsPerThreadgroup:MTLSizeMake(8, 8, 1)]; [metalEncoder endEncoding];
            CFTimeInterval started = NSDate.timeIntervalSinceReferenceDate; [command commit];
            double commitMS=(NSDate.timeIntervalSinceReferenceDate-started)*1000.0;
            MTLCommandBufferStatus preWaitStatus = [(id<MTLCommandBuffer>)command status];
            if (preWaitStatus != MTLCommandBufferStatusCommitted) Fail(@"pre-wait status is not committed");
            NSMutableData *early = [NSMutableData dataWithLength:Bytes]; BOOL rejected=NO;
            @try { [(id<MTLTexture>)destination getBytes:early.mutableBytes bytesPerRow:Row fromRegion:MTLRegionMake2D(0,0,Width,Height) mipmapLevel:0]; }
            @catch (NSException *exception) { rejected=[exception.name isEqualToString:NSInvalidArgumentException]; }
            if (!rejected) Fail(@"pre-completion read was accepted");
            NSMutableData *replacement=[NSMutableData dataWithLength:Bytes]; memset(replacement.mutableBytes,0x31,Bytes);
            [(id<MTLTexture>)source replaceRegion:MTLRegionMake2D(0,0,Width,Height) mipmapLevel:0 withBytes:replacement.bytes bytesPerRow:Row];
            [command waitUntilCompleted]; CFTimeInterval elapsed = (NSDate.timeIntervalSinceReferenceDate - started) * 1000.0;
            if ([(id<MTLCommandBuffer>)command status] != MTLCommandBufferStatusCompleted) Fail([(id<MTLCommandBuffer>)command error].description);
            NSMutableData *actual = [NSMutableData dataWithLength:Bytes];
            [(id<MTLTexture>)destination getBytes:actual.mutableBytes bytesPerRow:Row fromRegion:MTLRegionMake2D(0, 0, Width, Height) mipmapLevel:0];
            if (![actual isEqualToData:input]) Fail(@"GPU result differs");
            if (IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)) Fail(@"direct surface lock");
            const void *base=IOSurfaceGetBaseAddress(surface);
            BOOL direct=base && memcmp(base,input.bytes,Bytes)==0;
            if (IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL) || !direct) Fail(@"direct IOSurface bytes differ");
            if (generation == 0 && pass == 0 && elapsed < 180.0) Fail(@"200ms response delay missing");
            if (hostOnly && generation==0 && pass==0 && commitMS>100) Fail(@"host-only commit did not precede delayed response");
            fprintf(stderr,"HARNESS_RUN generation=%lu pass=%lu verified=1 commit_ms=%.3f total_ms=%.3f\n",(unsigned long)generation,(unsigned long)pass,commitMS,elapsed);
            [runs addObject:@{@"generation":@(generation), @"pass":@(pass), @"elapsed_ms":@(elapsed), @"commit_ms":@(commitMS), @"early_read_rejected":@YES, @"direct_surface_equal":@YES, @"pre_wait_status":@(preWaitStatus), @"status":@([(id<MTLCommandBuffer>)command status])}];
        }
        CFRelease(surface);
    }
    if (hostOnly) { close(writeFD); close(readFD); int status = 0; waitpid(child, &status, 0); if (!WIFEXITED(status) || WEXITSTATUS(status)) Fail(@"server exit"); }
    NSDictionary *result = @{@"host_only":@(hostOnly), @"air_sha256":@(airDigest), @"air_bytes":@(air.length), @"runs":runs, @"passed":@YES}; NSError *jsonError = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:&jsonError]; if (!json) Fail(jsonError.description);
    fprintf(stderr, "HARNESS_RESULT %.*s\n", (int)json.length, (const char *)json.bytes); return 0;
}}
