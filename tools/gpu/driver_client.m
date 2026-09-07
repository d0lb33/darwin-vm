// Native host process harness; frame transport is also used by the NS6 peer.
#import "driver_api.h"
#include <errno.h>
#include <time.h>
#include <unistd.h>
int DVMRunLuma(id<MTLDevice>, NSData *, unsigned);
#ifdef DVM_CA_PROBE
static void fail(const char *s){fprintf(stderr,"GPU_LOAD_ERROR driver=%s\n",s);exit(1);}
#include "consumer_probe.inc"
#endif
static BOOL transfer(int fd, void *p, size_t n, BOOL writeMode) {
    while (n) {
        ssize_t k = writeMode ? write(fd, p, n) : read(fd, p, n);
        if (k < 0 && errno == EINTR)
            continue;
        if (k <= 0)
            return NO;
        p = (uint8_t *)p + k;
        n -= k;
    }
    return YES;
}
int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 4)
            return 2;
        setvbuf(stderr, NULL, _IONBF, 0);
        NSData *air = [NSData dataWithContentsOfFile:@(argv[2])];
        if (!air)
            return 2;
        if (!strcmp(argv[1], "native"))
            return DVMRunLuma(MTLCreateSystemDefaultDevice(), air, (unsigned)atoi(argv[3]));
#ifdef DVM_CA_NATIVE
        if(!strcmp(argv[1],"consumer-native")){DVMRunQuartzCoreConsumer(MTLCreateSystemDefaultDevice());return 0;}
#endif
        __block uint64_t seq = 0;
        __block BOOL dead = NO;
        NSObject *lock = [NSObject new];
        DVMMetalRPC rpc = ^NSDictionary *(NSDictionary *request, NSError **e) {
            @synchronized(lock) {
                if (dead)
                    return nil;
                NSMutableDictionary *r = [request mutableCopy];
                r[@"seq"] = @(++seq);
                NSData *d = [NSJSONSerialization dataWithJSONObject:r options:0 error:e];
                if(getenv("DVM_CLIENT_AUDIT"))fprintf(stderr,"DVM_HOST_REQUEST %.*s\n",(int)d.length,(const char *)d.bytes);
                uint32_t n = (uint32_t)d.length;
                if (!d || !n || n > 2 * 1024 * 1024 || !transfer(1, &n, 4, YES) ||
                    !transfer(1, (void *)d.bytes, n, YES) || !transfer(0, &n, 4, NO) || !n ||
                    n > 2 * 1024 * 1024) {
                    dead = YES;
                    return nil;
                }
                NSMutableData *out = [NSMutableData dataWithLength:n];
                if (!transfer(0, out.mutableBytes, n, NO)) {
                    dead = YES;
                    return nil;
                }
                NSDictionary *reply = [NSJSONSerialization JSONObjectWithData:out
                                                                      options:0
                                                                        error:e];
                if(getenv("DVM_CLIENT_AUDIT")&&![reply[@"ok"] boolValue])fprintf(stderr,"DVM_HOST_REJECTION op=%s reply=%s\n",[r[@"op"] UTF8String],reply.description.UTF8String);
                if (![reply isKindOfClass:NSDictionary.class] ||
                    [reply[@"seq"] unsignedLongLongValue] != seq) {
                    dead = YES;
                    return nil;
                }
                if (![reply[@"ok"] boolValue]) {
                    if (e)
                        *e = [NSError
                            errorWithDomain:reply[@"domain"]
                                       code:[reply[@"code"] integerValue]
                                   userInfo:@{NSLocalizedDescriptionKey : reply[@"description"]}];
                    return nil;
                }
                return reply;
            }
        };
        @autoreleasepool {
#ifdef DVM_CA_PROBE
            if(!strcmp(argv[1],"consumer"))DVMRunQuartzCoreConsumer(DVMCreateMetalDevice(rpc));
            else
#endif
            DVMRunLuma(DVMCreateMetalDevice(rpc), air, (unsigned)atoi(argv[3]));
        }
        // Verify asynchronous retirement with a bounded condition, including
        // owner release after the caller drops the last device reference.
        struct timespec started, t;
        clock_gettime(CLOCK_MONOTONIC, &started);
        NSDictionary *stats = nil;
        NSError *e = nil;
        do {
            stats = rpc(@{@"op" : @"stats"}, &e);
            if (!stats)
                return 1;
            if ([stats[@"live"][@"objects"] unsignedIntegerValue] == 0)
                break;
            usleep(1000);
            clock_gettime(CLOCK_MONOTONIC, &t);
        } while (t.tv_sec - started.tv_sec < 5);
        BOOL consumer=!strcmp(argv[1],"consumer");
        if ([stats[@"live"][@"objects"] unsignedIntegerValue] ||
            (consumer?![stats[@"renderPasses"] unsignedIntegerValue]:[stats[@"submissions"] unsignedIntegerValue] != (unsigned)atoi(argv[3])))
            return 1;
        fprintf(stderr, "GPU_LOAD_COMPLETE scope=%s result=pass resources=0\n",consumer?"host-quartzcore-rehearsal":"host-driver-luma");
        return 0;
    }
}
