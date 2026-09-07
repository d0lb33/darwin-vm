// Host test of frontend ownership and asynchronous failure semantics. The fake
// transport never executes shaders; real GPU execution has a separate test.
#import "driver_api.h"
#include "driver_capabilities.h"
#include <stdio.h>
static void check(BOOL b, const char *why) {
    if (!b) {
        fprintf(stderr, "FAIL %s\n", why);
        exit(1);
    }
}
int main(void) {
    @autoreleasepool {
        __block unsigned handle = 0, attempts = 0, releases = 0;
        dispatch_semaphore_t uploading = dispatch_semaphore_create(0),
                             resume = dispatch_semaphore_create(0);
        NSObject *lock = [NSObject new];
        DVMMetalRPC rpc = ^NSDictionary *(NSDictionary *r, NSError **e) {
            (void)e;
            @synchronized(lock) {
                NSString *op = r[@"op"];
                if ([op isEqual:@"library"])
                    return @{@"handle" : @(++handle), @"functionNames" : @[ @"test" ]};
                if ([op isEqual:@"pipeline"])
                    return @{
                        @"handle" : @(++handle),
                        @"threadExecutionWidth" : @32,
                        @"maxTotalThreadsPerThreadgroup" : @64
                    };
                if ([op isEqual:@"buffer"])
                    return @{@"handle" : @(++handle)};
                if ([op isEqual:@"submit"]) {
                    attempts++;
                    dispatch_semaphore_signal(uploading);
                    check(!dispatch_semaphore_wait(
                              resume, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)),
                          "resume upload");
                    return nil; // Even a transport omitting NSError must fail closed.
                }
                if ([op isEqual:@"release"]) {
                    releases++;
                    return @{};
                }
                return nil;
            }
        };
        id<MTLDevice> device = DVMCreateMetalDevice(rpc), other = DVMCreateMetalDevice(rpc);
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> cb = [queue commandBuffer];
        dispatch_semaphore_t completed = dispatch_semaphore_create(0);
        __block BOOL failed = NO;
        @autoreleasepool {
            id<MTLCommandBuffer> second = [queue commandBuffer];
            @autoreleasepool {
                const char text[] = "stub";
                dispatch_data_t bytes = dispatch_data_create(text, sizeof(text), NULL,
                                                             DISPATCH_DATA_DESTRUCTOR_DEFAULT);
                NSError *error = nil;
                id<MTLLibrary> lib = [device newLibraryWithData:bytes error:&error];
                id<MTLFunction> f = [lib newFunctionWithName:@"test"];
                check(![other newComputePipelineStateWithFunction:f error:&error] && error,
                      "foreign function rejected");
                id<MTLComputePipelineState> p = [device newComputePipelineStateWithFunction:f
                                                                                      error:&error];
                id<MTLBuffer> b = [device newBufferWithLength:16
                                                      options:MTLResourceStorageModeShared];
                id<MTLBuffer> foreign = [other newBufferWithLength:16
                                                           options:MTLResourceStorageModeShared];
                id<MTLComputeCommandEncoder> encoder = [cb computeCommandEncoder];
                BOOL rejected = NO;
                @try {
                    [encoder setBuffer:foreign offset:0 atIndex:2];
                } @catch (NSException *e) {
                    rejected = YES;
                }
                check(rejected, "foreign buffer rejected");
                [encoder setComputePipelineState:p];
                [encoder setBuffer:b offset:0 atIndex:2];
                [encoder dispatchThreadgroups:MTLSizeMake(1, 1, 1)
                        threadsPerThreadgroup:MTLSizeMake(8, 1, 1)];
                [encoder endEncoding];
                rejected = NO;
                @try {
                    [encoder endEncoding];
                } @catch (NSException *e) {
                    rejected = YES;
                }
                check(rejected, "ended encoder rejected");
                encoder = [second computeCommandEncoder];
                [encoder setComputePipelineState:p];
                [encoder setBuffer:b offset:0 atIndex:2];
                [encoder dispatchThreadgroups:MTLSizeMake(1, 1, 1)
                        threadsPerThreadgroup:MTLSizeMake(8, 1, 1)];
                [encoder endEncoding];
            }
            // Only the command buffer now owns its resources.
            [cb addCompletedHandler:^(id<MTLCommandBuffer> b) {
                failed = b.status == MTLCommandBufferStatusError && b.error != nil;
                dispatch_semaphore_signal(completed);
            }];
            [cb commit];
            check(!dispatch_semaphore_wait(uploading,
                                           dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)),
                  "upload began asynchronously");
            [second commit];
            check(second.status==MTLCommandBufferStatusCommitted,"dependent command queued while predecessor is active");
            NSMutableArray *queued=[NSMutableArray array];
            for(unsigned i=2;i<DVM_QUEUED_COMMAND_BUFFERS;i++){id<MTLCommandBuffer> c=[queue commandBuffer];[c commit];[queued addObject:c];}
            BOOL refused=NO;@try{[[queue commandBuffer] commit];}@catch(NSException *e){refused=YES;}
            check(refused,"queue budget enforced before unbounded retention");
            dispatch_semaphore_signal(resume);
            [second waitUntilCompleted];
            check(second.status==MTLCommandBufferStatusError&&second.error!=nil,"failed predecessor cancels dependent queued work");
            for(id<MTLCommandBuffer> c in queued){[c waitUntilCompleted];check(c.status==MTLCommandBufferStatusError,"all pending dependents cancelled");}
            second = nil;
        } // Drain the rejected command and exception autoreleases before checking retirement.
        check(
            !dispatch_semaphore_wait(completed, dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC)),
            "callback without wait");
        [cb waitUntilCompleted];
        check(failed, "upload failure reaches completion");
        @synchronized(lock) {
            check(attempts == 1, "one failed atomic submit, no retry");
        }
        // Any ordinary synchronous device RPC drains retirements queued earlier.
        @autoreleasepool {
            id<MTLBuffer> drain = [device newBufferWithLength:16
                                                      options:MTLResourceStorageModeShared];
            check(drain != nil, "device queue still usable");
        }
        @synchronized(lock) {
            check(releases >= 3, "encoded resources retired after completion");
        }
        fprintf(stderr,
                "DRIVER_CONTRACT_PASS ownership=1 async_error=1 no_false_success=1 retirement=1\n");
        return 0;
    }
}
