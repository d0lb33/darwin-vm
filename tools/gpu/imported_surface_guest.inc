// Called before the legacy fixed-pool path. Registration, alias creation and
// asynchronous last-alias retirement share the device's serial executor.
static id<MTLTexture> DVMImportSurface(DVMDevice *device,MTLTextureDescriptor *d,IOSurfaceRef s,NSUInteger plane){
    NSUInteger bytes=d.pixelFormat==MTLPixelFormatBGRA8Unorm?4:d.pixelFormat==MTLPixelFormatRGBA16Float?8:0;
    uint32_t fourcc=bytes==4?0x42475241:0x52476841;
    if(!d||!s||plane||!bytes||d.textureType!=MTLTextureType2D||!d.width||!d.height||
       d.width>DVM_TEXTURE_DIMENSION||d.height>DVM_TEXTURE_DIMENSION||d.depth!=1||d.arrayLength!=1||
       d.mipmapLevelCount!=1||d.sampleCount!=1||d.storageMode!=MTLStorageModeShared||!d.usage||(d.usage&~5ul)||
       d.compressionType!=MTLTextureCompressionTypeLossless||!DVMResourceOptionsValid(d.resourceOptions)||
       ([(id)d respondsToSelector:@selector(protectionOptions)]&&[(id<DVMProtectionMetadata>)d protectionOptions])||
       IOSurfaceGetPlaneCount(s)||IOSurfaceGetWidth(s)!=d.width||IOSurfaceGetHeight(s)!=d.height||
       IOSurfaceGetBytesPerElement(s)!=bytes||IOSurfaceGetPixelFormat(s)!=fourcc||!IOSurfaceGetBaseAddress(s)||
       IOSurfaceGetBytesPerRow(s)<d.width*bytes||IOSurfaceGetBytesPerRow(s)>65536||
       IOSurfaceGetAllocSize(s)<IOSurfaceGetBytesPerRow(s)*d.height){DVMTextureRejection("import-descriptor",d,s,plane);return nil;}
    __block DVMTexture *texture=nil;__block NSError *failure=nil;
    dispatch_sync(device.serial,^{
        if(device.importFailure||device.lastSubmissionError){failure=device.importFailure?:device.lastSubmissionError;return;}
        if(!device.importContract)device.importContract=device.transport(@{@"op":@"surfaceImportCaps"},&failure);
        NSDictionary *caps=device.importContract;
        if([caps[@"registry"] unsignedIntegerValue]!=1||![caps[@"formats"] containsObject:@(d.pixelFormat)]||
           IOSurfaceGetAllocSize(s)>[caps[@"maxBytes"] unsignedLongLongValue]){failure=failure?:error(@"surface import capability contract");return;}
        id<DVMMetalImportedMapping> mapping=device.surfaceProvider(s,&failure);
        if(!mapping||mapping.mappingVersion!=2||mapping.retired||!mapping.resourceID||
           mapping.bytes!=IOSurfaceGetBaseAddress(s)||mapping.length!=IOSurfaceGetAllocSize(s)){
            failure=failure?:error(@"surface registration identity/extent");
            if(mapping){if(!device.quarantinedImports)device.quarantinedImports=[NSMutableArray array];[device.quarantinedImports addObject:mapping];}
            device.importFailure=failure;
            return;
        }
        NSDictionary *reply=device.transport(@{@"op":@"surfaceImport",@"id":@(mapping.resourceID),@"width":@(d.width),
            @"height":@(d.height),@"row":@(IOSurfaceGetBytesPerRow(s)),@"format":@(d.pixelFormat),
            @"length":@(mapping.length),@"usage":@(d.usage)},&failure);
        if(!reply||[reply[@"id"] unsignedLongLongValue]!=mapping.resourceID||![reply[@"handle"] unsignedLongLongValue]){
            if(!device.quarantinedImports)device.quarantinedImports=[NSMutableArray array];[device.quarantinedImports addObject:mapping];
            device.lastSubmissionError=failure?:error(@"surface host import failed; pages quarantined");device.importFailure=device.lastSubmissionError;return;
        }
        texture=[DVMTexture new];texture.owner=device;texture.handle=reply[@"handle"];
        texture.width=d.width;texture.height=d.height;texture.depth=1;texture.mipLevels=1;texture.textureType=d.textureType;
        texture.pixelFormat=d.pixelFormat;texture.usage=d.usage;texture.acceptedOptions=d.resourceOptions;
        texture.hostAllocatedSize=[reply[@"allocatedSize"] unsignedIntegerValue];texture.surfaceObject=(__bridge id)s;
        texture.surfaceMapping=mapping;texture.retirementMapping=mapping;
    });
    if(!texture)DVMTextureRejection("import-contract",d,s,plane);
    return texture;
}
