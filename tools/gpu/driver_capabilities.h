#pragma once
// Versioned forwarding limits, not a snapshot of the host MTLDevice limits.
// Use these constants in both validation and capability replies.
#define DVM_CONTRACT_VERSION 1u
#define DVM_TEXTURE_DIMENSION 512u
#define DVM_BUFFER_BYTES (1024u*1024u)
#define DVM_TEXTURE_BYTES (1024u*1024u)
#define DVM_BUFFER_BINDING_ALIGNMENT 16u
#define DVM_COMPUTE_BINDINGS 8u
#define DVM_INLINE_BYTES 4096u
#define DVM_TEXTURE_USAGE_MASK 7u
#define DVM_MANAGED_PAGE_BYTES 16384u
// Texture allocation with RenderTarget usage is implemented; render command
// encoding, fragment bindings and samplers are not. Do not advertise them.
#define DVM_FRAGMENT_TEXTURES 0u
#define DVM_FRAGMENT_SAMPLERS 0u
#define DVM_COLOR_ATTACHMENTS 0u

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
    return @{@"version":@DVM_CONTRACT_VERSION,@"profile":@"bounded-compute-and-resident-surface-v1",
        @"queries":@{DVM_CAPABILITY_QUERIES(DVM_BOOL_VALUE,DVM_UINT_VALUE)},
        @"computeBindings":@DVM_COMPUTE_BINDINGS,@"inlineBytes":@DVM_INLINE_BYTES,
        @"bufferBytes":@DVM_BUFFER_BYTES,@"textureBytes":@DVM_TEXTURE_BYTES,@"textureUsageMask":@DVM_TEXTURE_USAGE_MASK,
        @"renderEncoders":@NO,@"linearTextures":@NO,@"generalIOSurfaceTextureImport":@NO,
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
