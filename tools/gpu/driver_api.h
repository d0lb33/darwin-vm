#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

// Process-local, experimental compute subset. No global discovery or SPI claim.
// The transport receives serialized calls on one device queue and must fail
// closed after any framing/timeout error. It owns its session until block release.
typedef NSDictionary * (^DVMMetalRPC)(NSDictionary *request, NSError **error);
typedef id<MTLDevice> (*DVMCreateMetalDeviceFn)(DVMMetalRPC rpc);
id<MTLDevice> DVMCreateMetalDevice(DVMMetalRPC rpc);
// Opt-in raw payloads for the bounded DVB1 submission transport.
id<MTLDevice> DVMCreateBinaryMetalDevice(DVMMetalRPC rpc);
