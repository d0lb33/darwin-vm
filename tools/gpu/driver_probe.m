#import "driver_api.h"
#include "driver_report.h"
#import <CommonCrypto/CommonDigest.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <errno.h>
#include <pthread/qos.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <spawn.h>
#include <sched.h>
#include <time.h>
#include <unistd.h>
int DVMRunLuma(id<MTLDevice>, NSData *, unsigned);
static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static uint32_t crc(const void *data, size_t n) {
    uint32_t c = ~0u;
    const uint8_t *p = data;
    while (n--) {
        c ^= *p++;
        for (int i = 0; i < 8; i++)
            c = (c >> 1) ^ ((0u - (c & 1)) & 0xedb88320u);
    }
    return ~c;
}
static void fail(const char *s) {
    fprintf(stderr, "GPU_LOAD_ERROR driver=%s\n", s);
    exit(1);
}
static void (*activityEnd)(void);
static void activityBegin(void) {
    // Explicitly account for work outside XPC message delivery. In particular,
    // the supervisor is working while it waits for its child, not idle.
    typedef struct { int32_t pid, priority; uint64_t userData; int32_t limit; uint32_t state; } Entry;
    int (*control)(uint32_t,int32_t,uint32_t,void *,size_t) = dlsym(RTLD_DEFAULT,"memorystatus_control");
    void (*begin)(void) = dlsym(RTLD_DEFAULT,"xpc_transaction_begin");
    activityEnd = dlsym(RTLD_DEFAULT,"xpc_transaction_end");
    Entry before = {0}, after = {0};
    if (!control || !begin || !activityEnd ||
        control(1,getpid(),0,&before,sizeof(before)) != sizeof(before) || before.pid != getpid())
        fail("activity-query");
    begin();
    if (control(1,getpid(),0,&after,sizeof(after)) != sizeof(after) || after.pid != getpid())
        fail("activity-readback");
    fprintf(stderr,"GPU_LOAD_DRIVER_ACTIVITY before_priority=%d before_state=%x after_priority=%d after_state=%x\n",
            before.priority,before.state,after.priority,after.state);
    if ((after.state & 8) && !(after.state & 0x20)) fail("activity-not-dirty");
}
static void memoryBudget(void) {
    // XNU kern_memorystatus.h: commands 7/8, 16-byte properties structure.
    // Restrict changes to this disposable helper's own PID, keeping a finite
    // fatal limit. Read back the live contract instead of trusting launchd keys.
    typedef struct {
        int32_t active;
        uint32_t activeFlags;
        int32_t inactive;
        uint32_t inactiveFlags;
    } Limits;
    int (*control)(uint32_t, int32_t, uint32_t, void *, size_t) =
        dlsym(RTLD_DEFAULT, "memorystatus_control");
    if (!control)
        fail("memorystatus-symbol");
    Limits limits = {0};
    if (control(8, getpid(), 0, &limits, sizeof(limits))) {
        fprintf(stderr, "GPU_LOAD_ERROR memory-get errno=%d\n", errno);
        exit(1);
    }
    fprintf(stderr, "GPU_LOAD_DRIVER_MEMORY before_active=%d before_inactive=%d\n", limits.active,
            limits.inactive);
    if (limits.active != 64 || limits.inactive != 64) {
        limits = (Limits){64, 1, 64, 1};
        if (control(7, getpid(), 0, &limits, sizeof(limits))) {
            fprintf(stderr, "GPU_LOAD_ERROR memory-set errno=%d\n", errno);
            exit(1);
        }
    }
    if (control(8, getpid(), 0, &limits, sizeof(limits)) || limits.active != 64 ||
        limits.inactive != 64)
        fail("memory-limit-readback");
    fprintf(stderr, "GPU_LOAD_DRIVER_MEMORY verified_active=64 verified_inactive=64\n");
}
#ifdef DVM_DRIVER_MMIO
#include "driver_mmio_transport.inc"
#else
enum { Page = 4096, Max = 2 * 1024 * 1024, Raw = 64 * 1024 * 1024 };
@interface Namespace : NSObject
@property(nonatomic) io_connect_t client;
@property(nonatomic) __typeof__(&IOConnectCallScalarMethod) scalar;
@property(nonatomic) __typeof__(&IOServiceClose) closeClient;
@property(nonatomic) uint8_t *page;
@property(nonatomic) uint8_t *payload;
@property(nonatomic, strong) NSData *identity;
@property(nonatomic) uint64_t sequence;
@property(nonatomic) BOOL failed;
- (void)io:(unsigned)selector bytes:(void *)bytes length:(size_t)n offset:(uint64_t)offset;
- (NSDictionary *)call:(NSDictionary *)r error:(NSError **)e;
- (NSDictionary *)transaction:(NSDictionary *)r error:(NSError **)e;
@end
@implementation Namespace
- (void)dealloc {
    if (_client)
        _closeClient(_client);
    free(_page);
    free(_payload);
}
- (void)io:(unsigned)selector bytes:(void *)bytes length:(size_t)n offset:(uint64_t)offset {
    if (n % Page || offset % Page || n > Max || offset > Raw - n)
        fail("namespace-bounds");
    uint64_t args[] = {(uintptr_t)bytes, n, offset};
    kern_return_t kr = _scalar(_client, selector, args, 3, NULL, NULL);
    if (kr) {
        fprintf(stderr, "GPU_LOAD_ERROR driver=namespace-io selector=%u kr=%x\n", selector, kr);
        exit(1);
    }
}
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)e {
    @synchronized(self) {
        return [self transaction:request error:e];
    }
}
- (NSDictionary *)transaction:(NSDictionary *)request error:(NSError **)e {
    if (_failed) {
        if (e)
            *e = [NSError errorWithDomain:@"DVMTransport"
                                     code:1
                                 userInfo:@{NSLocalizedDescriptionKey : @"session retired"}];
        return nil;
    }
    NSMutableDictionary *r = [request mutableCopy];
    uint64_t seq = ++_sequence;
    r[@"seq"] = @(seq);
    NSData *d = [NSJSONSerialization dataWithJSONObject:r options:0 error:e];
    if (!d || !d.length || d.length > Max) {
        _failed = YES;
        return nil;
    }
    size_t aligned = (d.length + Page - 1) & ~(Page - 1);
    memset(_payload, 0, aligned);
    memcpy(_payload, d.bytes, d.length);
    [self io:1 bytes:_payload length:aligned offset:0x100000];
    memset(_page, 0, Page);
    memcpy(_page, _identity.bytes, 64);
    memcpy(_page + 64, &seq, 8);
    uint32_t length = (uint32_t)d.length, c = crc(d.bytes, d.length);
    memcpy(_page + 72, &length, 4);
    memcpy(_page + 76, &c, 4);
    c = crc(_page, Page - 4);
    memcpy(_page + Page - 4, &c, 4);
    [self io:1 bytes:_page length:Page offset:0x10000];
    double until = now() + 15;
    BOOL received = NO;
    do {
        [self io:0 bytes:_page length:Page offset:0x20000];
        uint64_t returned = 0;
        memcpy(&returned, _page + 64, 8);
        memcpy(&c, _page + Page - 4, 4);
        if (!memcmp(_page, _identity.bytes, 64) && returned == seq && c == crc(_page, Page - 4)) {
            received = YES;
            break;
        }
        usleep(1000);
    } while (now() < until);
    if (!received) {
        _failed = YES;
        if (e)
            *e = [NSError
                errorWithDomain:@"DVMTransport"
                           code:2
                       userInfo:@{NSLocalizedDescriptionKey : @"15-second completion deadline"}];
        return nil;
    }
    memcpy(&length, _page + 72, 4);
    memcpy(&c, _page + 76, 4);
    if (!length || length > Max) {
        _failed = YES;
        return nil;
    }
    [self io:0 bytes:_payload length:(length + Page - 1) & ~(Page - 1) offset:0x400000];
    if (crc(_payload, length) != c) {
        _failed = YES;
        return nil;
    }
    NSDictionary *reply = [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:_payload
                                                                                 length:length]
                                                          options:0
                                                            error:e];
    if (![reply isKindOfClass:NSDictionary.class] || [reply[@"seq"] unsignedLongLongValue] != seq) {
        _failed = YES;
        return nil;
    }
    if (![reply[@"ok"] boolValue]) {
        if (e)
            *e = [NSError errorWithDomain:reply[@"domain"]
                                     code:[reply[@"code"] integerValue]
                                 userInfo:@{NSLocalizedDescriptionKey : reply[@"description"]}];
        return nil;
    }
    return reply;
}
@end
#endif
static uint32_t be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static NSData *library(void) {
    NSData *fat = [NSData
        dataWithContentsOfFile:@"/System/Library/Frameworks/QuartzCore.framework/default.metallib"
                       options:NSDataReadingMappedIfSafe
                         error:NULL];
    const uint8_t *p = fat.bytes;
    if (fat.length < 8 || be32(p) != 0xcafebabe)
        fail("library-format");
    uint32_t count = be32(p + 4);
    if (count > 128 || fat.length < 8 + count * 20)
        fail("library-table");
    for (uint32_t i = 0; i < count; i++) {
        uint32_t off = be32(p + 8 + i * 20 + 8), n = be32(p + 8 + i * 20 + 12);
        if (n != 2705796 || (uint64_t)off + n > fat.length)
            continue;
        uint8_t digest[32];
        char hex[65];
        CC_SHA256(p + off, n, digest);
        for (int j = 0; j < 32; j++)
            snprintf(hex + 2 * j, 3, "%02x", digest[j]);
        if (!strcmp(hex, "8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364")) {
            fprintf(stderr, "GPU_LOAD_DRIVER_AIR sha256=%s bytes=%u\n", hex, n);
            return [NSData dataWithBytes:p + off length:n];
        }
    }
    fail("exact-air");
    return nil;
}
#define LOAD(name)                                                                                 \
    __typeof__(&name) p_##name = dlsym(lib, #name);                                                \
    if (!p_##name)                                                                                 \
    fail("symbol-" #name)
int main(int argc, char **argv) {
    @autoreleasepool {
        setvbuf(stderr, NULL, _IONBF, 0);
        fprintf(stderr, "GPU_LOAD_DRIVER_START version=1 pid=%d\n", getpid());
        // Match the existing native input helper's interactive workload policy.
        int priorityResult=setpriority(PRIO_PROCESS,0,-20);
        int priorityError=priorityResult?errno:0;
        int qosResult=pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE,0);
        fprintf(stderr,"GPU_LOAD_DRIVER_SCHED nice_result=%d nice_errno=%d qos_result=%d\n",priorityResult,priorityError,qosResult);
        if(priorityResult||qosResult)fail("interactive-scheduling");
        memoryBudget();
        activityBegin();
        if (argc == 1) {
            // A launchd exit is otherwise silent on this guest. Keep an explicit
            // parent so SIGKILL and normal worker exit have observable evidence.
            int (*spawn)(pid_t *, const char *, const posix_spawn_file_actions_t *,
                         const posix_spawnattr_t *, char *const[], char *const[]) =
                dlsym(RTLD_DEFAULT, "posix_spawn");
            if (!spawn) fail("spawn-symbol");
            pid_t child = 0;
            char *childArgs[] = {argv[0], "--worker", NULL};
            char *childEnv[] = {NULL};
            int rc = spawn(&child, argv[0], NULL, NULL, childArgs, childEnv);
            if (rc) {
                fprintf(stderr, "GPU_LOAD_ERROR spawn=%d\n", rc);
                return 1;
            }
            fprintf(stderr, "GPU_LOAD_DRIVER_CHILD pid=%d\n", child);
            int status = 0;
            pid_t waited;
            do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
            fprintf(stderr, "GPU_LOAD_DRIVER_CHILD_EXIT pid=%d waited=%d status=%x exit=%d signal=%d\n",
                    child, waited, status, WIFEXITED(status) ? WEXITSTATUS(status) : -1,
                    WIFSIGNALED(status) ? WTERMSIG(status) : 0);
            if (waited != child || !WIFEXITED(status) || WEXITSTATUS(status))
                fail("worker-exit");
            activityEnd();
            return 0;
        }
        if (argc != 2 || strcmp(argv[1], "--worker")) fail("arguments");
        fprintf(stderr, "GPU_LOAD_DRIVER_STAGE iokit\n");
        void *lib =
            dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW | RTLD_LOCAL);
        if (!lib)
            fail("iokit");
        // Validate the pinned cache identity while waiting, but defer reading
        // the 11 MB fat library until display readiness. Keep boot footprint low.
        const uint8_t digest[32] = {0x88, 0x60, 0xe4, 0xa1, 0x7d, 0x89, 0x78, 0x3d,
                                    0xa0, 0x64, 0x29, 0xa3, 0x02, 0xdb, 0x0b, 0xc6,
                                    0x1b, 0x29, 0x39, 0x96, 0x3f, 0x20, 0x2c, 0x0c,
                                    0x6a, 0xd3, 0x11, 0x89, 0xa1, 0x02, 0x13, 0x64};
        double until;
#ifdef DVM_DRIVER_MMIO
        Namespace *ns=openMMIO(lib);
        if(memcmp(ns.shared+0x100,digest,32))fail("host-library-identity");
#else
        LOAD(IORegistryEntryFromPath);
        LOAD(IOObjectGetClass);
        LOAD(IOObjectRelease);
        LOAD(IOServiceOpen);
        LOAD(IOServiceClose);
        LOAD(IOConnectCallScalarMethod);
        Namespace *ns = [Namespace new];
        ns.scalar = p_IOConnectCallScalarMethod;
        ns.closeClient = p_IOServiceClose;
        uint8_t *page = NULL, *payload = NULL;
        if (posix_memalign((void **)&page, 16384, Page) ||
            posix_memalign((void **)&payload, 16384, Max))
            fail("allocation");
        ns.page = page;
        ns.payload = payload;
        const char *path =
            "IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/ans@79600000/AppleASCWrapV6/"
            "iop-ans-nub/RTBuddy(ANS2)/RTBuddyService/AppleANS3CGv2Controller/NS_06";
        io_service_t service = 0;
        until = now() + 60;
        do {
            service = p_IORegistryEntryFromPath(0, path);
            if (!service)
                usleep(100000);
        } while (!service && now() < until);
        if (!service)
            fail("namespace");
        fprintf(stderr, "GPU_LOAD_DRIVER_STAGE service-found\n");
        char cls[128] = {0};
        if (p_IOObjectGetClass(service, cls) || strcmp(cls, "AppleNVMeNamespaceDevice"))
            fail("namespace-class");
        io_connect_t client = 0;
        kern_return_t kr = p_IOServiceOpen(service, mach_task_self(), 0, &client);
        p_IOObjectRelease(service);
        if (kr)
            fail("namespace-open");
        ns.client = client;
        fprintf(stderr, "GPU_LOAD_DRIVER_STAGE opened\n");
        for (unsigned i = 2; i <= 3; i++) {
            fprintf(stderr, "GPU_LOAD_DRIVER_STAGE capacity-%u\n", i);
            uint64_t v = 0;
            uint32_t n = 1;
            if (ns.scalar(client, i, NULL, 0, &v, &n) || n != 1 ||
                v != (i == 2 ? Page : Raw / Page))
                fail("namespace-capacity");
        }
        fprintf(stderr, "GPU_LOAD_DRIVER_STAGE header-read\n");
        [ns io:0 bytes:page length:Page offset:0];
        if (memcmp(page, "DVM-METAL-DRIVER-v1", 19))
            fail("session-header");
        if (memcmp(page + 64, digest, 32))
            fail("host-library-identity");
        ns.identity = [NSData dataWithBytes:page length:64];
        fprintf(stderr, "GPU_LOAD_DRIVER_WAIT_READY\n");
        until = now() + 420;
        uint32_t ready = 0;
        unsigned poll = 0;
        do {
            ++poll;
            BOOL trace = poll <= 8 || !(poll % 1024);
            if (trace) fprintf(stderr, "GPU_LOAD_DRIVER_POLL n=%u phase=read-enter t=%.6f\n", poll, now());
            [ns io:0 bytes:page length:Page offset:0];
            if (memcmp(page, ns.identity.bytes, 64) || memcmp(page + 64, digest, 32))
                fail("session-changed");
            memcpy(&ready, page + 96, 4);
            if (trace || ready) fprintf(stderr, "GPU_LOAD_DRIVER_POLL n=%u phase=read-return ready=%u t=%.6f\n", poll, ready, now());
            if (ready == 1)
                break;
            // The complete stalled snapshot locates this thread in sched_yield,
            // after a successful read. Let namespace I/O pace this diagnostic
            // wait without adding another voluntary scheduler wait. A production
            // transport still needs event-driven notification instead of polling.
        } while (now() < until);
        if (ready != 1)
            fail("display-readiness-deadline");
#endif
        fprintf(stderr, "GPU_LOAD_DRIVER_READY\n");
        memoryBudget();
        NSData *air = nil;
        @autoreleasepool {
            air = library();
        }
        uint8_t actual[32];
        CC_SHA256(air.bytes, (CC_LONG)air.length, actual);
        if (memcmp(actual, digest, 32))
            fail("guest-air-identity");
        NSBundle *bundle = [NSBundle bundleWithPath:@"/usr/local/libexec/DVMProxy.bundle"];
        NSError *e = nil;
        if (![bundle loadAndReturnError:&e])
            fail("bundle-load");
        fprintf(stderr, "GPU_LOAD_DRIVER_STAGE bundle-loaded\n");
        void *handle = dlopen("/usr/local/libexec/DVMProxy.bundle/DVMProxy", RTLD_NOW | RTLD_LOCAL);
        DVMCreateMetalDeviceFn create = handle ? dlsym(handle, "DVMCreateMetalDevice") : NULL;
        if (!create)
            fail("factory");
        @autoreleasepool {
            id<MTLDevice> device = create(^NSDictionary *(NSDictionary *r, NSError **error) {
                return [ns call:r error:error];
            });
            if (![device conformsToProtocol:@protocol(MTLDevice)])
                fail("device-protocol");
            fprintf(stderr, "GPU_LOAD_DRIVER_STAGE device-created\n");
            DVMRunLuma(device, air, 8);
        }
        // Retire queues run asynchronously; a bounded stats check also detects leaks.
        until = now() + 5;
        NSDictionary *stats = nil;
        do {
            stats = [ns call:@{@"op" : @"stats"} error:&e];
            if ([stats[@"live"][@"objects"] unsignedIntegerValue] == 0)
                break;
            usleep(10000);
        } while (now() < until);
        if (!stats || [stats[@"submissions"] unsignedIntegerValue] != 8 ||
            [stats[@"live"][@"objects"] unsignedIntegerValue] != 0)
            fail("resource-retirement");
        fprintf(
            stderr,
            "GPU_LOAD_COMPLETE result=pass scope=metal-driver-luma submissions=8 resources=0\n");
        activityEnd();
        return 0;
    }
}
