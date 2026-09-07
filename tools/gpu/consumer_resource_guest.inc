// Logical allocation metadata belongs to this forwarding resource. Host heap,
// GPU addresses and storage options must never leak into the guest contract.
@interface DVMResource : DVMObject
@property(nonatomic) MTLResourceOptions acceptedOptions;
@property(nonatomic) NSUInteger hostAllocatedSize;
@property(nonatomic) int32_t responsibleProcess;
@end
@implementation DVMResource
- (void)setResponsibleProcess:(int32_t)process {
    NSError *e=nil;NSNumber *bits=@((uint32_t)process);
    NSDictionary *r=[self.owner call:@{@"op":@"resourceProcess",@"handle":self.handle,@"processBits":bits} error:&e];
    if(![r[@"handle"] isEqual:self.handle]||![r[@"processBits"] isEqual:bits])reject(e.description?:@"resource process metadata acknowledgement");
    _responsibleProcess=process;
}
- (MTLStorageMode)storageMode {return (self.acceptedOptions>>4)&15;}
- (MTLCPUCacheMode)cpuCacheMode {return self.acceptedOptions&15;}
- (MTLHazardTrackingMode)hazardTrackingMode {return MTLHazardTrackingModeTracked;}
- (MTLResourceOptions)resourceOptions {return self.acceptedOptions|MTLResourceHazardTrackingModeTracked;}
- (NSUInteger)protectionOptions {return 0;} // Protected allocations are rejected.
- (NSUInteger)allocatedSize {return self.hostAllocatedSize;}
- (id<MTLHeap>)heap {return nil;}
- (NSUInteger)heapOffset {return 0;}
- (BOOL)isAliasable {return NO;}
- (void)makeAliasable {reject(@"resource has no heap allocation");}
- (MTLPurgeableState)setPurgeableState:(MTLPurgeableState)state {
    if(state!=MTLPurgeableStateKeepCurrent&&state!=MTLPurgeableStateNonVolatile)
        reject(@"purgeable allocations unsupported");
    return MTLPurgeableStateNonVolatile;
}
@end
@protocol DVMProtectionMetadata
- (NSUInteger)protectionOptions;
@end
static BOOL DVMResourceOptionsValid(MTLResourceOptions options) {
    // Managed shadows use full upload at submission; didModifyRange validates
    // ownership/range. Explicit untracked hazards and unknown bits are rejected.
    NSUInteger storage=(options>>4)&15,cache=options&15,hazard=(options>>8)&3;
    return !(options&~0x33ful)&&storage<=1&&cache==0&&(hazard==0||hazard==2);
}
static BOOL DVMTextureDescriptorValid(MTLTextureDescriptor *d) {
    if(!d||(d.textureType!=MTLTextureType2D&&d.textureType!=MTLTextureType3D)||!d.depth||d.depth>DVM_TEXTURE_DIMENSION||
       (d.textureType==MTLTextureType2D&&d.depth!=1)||(d.textureType==MTLTextureType3D&&d.usage!=MTLTextureUsageShaderRead)||d.arrayLength!=1||
       !DVMTextureLevelsValid(d.width,d.height,d.pixelFormat,d.storageMode,d.textureType,d.mipmapLevelCount)||d.sampleCount!=1||d.compressionType!=MTLTextureCompressionTypeLossless||d.storageMode>MTLStorageModePrivate||!DVMResourceOptionsValid(d.resourceOptions&~0xf0ul)||
       !d.width||!d.height||d.width>DVM_TEXTURE_DIMENSION||d.height>DVM_TEXTURE_DIMENSION||
       !DVMFormatBytes(d.pixelFormat)||!DVMTextureUsageValid(d.pixelFormat,d.storageMode,d.textureType,d.usage))return NO;
    if([(id)d respondsToSelector:@selector(protectionOptions)]&&[(id<DVMProtectionMetadata>)d protectionOptions])return NO;
    return DVMTextureAllocationBytes(d.width,d.height,d.depth,d.pixelFormat,d.mipmapLevelCount)<=
        (d.storageMode==MTLStorageModePrivate?DVM_PRIVATE_TEXTURE_BYTES:DVM_TEXTURE_BYTES);
}
