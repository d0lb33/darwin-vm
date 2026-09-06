// Immutable state forwarding exercised by CARenderer initialization.
@interface DVMDepthState : DVMObject <MTLDepthStencilState>
@end
@implementation DVMDepthState
@end
static NSArray *DVMStencil(MTLStencilDescriptor *s) {
    return s?@[@(s.stencilCompareFunction),@(s.stencilFailureOperation),@(s.depthFailureOperation),@(s.depthStencilPassOperation),@(s.readMask),@(s.writeMask)]:[NSArray array];
}
static id<MTLDepthStencilState> DVMNewDepth(DVMDevice *device,MTLDepthStencilDescriptor *d) {
    if(!d)return nil;
    NSError *e=nil;
    NSDictionary *reply=[device call:@{@"op":@"depthState",@"compare":@(d.depthCompareFunction),@"write":@(d.depthWriteEnabled),@"front":DVMStencil(d.frontFaceStencil),@"back":DVMStencil(d.backFaceStencil)} error:&e];
    if(!reply)reject(e.description?:@"depth state allocation");
    DVMDepthState *state=[DVMDepthState new];state.owner=device;state.handle=reply[@"handle"];state.label=d.label;
    return state;
}
