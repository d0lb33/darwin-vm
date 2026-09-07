// Bounded single-queue render forwarding. Each pass retains every referenced
// object until the host command buffer completes; no device-global publication.
@interface DVMRenderPipeline : DVMObject <MTLRenderPipelineState>
@property(nonatomic,strong) NSArray *functions;
@end
@implementation DVMRenderPipeline
- (NSUInteger)maxTotalThreadsPerThreadgroup {return 0;}
- (BOOL)threadgroupSizeMatchesTileSize {return NO;}
@end
@interface DVMSampler : DVMObject <MTLSamplerState>
@end
@implementation DVMSampler
@end
static id<MTLRenderPipelineState> DVMNewRenderPipeline(DVMDevice *device,MTLRenderPipelineDescriptor *d,NSError **err) {
    if(!d||![(id)d.vertexFunction isKindOfClass:DVMFunction.class]||![(id)d.fragmentFunction isKindOfClass:DVMFunction.class]) {
        if(err)*err=error(@"render requires owned vertex/fragment functions");return nil;
    }
    DVMFunction *v=(id)d.vertexFunction,*f=(id)d.fragmentFunction;
    if(v.library.owner!=device||f.library.owner!=device||d.rasterSampleCount!=1||!d.rasterizationEnabled||d.alphaToCoverageEnabled||d.alphaToOneEnabled||d.supportIndirectCommandBuffers) {
        if(err)*err=error(@"render descriptor outside single-sample direct contract");return nil;
    }
    for(unsigned i=1;i<8;i++)if(d.colorAttachments[i].pixelFormat){if(err)*err=error(@"only one color attachment");return nil;}
    NSMutableArray *attributes=[NSMutableArray array],*layouts=[NSMutableArray array];
    for(unsigned i=0;i<31;i++) {
        MTLVertexAttributeDescriptor *a=d.vertexDescriptor.attributes[i];
        if(a.format)[attributes addObject:@[@(i),@(a.format),@(a.offset),@(a.bufferIndex)]];
        MTLVertexBufferLayoutDescriptor *b=d.vertexDescriptor.layouts[i];
        if(b.stride)[layouts addObject:@[@(i),@(b.stride),@(b.stepFunction),@(b.stepRate)]];
    }
    MTLRenderPipelineColorAttachmentDescriptor *c=d.colorAttachments[0];
    NSDictionary *r=[device call:@{@"op":@"renderPipeline",@"vertexLibrary":v.library.handle,@"vertex":v.name,@"fragmentLibrary":f.library.handle,@"fragment":f.name,
        @"color":@[@(c.pixelFormat),@(c.blendingEnabled),@(c.sourceRGBBlendFactor),@(c.destinationRGBBlendFactor),@(c.rgbBlendOperation),@(c.sourceAlphaBlendFactor),@(c.destinationAlphaBlendFactor),@(c.alphaBlendOperation),@(c.writeMask)],
        @"vertexHandle":v.handle?:@0,@"fragmentHandle":f.handle?:@0,@"bufferWriteback":@YES,
        @"depth":@(d.depthAttachmentPixelFormat),@"stencil":@(d.stencilAttachmentPixelFormat),@"attributes":attributes,@"layouts":layouts} error:err];
    if(!r)return nil;DVMRenderPipeline *p=[DVMRenderPipeline new];p.owner=device;p.handle=r[@"handle"];p.functions=@[v,f];p.label=d.label;return p;
}
static id<MTLSamplerState> DVMNewSampler(DVMDevice *device,MTLSamplerDescriptor *d) {
    if(!d)return nil;NSError *e=nil;
    NSDictionary *r=[device call:@{@"op":@"sampler",@"values":@[@(d.minFilter),@(d.magFilter),@(d.mipFilter),@(d.sAddressMode),@(d.tAddressMode),@(d.rAddressMode),@(d.maxAnisotropy),@(d.normalizedCoordinates),@(d.lodMinClamp),@(d.lodMaxClamp),@(d.compareFunction)]} error:&e];
    if(!r)reject(e.description?:@"sampler creation");DVMSampler *s=[DVMSampler new];s.owner=device;s.handle=r[@"handle"];s.label=d.label;return s;
}
@interface DVMRenderEncoder : NSObject <MTLRenderCommandEncoder>
@property(nonatomic,strong) DVMCommand *command;
@property(nonatomic,strong) NSMutableArray *operations;
@property(nonatomic,strong) NSMutableDictionary *vertexBindings,*fragmentBindings;
@property(nonatomic) BOOL ended;
@property(atomic,copy) NSString *label;
@end
@implementation DVMRenderEncoder
- (id<MTLDevice>)device {return self.command.device;}
- (void)record:(NSArray *)op {
    if(self.ended)reject(@"render encoder ended");[self.command encoding];
    if(self.operations.count>=256)reject(@"render operation budget");[self.operations addObject:op];
}
- (NSNumber *)own:(id)object kind:(Class)kind {
    if(![object isKindOfClass:kind]||[object device]!=self.device)reject(@"foreign render resource");
    [self.command.resources addObject:object];return [object handle];
}
- (void)setRenderPipelineState:(id<MTLRenderPipelineState>)p {[self record:@[@"pipeline",[self own:p kind:DVMRenderPipeline.class]]];}
- (void)setDepthStencilState:(id<MTLDepthStencilState>)s {[self record:@[@"depth",s?[self own:s kind:DVMDepthState.class]:@0]];}
- (void)setCullMode:(MTLCullMode)m {[self record:@[@"cull",@(m)]];}
- (void)setFrontFacingWinding:(MTLWinding)w {[self record:@[@"winding",@(w)]];}
- (void)setTriangleFillMode:(MTLTriangleFillMode)m {[self record:@[@"fill",@(m)]];}
- (void)setStencilReferenceValue:(uint32_t)v {[self record:@[@"stencilRef",@(v)]];}
- (void)setViewport:(MTLViewport)v {[self record:@[@"viewport",@(v.originX),@(v.originY),@(v.width),@(v.height),@(v.znear),@(v.zfar)]];}
- (void)setScissorRect:(MTLScissorRect)s {[self record:@[@"scissor",@(s.x),@(s.y),@(s.width),@(s.height)]];}
- (void)setBlendColorRed:(float)r green:(float)g blue:(float)b alpha:(float)a {[self record:@[@"blend",@(r),@(g),@(b),@(a)]];}
- (void)buffer:(id<MTLBuffer>)b offset:(NSUInteger)o index:(NSUInteger)i stage:(NSString *)stage {
    if(i>=DVM_RENDER_BUFFERS||(b?(o>=b.length||o%16):o!=0))reject(@"render buffer bounds/alignment");
    [self record:@[stage,b?[self own:b kind:DVMBuffer.class]:@0,@(o),@(i)]];
    NSMutableDictionary *bindings=[stage isEqual:@"vertexBuffer"]?self.vertexBindings:self.fragmentBindings;
    if(b)bindings[@(i)]=b;else [bindings removeObjectForKey:@(i)];
}
- (void)setVertexBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i {[self buffer:b offset:o index:i stage:@"vertexBuffer"];}
- (void)setFragmentBuffer:(id<MTLBuffer>)b offset:(NSUInteger)o atIndex:(NSUInteger)i {[self buffer:b offset:o index:i stage:@"fragmentBuffer"];}
- (void)setVertexBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i {
    id<MTLBuffer> b=self.vertexBindings[@(i)];if(!b)reject(@"vertex offset requires a bound buffer");
    [self buffer:b offset:o index:i stage:@"vertexBuffer"];
}
- (void)setFragmentBufferOffset:(NSUInteger)o atIndex:(NSUInteger)i {
    id<MTLBuffer> b=self.fragmentBindings[@(i)];if(!b)reject(@"fragment offset requires a bound buffer");
    [self buffer:b offset:o index:i stage:@"fragmentBuffer"];
}
- (void)setVertexBuffers:(id<MTLBuffer> const __unsafe_unretained *)buffers offsets:(const NSUInteger *)offsets withRange:(NSRange)range {
    if(range.location>DVM_RENDER_BUFFERS||range.length>DVM_RENDER_BUFFERS-range.location||(!buffers&&range.length)||(!offsets&&range.length))reject(@"vertex buffer range");
    for(NSUInteger j=0;j<range.length;j++)[self setVertexBuffer:buffers[j] offset:offsets[j] atIndex:range.location+j];
}
- (void)setFragmentBuffers:(id<MTLBuffer> const __unsafe_unretained *)buffers offsets:(const NSUInteger *)offsets withRange:(NSRange)range {
    if(range.location>DVM_RENDER_BUFFERS||range.length>DVM_RENDER_BUFFERS-range.location||(!buffers&&range.length)||(!offsets&&range.length))reject(@"fragment buffer range");
    for(NSUInteger j=0;j<range.length;j++)[self setFragmentBuffer:buffers[j] offset:offsets[j] atIndex:range.location+j];
}
- (void)bytes:(const void *)p length:(NSUInteger)n index:(NSUInteger)i stage:(NSString *)stage {
    if(!p||!n||n>DVM_INLINE_BYTES||i>=DVM_RENDER_BUFFERS)reject(@"render inline bounds");
    [self record:@[stage,[[NSData dataWithBytes:p length:n] base64EncodedStringWithOptions:0],@(i)]];
    [([stage isEqual:@"vertexBytes"]?self.vertexBindings:self.fragmentBindings) removeObjectForKey:@(i)];
}
- (void)setVertexBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i {[self bytes:p length:n index:i stage:@"vertexBytes"];}
- (void)setFragmentBytes:(const void *)p length:(NSUInteger)n atIndex:(NSUInteger)i {[self bytes:p length:n index:i stage:@"fragmentBytes"];}
- (void)setFragmentTexture:(id<MTLTexture>)t atIndex:(NSUInteger)i {if(i>=DVM_FRAGMENT_TEXTURES)reject(@"fragment texture index");[self record:@[@"fragmentTexture",t?[self own:t kind:DVMTexture.class]:@0,@(i)]];}
- (void)setFragmentSamplerState:(id<MTLSamplerState>)s atIndex:(NSUInteger)i {if(i>=DVM_FRAGMENT_SAMPLERS)reject(@"fragment sampler index");[self record:@[@"fragmentSampler",s?[self own:s kind:DVMSampler.class]:@0,@(i)]];}
- (void)setFragmentTextures:(id<MTLTexture> const __unsafe_unretained *)textures withRange:(NSRange)range {
    if(range.location>DVM_FRAGMENT_TEXTURES||range.length>DVM_FRAGMENT_TEXTURES-range.location||(!textures&&range.length))reject(@"fragment texture range");
    for(NSUInteger j=0;j<range.length;j++)[self setFragmentTexture:textures[j] atIndex:range.location+j];
}
- (void)setFragmentSamplerStates:(id<MTLSamplerState> const __unsafe_unretained *)samplers withRange:(NSRange)range {
    if(range.location>DVM_FRAGMENT_SAMPLERS||range.length>DVM_FRAGMENT_SAMPLERS-range.location||(!samplers&&range.length))reject(@"fragment sampler range");
    for(NSUInteger j=0;j<range.length;j++)[self setFragmentSamplerState:samplers[j] atIndex:range.location+j];
}
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n {[self drawPrimitives:t vertexStart:s vertexCount:n instanceCount:1];}
- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)instances {
    [self record:@[@"draw",@(t),@(s),@(n),@(instances)]];
}
- (void)drawIndexedPrimitives:(MTLPrimitiveType)t indexCount:(NSUInteger)n indexType:(MTLIndexType)type indexBuffer:(id<MTLBuffer>)b indexBufferOffset:(NSUInteger)o {
    [self record:@[@"indexed",@(t),@(n),@(type),[self own:b kind:DVMBuffer.class],@(o)]];
}
- (void)pushDebugGroup:(NSString *)s {(void)s;}
- (void)popDebugGroup {}
- (void)insertDebugSignpost:(NSString *)s {(void)s;}
- (void)endEncoding {if(self.ended)reject(@"duplicate render end");self.ended=YES;self.command.openEncoders--;}
@end
static id<MTLRenderCommandEncoder> DVMNewRenderEncoder(DVMCommand *command,MTLRenderPassDescriptor *d) {
    [command encoding];if(command.openEncoders||!d)reject(@"render pass state");
    MTLRenderPassColorAttachmentDescriptor *a=d.colorAttachments[0];
    if(![(id)a.texture isKindOfClass:DVMTexture.class]||a.texture.device!=command.device||a.texture.textureType!=MTLTextureType2D||a.level>=a.texture.mipmapLevelCount||a.slice||a.depthPlane||a.resolveTexture||d.depthAttachment.texture||d.stencilAttachment.texture)
        reject(@"render pass outside single owned color target contract");
    for(unsigned i=1;i<8;i++)if(d.colorAttachments[i].texture)reject(@"multiple color attachments");
    [command.resources addObject:a.texture];
    NSMutableArray *ops=[NSMutableArray array];
    [command.commands addObject:@{@"kind":@"render",@"target":[(DVMTexture *)a.texture handle],@"level":@(a.level),@"load":@(a.loadAction),@"store":@(a.storeAction),@"clear":@[@(a.clearColor.red),@(a.clearColor.green),@(a.clearColor.blue),@(a.clearColor.alpha)],@"operations":ops}];
    command.openEncoders++;DVMRenderEncoder *e=[DVMRenderEncoder new];e.command=command;e.operations=ops;
    e.vertexBindings=[NSMutableDictionary dictionary];e.fragmentBindings=[NSMutableDictionary dictionary];return e;
}
