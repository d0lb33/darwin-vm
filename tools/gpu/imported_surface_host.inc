static NSDictionary *ImportedSurfaceRequest(DVMHost *host,uint64_t seq,NSDictionary *r){
    if([r[@"op"] isEqual:@"surfaceImportCaps"]){
        int fd=DVMOpenSurfaceRegistry();if(fd<0)return HostError(seq,ENOTSUP,@"surface registry unavailable");close(fd);
        return @{@"seq":@(seq),@"ok":@YES,@"registry":@1,@"formats":@[@80,@115],@"maxBytes":@(DVM_SURFACE_MAX_BYTES),@"maxLiveBytes":@(DVM_SURFACE_TOTAL_BYTES)};
    }
    uint64_t resourceID,width,height,row,format,length,usage;
    if(!Number(r[@"id"],&resourceID)||!Number(r[@"width"],&width)||!Number(r[@"height"],&height)||!Number(r[@"row"],&row)||
       !Number(r[@"format"],&format)||!Number(r[@"length"],&length)||!Number(r[@"usage"],&usage)||!width||!height||
       width>DVM_TEXTURE_DIMENSION||height>DVM_TEXTURE_DIMENSION||row>65536||!length||length>DVM_SURFACE_MAX_BYTES||
       (format!=80&&format!=115)||!usage||(usage&~5ull)||row<width*DVMFormatBytes(format)||row*height>length)
        return HostError(seq,EINVAL,@"imported surface descriptor/byte range");
    if(host.entries.count>=kMaxObjects)return HostError(seq,ENOSPC,@"object table full before import");
    if(!host.imports)host.imports=[NSMutableDictionary dictionary];
    DVMImportedPages *mapping=host.imports[@(resourceID)];NSString *why=nil;
    uint64_t importStart=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW),mapDone=importStart,bufferDone=importStart;
    if(!mapping){
        mapping=DVMOpenImportedPages(resourceID,&why);if(!mapping)return HostError(seq,EINVAL,why);
        // Failed imports remain held; no unpin permission on uncertain state.
        host.imports[@(resourceID)]=mapping;host.importedBytes+=mapping.span;
    }
    mapDone=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    if(mapping.retired||mapping.failed||mapping.length!=length||host.importedBytes>DVM_SURFACE_TOTAL_BYTES)return HostError(seq,EINVAL,@"registered surface state/length/budget");
    NSUInteger alignment=[host.device minimumLinearTextureAlignmentForPixelFormat:format];
    if(!alignment||row%alignment||mapping.offset%alignment)return HostError(seq,ENOTSUP,@"imported surface native linear alignment");
    if(!mapping.buffer)mapping.buffer=[host.device newBufferWithBytesNoCopy:mapping.mapping length:mapping.span options:MTLResourceStorageModeShared deallocator:nil];
    if(!mapping.buffer||mapping.buffer.contents!=mapping.mapping)return HostError(seq,ENOMEM,@"imported no-copy backing buffer");
    bufferDone=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format width:width height:height mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=usage;
    id<MTLTexture> texture=[mapping.buffer newTextureWithDescriptor:d offset:mapping.offset bytesPerRow:row];
    DVMEntry *entry=nil;if(!texture||!Add(host,@"texture",texture,&entry))return HostError(seq,ENOMEM,@"imported native texture");
    entry.importedPages=mapping;entry.width=width;entry.height=height;entry.row=row;entry.format=format;
    // Registration backing is counted once, separately from copied resources.
    uint64_t textureDone=clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW);
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle),@"id":@(resourceID),@"allocatedSize":@(texture.allocatedSize),@"mappedBytes":@(mapping.span),
        @"map_us":@((mapDone-importStart)/1000),@"buffer_us":@((bufferDone-mapDone)/1000),@"texture_us":@((textureDone-bufferDone)/1000)};
}
