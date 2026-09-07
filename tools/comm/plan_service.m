// Minimal no-modem provider for the exact guest's CellularPlanDaemon endpoint.
// Protocol encodings are recorded in plan_protocol_24A5430a.json. This is not
// a replacement for CommCenter's unrelated raw-XPC and baseband interfaces.
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>

// Private in iOS; validate against the exact Foundation image before staging.
@protocol DVMListenerInit
- (instancetype)initWithMachServiceName:(NSString *)name;
@end


@protocol DVMPlanContract
- (void)ping;
- (void)planItemsWithCompletion:(void (^)(NSArray *, NSError *))reply;
- (void)danglingPlanItemsShouldUpdate:(BOOL)update completion:(void (^)(NSArray *, NSError *))reply;
- (void)getPlansPendingTransferWithCompletion:(void (^)(NSArray *, NSError *))reply;
- (void)getPlansPendingTransferForTestabilityWithCompletion:(void (^)(NSArray *, NSError *))reply;
- (void)remotePlanItemsWithUpdateFetch:(BOOL)update completion:(void (^)(NSArray *, NSError *))reply;
- (void)carrierItemsShouldUpdate:(BOOL)update completion:(void (^)(NSArray *, NSError *))reply;
- (void)getSupportedFlowTypes:(void (^)(uint64_t, NSError *))reply;
- (void)shouldShowPlanSetup:(void (^)(BOOL))reply;
- (void)isRemotePlanCapableWithContext:(id)context completion:(void (^)(BOOL))reply;
- (void)shouldShowAddNewRemotePlanWithContext:(id)context completion:(void (^)(BOOL, uint64_t, NSString *, NSError *))reply;
- (void)getRemoteInfo:(void (^)(NSDictionary *, NSError *))reply;
@end

@protocol DVMPlanHostContract <DVMPlanContract>
- (void)startProvisioningWithCompletion:(void (^)(BOOL))reply;
@end

static void comm_log(NSString *event) {
    fprintf(stderr,"DVM_COMM %s\n",event.UTF8String);fflush(stderr);
}

@interface DVMPlanService : NSObject <DVMPlanContract, NSXPCListenerDelegate>
@property Protocol *wireProtocol;
@end
@implementation DVMPlanService
- (void)ping { comm_log(@"ping"); }
- (void)planItemsWithCompletion:(void (^)(NSArray *, NSError *))r {comm_log(@"plans empty");r(@[],nil);}
- (void)danglingPlanItemsShouldUpdate:(BOOL)u completion:(void (^)(NSArray *, NSError *))r {(void)u;comm_log(@"dangling empty");r(@[],nil);}
- (void)getPlansPendingTransferWithCompletion:(void (^)(NSArray *, NSError *))r {comm_log(@"transfers empty");r(@[],nil);}
- (void)getPlansPendingTransferForTestabilityWithCompletion:(void (^)(NSArray *, NSError *))r {r(@[],nil);}
- (void)remotePlanItemsWithUpdateFetch:(BOOL)u completion:(void (^)(NSArray *, NSError *))r {(void)u;r(@[],nil);}
- (void)carrierItemsShouldUpdate:(BOOL)u completion:(void (^)(NSArray *, NSError *))r {(void)u;r(@[],nil);}
- (void)getSupportedFlowTypes:(void (^)(uint64_t, NSError *))r {
    // Exact Settings consumer tests bit 15 inversely for activation support;
    // positive flow bits 0,1,5 remain clear. No provisionable modem exists.
    comm_log(@"flows activation-disabled");r(0x8000,nil);
}
- (void)shouldShowPlanSetup:(void (^)(BOOL))r {r(NO);}
- (void)isRemotePlanCapableWithContext:(id)c completion:(void (^)(BOOL))r {(void)c;r(NO);}
- (void)shouldShowAddNewRemotePlanWithContext:(id)c completion:(void (^)(BOOL,uint64_t,NSString *,NSError *))r {(void)c;r(NO,0,nil,nil);}
- (void)getRemoteInfo:(void (^)(NSDictionary *, NSError *))r {r(@{},nil);}
- (NSMethodSignature *)methodSignatureForSelector:(SEL)s {
    NSMethodSignature *known=[super methodSignatureForSelector:s];if(known)return known;
    struct objc_method_description d=protocol_getMethodDescription(self.wireProtocol,s,YES,YES);
    return d.types?[NSMethodSignature signatureWithObjCTypes:d.types]:nil;
}
- (void)forwardInvocation:(NSInvocation *)inv {
    // Unknown requests must fail the connection, not strand a synchronous
    // client or pretend that provisioning succeeded. No fabricated block ABI.
    comm_log([@"unsupported " stringByAppendingString:NSStringFromSelector(inv.selector)]);
    [NSXPCConnection.currentConnection invalidate];
}
- (BOOL)listener:(NSXPCListener *)listener shouldAcceptNewConnection:(NSXPCConnection *)c {
    (void)listener;
    c.exportedInterface=[NSXPCInterface interfaceWithProtocol:self.wireProtocol];
    c.exportedObject=self;
    comm_log([NSString stringWithFormat:@"accept pid=%d",c.processIdentifier]);
    [c resume];return YES;
}
@end

int main(int argc,const char **argv) { @autoreleasepool {
    BOOL host=argc==2&&!strcmp(argv[1],"--self-test");
    Protocol *wire=@protocol(DVMPlanHostContract);
    if(!host){
        if(!dlopen("/System/Library/PrivateFrameworks/CellularPlanManager.framework/CellularPlanManager",RTLD_NOW)){
            fprintf(stderr,"DVM_COMM dlopen failed: %s\n",dlerror());return 1;
        }
        wire=objc_getProtocol("CTCellularPlanClient");
        if(!wire){comm_log(@"missing exact guest protocol");return 1;}
    }
    DVMPlanService *service=[DVMPlanService new];service.wireProtocol=wire;
    NSXPCListener *listener=host?[NSXPCListener anonymousListener]:(NSXPCListener *)[(id<DVMListenerInit>)[NSXPCListener alloc]initWithMachServiceName:@"com.apple.CellularPlanDaemon.xpc"];
    listener.delegate=service;[listener resume];comm_log(@"ready no-modem v1");
    if(host){
        NSXPCConnection *c=[[NSXPCConnection alloc]initWithListenerEndpoint:listener.endpoint];
        c.remoteObjectInterface=[NSXPCInterface interfaceWithProtocol:wire];[c resume];
        __block BOOL failed=NO;
        id<DVMPlanContract> proxy=[c synchronousRemoteObjectProxyWithErrorHandler:^(NSError *e){comm_log(e.description);failed=YES;}];
        for(int i=0;i<20;i++){
            __block NSUInteger n=0;
            [proxy planItemsWithCompletion:^(NSArray *v,NSError *e){if(v.count||e)failed=YES;n++;}];
            [proxy danglingPlanItemsShouldUpdate:YES completion:^(NSArray *v,NSError *e){if(v.count||e)failed=YES;n++;}];
            [proxy getPlansPendingTransferWithCompletion:^(NSArray *v,NSError *e){if(v.count||e)failed=YES;n++;}];
            [proxy getSupportedFlowTypes:^(uint64_t v,NSError *e){if(v!=0x8000||e)failed=YES;n++;}];
            if(n!=4)failed=YES;
        }
        [c invalidate];
        NSXPCConnection *unsupported=[[NSXPCConnection alloc]initWithListenerEndpoint:listener.endpoint];
        unsupported.remoteObjectInterface=[NSXPCInterface interfaceWithProtocol:wire];[unsupported resume];
        __block BOOL rejected=NO,called=NO;
        id<DVMPlanHostContract> unknown=[unsupported synchronousRemoteObjectProxyWithErrorHandler:^(NSError *e){(void)e;rejected=YES;}];
        [unknown startProvisioningWithCompletion:^(BOOL result){(void)result;called=YES;}];
        if(!rejected||called)failed=YES;
        [unsupported invalidate];[listener invalidate];comm_log(failed?@"HOST_TEST_FAIL":@"HOST_TEST_PASS replies=80 unsupported_failed=1");return failed?1:0;
    }
    [[NSRunLoop currentRunLoop]run];return 1;
}}
