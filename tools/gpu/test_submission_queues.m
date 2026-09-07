// Host unit test of CPU scheduling, bounds and callback routing; no GPU claim.
#import "driver_api.h"
#include <stdio.h>
#include <stdlib.h>
@protocol DVMQueueScheduling
- (BOOL)setGPUPriority:(NSUInteger)priority;
- (BOOL)setBackgroundGPUPriority:(NSUInteger)priority;
- (void)setSubmissionQueue:(dispatch_queue_t)queue;
- (void)setCompletionQueue:(dispatch_queue_t)queue;
@end
@protocol DVMSubmitBoundary
- (BOOL)commitAndWaitUntilSubmitted;
@end
static void check(BOOL value,const char *message){if(!value){fprintf(stderr,"FAIL %s\n",message);exit(1);}}
int main(void){@autoreleasepool{
    static int submissionKey,completionKey;
    dispatch_queue_t submit=dispatch_queue_create("test.dvm.submit",DISPATCH_QUEUE_SERIAL);
    dispatch_queue_t complete=dispatch_queue_create("test.dvm.complete",DISPATCH_QUEUE_SERIAL);
    dispatch_queue_set_specific(submit,&submissionKey,&submissionKey,NULL);
    dispatch_queue_set_specific(complete,&completionKey,&completionKey,NULL);
    __block unsigned submissions=0,callbacks=0;
    id<MTLDevice> device=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *request,NSError **error){
        (void)error;check(dispatch_get_specific(&submissionKey)==NULL,"transport execution independent of client workloop");
        check([request[@"op"] isEqual:@"renderSubmit"],"empty render submission");submissions++;
        return @{@"status":@(MTLCommandBufferStatusCompleted),@"buffers":@{}};
    });
    check(![device newCommandQueueWithMaxCommandBufferCount:0],"zero bound rejected");
    check(![device newCommandQueueWithMaxCommandBufferCount:33],"over-contract bound rejected");
    id<MTLCommandQueue> queue=[device newCommandQueueWithMaxCommandBufferCount:2];
    check(![(id<DVMQueueScheduling>)queue setGPUPriority:4],"unsupported GPU priority declined");
    check(![(id<DVMQueueScheduling>)queue setBackgroundGPUPriority:0],"unsupported background priority declined");
    [(id<DVMQueueScheduling>)queue setSubmissionQueue:submit];
    [(id<DVMQueueScheduling>)queue setCompletionQueue:complete];
    dispatch_group_t done=dispatch_group_create();NSMutableArray *commands=[NSMutableArray array];
    for(unsigned i=0;i<3;i++){
        id<MTLCommandBuffer> command=[queue commandBuffer];[commands addObject:command];dispatch_group_enter(done);
        [command addCompletedHandler:^(id<MTLCommandBuffer> buffer){
            check(dispatch_get_specific(&completionKey)==&completionKey,"completion queue routing");
            check(buffer.status==MTLCommandBufferStatusCompleted&&!buffer.error,"completed result");
            check(callbacks==i,"FIFO callback delivery");callbacks++;dispatch_group_leave(done);
        }];
    }
    BOOL rejected=NO;@try{[(id<DVMQueueScheduling>)queue setCompletionQueue:complete];}@catch(NSException *e){(void)e;rejected=YES;}
    check(rejected,"configuration frozen after buffer creation");
    dispatch_suspend(submit);
    [(id<MTLCommandBuffer>)commands[0] commit];[(id<MTLCommandBuffer>)commands[1] commit];
    dispatch_sync([(id)device valueForKey:@"serial"],^{check(submissions==0,"suspended client queue gates admission without occupying transport");});
    rejected=NO;@try{[(id<MTLCommandBuffer>)commands[2] commit];}@catch(NSException *e){(void)e;rejected=YES;}
    check(rejected,"per-queue pending bound enforced");
    dispatch_resume(submit);
    [(id<MTLCommandBuffer>)commands[1] waitUntilCompleted];
    [(id<MTLCommandBuffer>)commands[2] commit];
    check(dispatch_group_wait(done,dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC))==0,"callback deadline");
    check(submissions==3&&callbacks==3,"all queued work completed");
    id<MTLCommandBuffer> boundary=[queue commandBuffer];
    check([(id<DVMSubmitBoundary>)boundary commitAndWaitUntilSubmitted]&&boundary.status==MTLCommandBufferStatusCompleted&&submissions==4,"submission boundary waits for acknowledged work");
    rejected=NO;@try{[(id<DVMSubmitBoundary>)boundary commitAndWaitUntilSubmitted];}@catch(NSException *e){(void)e;rejected=YES;}
    check(rejected&&submissions==4,"no duplicate submission");
    id<MTLDevice> bad=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *r,NSError **e){(void)r;if(e)*e=[NSError errorWithDomain:@"injected" code:1 userInfo:nil];return nil;});
    id<MTLCommandBuffer> failed=[[bad newCommandQueue] commandBuffer];
    check(![(id<DVMSubmitBoundary>)failed commitAndWaitUntilSubmitted]&&failed.status==MTLCommandBufferStatusError&&failed.error,"submission failure is not success");
    puts("DVM_QUEUE_SCHEDULING_PASS submissions=4 callbacks=3 FIFO=1 targets=verified bound=2 submitted_boundary=1 failure=1 duplicate_rejected=1");
    return 0;
}}
