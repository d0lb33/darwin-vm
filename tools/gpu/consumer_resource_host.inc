// Linear textures alias a real host MTLBuffer. Alignment is negotiated for this
// implemented allocation operation, never used to advertise a GPU family.
static NSUInteger LinearAlignment(DVMHost *host,MTLPixelFormat format) {
    if(!DVMFormatBytes(format))return 0;
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
