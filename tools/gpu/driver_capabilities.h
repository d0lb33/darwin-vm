#pragma once
// Versioned forwarding limits, not a snapshot of the host MTLDevice limits.
// Use these constants in both validation and capability replies.
#define DVM_CONTRACT_VERSION 30u
#define DVM_COMPUTE_THREADS 1024u
#define DVM_COMPUTE_MEMORY 32768u
#define DVM_COMPUTE_INVOCATIONS (16u*1024u*1024u)
#define DVM_OBJECTS 4096u
#define DVM_ORDERED_COMMANDS 256u
#define DVM_ENCODER_OPERATIONS 4096u
#define DVM_RENDER_REQUEST_BYTES (2u*1024u*1024u)
#define DVM_RENDER_REQUEST_CHUNK 32768u
#define DVM_RENDER_DIRECT_BYTES 60000u
#define DVM_TEXTURE_TRANSFER_CHUNK 32768u
// Staged bulk transfers (2026-09-07, docs/re/gpu-home-sluggishness-ios27.md):
// the guest copies raw bytes into an owned region of the mode-3 shared RAM and
// sends only {offset,length,crc} in the framed JSON request; the host peer
// verifies the CRC and hands the worker the same base64 field it always
// accepted. One request per texture or dirty buffer span instead of one per
// 32 KiB base64 chunk. Mode 2's 4 MiB framed request region overlaps this
// range, so only the present/managed layout publishes the descriptor.
#define DVM_STAGING_OFFSET 0x20000u
#define DVM_STAGING_BYTES 0x1E0000u
#define DVM_STAGING_DESCRIPTOR 0x340u
#define DVM_STAGING_MAGIC 0x31474154534d5644ull /* "DVMSTAG1" little-endian */
#define DVM_STAGING_FLAG_ENABLED 1u
#define DVM_STAGING_FLAG_RAM_POLL 2u
#define DVM_QUEUED_COMMAND_BUFFERS 32u
#define DVM_TEXTURE_DIMENSION 4096u
#define DVM_BUFFER_BYTES (1024u*1024u)
// Exact compositor requests a 1280x932 RG8 sampled texture (2,385,920 bytes).
// Allocation is distinct from the 32 KiB chunk and 1 MiB legacy reply bounds.
#define DVM_TEXTURE_BYTES (4u*1024u*1024u)
#define DVM_TEXTURE_DIRECT_READ_BYTES (1024u*1024u)
// Private images never cross the framed CPU-transfer channel. Keep their
// allocation budget separate; total live ordinary/shared resources stay capped.
#define DVM_PRIVATE_TEXTURE_BYTES (128u*1024u*1024u)
// Observed 1216x2560 RGBA16F private compositor target is 24,903,680 bytes.
// Development headroom, not an Apple GPU-family limit or a leak allowance.
// CA_TEXTURE_VIEW_PACING1 RPC972 needs 68,698,622 simultaneous logical bytes;
// CA_BUDGET_GUEST1 subsequently reaches the old 256-operation pass limit.
// This is a logical allocation budget, not native Metal/RSS accounting.
// Imported DRAM remains separately bounded; release/alias rules are unchanged.
#define DVM_ORDINARY_RESOURCE_BYTES (512u*1024u*1024u)
#define DVM_BUFFER_BINDING_ALIGNMENT 16u
#define DVM_COMPUTE_BINDINGS 8u
#define DVM_RENDER_BUFFERS 31u
#define DVM_INLINE_BYTES 4096u
#define DVM_TEXTURE_USAGE_MASK 7u
// Native Metal descriptor/texture descriptions identify this exact bit.
// Preserve it for owned private 2D color textures; never silently clear it.
#define DVM_TEXTURE_BLOCK_WRITES_ONLY 0x10000u
#define DVM_MANAGED_PAGE_BYTES 16384u
// Texture row/base alignment is separate from kernel page registration. The
// owned BGRA IOSurface has a 4864-byte row (64 aligned), not a 16 KiB row.
// The host verifies native BGRA linear textures can honor this profile.
#define DVM_SHARED_TEXTURE_ALIGNMENT 64u
// Bounded single-color render encoding, with sixteen fragment binding indices.
// This is not a complete GPU family or an arbitrary shader-execution contract.
#define DVM_FRAGMENT_TEXTURES 16u
#define DVM_FRAGMENT_SAMPLERS 16u
#define DVM_COLOR_ATTACHMENTS 1u
static inline unsigned DVMFormatBytes(NSUInteger format) {
    switch(format) {case 1:case 10:return 1;case 23:case 25:case 30:return 2;case 55:case 70:case 80:case 554:return 4;case 105:case 115:return 8;case 125:return 16;default:return 0;}
}
static inline BOOL DVM1DFormat(NSUInteger format) {return format==23||format==25||format==55||format==105;}
static inline BOOL DVMColorFormat(NSUInteger format) {return format==10||format==30||format==70||format==80||format==115||format==554;}
static inline unsigned DVMFormatUsageMask(NSUInteger format) {
    // A8 is an alpha-only sampled image in this profile. It is not a color
    // attachment or compute-write target. Other existing formats keep v8 usage.
    return (format==1||format==125||DVM1DFormat(format))?1:format==554?5:(DVMFormatBytes(format)?DVM_TEXTURE_USAGE_MASK:0);
}
static inline BOOL DVMTextureUsageValid(NSUInteger format,NSUInteger storage,NSUInteger type,NSUInteger usage) {
    // Exact backboardd HDRProcessing requests: shared integer and float LUTs.
    // 1D is sampled/read only, with no array, mip, or linear-view claim.
    if(type==MTLTextureType1D||DVM1DFormat(format))
        return type==MTLTextureType1D&&DVM1DFormat(format)&&storage==MTLStorageModeShared&&usage==MTLTextureUsageShaderRead;
    if(format==554&&type!=MTLTextureType2D)return NO;
    if(usage&DVM_TEXTURE_BLOCK_WRITES_ONLY) {
        // Observed QuartzCore allocation: render writes followed by sampling.
        // Compute writes and other private-bit combinations remain unvalidated.
        return storage==MTLStorageModePrivate&&type==MTLTextureType2D&&
            (format==70||format==80||format==115)&&usage==(DVM_TEXTURE_BLOCK_WRITES_ONLY|5u);
    }
    return usage&&!(usage&~DVMFormatUsageMask(format));
}
static inline BOOL DVMTextureLevelsValid(NSUInteger width,NSUInteger height,NSUInteger format,NSUInteger storage,NSUInteger type,NSUInteger levels){
    if(!width||!height||!levels)return NO;
    if(levels==1)return YES;
    if(storage!=MTLStorageModePrivate||type!=MTLTextureType2D||!DVMColorFormat(format))return NO;
    NSUInteger maxLevels=1,extent=MAX(width,height);
    while(extent>1){extent>>=1;maxLevels++;}
    return levels<=maxLevels;
}
static inline NSUInteger DVMTextureAllocationBytes(NSUInteger width,NSUInteger height,NSUInteger depth,NSUInteger format,NSUInteger levels){
    NSUInteger bytes=0;
    for(NSUInteger level=0;level<levels;level++){
        bytes+=MAX((NSUInteger)1,width>>level)*MAX((NSUInteger)1,height>>level)*depth*DVMFormatBytes(format);
    }
    return bytes;
}
static inline unsigned DVMConstantBytes(NSUInteger type) {
    switch(type){case MTLDataTypeBool:case MTLDataTypeUChar:return 1;case MTLDataTypeUChar2:return 2;case MTLDataTypeUChar4:return 4;case MTLDataTypeUInt:case MTLDataTypeInt:case MTLDataTypeFloat:return 4;default:return 0;}
}

// Exact 24A5430a new_metal_context selector inventory, plus public predicates
// and the framebuffer-read query observed in the host-only rehearsal.
#define DVM_CAPABILITY_QUERIES(B,U) \
 B(hasUnifiedMemory,NO) \
 B(supportsMemorylessRenderTargets,NO) \
 U(minConstantBufferAlignmentBytes,DVM_BUFFER_BINDING_ALIGNMENT) \
 U(maxFragmentTextures,DVM_FRAGMENT_TEXTURES) \
 U(maxFragmentSamplers,DVM_FRAGMENT_SAMPLERS) \
 U(maxTextureWidth2D,DVM_TEXTURE_DIMENSION) \
 U(maxTextureHeight2D,DVM_TEXTURE_DIMENSION) \
 U(maxColorAttachments,DVM_COLOR_ATTACHMENTS) \
 U(maxBufferLength,DVM_BUFFER_BYTES) \
 U(iosurfaceReadOnlyTextureAlignmentBytes,DVM_SHARED_TEXTURE_ALIGNMENT) \
 B(supportsRasterOrderGroups,NO) \
 B(supportsYCBCRFormats12,NO) \
 B(supportsYCBCRPackedFormats12,NO) \
 B(supportsNativeHardwareFP16,NO) \
 B(supportsTileShaders,NO) \
 B(supportsSIMDGroup,NO) \
 B(supportsSIMDShuffleAndFill,NO) \
 B(supportsSIMDReduction,NO) \
 B(supportsCorrectTextureUsageBits,NO) \
 B(isFramebufferReadSupported,YES) \
 B(supportsBufferlessClientStorageTexture,NO) \
 B(supportsLossyCompression,NO) \
 B(supportsPerPlaneCompression,NO) \
 B(supportsASTCTextureCompression,NO) \
 B(supportsASTCHDRTextureCompression,NO) \
 B(supportsBCTextureCompression,NO) \
 B(supportsPublicXR10Formats,NO) \
 B(supportsExtendedXR10Formats,NO) \
 B(supportsLimitedYUVFormats,NO) \
 B(supportsExtendedYUVFormats,NO) \
 B(supportsAlphaYUVFormats,NO) \
 B(supportsYCBCRFormats,NO) \
 B(supportsYCBCRFormatsPQ,NO) \
 B(supportsYCBCRFormatsXR,NO) \
 B(supportsYCBCRPackedFormatsPQ,NO) \
 B(supportsYCBCRPackedFormatsXR,NO) \
 B(supportsBfloat16Format,NO) \
 U(minBufferNoCopyAlignmentBytes,DVM_MANAGED_PAGE_BYTES)

static inline NSDictionary *DVMContractProfile(void) {
#define DVM_BOOL_VALUE(selector,value) @#selector:@((BOOL)(value)),
#define DVM_UINT_VALUE(selector,value) @#selector:@((NSUInteger)(value)),
    return @{@"version":@DVM_CONTRACT_VERSION,@"profile":@"quartzcore-development-headroom-v28",
        @"computeDescriptorVersion":@1,@"orderedComputeRenderBlit":@YES,@"orderedCommandLimit":@DVM_ORDERED_COMMANDS,@"encoderOperationLimit":@DVM_ENCODER_OPERATIONS,@"computeReflectionArrays":@NO,
        @"computeThreadsPerGroup":@DVM_COMPUTE_THREADS,@"computeThreadgroupBytes":@DVM_COMPUTE_MEMORY,@"computeInvocationsPerDispatch":@DVM_COMPUTE_INVOCATIONS,
        @"resourcePurgeabilityVersion":@1,@"resourcePurgeabilityStates":@[@1,@2,@3,@4],
        @"pinnedSurfacePurgeabilityStates":@[@1,@2],@"volatileResourceAccess":@"reacquire-nonvolatile-before-use",
        @"texture1DFormats":@[@23,@25,@55,@105],@"texture1DUsageMask":@1,@"texture1DStorageModes":@[@0],
        @"blitEncoders":@YES,@"blitBufferAlignment":@4,@"blitIOSurfaceImport":@NO,@"blitTextureTypes":@[@2],
        @"privateColorTextureAdditionalUsages":@[@(DVM_TEXTURE_BLOCK_WRITES_ONLY|5u)],
        @"privateColorTextureUsageFormats":@[@70,@80,@115],@"colorAttachmentFormats":@[@10,@30,@70,@80,@115,@554],
        @"private2DMipFormats":@[@10,@30,@70,@80,@115,@554],@"maximumMipLevels":@13,@"mipRenderAttachments":@YES,@"privateMipReadWrite":@"application-guaranteed-disjoint-subresources-native-hazard-tracking",@"mipGeneration":@YES,@"mipGenerationFormats":@[@10,@30,@70,@80,@115,@554],@"textureViews":@NO,@"textureViewVersion":@1,@"textureViewTypes":@[@2],@"textureViewFormats":@"same-format",@"textureViewSlices":@[@0,@1],@"textureViewMipRanges":@YES,
        @"renderRequestBytes":@DVM_RENDER_REQUEST_BYTES,@"renderRequestChunkBytes":@DVM_RENDER_REQUEST_CHUNK,@"renderRequestTransactions":@1,
        @"framebufferRead":@"current-fragment-single-color-attachment-ordered-programmable-blending",
        @"textureTransferChunkBytes":@DVM_TEXTURE_TRANSFER_CHUNK,@"textureDirectReadBytes":@DVM_TEXTURE_DIRECT_READ_BYTES,@"textureUploadTransactions":@1,
        @"stagedTransferVersion":@1,@"stagedTransferBytes":@DVM_STAGING_BYTES,
        @"queuedCommandBuffers":@DVM_QUEUED_COMMAND_BUFFERS,@"executionQueues":@1,@"maximumLiveObjects":@DVM_OBJECTS,
        @"queries":@{DVM_CAPABILITY_QUERIES(DVM_BOOL_VALUE,DVM_UINT_VALUE)},
        @"computeBindings":@DVM_COMPUTE_BINDINGS,@"inlineBytes":@DVM_INLINE_BYTES,
        @"bufferBytes":@DVM_BUFFER_BYTES,@"textureBytes":@DVM_TEXTURE_BYTES,@"privateTextureBytes":@DVM_PRIVATE_TEXTURE_BYTES,@"ordinaryResourceBytes":@DVM_ORDINARY_RESOURCE_BYTES,@"textureUsageMask":@DVM_TEXTURE_USAGE_MASK,
        @"renderEncoders":@YES,@"linearTextures":@YES,@"generalIOSurfaceTextureImport":@NO,
        @"resourceMetadataVersion":@2,@"resourceProcessAttribution":@"opaque-guest-pid32-unset-zero-host-execution-owner-unchanged",@"textureFormats":@[@1,@10,@23,@25,@30,@55,@70,@80,@105,@115,@554],@"textureFormatUsageOverrides":@{@"1":@1,@"23":@1,@"25":@1,@"55":@1,@"105":@1,@"554":@5},@"textureTypes":@[@0,@2,@7],@"texture3DUsageMask":@1,
        @"bufferStorageModes":@[@0,@1],@"textureStorageModes":@[@0,@1,@2],@"textureCompressionTypes":@[@0],@"protectedResources":@NO,@"heaps":@NO,
        @"clientBufferStorage":@"retained-guest-pages-upload-before-submit-writeback-before-completion",
        @"renderTimestampDomain":@"host-mach-absolute-seconds",
        @"linearTextureUsageMask":@1,@"renderBufferAccess":@"vertex-completion-writeback",@"renderBuffers":@DVM_RENDER_BUFFERS,@"renderWritebackBytes":@DVM_BUFFER_BYTES,
        @"taskAttribution":@"opaque-guest-u32-metadata-host-process-owns-execution",
        @"residentManagedSurfaceExtensionVersion":@1,@"generalShaderCompilation":@NO};
#undef DVM_BOOL_VALUE
#undef DVM_UINT_VALUE
}
@protocol DVMCapabilityQueries
#define DVM_BOOL_DECL(selector,value) - (BOOL)selector;
#define DVM_UINT_DECL(selector,value) - (NSUInteger)selector;
DVM_CAPABILITY_QUERIES(DVM_BOOL_DECL,DVM_UINT_DECL)
#undef DVM_BOOL_DECL
#undef DVM_UINT_DECL
@end
