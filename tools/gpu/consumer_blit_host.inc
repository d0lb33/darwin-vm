// Validate before native encoding; the enclosing batch is never committed if
// any later operation fails. No IOSurface lease or buffer-texture alias bypass.
static BOOL BlitPair(id raw) {
    return [raw isKindOfClass:NSArray.class]&&[raw count]==2&&RenderUInt(raw[0],DVM_TEXTURE_DIMENSION)&&RenderUInt(raw[1],DVM_TEXTURE_DIMENSION);
}
static NSString *EncodeBlit(DVMHost *host,id<MTLCommandBuffer> cb,NSDictionary *pass,NSMutableSet *written) {
    NSArray *ops=pass[@"operations"];
    if(![ops isKindOfClass:NSArray.class]||ops.count>256)return @"blit operation budget";
    id<MTLBlitCommandEncoder> encoder=[cb blitCommandEncoder];if(!encoder)return @"native blit encoder allocation";
    NSString *failure=nil;
    for(NSArray *op in ops){
        if(![op isKindOfClass:NSArray.class]||!op.count||![op[0] isKindOfClass:NSString.class]){failure=@"blit operation shape";break;}
        if([op[0] isEqual:@"copyTexture"]&&op.count==8){
            DVMEntry *s=Entry(host,op[1],@"texture"),*d=Entry(host,op[5],@"texture");
            id<MTLTexture> src=s.object,dst=d.object;uint64_t sl,dl;
            if(!s||!d||s.sharedRender||d.sharedRender||src.buffer||dst.buffer||src.textureType!=MTLTextureType2D||dst.textureType!=MTLTextureType2D||
               src.pixelFormat!=dst.pixelFormat||!Number(op[2],&sl)||!Number(op[6],&dl)||sl>=src.mipmapLevelCount||dl>=dst.mipmapLevelCount||
               !BlitPair(op[3])||!BlitPair(op[4])||!BlitPair(op[7])){failure=@"blit texture ownership/subresource";break;}
            NSUInteger sx=[op[3][0] unsignedIntegerValue],sy=[op[3][1] unsignedIntegerValue],w=[op[4][0] unsignedIntegerValue],h=[op[4][1] unsignedIntegerValue],dx=[op[7][0] unsignedIntegerValue],dy=[op[7][1] unsignedIntegerValue];
            NSUInteger sw=MAX((NSUInteger)1,src.width>>sl),sh=MAX((NSUInteger)1,src.height>>sl),dw=MAX((NSUInteger)1,dst.width>>dl),dh=MAX((NSUInteger)1,dst.height>>dl);
            if(!w||!h||sx>sw||w>sw-sx||sy>sh||h>sh-sy||dx>dw||w>dw-dx||dy>dh||h>dh-dy||
               (TextureRoot(s)==TextureRoot(d)&&TextureBaseLevel(s)+sl==TextureBaseLevel(d)+dl&&sx<dx+w&&dx<sx+w&&sy<dy+h&&dy<sy+h)){failure=@"blit texture region bounds/overlap";break;}
            [encoder copyFromTexture:src sourceSlice:0 sourceLevel:sl sourceOrigin:MTLOriginMake(sx,sy,0) sourceSize:MTLSizeMake(w,h,1)
                          toTexture:dst destinationSlice:0 destinationLevel:dl destinationOrigin:MTLOriginMake(dx,dy,0)];
        }else if([op[0] isEqual:@"mips"]&&op.count==2){
            DVMEntry *e=Entry(host,op[1],@"texture");id<MTLTexture> t=e.object;
            if(!e||e.sharedRender||t.buffer||t.textureType!=MTLTextureType2D||t.storageMode!=MTLStorageModePrivate||t.mipmapLevelCount<2||
               !DVMColorFormat(t.pixelFormat)){failure=@"blit mipmap format/storage";break;}
            [encoder generateMipmapsForTexture:t];
        }else if([op[0] isEqual:@"fillBuffer"]&&op.count==5){
            DVMEntry *e=Entry(host,op[1],@"buffer");uint64_t offset,length,value;
            if(!e||!Number(op[2],&offset)||!Number(op[3],&length)||!Number(op[4],&value)||value>255||!length||offset%4||length%4||offset>e.textureBytes||length>e.textureBytes-offset){failure=@"blit fill bounds";break;}
            [encoder fillBuffer:e.object range:NSMakeRange(offset,length) value:value];[written addObject:@(e.handle)];
        }else if([op[0] isEqual:@"copyBuffer"]&&op.count==6){
            DVMEntry *s=Entry(host,op[1],@"buffer"),*d=Entry(host,op[3],@"buffer");uint64_t so,offset,length;
            if(!s||!d||!Number(op[2],&so)||!Number(op[4],&offset)||!Number(op[5],&length)||!length||so%4||offset%4||length%4||
               so>s.textureBytes||length>s.textureBytes-so||offset>d.textureBytes||length>d.textureBytes-offset||
               (s==d&&so<offset+length&&offset<so+length)){failure=@"blit buffer bounds/alias";break;}
            [encoder copyFromBuffer:s.object sourceOffset:so toBuffer:d.object destinationOffset:offset size:length];[written addObject:@(d.handle)];
        }else {failure=@"unsupported blit operation";break;}
    }
    [encoder endEncoding];return failure;
}
