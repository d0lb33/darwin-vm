// Linear textures alias a real host MTLBuffer. Alignment is negotiated for this
// implemented allocation operation, never used to advertise a GPU family.
static NSDictionary *ResourcePurgeable(DVMHost *host,uint64_t seq,NSDictionary *r){
    uint64_t handle,state;
    if(!Number(r[@"handle"],&handle)||!Number(r[@"state"],&state)||state<1||state>4)return HostError(seq,EINVAL,@"purgeability handle/state");
    DVMEntry *entry=host.entries[@(handle)];
    if(!entry||(![entry.kind isEqual:@"buffer"]&&![entry.kind isEqual:@"texture"]))return HostError(seq,EINVAL,@"purgeability resource ownership");
    DVMEntry *root=ResourceRoot(entry);
    if(root.textureUpload)return HostError(seq,EBUSY,@"purgeability during partial texture upload");
    BOOL pinned=entry.sharedRender||entry.importedPages;
    if(pinned&&state>MTLPurgeableStateNonVolatile)return HostError(seq,ENOTSUP,@"pinned IOSurface volatility requires native surface/pin retirement");
    MTLPurgeableState old=MTLPurgeableStateNonVolatile,current=MTLPurgeableStateNonVolatile,rootPrevious=root.purgeableState,rootCurrent=root.purgeableState;
    if(!pinned){
        // The worker serves one request at a time, after prior GPU completion.
        // Native allocation owns discard/reacquire, not a fabricated state bit.
        DVMEntry *nativeTarget=entry.textureView?entry:root;
        rootPrevious=[(id<MTLResource>)root.object setPurgeableState:MTLPurgeableStateKeepCurrent];
        old=[(id<MTLResource>)nativeTarget.object setPurgeableState:state];
        current=[(id<MTLResource>)nativeTarget.object setPurgeableState:MTLPurgeableStateKeepCurrent];
        rootCurrent=[(id<MTLResource>)root.object setPurgeableState:MTLPurgeableStateKeepCurrent];
        if(old<2||old>4||current<2||current>4||rootPrevious<2||rootPrevious>4||rootCurrent<2||rootCurrent>4){root.purgeableState=MTLPurgeableStateEmpty;return HostError(seq,EIO,@"native purgeability state contract");}
    }
    root.purgeableState=rootCurrent;
    for(DVMEntry *alias in host.entries.allValues)if(ResourceRoot(alias)==root&&!alias.textureView)alias.purgeableState=rootCurrent;
    if(entry.textureView)entry.purgeableState=current;
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(handle),@"root":@(root.handle),@"requested":@(state),@"previous":@(old),@"current":@(current),@"rootPrevious":@(rootPrevious),@"rootCurrent":@(rootCurrent)};
}
static NSUInteger LinearAlignment(DVMHost *host,MTLPixelFormat format) {
    if(!DVMFormatBytes(format)||DVM1DFormat(format))return 0;
    NSUInteger a=[host.device minimumLinearTextureAlignmentForPixelFormat:format];
    return a&&a<=4096&&!(a&(a-1))?MAX(a,16):0;
}
static NSDictionary *LinearLayout(DVMHost *host,uint64_t seq,NSDictionary *r) {
    uint64_t format;if(!Number(r[@"format"],&format))return HostError(seq,EINVAL,@"linear format");
    NSUInteger alignment=LinearAlignment(host,format);
    if(!alignment)return HostError(seq,ENOTSUP,@"unsupported linear format/alignment");
    return @{@"seq":@(seq),@"ok":@YES,@"alignment":@(alignment),@"format":@(format)};
}
static NSDictionary *LinearTexture(DVMHost *host,uint64_t seq,NSDictionary *r) {
    uint64_t w,h,format,offset,row;MTLTextureUsage usage;
    DVMEntry *parent=Entry(host,r[@"buffer"],@"buffer");
    if(!parent||!Number(r[@"width"],&w)||!Number(r[@"height"],&h)||!w||!h||w>DVM_TEXTURE_DIMENSION||h>DVM_TEXTURE_DIMENSION||
       !Number(r[@"format"],&format)||!Number(r[@"offset"],&offset)||!Number(r[@"row"],&row)||!Usage(r[@"usage"],&usage))
        return HostError(seq,EINVAL,@"linear descriptor");
    if(usage!=MTLTextureUsageShaderRead)return HostError(seq,ENOTSUP,@"linear views are read-only GPU inputs");
    NSUInteger alignment=LinearAlignment(host,format),bpp=DVMFormatBytes(format);
    if(!alignment||offset%alignment||row%alignment||row<w*bpp||row>DVM_BUFFER_BYTES||
       offset>parent.textureBytes||row*h>parent.textureBytes-offset||w*h*bpp>DVM_TEXTURE_BYTES||
       w*h*bpp>kMaxTextures-host.textureBytes)return HostError(seq,EINVAL,@"linear extent/alignment");
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:w height:h mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=usage;
    id<MTLTexture> t=[(id<MTLBuffer>)parent.object newTextureWithDescriptor:d offset:offset bytesPerRow:row];
    DVMEntry *entry;if(!t||!Add(host,@"texture",t,&entry))return HostError(seq,ENOMEM,@"linear allocation");
    entry.parent=parent;entry.width=w;entry.height=h;entry.row=w*bpp;entry.format=format;
    entry.textureBytes=w*h*bpp;host.textureBytes+=entry.textureBytes;
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"allocatedSize":@(t.allocatedSize)};
}

// Same-format, same-type 2D mip ranges preserve native allocation identity.
// Reinterpretation, arrays, planes and swizzles require separate contracts.
static NSDictionary *TextureView(DVMHost *host,uint64_t seq,NSDictionary *r){
    DVMEntry *parent=Entry(host,r[@"texture"],@"texture");id<MTLTexture> source=parent.object;
    uint64_t format,type,level,levels,slice,slices;
    if(!parent||!Number(r[@"format"],&format)||!Number(r[@"type"],&type)||!Number(r[@"level"],&level)||!Number(r[@"levels"],&levels)||!Number(r[@"slice"],&slice)||!Number(r[@"slices"],&slices)||
       type!=MTLTextureType2D||type!=source.textureType||format!=source.pixelFormat||slice||slices!=1||!levels||level>=source.mipmapLevelCount||levels>source.mipmapLevelCount-level)
        return HostError(seq,ENOTSUP,@"texture view format/type/subresource contract");
    if(ResourceRoot(parent).textureUpload||parent.importedPages.failed||(parent.sharedRender&&parent.sharedRender.state!=DVMSharedIdle&&parent.sharedRender.state!=DVMSharedAcquired))return HostError(seq,EBUSY,@"texture view parent ownership");
    id<MTLTexture> view=[source newTextureViewWithPixelFormat:format textureType:type levels:NSMakeRange(level,levels) slices:NSMakeRange(0,1)];
    DVMEntry *entry=nil;if(!view||!Add(host,@"texture",view,&entry))return HostError(seq,ENOMEM,@"native texture view allocation");
    entry.parent=parent;entry.textureView=YES;entry.viewBaseLevel=level;entry.format=format;
    entry.width=view.width;entry.height=view.height;entry.row=view.width*DVMFormatBytes(format);entry.textureBytes=entry.row*entry.height;
    entry.importedPages=parent.importedPages;entry.sharedRender=parent.sharedRender;
    entry.purgeableState=ResourceRoot(parent).purgeableState;
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"allocatedSize":@(view.allocatedSize),@"width":@(view.width),@"height":@(view.height),@"levels":@(view.mipmapLevelCount),@"parentLevel":@(view.parentRelativeLevel),@"parentSlice":@(view.parentRelativeSlice)};
}
