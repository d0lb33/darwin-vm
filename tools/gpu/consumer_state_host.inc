static MTLStencilDescriptor *DVMHostStencil(id input,BOOL *valid) {
    if(![input isKindOfClass:NSArray.class]||([input count]!=0&&[input count]!=6)){*valid=NO;return nil;}
    if(![input count])return nil;
    uint64_t values[6];
    for(unsigned i=0;i<6;i++)if(!Number(input[i],&values[i])||values[i]>(i<4?7:UINT32_MAX)){*valid=NO;return nil;}
    MTLStencilDescriptor *d=[MTLStencilDescriptor new];
    d.stencilCompareFunction=values[0];d.stencilFailureOperation=values[1];d.depthFailureOperation=values[2];d.depthStencilPassOperation=values[3];d.readMask=(uint32_t)values[4];d.writeMask=(uint32_t)values[5];
    return d;
}
static NSDictionary *DepthState(DVMHost *host,uint64_t seq,NSDictionary *r) {
    uint64_t compare;BOOL valid=YES;
    if(!Number(r[@"compare"],&compare)||compare>7||!r[@"write"]||CFGetTypeID((__bridge CFTypeRef)r[@"write"])!=CFBooleanGetTypeID())return HostError(seq,EINVAL,@"depth descriptor");
    MTLDepthStencilDescriptor *d=[MTLDepthStencilDescriptor new];
    d.depthCompareFunction=compare;d.depthWriteEnabled=[r[@"write"] boolValue];
    d.frontFaceStencil=DVMHostStencil(r[@"front"],&valid);d.backFaceStencil=DVMHostStencil(r[@"back"],&valid);
    if(!valid)return HostError(seq,EINVAL,@"stencil descriptor");
    id<MTLDepthStencilState> state=[host.device newDepthStencilStateWithDescriptor:d];
    if(!state)return HostError(seq,EINVAL,@"host depth state");
    DVMEntry *entry;if(!Add(host,@"depthState",state,&entry))return HostError(seq,ENOSPC,@"state table full");
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle)};
}
