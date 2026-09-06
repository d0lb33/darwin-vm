// Experimental process-local Metal compute driver. Public selector/structure ABI;
// only the documented subset is implemented. Never publish this device globally.
#import "driver_api.h"
#import <CommonCrypto/CommonDigest.h>
#import <IOSurface/IOSurface.h>

static NSError *error(NSString *s) {
    return [NSError errorWithDomain:@"DVMMetalDriver"
                               code:1
                           userInfo:@{NSLocalizedDescriptionKey : s}];
}
static void reject(NSString *s) {
    [NSException raise:NSInvalidArgumentException format:@"DVM Metal: %@", s];
}
@class DVMDevice, DVMLibrary, DVMFunction, DVMPipeline, DVMTexture, DVMCommand, DVMBuffer;

@interface DVMDevice : NSObject <MTLDevice>
@property(nonatomic, copy) DVMMetalRPC transport;
@property(nonatomic, strong) dispatch_queue_t serial;
@property(nonatomic) BOOL submissionInFlight;
@property(nonatomic) BOOL binaryPayloads;
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)err;
- (void)retire:(NSNumber *)handle;
@end
@interface DVMObject : NSObject
@property(nonatomic, strong) DVMDevice *owner;
@property(nonatomic, strong) NSNumber *handle;
@property(atomic, copy) NSString *label;
@end
@implementation DVMObject
- (id<MTLDevice>)device {
    return _owner;
}
- (void)dealloc {
    if (_handle)
        [_owner retire:_handle];
}
@end
@interface DVMLibrary : DVMObject <MTLLibrary>
@property(nonatomic, strong) NSArray<NSString *> *functionNames;
@end
@interface DVMFunction : NSObject <MTLFunction>
@property(nonatomic, strong) DVMLibrary *library;
@property(nonatomic, strong) NSString *name;
@property(atomic, copy) NSString *label;
@end
@interface DVMBuffer : DVMObject <MTLBuffer>
@property(nonatomic, strong) NSMutableData *shadow;
@end
@interface DVMPipeline : DVMObject <MTLComputePipelineState>
@property(nonatomic, strong) DVMFunction *function;
@property(nonatomic) NSUInteger threadExecutionWidth;
@property(nonatomic) NSUInteger maxTotalThreadsPerThreadgroup;
@end
@interface DVMTexture : DVMObject <MTLTexture>
@property(nonatomic, strong) NSData *pendingUpload;
@property(nonatomic, strong) NSData *completedShadow;
@property(nonatomic) NSUInteger width;
@property(nonatomic) NSUInteger height;
@property(nonatomic) MTLPixelFormat pixelFormat;
@property(nonatomic) MTLTextureUsage usage;
- (NSUInteger)row;
- (NSData *)read;
@end
@interface DVMQueue : NSObject <MTLCommandQueue>
@property(nonatomic, strong) DVMDevice *owner;
@property(atomic, copy) NSString *label;
@end
@interface DVMEncoder : NSObject <MTLComputeCommandEncoder>
@property(nonatomic, strong) DVMCommand *command;
@property(nonatomic, strong) DVMPipeline *pipeline;
@property(nonatomic, strong) NSMutableDictionary *textures;
@property(nonatomic, strong) NSMutableDictionary *constants;
@property(nonatomic, strong) NSMutableDictionary *buffers;
@property(nonatomic, strong) NSMutableDictionary *scratch;
@property(nonatomic) BOOL ended;
@property(nonatomic, strong) NSArray *imageblock;
@property(atomic, copy) NSString *label;
@end
@interface DVMCommand : NSObject <MTLCommandBuffer>
@property(nonatomic, strong) DVMQueue *commandQueue;
@property(nonatomic, strong) NSMutableArray *commands;
@property(nonatomic, strong) NSMutableArray *resources;
@property(nonatomic, strong) NSMutableArray *handlers;
@property(nonatomic, strong) dispatch_group_t completion;
@property(atomic) MTLCommandBufferStatus status;
@property(atomic, strong) NSError *error;
@property(nonatomic) NSUInteger openEncoders;
@property(atomic, copy) NSString *label;
- (void)encoding;
@end

@implementation DVMDevice
- (NSString *)name {
    return @"DVM host Metal (experimental compute subset)";
}
- (BOOL)hasUnifiedMemory {
    return NO;
} // Guest and host storage are explicitly copied.
- (BOOL)supportsFamily:(MTLGPUFamily)family {
    (void)family;
    return NO;
}
- (NSDictionary *)call:(NSDictionary *)request error:(NSError **)err {
    __block NSDictionary *reply = nil;
    __block NSError *failure = nil;
    dispatch_sync(_serial, ^{
        reply = self.transport(request, &failure);
    });
    if (!reply && err)
        *err = failure ?: error(@"transport failed");
    return reply;
}
- (void)retire:(NSNumber *)handle {
    // Queued retirement follows earlier submissions. Pending command buffers
    // retain their resources, so deallocation cannot overtake encoded use.
    dispatch_async(_serial, ^{
        NSError *e = nil;
        self.transport(@{@"op" : @"release", @"handle" : handle}, &e);
        if (e)
            fprintf(stderr, "GPU_LOAD_DRIVER_RETIRE_ERROR %s\n", e.description.UTF8String);
    });
}
- (id<MTLLibrary>)newLibraryWithData:(dispatch_data_t)data error:(NSError **)err {
    const void *p = NULL;
    size_t n = 0;
    dispatch_data_t map = data ? dispatch_data_create_map(data, &p, &n) : nil;
    if (!map || !p || n > 12 * 1024 * 1024) {
        if (err)
            *err = error(@"invalid library data");
        return nil;
    }
    uint8_t sha[32];
    char hex[65];
    CC_SHA256(p, (CC_LONG)n, sha);
    for (unsigned i = 0; i < 32; i++)
        snprintf(hex + 2 * i, 3, "%02x", sha[i]);
    NSDictionary *r = [self call:@{@"op" : @"library", @"length" : @(n), @"sha256" : @(hex)}
                           error:err];
    if (!r)
        return nil;
    DVMLibrary *o = [DVMLibrary new];
    o.owner = self;
    o.handle = r[@"handle"];
    o.functionNames = r[@"functionNames"];
    return o;
}
- (id<MTLComputePipelineState>)newComputePipelineStateWithFunction:(id<MTLFunction>)f
                                                             error:(NSError **)err {
    if (![f isKindOfClass:DVMFunction.class] || ((DVMFunction *)f).library.owner != self) {
        if (err)
            *err = error(@"foreign function");
        return nil;
    }
    DVMFunction *function = (DVMFunction *)f;
    NSDictionary *r = [self call:@{
        @"op" : @"pipeline",
        @"library" : function.library.handle,
        @"function" : function.name
    }
                           error:err];
    if (!r)
        return nil;
    DVMPipeline *o = [DVMPipeline new];
    o.owner = self;
    o.handle = r[@"handle"];
    o.function = function;
    o.threadExecutionWidth = [r[@"threadExecutionWidth"] unsignedIntegerValue];
    o.maxTotalThreadsPerThreadgroup = [r[@"maxTotalThreadsPerThreadgroup"] unsignedIntegerValue];
    return o;
}
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor *)d {
    if (!d || d.textureType != MTLTextureType2D || d.depth != 1 || d.arrayLength != 1 ||
        d.mipmapLevelCount != 1 || d.sampleCount != 1 || d.storageMode != MTLStorageModeShared ||
        d.width < 1 || d.height < 1 || d.width > 512 || d.height > 512 ||
        (d.pixelFormat != MTLPixelFormatBGRA8Unorm && d.pixelFormat != MTLPixelFormatRGBA16Float) ||
        (d.usage & ~(MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite)))
        return nil;
    NSError *e = nil;
    NSDictionary *r = [self call:@{
        @"op" : @"texture",
        @"width" : @(d.width),
        @"height" : @(d.height),
        @"format" : @(d.pixelFormat),
        @"usage" : @(d.usage)
    }
                           error:&e];
    if (!r)
        return nil;
    DVMTexture *o = [DVMTexture new];
    o.owner = self;
    o.handle = r[@"handle"];
    o.width = d.width;
    o.height = d.height;
    o.pixelFormat = d.pixelFormat;
    o.usage = d.usage;
    return o;
}
- (id<MTLTexture>)newTextureWithDescriptor:(MTLTextureDescriptor *)d
                                 iosurface:(IOSurfaceRef)s
                                     plane:(NSUInteger)plane {
    // Shared IOSurface backing/coherence is not implemented by copied storage.
    // Refuse this path until both initial contents and GPU writes are coherent.
    (void)d;
    (void)s;
    (void)plane;
    return nil;
}
- (id<MTLCommandQueue>)newCommandQueue {
    DVMQueue *q = [DVMQueue new];
    q.owner = self;
    return q;
}
- (id<MTLBuffer>)newBufferWithLength:(NSUInteger)n options:(MTLResourceOptions)options {
    if (!n || n > 1024 * 1024 || options != MTLResourceStorageModeShared)
        return nil;
    NSError *e = nil;
    NSDictionary *r = [self call:@{@"op" : @"buffer", @"length" : @(n)} error:&e];
    if (!r)
        return nil;
    DVMBuffer *b = [DVMBuffer new];
    b.owner = self;
    b.handle = r[@"handle"];
    b.shadow = [NSMutableData dataWithLength:n];
    return b;
}
@end
@implementation DVMLibrary
- (id<MTLFunction>)newFunctionWithName:(NSString *)name {
    if (![_functionNames containsObject:name])
        return nil;
    DVMFunction *f = [DVMFunction new];
    f.library = self;
    f.name = name;
    return f;
}
@end
@implementation DVMFunction
- (id<MTLDevice>)device {
    return _library.owner;
}
@end
@implementation DVMPipeline
@end
@implementation DVMBuffer
- (NSUInteger)length {
    return _shadow.length;
}
- (void *)contents {
    return _shadow.mutableBytes;
}
- (MTLStorageMode)storageMode {
    return MTLStorageModeShared;
}
@end

@implementation DVMTexture
- (NSUInteger)row {
    return _width * (_pixelFormat == MTLPixelFormatRGBA16Float ? 8 : 4);
}
- (MTLTextureType)textureType {
    return MTLTextureType2D;
}
- (MTLStorageMode)storageMode {
    return MTLStorageModeShared;
}
- (NSUInteger)depth {
    return 1;
}
- (NSUInteger)mipmapLevelCount {
    return 1;
}
- (NSUInteger)arrayLength {
    return 1;
}
- (NSUInteger)sampleCount {
    return 1;
}
- (void)check:(MTLRegion)r level:(NSUInteger)level row:(NSUInteger)row pointer:(const void *)p {
    if (!p || level || r.origin.x || r.origin.y || r.origin.z || r.size.width != _width ||
        r.size.height != _height || r.size.depth != 1 || row < [self row])
        reject(@"only full texture transfers supported");
}
- (void)replaceRegion:(MTLRegion)r
          mipmapLevel:(NSUInteger)level
            withBytes:(const void *)p
          bytesPerRow:(NSUInteger)row {
    [self check:r level:level row:row pointer:p];
    NSMutableData *d = [NSMutableData dataWithLength:[self row] * _height];
    for (NSUInteger y = 0; y < _height; y++)
        memcpy((uint8_t *)d.mutableBytes + y * [self row], (const uint8_t *)p + y * row,
               [self row]);
    if (self.owner.submissionInFlight) reject(@"texture upload while GPU work is in flight");
    self.pendingUpload = d;
    self.completedShadow=nil;
}
- (NSData *)read {
    if(self.owner.submissionInFlight)reject(@"texture read while GPU work is in flight");
    if(self.completedShadow)return self.completedShadow;
    NSError *e = nil;
    if (self.pendingUpload) {
        if (![self.owner call:@{@"op":@"upload", @"texture":self.handle,
              @"row":@([self row]), @"data":[self.pendingUpload base64EncodedStringWithOptions:0]} error:&e])
            reject(e.description ?: @"texture upload failed");
        self.pendingUpload = nil;
    }
    NSDictionary *r = [self.owner call:@{@"op" : @"read", @"texture" : self.handle} error:&e];
    NSData *d = r ? [[NSData alloc] initWithBase64EncodedString:r[@"data"] options:0] : nil;
    if (!d || d.length != [self row] * _height || [r[@"row"] unsignedIntegerValue] != [self row])
        reject(e.description ?: @"bad texture readback");
    return d;
}
- (void)getBytes:(void *)p
     bytesPerRow:(NSUInteger)row
      fromRegion:(MTLRegion)r
     mipmapLevel:(NSUInteger)level {
    [self check:r level:level row:row pointer:p];
    NSData *d = [self read];
    for (NSUInteger y = 0; y < _height; y++)
        memcpy((uint8_t *)p + y * row, (const uint8_t *)d.bytes + y * [self row], [self row]);
}

@end

@implementation DVMQueue
- (id<MTLDevice>)device {
    return _owner;
}
- (id<MTLCommandBuffer>)commandBuffer {
    DVMCommand *b = [DVMCommand new];
    b.commandQueue = self;
    b.commands = [NSMutableArray array];
    b.resources = [NSMutableArray array];
    b.handlers = [NSMutableArray array];
    b.completion = dispatch_group_create();
    b.status = MTLCommandBufferStatusNotEnqueued;
    return b;
}
@end
@implementation DVMCommand
- (id<MTLDevice>)device {
    return _commandQueue.owner;
}
- (BOOL)retainedReferences {
    return YES;
}
- (void)encoding {
    if (self.status != MTLCommandBufferStatusNotEnqueued)
        reject(@"command already committed");
}
- (id<MTLComputeCommandEncoder>)computeCommandEncoder {
    [self encoding];
    if (_openEncoders)
        reject(@"overlapping encoders");
    _openEncoders++;
    DVMEncoder *e = [DVMEncoder new];
    e.command = self;
    e.textures = [NSMutableDictionary dictionary];
    e.constants = [NSMutableDictionary dictionary];
    e.buffers = [NSMutableDictionary dictionary];
    e.scratch = [NSMutableDictionary dictionary];
    return e;
}
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler {
    @synchronized(self) {
        if (!handler || self.status != MTLCommandBufferStatusNotEnqueued)
            reject(@"register completion before commit");
        [_handlers addObject:[handler copy]];
    }
}
- (void)commit {
    @synchronized(self) {
        [self encoding];
        if (_openEncoders || !_commands.count)
            reject(@"unclosed or empty command buffer");
        @synchronized(_commandQueue.owner) {
            if (_commandQueue.owner.submissionInFlight)
                reject(@"only one in-flight command buffer supported");
            _commandQueue.owner.submissionInFlight = YES;
        }
        self.status = MTLCommandBufferStatusCommitted;
        dispatch_group_enter(_completion);
    }
    // Encoding is finished; arrays cannot be mutated after commit. Resources
    // remain strongly owned until completion even if the caller releases them.
    NSMutableArray *uploads = [NSMutableArray array], *buffers = [NSMutableArray array],
                   *textures = [NSMutableArray array], *readbacks = [NSMutableArray array];
    for (id o in _resources)
        if ([o isKindOfClass:DVMBuffer.class] && ![buffers containsObject:o]) {
            DVMBuffer *b = o;
            [buffers addObject:b];
            [readbacks addObject:b.handle];
            [uploads addObject:@{
                @"op" : @"upload",
                @"buffer" : b.handle,
                @"data" : self.commandQueue.owner.binaryPayloads ? [b.shadow copy] : [b.shadow base64EncodedStringWithOptions:0]
            }];
        }
    for (id o in _resources)
        if ([o isKindOfClass:DVMTexture.class] && ![textures containsObject:o]) {
            DVMTexture *t = o;
            [textures addObject:t];
            if (t.pendingUpload)
                [uploads addObject:@{@"texture":t.handle, @"row":@([t row]),
                    @"data":self.commandQueue.owner.binaryPayloads ? t.pendingUpload : [t.pendingUpload base64EncodedStringWithOptions:0]}];
        }
    dispatch_async(_commandQueue.owner.serial, ^{
        @autoreleasepool {
            @try {
                NSError *e = nil;
                BOOL blur=self.commands[0][@"imageblock"]!=nil;
                DVMTexture *output=nil;
                if(blur)for(DVMTexture *t in textures)if([t.handle isEqual:[self.commands.lastObject[@"textures"] lastObject]])output=t;
                if(blur && (!output||!self.commandQueue.owner.binaryPayloads))reject(@"blur requires binary transport and output");
                NSMutableDictionary *request=[@{@"op":blur?@"blurSubmit":@"submit",@"commands":self.commands,@"uploads":uploads,@"readbacks":readbacks} mutableCopy];
                if(blur){request[@"w"]=@(output.width);request[@"h"]=@(output.height);}
                NSDictionary *r=self.commandQueue.owner.transport(request,&e);
                if (!r || [r[@"status"] integerValue] != MTLCommandBufferStatusCompleted)
                    self.error = e ?: error(@"GPU did not complete");
                else {
                    NSDictionary *returned = r[@"buffers"];
                    if (![returned isKindOfClass:NSDictionary.class] || returned.count != buffers.count)
                        reject(@"bad batched readback table");
                    NSMutableArray *decoded = [NSMutableArray array];
                    for (DVMBuffer *b in buffers) {
                        id encoded = returned[b.handle.stringValue];
                        NSData *d = self.commandQueue.owner.binaryPayloads
                            ? ([encoded isKindOfClass:NSData.class] ? encoded : nil)
                            : ([encoded isKindOfClass:NSString.class] ? [[NSData alloc] initWithBase64EncodedString:encoded options:0] : nil);
                        if (d.length != b.length) reject(@"buffer readback size");
                        [decoded addObject:d];
                    }
                    if(blur){
                        NSData *pixels=r[@"texture"];
                        if(![pixels isKindOfClass:NSData.class]||pixels.length!=[output row]*output.height||![r[@"output"] isEqual:output.handle]||[r[@"w"] unsignedIntegerValue]!=output.width||[r[@"h"] unsignedIntegerValue]!=output.height)reject(@"blur texture reply");
                        output.completedShadow=pixels;
                    }
                    // Validate all readbacks before publishing any CPU shadow.
                    for (NSUInteger i = 0; i < buffers.count; i++) {
                        DVMBuffer *b = buffers[i]; NSData *d = decoded[i];
                        memcpy(b.contents, d.bytes, d.length);
                    }
                    for (DVMTexture *t in textures) t.pendingUpload = nil;
                }
            } @catch (NSException *e) {
                self.error = error(e.reason);
            }
            NSArray *handlers = nil;
            @synchronized(self) {
                self.status =
                    self.error ? MTLCommandBufferStatusError : MTLCommandBufferStatusCompleted;
                handlers = [self.handlers copy];
                [self.handlers removeAllObjects];
                [self.resources removeAllObjects];
            }
            @synchronized(self.commandQueue.owner) {
                self.commandQueue.owner.submissionInFlight = NO;
            }
            dispatch_group_leave(self.completion);
            for (MTLCommandBufferHandler h in handlers)
                dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE, 0), ^{
                    h(self);
                });
        }
    });
}
- (void)waitUntilCompleted {
    if (self.status < MTLCommandBufferStatusCommitted)
        reject(@"wait before commit");
    dispatch_group_wait(_completion, DISPATCH_TIME_FOREVER);
}
@end
@implementation DVMEncoder
- (id<MTLDevice>)device {
    return _command.device;
}
- (void)check {
    if (_ended || !_command)
        reject(@"ended encoder");
    [_command encoding];
}
- (void)setComputePipelineState:(id<MTLComputePipelineState>)p {
    [self check];
    if (![p isKindOfClass:DVMPipeline.class] || p.device != self.device)
        reject(@"foreign pipeline");
    _pipeline = (DVMPipeline *)p;
}
- (void)setTexture:(id<MTLTexture>)t atIndex:(NSUInteger)i {
    [self check];
    if (i > 7 || ![t isKindOfClass:DVMTexture.class] || t.device != self.device)
        reject(@"foreign texture or index");
    _textures[@(i)] = t;
}
- (void)setBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i {
    [self check];
    if (!p || !n || n > 4096 || i > 7)
        reject(@"invalid inline bytes");
    _constants[@(i)] = [NSData dataWithBytes:p length:n];
    [_buffers removeObjectForKey:@(i)];
}
- (void)setBuffer:(id<MTLBuffer>)b offset:(NSUInteger)offset atIndex:(NSUInteger)i {
    [self check];
    if (i > 7 || ![b isKindOfClass:DVMBuffer.class] || b.device != self.device ||
        offset >= b.length || offset % 16)
        reject(@"invalid buffer binding");
    _buffers[@(i)] = @{@"object" : b, @"offset" : @(offset)};
    [_constants removeObjectForKey:@(i)];
}
- (void)setImageblockWidth:(NSUInteger)w height:(NSUInteger)h {
    [self check];if(w!=32||h!=32)reject(@"only audited 32x32 imageblock supported");_imageblock=@[@(w),@(h)];
}
- (void)setThreadgroupMemoryLength:(NSUInteger)n atIndex:(NSUInteger)i {
    [self check];
    if (i > 7 || !n || n > 16384 || n % 16)
        reject(@"invalid threadgroup scratch");
    _scratch[@(i)] = @(n);
}
- (void)dispatchThreadgroups:(MTLSize)g threadsPerThreadgroup:(MTLSize)t {
    [self check];
    if (!_pipeline || _command.commands.count >= 32 || !g.width || !g.height || g.depth != 1 ||
        g.width > 512 || g.height > 512 || !t.width || !t.height || t.depth != 1 || t.width > 32 ||
        t.height > 32 || t.width * t.height > _pipeline.maxTotalThreadsPerThreadgroup)
        reject(@"unsupported dispatch geometry");
    NSMutableArray *textures = [NSMutableArray array], *bytes = [NSMutableArray array],
                   *buffers = [NSMutableArray array], *scratch = [NSMutableArray array];
    for (NSUInteger i = 0; i < _textures.count; i++) {
        DVMTexture *o = _textures[@(i)];
        if (!o)
            reject(@"sparse texture bindings unsupported");
        [textures addObject:o.handle];
        [_command.resources addObject:o];
    }
    for (NSNumber *i in _constants)
        [bytes
            addObject:@{@"index" : i, @"data" : ((DVMDevice *)self.device).binaryPayloads ? _constants[i] : [_constants[i] base64EncodedStringWithOptions:0]}];
    for (NSNumber *i in _buffers) {
        DVMBuffer *b = _buffers[i][@"object"];
        [buffers
            addObject:@{@"index" : i, @"buffer" : b.handle, @"offset" : _buffers[i][@"offset"]}];
        [_command.resources addObject:b];
    }
    for (NSNumber *i in _scratch)
        [scratch addObject:@{@"index" : i, @"length" : _scratch[i]}];
    [_command.resources addObject:_pipeline];
    NSMutableDictionary *encoded=[@{
        @"pipeline" : _pipeline.handle,
        @"textures" : textures,
        @"bytes" : bytes,
        @"buffers" : buffers,
        @"threadgroupMemory" : scratch,
        @"groups" : @[ @(g.width), @(g.height), @(g.depth) ],
        @"threads" : @[ @(t.width), @(t.height), @(t.depth) ]
    } mutableCopy];
    if(_imageblock)encoded[@"imageblock"]=_imageblock;
    [_command.commands addObject:encoded];
}
- (void)endEncoding {
    [self check];
    _ended = YES;
    _command.openEncoders--;
    _command = nil;
}
@end

id<MTLDevice> DVMCreateMetalDevice(DVMMetalRPC rpc) {
    if (!rpc)
        return nil;
    DVMDevice *d = [DVMDevice new];
    d.transport = rpc;
    dispatch_queue_attr_t attr=dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_USER_INTERACTIVE,0);
    d.serial = dispatch_queue_create("org.darwin-vm.metal.transport", attr);
    return d;
}

id<MTLDevice> DVMCreateBinaryMetalDevice(DVMMetalRPC rpc) {
    DVMDevice *d=(DVMDevice *)DVMCreateMetalDevice(rpc);
    d.binaryPayloads=YES;return d;
}

// Narrow resident-workload extension. This is not an implementation of general
// MTLTexture shared backing; explicit entry points keep that limitation visible.
@interface DVMResidentTask : DVMObject
@property(nonatomic,strong) DVMLibrary *library;
@property(nonatomic) uint32_t lastFrame;
@end
@implementation DVMResidentTask
@end
id DVMCreateResidentBlur(id<MTLDevice> device,id<MTLLibrary> library,uint32_t nonce) {
    if(![(id)device isKindOfClass:DVMDevice.class]||![(id)library isKindOfClass:DVMLibrary.class])reject(@"resident object type");
    DVMDevice *d=(id)device;DVMLibrary *l=(id)library;
    if(l.owner!=d||!d.binaryPayloads||d.submissionInFlight)reject(@"resident device ownership/state");
    NSError *e=nil;NSDictionary *r=[d call:@{@"op":@"residentCreate",@"library":l.handle,@"nonce":@(nonce)} error:&e];
    if(!r)reject(e.description);
    DVMResidentTask *task=[DVMResidentTask new];task.owner=d;task.library=l;task.handle=r[@"handle"];return task;
}
NSDictionary *DVMDrawResidentBlur(id object,uint32_t frame) {
    if(![object isKindOfClass:DVMResidentTask.class])reject(@"resident object type");
    DVMResidentTask *task=object;
    if(frame!=task.lastFrame+1||frame>33||task.owner.submissionInFlight)reject(@"resident frame sequence");
    NSError *e=nil;NSDictionary*r=[task.owner call:@{@"op":@"residentDraw",@"handle":task.handle,@"frame":@(frame)} error:&e];
    if(!r||[r[@"frame"] unsignedIntValue]!=frame||[r[@"status"] unsignedIntValue]!=4)reject(e.description?:@"resident completion");
    task.lastFrame=frame;return r;
}
NSDictionary *DVMVerifyResidentBlur(id object) {
    if(![object isKindOfClass:DVMResidentTask.class]||((DVMResidentTask *)object).lastFrame!=33)reject(@"resident final verification state");
    DVMResidentTask *task=object;NSError *e=nil;
    NSDictionary*r=[task.owner call:@{@"op":@"residentVerify",@"handle":task.handle} error:&e];if(!r)reject(e.description);return r;
}
