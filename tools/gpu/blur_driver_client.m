// Native host process harness; frame transport is also used by the NS6 peer.
#import "driver_api.h"
#include <errno.h>
#include <time.h>
#include <unistd.h>
#import <CommonCrypto/CommonDigest.h>
#import <IOSurface/IOSurface.h>
#include "blur_wire.h"
static double blurRPCus,blurDoorbellus;
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
static void fail(const char*s){fprintf(stderr,"GPU_LOAD_ERROR %s\n",s);exit(1);}
#include "blur_workload.inc"
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

        __block uint64_t seq = 0;
        __block BOOL dead = NO;
        NSObject *lock = [NSObject new];
        DVMMetalRPC rpc = ^NSDictionary *(NSDictionary *request, NSError **e) {
            @synchronized(lock) {
                if (dead)
                    return nil;
                NSMutableDictionary *r = [request mutableCopy];
                r[@"seq"] = @(++seq);
                NSData *d = [r[@"op"] isEqual:@"blurSubmit"]?DVMBlurEncode(r):[NSJSONSerialization dataWithJSONObject:r options:0 error:e];
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
                NSDictionary *reply=DVMBMagic(out)==DVM_BLUR_REPLY?DVMBlurReplyDecode(out):[NSJSONSerialization JSONObjectWithData:out options:0 error:e];
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
            DVMRunBlur(DVMCreateBinaryMetalDevice(rpc),air);
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
        if ([stats[@"live"][@"objects"] unsignedIntegerValue] ||
            [stats[@"submissions"] unsignedIntegerValue] != (unsigned)atoi(argv[3]))
            return 1;
        fprintf(stderr, "GPU_LOAD_COMPLETE scope=host-driver-blur result=pass resources=0\n");
        return 0;
    }
}
