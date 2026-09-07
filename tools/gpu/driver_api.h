#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

// Process-local, experimental render/compute subset. No global discovery claim.
// The transport receives serialized calls on one device queue and must fail
// closed after any framing/timeout error. It owns its session until block release.
typedef NSDictionary * (^DVMMetalRPC)(NSDictionary *request, NSError **error);
typedef id<MTLDevice> (*DVMCreateMetalDeviceFn)(DVMMetalRPC rpc);
id<MTLDevice> DVMCreateMetalDevice(DVMMetalRPC rpc);
// Opt-in raw payloads for the bounded DVB1 submission transport.
id<MTLDevice> DVMCreateBinaryMetalDevice(DVMMetalRPC rpc);

// Process-local mapping ABI v1. The provider must retain the owning service
// connection until its last reference dies. This is never serialized on wire.
// Only the registered display pool is supported; arbitrary client pages are not.
@protocol DVMMetalOwnedMapping <NSObject>
- (NSUInteger)mappingVersion;
- (void *)bytes;
- (NSUInteger)length;
@end
typedef id<DVMMetalOwnedMapping> (^DVMMetalMappingProvider)(NSError **error);
typedef id<MTLDevice> (*DVMCreateSharedMetalDeviceFn)(DVMMetalRPC,DVMMetalMappingProvider);
id<MTLDevice> DVMCreateSharedMetalDevice(DVMMetalRPC rpc,DVMMetalMappingProvider provider);
id<DVMMetalOwnedMapping> DVMGetOwnedMetalMapping(id<MTLDevice> device,NSError **error);
NSDictionary *DVMSharedTextureAcquire(id<MTLTexture> texture,uint64_t epoch);
NSDictionary *DVMSharedTextureSeal(id<MTLTexture> texture,uint64_t epoch);
NSDictionary *DVMSharedTextureRetire(id<MTLTexture> texture,uint64_t epoch,uint32_t swap,int waitResult);
