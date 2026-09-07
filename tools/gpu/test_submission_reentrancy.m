// Reproduces INPUT_MANUAL3's CA cleanup-workloop -> transport -> workloop cycle.
// CPU scheduling contract test; does not establish guest or GPU pixel behavior.
#import "driver_api.h"
#include <stdio.h>
#include <stdlib.h>
@protocol DVMSubmissionScheduling
- (void)setSubmissionQueue:(dispatch_queue_t)queue;
@end
static void check(BOOL ok,const char *why){if(!ok){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
static void awaitSignal(dispatch_semaphore_t s,const char *why){check(!dispatch_semaphore_wait(s,dispatch_time(DISPATCH_TIME_NOW,3*NSEC_PER_SEC)),why);}
int main(void){@autoreleasepool{
    dispatch_queue_t workloop=dispatch_queue_create("test.ca.workloop",DISPATCH_QUEUE_SERIAL);
    dispatch_queue_t cleanup=dispatch_queue_create_with_target("test.ca.mtl_dealloc",DISPATCH_QUEUE_SERIAL,workloop);
    dispatch_semaphore_t entered=dispatch_semaphore_create(0),go=dispatch_semaphore_create(0),cleaned=dispatch_semaphore_create(0),done=dispatch_semaphore_create(0);
    __block unsigned renders=0,callbacks=0,purges=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *r,NSError **e){
        (void)e;NSString *op=r[@"op"];
        if([op isEqual:@"buffer"])return @{@"handle":@1};
        if([op isEqual:@"resourcePurgeable"]){purges++;return @{@"ok":@YES,@"handle":@1,@"root":@1,@"requested":r[@"state"],@"previous":@2,@"current":@3};}
        if([op isEqual:@"renderSubmit"]){renders++;return @{@"status":@4,@"buffers":@{}};}
        if([op isEqual:@"writeRenderBuffer"]||[op isEqual:@"release"])return @{@"ok":@YES};
        fprintf(stderr,"unexpected %s\n",op.UTF8String);exit(1);
    });
    id<MTLBuffer> unused=[device newBufferWithLength:16 options:0];check(unused!=nil,"allocation");
    id<MTLCommandQueue> q=[device newCommandQueue];[(id<DVMSubmissionScheduling>)q setSubmissionQueue:workloop];
    dispatch_async(cleanup,^{dispatch_semaphore_signal(entered);awaitSignal(go,"allow cleanup");
        check([unused setPurgeableState:MTLPurgeableStateVolatile]==MTLPurgeableStateNonVolatile,"purge acknowledgement");dispatch_semaphore_signal(cleaned);
    });
    awaitSignal(entered,"cleanup owns workloop");
    id<MTLCommandBuffer> a=[q commandBuffer],b=[q commandBuffer];
    for(id<MTLCommandBuffer> cb in @[a,b]) [cb addCompletedHandler:^(id<MTLCommandBuffer> c){check(c.status==4&&!c.error,"command completion");@synchronized(device){callbacks++;}dispatch_semaphore_signal(done);}];
    // commit publishes work before cleanup synchronously requests the transport.
    // The old transport FIFO therefore blocks on the workloop before servicing purge.
    [a commit];[b commit];dispatch_semaphore_signal(go);
    awaitSignal(cleaned,"cleanup blocked by circular queue wait");awaitSignal(done,"first completion");awaitSignal(done,"second completion");
    check(renders==2&&callbacks==2&&purges==1,"exactly-once submission/purge/completion");
    puts("DVM_SUBMISSION_REENTRANCY_PASS renders=2 callbacks=2 purge=1 cleanup_target=workloop");
}}
