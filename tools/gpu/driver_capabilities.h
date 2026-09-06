#pragma once
// Versioned forwarding limits, not a snapshot of the host MTLDevice limits.
// Use these constants in both validation and capability replies.
#define DVM_CONTRACT_VERSION 2u
#define DVM_TEXTURE_DIMENSION 512u
#define DVM_BUFFER_BYTES (1024u*1024u)
#define DVM_TEXTURE_BYTES (1024u*1024u)
#define DVM_BUFFER_BINDING_ALIGNMENT 16u
#define DVM_COMPUTE_BINDINGS 8u
#define DVM_INLINE_BYTES 4096u
#define DVM_TEXTURE_USAGE_MASK 7u
#define DVM_MANAGED_PAGE_BYTES 16384u
// Bounded single-color render encoding, with eight fragment binding indices.
// This is not a complete GPU family or an arbitrary shader-execution contract.
#define DVM_FRAGMENT_TEXTURES 8u
#define DVM_FRAGMENT_SAMPLERS 8u
#define DVM_COLOR_ATTACHMENTS 1u
static inline unsigned DVMFormatBytes(NSUInteger format) {
    switch(format) {case 10:return 1;case 30:return 2;case 70:case 80:return 4;case 115:return 8;default:return 0;}
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
 U(iosurfaceReadOnlyTextureAlignmentBytes,DVM_MANAGED_PAGE_BYTES) \
 B(supportsRasterOrderGroups,NO) \
 B(supportsYCBCRFormats12,NO) \
 B(supportsYCBCRPackedFormats12,NO) \
 B(supportsNativeHardwareFP16,NO) \
 B(supportsTileShaders,NO) \
 B(supportsSIMDGroup,NO) \
 B(supportsSIMDShuffleAndFill,NO) \
 B(supportsSIMDReduction,NO) \
 B(supportsCorrectTextureUsageBits,NO) \
 B(isFramebufferReadSupported,NO)

static inline NSDictionary *DVMContractProfile(void) {
#define DVM_BOOL_VALUE(selector,value) @#selector:@((BOOL)(value)),
#define DVM_UINT_VALUE(selector,value) @#selector:@((NSUInteger)(value)),
    return @{@"version":@DVM_CONTRACT_VERSION,@"profile":@"bounded-compute-and-resource-metadata-v2",
        @"queries":@{DVM_CAPABILITY_QUERIES(DVM_BOOL_VALUE,DVM_UINT_VALUE)},
        @"computeBindings":@DVM_COMPUTE_BINDINGS,@"inlineBytes":@DVM_INLINE_BYTES,
        @"bufferBytes":@DVM_BUFFER_BYTES,@"textureBytes":@DVM_TEXTURE_BYTES,@"textureUsageMask":@DVM_TEXTURE_USAGE_MASK,
        @"renderEncoders":@YES,@"linearTextures":@YES,@"generalIOSurfaceTextureImport":@NO,
        @"resourceMetadataVersion":@1,@"textureFormats":@[@10,@30,@70,@80,@115],
        @"logicalStorageModes":@[@0,@1],@"protectedResources":@NO,@"heaps":@NO,
        @"renderTimestampDomain":@"host-mach-absolute-seconds",
        @"linearTextureUsageMask":@1,@"renderBufferAccess":@"read-only",
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
