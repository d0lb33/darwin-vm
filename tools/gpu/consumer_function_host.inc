static NSDictionary *SpecializedFunction(DVMHost *host,uint64_t seq,NSDictionary *r) {
    DVMEntry *lib=Entry(host,r[@"library"],@"library");NSArray *constants=r[@"constants"];
    if(!lib||![r[@"name"] isKindOfClass:NSString.class]||![r[@"specialized"] isKindOfClass:NSString.class]||[r[@"specialized"] length]>128||![constants isKindOfClass:NSArray.class]||constants.count>256)return HostError(seq,EINVAL,@"function descriptor");
    MTLFunctionConstantValues *values=[MTLFunctionConstantValues new];NSMutableSet *seen=[NSMutableSet set];
    for(NSDictionary *c in constants){
        uint64_t type;if(![c isKindOfClass:NSDictionary.class]||!Number(c[@"type"],&type)||!c[@"indexed"]||CFGetTypeID((__bridge CFTypeRef)c[@"indexed"])!=CFBooleanGetTypeID())return HostError(seq,EINVAL,@"constant descriptor");
        NSData *data=DVMBPayload(c[@"data"]);NSUInteger size=DVMConstantBytes(type);BOOL indexed=[c[@"indexed"] boolValue];id key=c[@"key"];
        if(!size||data.length!=size||(indexed?!RenderUInt(key,65535):(![key isKindOfClass:NSString.class]||![key length]||[key length]>128))||[seen containsObject:key])return HostError(seq,EINVAL,@"constant type/bytes/key");
        [seen addObject:key];if(indexed)[values setConstantValue:data.bytes type:type atIndex:[key unsignedIntegerValue]];else [values setConstantValue:data.bytes type:type withName:key];
    }
    MTLFunctionDescriptor *d=[MTLFunctionDescriptor functionDescriptor];d.name=r[@"name"];d.constantValues=values;
    if([r[@"specialized"] length])d.specializedName=r[@"specialized"];
    NSError *e=nil;id<MTLFunction> f=[lib.object newFunctionWithDescriptor:d error:&e];
    if(!f)return NSErrorReply(seq,e,@"function specialization");DVMEntry *entry;
    if(!Add(host,@"function",f,&entry))return HostError(seq,ENOSPC,@"function state table");
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"type":@(f.functionType)};
}
