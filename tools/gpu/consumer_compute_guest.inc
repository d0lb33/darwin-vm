// Metal returns lazy, non-null empty descriptors. Reject active stage input or
// linking, not the presence of those default objects (native probe retained).
static BOOL DVMEmptyComputeStageInput(MTLStageInputOutputDescriptor *s){
    if(!s)return YES;
    if(s.indexBufferIndex||s.indexType!=MTLIndexTypeUInt16)return NO;
    for(unsigned i=0;i<31;i++)if(s.attributes[i].format!=MTLAttributeFormatInvalid||s.attributes[i].offset||s.attributes[i].bufferIndex||s.layouts[i].stride||s.layouts[i].stepFunction!=MTLStepFunctionPerVertex||s.layouts[i].stepRate!=1)return NO;
    return YES;
}
static BOOL DVMEmptyComputeLinks(MTLLinkedFunctions *l){
    return !l||(!l.functions.count&&!l.binaryFunctions.count&&!l.privateFunctions.count&&!l.groups.count);
}
static id<MTLComputePipelineState> DVMNewComputePipeline(DVMDevice *device,MTLComputePipelineDescriptor *d,MTLPipelineOption options,MTLComputePipelineReflection **reflection,NSError **err){
    if(reflection)*reflection=nil;
    if(!d||![(id)d.computeFunction isKindOfClass:DVMFunction.class]||d.computeFunction.device!=device||d.computeFunction.functionType!=MTLFunctionTypeKernel){if(err)*err=error(@"compute descriptor requires an owned kernel function");return nil;}
    MTLSize required=d.requiredThreadsPerThreadgroup;
    int (*report)(FILE *,const char *,...)=dlsym(RTLD_DEFAULT,"DVMReport");if(!report)report=fprintf;
    report(stderr,"GPU_LOAD_COMPUTE_DESCRIPTOR function=%.128s max=%lu multiple=%u required=%lu,%lu,%lu archives=%lu options=%lu stageInput=%u indirect=%u stack=%lu validation=%lu\n",d.computeFunction.name.UTF8String,(unsigned long)d.maxTotalThreadsPerThreadgroup,d.threadGroupSizeIsMultipleOfThreadExecutionWidth,(unsigned long)required.width,(unsigned long)required.height,(unsigned long)required.depth,(unsigned long)d.binaryArchives.count,(unsigned long)options,d.stageInputDescriptor!=nil,d.supportIndirectCommandBuffers,(unsigned long)d.maxCallStackDepth,(unsigned long)d.shaderValidation);
    // Native reflection is consumed by the backend validator. Fabricating an
    // Objective-C reflection object for the guest is outside this contract.
    if(options||reflection||!DVMEmptyComputeStageInput(d.stageInputDescriptor)||d.supportIndirectCommandBuffers||d.supportAddingBinaryFunctions||d.maxCallStackDepth!=1||d.shaderValidation!=MTLShaderValidationDefault||d.binaryArchives.count||d.preloadedLibraries.count||!DVMEmptyComputeLinks(d.linkedFunctions)){if(err)*err=error(@"compute descriptor options/archives/linking/reflection unsupported");return nil;}
    for(unsigned i=0;i<31;i++)if(d.buffers[i].mutability!=MTLMutabilityDefault){if(err)*err=error(@"compute buffer mutability unsupported");return nil;}
    DVMFunction *function=(id)d.computeFunction;
    NSDictionary *r=[device call:@{@"op":@"computePipeline",@"functionHandle":function.handle,@"maxThreads":@(d.maxTotalThreadsPerThreadgroup),@"multiple":@(d.threadGroupSizeIsMultipleOfThreadExecutionWidth),@"requiredThreads":@[@(required.width),@(required.height),@(required.depth)]} error:err];
    if(!r)return nil;
    DVMPipeline *p=[DVMPipeline new];p.owner=device;p.handle=r[@"handle"];p.function=function;p.label=d.label;p.descriptorBased=YES;p.requiredThreadsPerThreadgroup=required;
    p.threadExecutionWidth=[r[@"threadExecutionWidth"] unsignedIntegerValue];p.maxTotalThreadsPerThreadgroup=[r[@"maxTotalThreadsPerThreadgroup"] unsignedIntegerValue];p.staticThreadgroupMemoryLength=[r[@"staticThreadgroupMemoryLength"] unsignedIntegerValue];
    return p;
}

static void DVMRecordCompute(DVMEncoder *encoder,MTLSize groups,MTLSize threads){
    if(encoder.command.commands.count>=DVM_ORDERED_COMMANDS)reject(@"compute batch extent");
    NSMutableArray *textures=[NSMutableArray array],*buffers=[NSMutableArray array],*bytes=[NSMutableArray array],*scratch=[NSMutableArray array],*samplers=[NSMutableArray array];
    for(NSNumber *i in encoder.textures){DVMTexture *t=encoder.textures[i];[textures addObject:@[i,t.handle]];[encoder.command.resources addObject:t];}
    for(NSNumber *i in encoder.buffers){DVMBuffer *b=encoder.buffers[i][@"object"];[buffers addObject:@{@"index":i,@"buffer":b.handle,@"offset":encoder.buffers[i][@"offset"]}];[encoder.command.resources addObject:b];}
    for(NSNumber *i in encoder.constants)[bytes addObject:@{@"index":i,@"data":[encoder.constants[i] base64EncodedStringWithOptions:0]}];
    for(NSNumber *i in encoder.scratch)[scratch addObject:@{@"index":i,@"length":encoder.scratch[i]}];
    for(NSNumber *i in encoder.samplers){DVMSampler *s=encoder.samplers[i];[samplers addObject:@[i,s.handle]];[encoder.command.resources addObject:s];}
    [encoder.command.resources addObject:encoder.pipeline];
    [encoder.command.commands addObject:@{@"kind":@"compute",@"pipeline":encoder.pipeline.handle,@"textures":textures,@"buffers":buffers,@"bytes":bytes,@"samplers":samplers,@"threadgroupMemory":scratch,@"groups":@[@(groups.width),@(groups.height),@(groups.depth)],@"threads":@[@(threads.width),@(threads.height),@(threads.depth)]}];
}
