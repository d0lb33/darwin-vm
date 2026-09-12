// Stage oversized serialized command lists without splitting their semantics.
// Resource lifetime and serialization remain owned by the existing device FIFO.
static NSDictionary *DVMRenderTransport(DVMMetalRPC rpc,DVMMetalStagedRPC stagedRPC,
                                        NSUInteger stagedBytes,NSDictionary *request,NSError **failure){
    NSData *data=[NSJSONSerialization dataWithJSONObject:request options:0 error:failure];
    if(!data)return nil;
    if(data.length>DVM_RENDER_REQUEST_BYTES){if(failure)*failure=error(@"render command request exceeds negotiated byte limit");return nil;}
    // The ordinary dictionary RPC serializes its request after assigning the
    // sequence.  Reusing it here made every render command list pay for the
    // same large Objective-C -> JSON conversion twice in the guest.  The
    // staging transport carries these already-serialized bytes and sends only
    // a small sequenced descriptor through the ordinary path.
    if(stagedRPC&&data.length<=stagedBytes)
        return stagedRPC(@{@"op":@"renderSubmitJSON"},data,failure);
    if(data.length<=DVM_RENDER_DIRECT_BYTES)return rpc(request,failure);
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];char hex[65];
    CC_SHA256(data.bytes,(CC_LONG)data.length,digest);
    for(unsigned i=0;i<32;i++)snprintf(hex+i*2,3,"%02x",digest[i]);
    NSDictionary *reply=rpc(@{@"op":@"renderStageBegin",@"length":@(data.length),@"sha256":@(hex)},failure);
    NSNumber *token=reply[@"token"];
    if(![token isKindOfClass:NSNumber.class]||!token.unsignedLongLongValue||![reply[@"offset"] isEqual:@0]){
        if(failure&&!*failure)*failure=error(@"render staging begin acknowledgement");return nil;
    }
    for(NSUInteger offset=0;offset<data.length;){
        NSUInteger length=MIN(DVM_RENDER_REQUEST_CHUNK,data.length-offset);
        NSDictionary *chunkRequest=nil;
        do {
            NSData *chunk=[data subdataWithRange:NSMakeRange(offset,length)];
            chunkRequest=@{@"op":@"renderStageChunk",@"token":token,@"offset":@(offset),@"payload":[chunk base64EncodedStringWithOptions:0]};
            // The installed transport may escape base64 slashes. Bound the
            // encoded envelope too, leaving room for its sequence field.
            if([NSJSONSerialization dataWithJSONObject:chunkRequest options:0 error:nil].length<=DVM_RENDER_DIRECT_BYTES)break;
            length/=2;
        }while(length);
        if(!length)reject(@"render staging cannot fit chunk envelope");
        reply=rpc(chunkRequest,failure);
        if(![reply[@"token"] isEqual:token]||![reply[@"offset"] isEqual:@(offset+length)]){
            if(failure&&!*failure)*failure=error(@"render staging chunk acknowledgement");
            rpc(@{@"op":@"renderStageAbort",@"token":token},nil);return nil;
        }
        offset+=length;
    }
    reply=rpc(@{@"op":@"renderStageCommit",@"token":token},failure);
    if(reply&&(![reply[@"stagedBytes"] isEqual:@(data.length)]||![reply[@"stagedSHA256"] isEqual:@(hex)])){
        if(failure)*failure=error(@"render staging completion identity");return nil;
    }
    return reply;
}
