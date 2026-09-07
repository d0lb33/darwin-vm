// One owned, bounded transaction. Partial bytes never reach command validation
// or execution. While staging, resource mutation/release and other submissions
// are refused. The existing full render validator is the only execution gate.
static NSDictionary *RenderStage(DVMHost *host,uint64_t seq,NSDictionary *r){
    NSString *op=r[@"op"];
    if([op isEqual:@"renderStageBegin"]){
        uint64_t length;NSString *sha=r[@"sha256"];
        if(host.renderStage)return HostError(seq,EBUSY,@"render staging already active");
        if(!Number(r[@"length"],&length)||!length||length>DVM_RENDER_REQUEST_BYTES||
           ![sha isKindOfClass:NSString.class]||sha.length!=64||
           [sha rangeOfCharacterFromSet:[[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet]].location!=NSNotFound)
            return HostError(seq,EINVAL,@"render staging length/hash");
        if(host.renderStageToken==UINT64_MAX)return HostError(seq,EOVERFLOW,@"render staging token exhausted");
        host.renderStage=[NSMutableData dataWithCapacity:length];host.renderStageLength=length;
        host.renderStageSHA=sha;host.renderStageToken++;
        return @{@"seq":@(seq),@"ok":@YES,@"token":@(host.renderStageToken),@"offset":@0};
    }
    uint64_t token;
    if(!host.renderStage||!Number(r[@"token"],&token)||token!=host.renderStageToken)
        return HostError(seq,EINVAL,@"render staging token");
    if([op isEqual:@"renderStageAbort"]){
        host.renderStage=nil;host.renderStageSHA=nil;host.renderStageLength=0;
        return @{@"seq":@(seq),@"ok":@YES,@"token":@(token)};
    }
    if([op isEqual:@"renderStageChunk"]){
        uint64_t offset;NSData *data=DVMBPayload(r[@"payload"]);
        if(!Number(r[@"offset"],&offset)||offset!=host.renderStage.length||!data.length||
           data.length>DVM_RENDER_REQUEST_CHUNK||data.length>host.renderStageLength-offset)
            return HostError(seq,EINVAL,@"render staging chunk order/extent");
        [host.renderStage appendData:data];
        return @{@"seq":@(seq),@"ok":@YES,@"token":@(token),@"offset":@(host.renderStage.length)};
    }
    if(![op isEqual:@"renderStageCommit"])return HostError(seq,EINVAL,@"render staging operation");
    if(host.renderStage.length!=host.renderStageLength)return HostError(seq,EINVAL,@"incomplete render staging bytes");
    NSData *data=host.renderStage;NSString *sha=host.renderStageSHA;
    host.renderStage=nil;host.renderStageSHA=nil;host.renderStageLength=0;
    if(![HexDigest(data) isEqual:sha])return HostError(seq,EINVAL,@"render staging hash mismatch");
    id decoded=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if(![decoded isKindOfClass:NSDictionary.class]||![decoded[@"op"] isEqual:@"renderSubmit"]||decoded[@"seq"])
        return HostError(seq,EINVAL,@"render staging requires unsequenced renderSubmit");
    NSMutableDictionary *reply=[ProcessRequest(host,seq,decoded) mutableCopy];
    reply[@"stagedBytes"]=@(data.length);reply[@"stagedSHA256"]=sha;return reply;
}
