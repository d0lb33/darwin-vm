// Blits share the ordered render submission, retention and completion contract.
@interface DVMBlitEncoder : NSObject <MTLBlitCommandEncoder>
@property(nonatomic,strong) DVMCommand *command;
@property(nonatomic,strong) NSMutableArray *operations;
@property(nonatomic) BOOL ended;
@property(atomic,copy) NSString *label;
@end
@implementation DVMBlitEncoder
- (id<MTLDevice>)device {return self.command.device;}
- (void)record:(NSArray *)op {
    [self.command encoding];if(self.ended||self.operations.count>=256)reject(@"blit encoder state/budget");
    [self.operations addObject:op];
}
- (NSNumber *)own:(id)object kind:(Class)kind {
    if(![object isKindOfClass:kind]||[object device]!=self.device)reject(@"foreign blit resource");
    if([object isKindOfClass:DVMTexture.class]&&([(DVMTexture *)object surfaceMapping]||[(DVMTexture *)object backingBuffer]))
        reject(@"blit IOSurface/buffer-backed texture ownership unsupported");
    [self.command.resources addObject:object];return [object handle];
}
- (void)copyFromTexture:(id<MTLTexture>)source sourceSlice:(NSUInteger)slice sourceLevel:(NSUInteger)level
          sourceOrigin:(MTLOrigin)origin sourceSize:(MTLSize)size toTexture:(id<MTLTexture>)destination
      destinationSlice:(NSUInteger)dstSlice destinationLevel:(NSUInteger)dstLevel destinationOrigin:(MTLOrigin)dst {
    NSNumber *s=[self own:source kind:DVMTexture.class],*d=[self own:destination kind:DVMTexture.class];
    if(slice||dstSlice||source.textureType!=MTLTextureType2D||destination.textureType!=MTLTextureType2D||
       source.pixelFormat!=destination.pixelFormat||level>=source.mipmapLevelCount||dstLevel>=destination.mipmapLevelCount||
       origin.z||dst.z||size.depth!=1||!size.width||!size.height||
       origin.x>MAX((NSUInteger)1,source.width>>level)||size.width>MAX((NSUInteger)1,source.width>>level)-origin.x||
       origin.y>MAX((NSUInteger)1,source.height>>level)||size.height>MAX((NSUInteger)1,source.height>>level)-origin.y||
       dst.x>MAX((NSUInteger)1,destination.width>>dstLevel)||size.width>MAX((NSUInteger)1,destination.width>>dstLevel)-dst.x||
       dst.y>MAX((NSUInteger)1,destination.height>>dstLevel)||size.height>MAX((NSUInteger)1,destination.height>>dstLevel)-dst.y||
       (source==destination&&level==dstLevel&&origin.x<dst.x+size.width&&dst.x<origin.x+size.width&&origin.y<dst.y+size.height&&dst.y<origin.y+size.height))
        reject([NSString stringWithFormat:@"blit copy rejected src=%lux%lu/m%lu/f%lu l=%lu xy=%lu,%lu dst=%lux%lu/m%lu/f%lu l=%lu xy=%lu,%lu size=%lu,%lu,%lu slices=%lu,%lu z=%lu,%lu same=%d",
            (unsigned long)source.width,(unsigned long)source.height,(unsigned long)source.mipmapLevelCount,(unsigned long)source.pixelFormat,(unsigned long)level,(unsigned long)origin.x,(unsigned long)origin.y,
            (unsigned long)destination.width,(unsigned long)destination.height,(unsigned long)destination.mipmapLevelCount,(unsigned long)destination.pixelFormat,(unsigned long)dstLevel,(unsigned long)dst.x,(unsigned long)dst.y,
            (unsigned long)size.width,(unsigned long)size.height,(unsigned long)size.depth,(unsigned long)slice,(unsigned long)dstSlice,(unsigned long)origin.z,(unsigned long)dst.z,source==destination]);
    [self record:@[@"copyTexture",s,@(level),@[@(origin.x),@(origin.y)],@[@(size.width),@(size.height)],d,@(dstLevel),@[@(dst.x),@(dst.y)]]];
}
- (void)generateMipmapsForTexture:(id<MTLTexture>)texture {
    NSNumber *h=[self own:texture kind:DVMTexture.class];
    if(texture.textureType!=MTLTextureType2D||texture.storageMode!=MTLStorageModePrivate||texture.mipmapLevelCount<2||
       !DVMColorFormat(texture.pixelFormat))reject(@"mipmap generation format/storage contract");
    [self record:@[@"mips",h]];
}
- (void)fillBuffer:(id<MTLBuffer>)buffer range:(NSRange)range value:(uint8_t)value {
    NSNumber *h=[self own:buffer kind:DVMBuffer.class];
    if(!range.length||range.location%4||range.length%4||range.location>buffer.length||range.length>buffer.length-range.location)
        reject(@"blit fill bounds/alignment");
    [self record:@[@"fillBuffer",h,@(range.location),@(range.length),@(value)]];
}
- (void)copyFromBuffer:(id<MTLBuffer>)source sourceOffset:(NSUInteger)offset toBuffer:(id<MTLBuffer>)destination destinationOffset:(NSUInteger)dst size:(NSUInteger)size {
    NSNumber *s=[self own:source kind:DVMBuffer.class],*d=[self own:destination kind:DVMBuffer.class];
    if(!size||offset%4||dst%4||size%4||offset>source.length||size>source.length-offset||dst>destination.length||size>destination.length-dst||
       (source==destination&&offset<dst+size&&dst<offset+size))reject(@"blit buffer bounds/alignment/alias");
    [self record:@[@"copyBuffer",s,@(offset),d,@(dst),@(size)]];
}
- (void)pushDebugGroup:(NSString *)s {(void)s;}
- (void)popDebugGroup {}
- (void)insertDebugSignpost:(NSString *)s {(void)s;}
- (void)endEncoding {if(self.ended)reject(@"duplicate blit end");self.ended=YES;self.command.openEncoders--;}
@end
static id<MTLBlitCommandEncoder> DVMNewBlitEncoder(DVMCommand *command) {
    [command encoding];if(command.openEncoders||command.commands.count>=32)reject(@"blit command state/budget");
    for(NSDictionary *pass in command.commands)if(!pass[@"kind"])reject(@"compute/blit mixing unsupported");
    DVMBlitEncoder *encoder=[DVMBlitEncoder new];encoder.command=command;encoder.operations=[NSMutableArray array];
    [command.commands addObject:@{@"kind":@"blit",@"operations":encoder.operations}];command.openEncoders++;
    return encoder;
}
