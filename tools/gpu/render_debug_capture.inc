// Compiled only into an explicitly diagnostic host replay worker. Blit copies
// change GPU scheduling: these captures are not performance measurements.
// Never expose this mechanism as a guest command or capability.
static NSUInteger DVMDebugCaptureBytes,DVMDebugCaptureIndex;
static void DVMDebugSnapshot(DVMHost *host,id<MTLCommandBuffer> command,
    NSMutableArray *captures,DVMEntry *entry,NSUInteger level,uint64_t seq,
    NSUInteger pass,NSString *phase){
    if(!getenv("DVM_RENDER_CAPTURE_DIR"))return;
    id<MTLTexture> texture=entry.object;
    if(texture.textureType!=MTLTextureType2D)return;
    NSUInteger width=MAX((NSUInteger)1,texture.width>>level),height=MAX((NSUInteger)1,texture.height>>level);
    NSUInteger row=(width*DVMFormatBytes(texture.pixelFormat)+255)&~255ul,length=row*height;
    if(!length||length>16*1024*1024||DVMDebugCaptureBytes>128*1024*1024-length){fprintf(stderr,"DVM_CAPTURE_FAIL byte budget\n");exit(78);}
    id<MTLBuffer> buffer=[host.device newBufferWithLength:length options:MTLResourceStorageModeShared];
    if(!buffer){fprintf(stderr,"DVM_CAPTURE_FAIL allocation\n");exit(78);}
    id<MTLBlitCommandEncoder> blit=[command blitCommandEncoder];
    [blit copyFromTexture:texture sourceSlice:0 sourceLevel:level sourceOrigin:MTLOriginMake(0,0,0)
        sourceSize:MTLSizeMake(width,height,1) toBuffer:buffer destinationOffset:0 destinationBytesPerRow:row destinationBytesPerImage:length];
    [blit endEncoding];DVMDebugCaptureBytes+=length;
    [captures addObject:@{@"buffer":buffer,@"index":@(DVMDebugCaptureIndex++),@"seq":@(seq),@"pass":@(pass),
        @"phase":phase,@"texture":@(entry.handle),@"level":@(level),@"format":@(texture.pixelFormat),
        @"width":@(width),@"height":@(height),@"row":@(row),@"bytes":@(length)}];
}
static void DVMDebugSave(NSArray *captures){
    for(NSDictionary *capture in captures){
        id<MTLBuffer> buffer=capture[@"buffer"];NSMutableDictionary *record=[capture mutableCopy];[record removeObjectForKey:@"buffer"];
        NSString *stem=[NSString stringWithFormat:@"capture-%06lu",[capture[@"index"] unsignedLongValue]];
        NSString *directory=@(getenv("DVM_RENDER_CAPTURE_DIR"));
        NSData *pixels=[NSData dataWithBytes:buffer.contents length:buffer.length];
        record[@"file"]=[stem stringByAppendingString:@".texels"];
        record[@"scope"]=@"instrumented-host-replay-intermediate-not-new-guest-output";
        NSData *json=[NSJSONSerialization dataWithJSONObject:record options:NSJSONWritingPrettyPrinted error:nil];
        if(!json||![pixels writeToFile:[directory stringByAppendingPathComponent:record[@"file"]] atomically:YES]||
           ![json writeToFile:[directory stringByAppendingPathComponent:[stem stringByAppendingString:@".json"]] atomically:YES]){
            fprintf(stderr,"DVM_CAPTURE_FAIL output\n");exit(78);
        }
    }
}
