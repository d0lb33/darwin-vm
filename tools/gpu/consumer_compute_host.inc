// General direct compute, validated from native pipeline reflection. No shader
// name whitelist. Data-dependent shader accesses remain the application's Metal
// responsibility; reflection proves bindings/types, not arbitrary array indices.
static NSDictionary *ComputePipeline(DVMHost *host,uint64_t seq,NSDictionary *r) {
    DVMEntry *function=Entry(host,r[@"functionHandle"],@"function");
    uint64_t maximum;NSArray *required=r[@"requiredThreads"];
    if(!function||[(id<MTLFunction>)function.object functionType]!=MTLFunctionTypeKernel||
       !Number(r[@"maxThreads"],&maximum)||maximum>DVM_COMPUTE_THREADS||
       ![r[@"multiple"] isKindOfClass:NSNumber.class]||CFGetTypeID((__bridge CFTypeRef)r[@"multiple"])!=CFBooleanGetTypeID()||
       !RenderNumbers(required,3))return HostError(seq,EINVAL,@"compute descriptor ownership/shape");
    uint64_t product=1;BOOL zero=[required[0] isEqual:@0]&&[required[1] isEqual:@0]&&[required[2] isEqual:@0];
    for(id n in required){uint64_t v;if(!Number(n,&v)||(!zero&&(!v||v>DVM_COMPUTE_THREADS/product)))return HostError(seq,EINVAL,@"required compute threads");if(!zero)product*=v;}
    if(!zero&&maximum&&product>maximum)return HostError(seq,EINVAL,@"required threads exceed descriptor maximum");
    MTLComputePipelineDescriptor *d=[MTLComputePipelineDescriptor new];d.computeFunction=function.object;
    d.maxTotalThreadsPerThreadgroup=maximum;d.threadGroupSizeIsMultipleOfThreadExecutionWidth=[r[@"multiple"] boolValue];
    d.requiredThreadsPerThreadgroup=MTLSizeMake([required[0] unsignedIntegerValue],[required[1] unsignedIntegerValue],[required[2] unsignedIntegerValue]);
    NSError *error=nil;MTLComputePipelineReflection *reflection=nil;
    id<MTLComputePipelineState> state=[host.device newComputePipelineStateWithDescriptor:d options:MTLPipelineOptionArgumentInfo reflection:&reflection error:&error];
    if(!state)return NSErrorReply(seq,error,@"compute descriptor pipeline");
    if(!reflection)return HostError(seq,ENOTSUP,@"native compute reflection unavailable");
    if(state.staticThreadgroupMemoryLength>DVM_COMPUTE_MEMORY)return HostError(seq,ENOTSUP,@"static threadgroup memory exceeds transport profile");
    NSMutableArray *bindings=[NSMutableArray array];
    for(MTLArgument *arg in reflection.arguments)if(arg.active){
        if(arg.index>=DVM_COMPUTE_BINDINGS||arg.arrayLength>1||arg.access>MTLArgumentAccessWriteOnly||
           (arg.type!=MTLArgumentTypeBuffer&&arg.type!=MTLArgumentTypeTexture&&arg.type!=MTLArgumentTypeSampler&&arg.type!=MTLArgumentTypeThreadgroupMemory)||
           (arg.type==MTLArgumentTypeTexture&&arg.textureType!=MTLTextureType1D&&arg.textureType!=MTLTextureType2D&&arg.textureType!=MTLTextureType3D))
            return HostError(seq,ENOTSUP,[NSString stringWithFormat:@"compute reflection outside contract: %@ type=%lu index=%lu array=%lu",arg.name,(unsigned long)arg.type,(unsigned long)arg.index,(unsigned long)arg.arrayLength]);
        [bindings addObject:@[@(arg.type),@(arg.index),@(arg.access),@(arg.type==MTLArgumentTypeBuffer?arg.bufferDataSize:arg.type==MTLArgumentTypeThreadgroupMemory?arg.threadgroupMemoryDataSize:0),@(arg.type==MTLArgumentTypeBuffer?arg.bufferAlignment:arg.type==MTLArgumentTypeThreadgroupMemory?arg.threadgroupMemoryAlignment:1),@(arg.type==MTLArgumentTypeTexture?arg.textureType:0)]];
    }
    NSUInteger threads=MIN((NSUInteger)DVM_COMPUTE_THREADS,state.maxTotalThreadsPerThreadgroup);
    DVMEntry *entry;if(!Add(host,@"computePipeline",@{@"state":state,@"bindings":bindings,@"required":required,@"multiple":r[@"multiple"],@"threads":@(threads)},&entry))return HostError(seq,ENOSPC,@"compute pipeline table");
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"threadExecutionWidth":@(state.threadExecutionWidth),@"maxTotalThreadsPerThreadgroup":@(threads),@"staticThreadgroupMemoryLength":@(state.staticThreadgroupMemoryLength),@"bindings":bindings};
}

static NSString *EncodeCompute(DVMHost *host,id<MTLCommandBuffer> cb,NSDictionary *pass,NSMutableSet *written,NSMutableSet *sharedTargets) {
    DVMEntry *pipeline=Entry(host,pass[@"pipeline"],@"computePipeline");
    NSArray *groups=pass[@"groups"],*threads=pass[@"threads"],*tex=pass[@"textures"],*buf=pass[@"buffers"],*bytes=pass[@"bytes"],*samplers=pass[@"samplers"],*scratch=pass[@"threadgroupMemory"];
    if(!pipeline||!RenderNumbers(groups,3)||!RenderNumbers(threads,3))return @"compute pipeline/geometry shape";
    uint64_t total=1,groupThreads=1;
    for(unsigned i=0;i<3;i++){
        uint64_t g,t;if(!Number(groups[i],&g)||!Number(threads[i],&t)||!g||!t||g>4096||t>[pipeline.object[@"threads"] unsignedIntegerValue]/groupThreads)return @"compute dispatch geometry";
        groupThreads*=t;
        if(g>DVM_COMPUTE_INVOCATIONS/total)return @"compute dispatch work budget";total*=g;
    }
    if(groupThreads>DVM_COMPUTE_INVOCATIONS/total)return @"compute dispatch work budget";
    id<MTLComputePipelineState> state=pipeline.object[@"state"];
    if([pipeline.object[@"multiple"] boolValue]&&groupThreads%state.threadExecutionWidth)return @"compute execution-width promise";
    NSArray *required=pipeline.object[@"required"];
    if(![required[0] isEqual:@0]&&![required isEqual:threads])return @"compute required threadgroup shape";
    for(id array in @[tex?:NSNull.null,buf?:NSNull.null,bytes?:NSNull.null,samplers?:NSNull.null,scratch?:NSNull.null])if(![array isKindOfClass:NSArray.class]||[array count]>DVM_COMPUTE_BINDINGS)return @"compute binding array extent";
    NSMutableDictionary *textures=[NSMutableDictionary dictionary],*buffers=[NSMutableDictionary dictionary],*inlineBytes=[NSMutableDictionary dictionary],*sampling=[NSMutableDictionary dictionary],*memory=[NSMutableDictionary dictionary];
    for(NSArray *binding in tex){
        if(!RenderNumbers(binding,2)||!RenderUInt(binding[0],DVM_COMPUTE_BINDINGS-1)||textures[binding[0]])return @"compute texture index";
        DVMEntry *e=Entry(host,binding[1],@"texture");
        if(!e||e.textureUpload||e.importedPages.failed||(e.sharedRender&&e.sharedRender.state!=DVMSharedAcquired))return @"compute texture ownership";textures[binding[0]]=e;
    }
    for(NSDictionary *binding in buf){
        if(![binding isKindOfClass:NSDictionary.class]||!RenderUInt(binding[@"index"],DVM_COMPUTE_BINDINGS-1)||buffers[binding[@"index"]])return @"compute buffer index";
        DVMEntry *e=Entry(host,binding[@"buffer"],@"buffer");uint64_t offset;
        if(!e||!Number(binding[@"offset"],&offset)||offset>=e.textureBytes||offset%DVM_BUFFER_BINDING_ALIGNMENT)return @"compute buffer bounds";
        buffers[binding[@"index"]]=@[e,@(offset)];
    }
    for(NSDictionary *binding in bytes){
        if(![binding isKindOfClass:NSDictionary.class]||!RenderUInt(binding[@"index"],DVM_COMPUTE_BINDINGS-1)||buffers[binding[@"index"]]||inlineBytes[binding[@"index"]])return @"compute inline index";
        NSData *data=DVMBPayload(binding[@"data"]);if(!data.length||data.length>DVM_INLINE_BYTES)return @"compute inline extent";inlineBytes[binding[@"index"]]=data;
    }
    for(NSArray *binding in samplers){
        if(!RenderNumbers(binding,2)||!RenderUInt(binding[0],DVM_COMPUTE_BINDINGS-1)||sampling[binding[0]])return @"compute sampler index";
        DVMEntry *e=Entry(host,binding[1],@"sampler");if(!e)return @"compute sampler ownership";sampling[binding[0]]=e;
    }
    NSUInteger used=state.staticThreadgroupMemoryLength;
    for(NSDictionary *binding in scratch){
        uint64_t length;
        if(![binding isKindOfClass:NSDictionary.class]||!RenderUInt(binding[@"index"],DVM_COMPUTE_BINDINGS-1)||memory[binding[@"index"]]||!Number(binding[@"length"],&length)||!length||length%16||length>DVM_COMPUTE_MEMORY-used||length>host.device.maxThreadgroupMemoryLength-used)return @"compute threadgroup memory bounds";
        used+=length;memory[binding[@"index"]]=@(length);
    }
    for(NSArray *need in pipeline.object[@"bindings"]){
        NSUInteger type=[need[0] unsignedIntegerValue],access=[need[2] unsignedIntegerValue],size=[need[3] unsignedIntegerValue],alignment=MAX((NSUInteger)1,[need[4] unsignedIntegerValue]);id index=need[1];
        if(type==MTLArgumentTypeBuffer){
            NSArray *b=buffers[index];NSData *data=inlineBytes[index];
            if(b){DVMEntry *e=b[0];NSUInteger offset=[b[1] unsignedIntegerValue];if(offset%alignment||e.textureBytes-offset<size)return @"compute reflected buffer extent/alignment";if(access!=MTLArgumentAccessReadOnly)[written addObject:@(e.handle)];}
            else if(!data||data.length<size||access!=MTLArgumentAccessReadOnly)return @"compute missing/read-write inline buffer";
        }else if(type==MTLArgumentTypeTexture){
            DVMEntry *e=textures[index];NSUInteger needed=access==MTLArgumentAccessReadOnly?1:access==MTLArgumentAccessWriteOnly?2:3;
            if(!e||[(id<MTLTexture>)e.object textureType]!=[need[5] unsignedIntegerValue]||([(id<MTLTexture>)e.object usage]&needed)!=needed)return @"compute reflected texture type/usage";
            if(e.sharedRender&&needed&2)[sharedTargets addObject:e.sharedRender];
        }else if(type==MTLArgumentTypeSampler){if(!sampling[index])return @"compute missing sampler";}
        else if(!memory[index]||[memory[index] unsignedIntegerValue]<size||[memory[index] unsignedIntegerValue]%alignment)return @"compute reflected threadgroup memory";
    }
    // Writable buffers aliased as linear textures require a tested cache barrier.
    for(DVMEntry *e in textures.allValues)if(e.parent&&[written containsObject:@(e.parent.handle)])return @"compute writable buffer/texture alias";
    id<MTLComputeCommandEncoder> encoder=[cb computeCommandEncoder];[encoder setComputePipelineState:state];
    for(NSNumber *i in buffers){NSArray *b=buffers[i];[encoder setBuffer:((DVMEntry *)b[0]).object offset:[b[1] unsignedIntegerValue] atIndex:i.unsignedIntegerValue];}
    for(NSNumber *i in inlineBytes){NSData *d=inlineBytes[i];[encoder setBytes:d.bytes length:d.length atIndex:i.unsignedIntegerValue];}
    for(NSNumber *i in textures)[encoder setTexture:((DVMEntry *)textures[i]).object atIndex:i.unsignedIntegerValue];
    for(NSNumber *i in sampling)[encoder setSamplerState:((DVMEntry *)sampling[i]).object atIndex:i.unsignedIntegerValue];
    for(NSNumber *i in memory)[encoder setThreadgroupMemoryLength:[memory[i] unsignedIntegerValue] atIndex:i.unsignedIntegerValue];
    [encoder dispatchThreadgroups:MTLSizeMake([groups[0] unsignedIntegerValue],[groups[1] unsignedIntegerValue],[groups[2] unsignedIntegerValue]) threadsPerThreadgroup:MTLSizeMake([threads[0] unsignedIntegerValue],[threads[1] unsignedIntegerValue],[threads[2] unsignedIntegerValue])];
    [encoder endEncoding];return nil;
}
