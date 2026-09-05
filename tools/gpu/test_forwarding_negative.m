// Host-only negative tests for the narrow forwarding objects.
// It provides two tiny acknowledged protocol peers; rejected operations must not
// reach either peer after their valid LIB/PIPE setup is drained.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

typedef id (*CreateDevice)(int, int);
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

static pid_t ownedPeers[2];
static void Cleanup(void) {
    for(unsigned i=0;i<2;i++) if(ownedPeers[i]>0) {
        kill(ownedPeers[i],SIGTERM);
        while(waitpid(ownedPeers[i],NULL,0)<0 && errno==EINTR) {}
        ownedPeers[i]=0;
    }
}
static void Die(const char *why) { fprintf(stderr, "NEGATIVE_FAIL %s\n", why); exit(1); }
static void Must(bool condition, const char *why) { if (!condition) Die(why); }
static bool ReadAll(int fd, void *bytes, size_t length) {
    uint8_t *p = bytes;
    while (length) { ssize_t n = read(fd, p, length); if (n <= 0) return false; p += n; length -= (size_t)n; }
    return true;
}
static bool ReadLine(int fd, char line[256]) {
    size_t used = 0;
    while (used + 1 < 256) { char c; if (!ReadAll(fd, &c, 1)) return false; if (c == '\n') { line[used] = 0; return true; } line[used++] = c; }
    return false;
}
static void Peer(int fd, int eventFD) {
    for (;;) {
        char line[256]; if (!ReadLine(fd, line)) _exit(0);
        unsigned long long ident = 0, bytes = 0;
        if (sscanf(line, "LIB %llu %llu", &ident, &bytes) == 2) {
            char event = 'L'; (void)write(eventFD, &event, 1);
            uint8_t discard[512]; while (bytes) { size_t take = bytes > sizeof(discard) ? sizeof(discard) : (size_t)bytes; if (!ReadAll(fd, discard, take)) _exit(2); bytes -= take; }
        } else if (sscanf(line, "PIPE %llu read_write_surf_compute", &ident) == 1) {
            char event = 'P'; (void)write(eventFD, &event, 1);
        } else {
            char event = 'X'; (void)write(eventFD, &event, 1); _exit(3);
        }
        char reply[64]; int count = snprintf(reply, sizeof(reply), "OK %llu\n", ident);
        if (write(fd, reply, (size_t)count) != count) _exit(4);
    }
}
typedef struct { id<NarrowDevice> device; int eventFD; int clientFD; pid_t peer; } PeerDevice;
static PeerDevice MakeDevice(CreateDevice create) {
    int sockets[2], events[2]; Must(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0, "socketpair"); Must(pipe(events) == 0, "event pipe");
    pid_t pid = fork(); Must(pid >= 0, "fork");
    if (!pid) { close(sockets[0]); close(events[0]); Peer(sockets[1], events[1]); }
    ownedPeers[ownedPeers[0] ? 1 : 0]=pid;
    close(sockets[1]); close(events[1]);
    int flags = fcntl(events[0], F_GETFL); Must(flags >= 0 && fcntl(events[0], F_SETFL, flags | O_NONBLOCK) == 0, "event nonblock");
    id<NarrowDevice> device = create(sockets[0], sockets[0]); Must(device != nil, "factory");
    return (PeerDevice){ device, events[0], sockets[0], pid };
}
static void DrainExpected(PeerDevice *peer, unsigned expected, const char *label) {
    unsigned count = 0; char event;
    for (;;) { ssize_t n = read(peer->eventFD, &event, 1); if (n == 1) { count++; continue; } if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break; Die("event read"); }
    if (count != expected) { fprintf(stderr, "NEGATIVE_FAIL %s events=%u expected=%u\n", label, count, expected); exit(1); }
}
static void ExpectNoWrite(PeerDevice *peer, const char *label) {
    struct pollfd p = { .fd = peer->eventFD, .events = POLLIN };
    int rc = poll(&p, 1, 30); if (rc != 0) { fprintf(stderr, "NEGATIVE_FAIL write-after-reject %s\n", label); exit(1); }
    fprintf(stderr, "NEGATIVE_REJECT label=%s writes=0\n", label);
}
static void DestroyDevice(PeerDevice *peer) {
    peer->device = nil; close(peer->clientFD); close(peer->eventFD); Must(kill(peer->peer, SIGTERM) == 0, "stop peer"); int status; Must(waitpid(peer->peer, &status, 0) == peer->peer, "wait peer");
    for(unsigned i=0;i<2;i++) if(ownedPeers[i]==peer->peer) ownedPeers[i]=0;
    Must((WIFEXITED(status) && WEXITSTATUS(status)==0) ||
         (WIFSIGNALED(status) && WTERMSIG(status) == SIGTERM), "peer stop");
}
static MTLTextureDescriptor *Descriptor(void) {
    return [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:Width height:Height mipmapped:NO];
}
static IOSurfaceRef Surface(uint32_t format, NSUInteger bpe, NSUInteger row, NSUInteger alloc) {
    return IOSurfaceCreate((__bridge CFDictionaryRef)@{ (id)kIOSurfaceWidth:@(Width), (id)kIOSurfaceHeight:@(Height), (id)kIOSurfaceBytesPerElement:@(bpe), (id)kIOSurfaceBytesPerRow:@(row), (id)kIOSurfaceAllocSize:@(alloc), (id)kIOSurfacePixelFormat:@(format) });
}
static void MustReject(id result, const char *label) { if (result) { fprintf(stderr, "NEGATIVE_FAIL accepted %s\n", label); exit(1); } fprintf(stderr, "NEGATIVE_DESCRIPTOR label=%s rejected=1\n", label); }
static void ExpectCommandError(id command, const char *label) {
    if ([(id<MTLCommandBuffer>)command status] != MTLCommandBufferStatusError) { fprintf(stderr, "NEGATIVE_FAIL command status %s\n", label); exit(1); }
}

int main(int argc, const char *argv[]) { @autoreleasepool {
    atexit(Cleanup);
    if (argc != 2) return 2;
    void *bundle = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL); Must(bundle != NULL, "dlopen");
    CreateDevice create = (CreateDevice)dlsym(bundle, "DVMCreateForwardingDevice"); Must(create != NULL, "factory symbol");
    PeerDevice first = MakeDevice(create), second = MakeDevice(create);
    NSData *bytes = [NSData dataWithBytes:"x" length:1]; NSError *error = nil;
    id library1 = [first.device newLibraryWithData:dispatch_data_create(bytes.bytes, bytes.length, NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT) error:&error]; Must(library1 && !error, "library first");
    id function1 = [(id<NarrowLibrary>)library1 newFunctionWithName:@"read_write_surf_compute"]; Must(function1 != nil, "function first");
    id pipeline1 = [first.device newComputePipelineStateWithFunction:function1 error:&error]; Must(pipeline1 && !error, "pipeline first"); DrainExpected(&first, 2, "first setup");
    error = nil; id library2 = [second.device newLibraryWithData:dispatch_data_create(bytes.bytes, bytes.length, NULL, DISPATCH_DATA_DESTRUCTOR_DEFAULT) error:&error]; Must(library2 && !error, "library second");
    id function2 = [(id<NarrowLibrary>)library2 newFunctionWithName:@"read_write_surf_compute"]; Must(function2 != nil, "function second");
    id pipeline2 = [second.device newComputePipelineStateWithFunction:function2 error:&error]; Must(pipeline2 && !error, "pipeline second"); DrainExpected(&second, 2, "second setup");

    error = nil; Must([second.device newComputePipelineStateWithFunction:function1 error:&error] == nil && error != nil, "foreign function rejected"); ExpectNoWrite(&second, "foreign-function");
    MTLTextureDescriptor *good = Descriptor(); IOSurfaceRef surface1 = Surface((uint32_t)'BGRA', 4, Row, Bytes); IOSurfaceRef surface2 = Surface((uint32_t)'BGRA', 4, Row, Bytes); Must(surface1 && surface2, "valid surfaces");
    id source1 = [first.device newTextureWithDescriptor:good], destination1 = [first.device newTextureWithDescriptor:good iosurface:surface1 plane:0];
    id source2 = [second.device newTextureWithDescriptor:good], destination2 = [second.device newTextureWithDescriptor:good iosurface:surface2 plane:0]; Must(source1 && destination1 && source2 && destination2, "valid textures");
    id<NarrowQueue> queue = [first.device newCommandQueue]; Must(queue != nil, "queue");
    id command = [queue commandBuffer]; id<MTLComputeCommandEncoder> encoder = [command computeCommandEncoder]; [encoder setComputePipelineState:(id<MTLComputePipelineState>)pipeline2]; ExpectCommandError(command, "foreign-pipeline"); [encoder endEncoding]; [command commit]; ExpectNoWrite(&first, "foreign-pipeline");
    command = [queue commandBuffer]; encoder = [command computeCommandEncoder]; [encoder setComputePipelineState:(id<MTLComputePipelineState>)pipeline1]; [encoder setTexture:(id<MTLTexture>)source2 atIndex:0]; ExpectCommandError(command, "foreign-source"); [encoder endEncoding]; [command commit]; ExpectNoWrite(&first, "foreign-source");
    command = [queue commandBuffer]; encoder = [command computeCommandEncoder]; [encoder setComputePipelineState:(id<MTLComputePipelineState>)pipeline1]; [encoder setTexture:(id<MTLTexture>)source1 atIndex:0]; [encoder setTexture:(id<MTLTexture>)destination2 atIndex:1]; ExpectCommandError(command, "foreign-destination"); [encoder endEncoding]; [command commit]; ExpectNoWrite(&first, "foreign-destination");

    MTLTextureDescriptor *bad = Descriptor(); bad.mipmapLevelCount = 2; MustReject([first.device newTextureWithDescriptor:bad], "mip-count");
    bad = Descriptor(); bad.sampleCount = 2; MustReject([first.device newTextureWithDescriptor:bad], "sample-count");
    bad = Descriptor(); bad.arrayLength = 2; MustReject([first.device newTextureWithDescriptor:bad], "array-length");
    bad = Descriptor(); bad.textureType = MTLTextureType2DArray; MustReject([first.device newTextureWithDescriptor:bad], "texture-type");
    bad = Descriptor(); bad.depth = 2; MustReject([first.device newTextureWithDescriptor:bad], "depth");
    IOSurfaceRef invalid = Surface((uint32_t)'RGBA', 4, Row, Bytes); Must(invalid != NULL, "wrong-format surface"); MustReject([first.device newTextureWithDescriptor:good iosurface:invalid plane:0], "surface-format"); CFRelease(invalid);
    invalid = Surface((uint32_t)'BGRA', 4, Row + 4, (Row + 4) * Height); Must(invalid != NULL, "wrong-row surface"); MustReject([first.device newTextureWithDescriptor:good iosurface:invalid plane:0], "surface-row"); CFRelease(invalid);
    invalid = Surface((uint32_t)'BGRA', 2, Row, Bytes); if (invalid) { MustReject([first.device newTextureWithDescriptor:good iosurface:invalid plane:0], "surface-bpe"); CFRelease(invalid); } else fprintf(stderr, "NEGATIVE_DESCRIPTOR label=surface-bpe creation_rejected=1\\n");
    MustReject([first.device newTextureWithDescriptor:good iosurface:surface1 plane:1], "surface-plane");
    ExpectNoWrite(&first, "descriptor-surface-rejections");
    CFRelease(surface1); CFRelease(surface2); DestroyDevice(&first); DestroyDevice(&second);
    fprintf(stderr, "NEGATIVE_RESULT passed=1 cases=13\n"); return 0;
}}
