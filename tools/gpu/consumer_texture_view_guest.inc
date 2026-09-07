// View identity is distinct; data and residency belong to the parent allocation.
static id<MTLTexture> DVMNewTextureView(DVMTexture *parent,MTLPixelFormat format,MTLTextureType type,NSRange levels,NSRange slices){
    [parent requireResident];
    int (*report)(FILE *,const char *,...)=dlsym(RTLD_DEFAULT,"DVMReport");if(!report)report=fprintf;
    report(stderr,"GPU_LOAD_TEXTURE_VIEW parent=%llu format=%lu type=%lu levels=%lu,%lu slices=%lu,%lu source=%lux%lu sourceLevels=%lu storage=%lu\n",parent.handle.unsignedLongLongValue,(unsigned long)format,(unsigned long)type,(unsigned long)levels.location,(unsigned long)levels.length,(unsigned long)slices.location,(unsigned long)slices.length,(unsigned long)parent.width,(unsigned long)parent.height,(unsigned long)parent.mipmapLevelCount,(unsigned long)parent.storageMode);
    if(format!=parent.pixelFormat||type!=MTLTextureType2D||type!=parent.textureType||slices.location||slices.length!=1||!levels.length||levels.location>=parent.mipmapLevelCount||levels.length>parent.mipmapLevelCount-levels.location)return nil;
    NSError *error=nil;NSDictionary *r=[parent.owner call:@{@"op":@"textureView",@"texture":parent.handle,@"format":@(format),@"type":@(type),@"level":@(levels.location),@"levels":@(levels.length),@"slice":@(slices.location),@"slices":@(slices.length)} error:&error];
    if(!r)return nil;
    DVMTexture *view=[DVMTexture new];view.owner=parent.owner;view.handle=r[@"handle"];view.viewParent=parent;view.viewLevel=levels.location;
    view.width=MAX((NSUInteger)1,parent.width>>levels.location);view.height=MAX((NSUInteger)1,parent.height>>levels.location);view.depth=1;view.mipLevels=levels.length;
    view.textureType=type;view.pixelFormat=format;view.usage=parent.usage;view.acceptedOptions=parent.acceptedOptions;view.hostAllocatedSize=[r[@"allocatedSize"] unsignedIntegerValue];
    view.backingBuffer=parent.backingBuffer;view.backingOffset=parent.backingOffset;view.backingRow=parent.backingRow;view.surfaceObject=parent.surfaceObject;view.surfaceMapping=parent.surfaceMapping;
    view.residencyState=[r[@"purgeableState"] unsignedIntegerValue];
    if(view.residencyState<2||view.residencyState>4||![r[@"width"] isEqual:@(view.width)]||![r[@"height"] isEqual:@(view.height)]||![r[@"levels"] isEqual:@(view.mipLevels)]||![r[@"parentLevel"] isEqual:@(levels.location)]||![r[@"parentSlice"] isEqual:@0])reject(@"native texture view metadata contract");
    return view;
}

// Native view hints can be no-ops while the root reports Volatile. Preserve
// both answers: per-view API metadata and the allocation's safe-reuse gate.
static MTLPurgeableState DVMTextureViewPurgeable(DVMTexture *view,MTLPurgeableState state){
    if(state<1||state>4)reject(@"texture view purgeability state");
    if(view.surfaceMapping&&state>MTLPurgeableStateNonVolatile)reject(@"pinned IOSurface volatility requires native retirement");
    DVMResource *root=view.purgeabilityRoot;__block NSDictionary *reply=nil;__block NSError *failure=nil;
    dispatch_sync(view.owner.serial,^{
      BOOL sent=NO;
      @try {
        if(root.purgeabilityFailed){failure=error(@"uncertain parent purgeability");return;}
        sent=YES;
        reply=view.owner.transport(@{@"op":@"resourcePurgeable",@"handle":view.handle,@"state":@(state)},&failure);
        NSUInteger old=[reply[@"previous"] unsignedIntegerValue],current=[reply[@"current"] unsignedIntegerValue],rootOld=[reply[@"rootPrevious"] unsignedIntegerValue],rootCurrent=[reply[@"rootCurrent"] unsignedIntegerValue];
        if(![reply[@"ok"] boolValue]||![reply[@"handle"] isEqual:view.handle]||![reply[@"root"] isEqual:root.handle]||![reply[@"requested"] isEqual:@(state)]||old<2||old>4||current<2||current>4||rootOld<2||rootOld>4||rootCurrent<2||rootCurrent>4){root.purgeabilityFailed=YES;reply=nil;return;}
        [root purgeabilityChanged:rootOld current:rootCurrent];view.residencyState=current;
      } @catch(NSException *exception){if(sent)root.purgeabilityFailed=YES;reply=nil;failure=error(exception.reason);}
    });
    if(!reply)reject(failure.description?:@"texture view purgeability acknowledgement");
    return [reply[@"previous"] unsignedIntegerValue];
}
