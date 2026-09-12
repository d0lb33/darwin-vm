// Host-native frontend contract: repeated resource attribution is a local
// no-op while changes remain ordered and acknowledged by the backend.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
#include <assert.h>

@protocol DVMResponsibleProcessTest
- (void)setResponsibleProcess:(int32_t)process;
- (int32_t)responsibleProcess;
@end

int main(void){@autoreleasepool{
    DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();
    host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
    __block uint64_t seq=0;__block NSUInteger processRequests=0;
    DVMMetalRPC rpc=^NSDictionary *(NSDictionary *request,NSError **failure){@synchronized(host){
        if([request[@"op"] isEqual:@"resourceProcess"])processRequests++;
        NSDictionary *reply=ProcessRequest(host,++seq,request);
        if(![reply[@"ok"] boolValue]&&failure)*failure=[NSError errorWithDomain:@"resource-process-test" code:1 userInfo:nil];
        return [reply[@"ok"] boolValue]?reply:nil;
    }};
    id<MTLDevice> device=DVMCreateMetalDevice(rpc);
    id<MTLBuffer> buffer=[device newBufferWithLength:256 options:0];assert(buffer);
    id<DVMResponsibleProcessTest> resource=(id)buffer;
    assert(resource.responsibleProcess==0);
    [resource setResponsibleProcess:391];assert(processRequests==1&&resource.responsibleProcess==391);
    [resource setResponsibleProcess:391];assert(processRequests==1&&resource.responsibleProcess==391);
    [resource setResponsibleProcess:392];assert(processRequests==2&&resource.responsibleProcess==392);
    NSNumber *handle=[(id)buffer valueForKey:@"handle"];
    assert([host.entries[handle] guestProcessBits]==392);
    puts("DVM_RESOURCE_PROCESS_PASS changed=2 duplicate_elided=1 host_state=392");
}}
