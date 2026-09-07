#include <math.h>
static BOOL RenderNumbers(id raw,NSUInteger count) {
    if(![raw isKindOfClass:NSArray.class]||[raw count]!=count)return NO;
    for(id x in raw)if(![x isKindOfClass:NSNumber.class]||!isfinite([x doubleValue]))return NO;
    return YES;
}
static BOOL RenderUInt(id raw,uint64_t max) {uint64_t n;return Number(raw,&n)&&n<=max;}
static BOOL RenderBindingsFit(DVMEntry *pipeline,NSDictionary *vertex,NSDictionary *fragment,NSDictionary *textures,NSDictionary *samplers,NSMutableSet *written) {
    for(NSArray *need in pipeline.object[@"bindings"]){
        NSUInteger stage=[need[0] unsignedIntegerValue],type=[need[1] unsignedIntegerValue];id index=need[2];
        if(type==MTLArgumentTypeBuffer){
            NSArray *b=(stage?fragment:vertex)[index];
            if(!b||[b[0] unsignedIntegerValue]<[need[3] unsignedIntegerValue])return NO;
            if([need[4] unsignedIntegerValue]!=MTLArgumentAccessReadOnly){
                // Inline bytes have no owned allocation to publish back.
                if(b.count!=2)return NO;
                [written addObject:b[1]];
            }
        }
        else if(type==MTLArgumentTypeTexture){DVMEntry *t=textures[index];if(!t||[(id<MTLTexture>)t.object textureType]!=[need[5] unsignedIntegerValue])return NO;}
        else if(type==MTLArgumentTypeSampler){if(!samplers[index])return NO;}
    }
    // Buffer/linear-texture cache aliasing needs a separately tested contract.
    // An intervening completed submission publishes the CPU shadow safely.
    for(DVMEntry *texture in textures.allValues)
        if(texture.parent&&[written containsObject:@(texture.parent.handle)])return NO;
    return YES;
}
static NSDictionary *RenderPipeline(DVMHost *host,uint64_t seq,NSDictionary *r) {
    DVMEntry *vl=Entry(host,r[@"vertexLibrary"],@"library"),*fl=Entry(host,r[@"fragmentLibrary"],@"library");
    if(!vl||!fl||![r[@"vertex"] isKindOfClass:NSString.class]||![r[@"fragment"] isKindOfClass:NSString.class])return HostError(seq,EINVAL,@"render function ownership");
    id<MTLFunction> v=[r[@"vertexHandle"] unsignedLongLongValue]?Entry(host,r[@"vertexHandle"],@"function").object:[vl.object newFunctionWithName:r[@"vertex"]];
    id<MTLFunction> f=[r[@"fragmentHandle"] unsignedLongLongValue]?Entry(host,r[@"fragmentHandle"],@"function").object:[fl.object newFunctionWithName:r[@"fragment"]];
    if(!v||!f||v.functionType!=MTLFunctionTypeVertex||f.functionType!=MTLFunctionTypeFragment)return HostError(seq,EINVAL,@"render function stages");
    NSArray *c=r[@"color"],*attrs=r[@"attributes"],*layouts=r[@"layouts"];
    if(!RenderNumbers(c,9)||(![c[0] isEqual:@80]&&![c[0] isEqual:@70])||[c[1] unsignedIntValue]>1||
       !RenderUInt(c[2],18)||!RenderUInt(c[3],18)||!RenderUInt(c[4],4)||!RenderUInt(c[5],18)||!RenderUInt(c[6],18)||!RenderUInt(c[7],4)||!RenderUInt(c[8],15)||
       ![r[@"depth"] isEqual:@0]||![r[@"stencil"] isEqual:@0]||![attrs isKindOfClass:NSArray.class]||attrs.count>DVM_RENDER_BUFFERS||![layouts isKindOfClass:NSArray.class]||layouts.count>DVM_RENDER_BUFFERS)
        return HostError(seq,ENOTSUP,@"render attachment/layout contract");
    MTLRenderPipelineDescriptor *d=[MTLRenderPipelineDescriptor new];d.vertexFunction=v;d.fragmentFunction=f;
    MTLRenderPipelineColorAttachmentDescriptor *a=d.colorAttachments[0];
    a.pixelFormat=[c[0] unsignedIntegerValue];a.blendingEnabled=[c[1] boolValue];a.sourceRGBBlendFactor=[c[2] unsignedIntegerValue];a.destinationRGBBlendFactor=[c[3] unsignedIntegerValue];a.rgbBlendOperation=[c[4] unsignedIntegerValue];a.sourceAlphaBlendFactor=[c[5] unsignedIntegerValue];a.destinationAlphaBlendFactor=[c[6] unsignedIntegerValue];a.alphaBlendOperation=[c[7] unsignedIntegerValue];a.writeMask=[c[8] unsignedIntegerValue];
    d.vertexDescriptor=[MTLVertexDescriptor vertexDescriptor];
    NSMutableSet *seen=[NSMutableSet set];
    for(NSArray *x in attrs){
        if(!RenderNumbers(x,4)||!RenderUInt(x[0],30)||!RenderUInt(x[1],54)||![x[1] unsignedIntValue]||!RenderUInt(x[2],4095)||!RenderUInt(x[3],DVM_RENDER_BUFFERS-1)||[seen containsObject:x[0]])return HostError(seq,EINVAL,@"vertex attribute");
        [seen addObject:x[0]];MTLVertexAttributeDescriptor *b=d.vertexDescriptor.attributes[[x[0] unsignedIntegerValue]];b.format=[x[1] unsignedIntegerValue];b.offset=[x[2] unsignedIntegerValue];b.bufferIndex=[x[3] unsignedIntegerValue];
    }
    [seen removeAllObjects];
    for(NSArray *x in layouts){
        if(!RenderNumbers(x,4)||!RenderUInt(x[0],DVM_RENDER_BUFFERS-1)||!RenderUInt(x[1],4096)||![x[1] unsignedIntValue]||!RenderUInt(x[2],2)||![x[3] isEqual:@1]||[seen containsObject:x[0]])return HostError(seq,EINVAL,@"vertex layout");
        [seen addObject:x[0]];MTLVertexBufferLayoutDescriptor *b=d.vertexDescriptor.layouts[[x[0] unsignedIntegerValue]];b.stride=[x[1] unsignedIntegerValue];b.stepFunction=[x[2] unsignedIntegerValue];b.stepRate=1;
    }
    NSError *e=nil;MTLRenderPipelineReflection *reflection=nil;
    id<MTLRenderPipelineState> p=[host.device newRenderPipelineStateWithDescriptor:d options:MTLPipelineOptionArgumentInfo reflection:&reflection error:&e];
    if(!p)return NSErrorReply(seq,e,@"render pipeline creation");
    NSMutableArray *bindings=[NSMutableArray array];
    NSArray *stages=@[reflection.vertexArguments?:@[],reflection.fragmentArguments?:@[]];
    if(getenv("DVM_RENDER_REFLECTION"))for(unsigned stage=0;stage<2;stage++)for(MTLArgument *arg in stages[stage])if(arg.active)
        fprintf(stderr,"DVM_RENDER_BINDING seq=%llu stage=%u name=%s index=%lu type=%lu access=%lu array=%lu size=%lu textureType=%lu\n",(unsigned long long)seq,stage,arg.name.UTF8String,(unsigned long)arg.index,(unsigned long)arg.type,(unsigned long)arg.access,(unsigned long)arg.arrayLength,(unsigned long)(arg.type==MTLArgumentTypeBuffer?arg.bufferDataSize:0),(unsigned long)(arg.type==MTLArgumentTypeTexture?arg.textureType:0));
    for(unsigned stage=0;stage<2;stage++)for(MTLArgument *arg in stages[stage])if(arg.active){
        BOOL writableBuffer=!stage&&arg.type==MTLArgumentTypeBuffer&&[r[@"bufferWriteback"] isEqual:@YES]&&
                            (arg.access==MTLArgumentAccessReadWrite||arg.access==MTLArgumentAccessWriteOnly);
        if(arg.index>=(arg.type==MTLArgumentTypeBuffer?DVM_RENDER_BUFFERS:arg.type==MTLArgumentTypeTexture?DVM_FRAGMENT_TEXTURES:DVM_FRAGMENT_SAMPLERS)||arg.arrayLength>1||(arg.access!=MTLArgumentAccessReadOnly&&!writableBuffer)||
           (arg.type!=MTLArgumentTypeBuffer&&arg.type!=MTLArgumentTypeTexture&&arg.type!=MTLArgumentTypeSampler)||
           (!stage&&arg.type!=MTLArgumentTypeBuffer)||
           (arg.type==MTLArgumentTypeTexture&&arg.textureType!=MTLTextureType2D&&arg.textureType!=MTLTextureType3D))return HostError(seq,ENOTSUP,[NSString stringWithFormat:@"shader binding outside render contract: stage=%u name=%@ index=%lu type=%lu access=%lu array=%lu textureType=%lu",stage,arg.name,(unsigned long)arg.index,(unsigned long)arg.type,(unsigned long)arg.access,(unsigned long)arg.arrayLength,(unsigned long)(arg.type==MTLArgumentTypeTexture?arg.textureType:0)]);
        [bindings addObject:@[@(stage),@(arg.type),@(arg.index),@(arg.type==MTLArgumentTypeBuffer?arg.bufferDataSize:0),@(arg.access),@(arg.type==MTLArgumentTypeTexture?arg.textureType:0)]];
    }
    DVMEntry *entry;if(!Add(host,@"renderPipeline",@{@"state":p,@"layouts":layouts,@"format":c[0],@"bindings":bindings},&entry))return HostError(seq,ENOSPC,@"render state table");
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle)};
}
static NSDictionary *Sampler(DVMHost *host,uint64_t seq,NSDictionary *r) {
    NSArray *x=r[@"values"];
    if(!RenderNumbers(x,11)||!RenderUInt(x[0],1)||!RenderUInt(x[1],1)||!RenderUInt(x[2],2)||!RenderUInt(x[3],4)||!RenderUInt(x[4],4)||!RenderUInt(x[5],4)||!RenderUInt(x[6],16)||![x[6] unsignedIntValue]||[x[7] unsignedIntValue]>1||[x[8] doubleValue]<0||[x[9] doubleValue]<[x[8] doubleValue]||!RenderUInt(x[10],7))return HostError(seq,EINVAL,@"sampler descriptor");
    MTLSamplerDescriptor *d=[MTLSamplerDescriptor new];d.minFilter=[x[0] unsignedIntegerValue];d.magFilter=[x[1] unsignedIntegerValue];d.mipFilter=[x[2] unsignedIntegerValue];d.sAddressMode=[x[3] unsignedIntegerValue];d.tAddressMode=[x[4] unsignedIntegerValue];d.rAddressMode=[x[5] unsignedIntegerValue];d.maxAnisotropy=[x[6] unsignedIntegerValue];d.normalizedCoordinates=[x[7] boolValue];d.lodMinClamp=[x[8] floatValue];d.lodMaxClamp=[x[9] floatValue];d.compareFunction=[x[10] unsignedIntegerValue];
    id<MTLSamplerState> s=[host.device newSamplerStateWithDescriptor:d];DVMEntry *entry;
    if(!s||!Add(host,@"sampler",s,&entry))return HostError(seq,ENOMEM,@"sampler allocation");
    return @{@"seq":@(seq),@"ok":@YES,@"handle":@(entry.handle)};
}
static NSDictionary *RenderSubmit(DVMHost *host,uint64_t seq,NSDictionary *r) {
    NSArray *tasks=r[@"guestTaskIDs"]?:@[];
    if(![tasks isKindOfClass:NSArray.class]||tasks.count>16)return HostError(seq,EINVAL,@"guest attribution extent");
    for(id task in tasks)if(!RenderUInt(task,UINT32_MAX))return HostError(seq,EINVAL,@"guest task ID");
    NSArray *passes=r[@"commands"],*uploads=r[@"uploads"],*readbacks=r[@"readbacks"];
    if(![passes isKindOfClass:NSArray.class]||passes.count>32||![uploads isKindOfClass:NSArray.class]||uploads.count>128||![readbacks isKindOfClass:NSArray.class]||readbacks.count>128)return HostError(seq,EINVAL,@"render batch extent");
    NSMutableArray *transfers=[NSMutableArray array],*reads=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];NSUInteger totalRead=0;
    for(NSDictionary *u in uploads){
        if(![u isKindOfClass:NSDictionary.class])return HostError(seq,EINVAL,@"render upload shape");
        BOOL buffer=u[@"buffer"]!=nil;DVMEntry *e=Entry(host,u[buffer?@"buffer":@"texture"],buffer?@"buffer":@"texture");NSData *data=DVMBPayload(u[@"data"]);
        if(!e||!data||data.length!=e.textureBytes||(buffer&&u[@"texture"])||(!buffer&&![u[@"row"] isEqual:@(e.row)])||[seen containsObject:@(e.handle)])return HostError(seq,EINVAL,@"render upload extent/ownership");
        [seen addObject:@(e.handle)];[transfers addObject:@[e,data]];
    }
    [seen removeAllObjects];
    for(id h in readbacks){DVMEntry *e=Entry(host,h,@"buffer");if(!e||[seen containsObject:h]||e.textureBytes>DVM_BUFFER_BYTES-totalRead)return HostError(seq,EINVAL,@"render readback extent");[seen addObject:h];totalRead+=e.textureBytes;[reads addObject:e];}
    // Encode into an uncommitted command buffer. Validate every pass before any
    // upload or commit, so a bad later operation cannot execute an earlier pass.
    id<MTLCommandBuffer> cb=[host.queue commandBuffer];NSUInteger draws=0;
    NSMutableSet *written=[NSMutableSet set];
    for(NSDictionary *pass in passes){
        if(![pass isKindOfClass:NSDictionary.class]||![pass[@"kind"] isEqual:@"render"])return HostError(seq,EINVAL,@"mixed render batch");
        DVMEntry *target=Entry(host,pass[@"target"],@"texture");NSArray *clear=pass[@"clear"],*ops=pass[@"operations"];
        if(!target||[(id<MTLTexture>)target.object textureType]!=MTLTextureType2D||!([(id<MTLTexture>)target.object usage]&MTLTextureUsageRenderTarget)||!RenderUInt(pass[@"load"],2)||!RenderUInt(pass[@"store"],1)||!RenderNumbers(clear,4)||![ops isKindOfClass:NSArray.class]||ops.count>256)return HostError(seq,EINVAL,@"render pass descriptor");
        MTLRenderPassDescriptor *d=[MTLRenderPassDescriptor renderPassDescriptor];d.colorAttachments[0].texture=target.object;d.colorAttachments[0].loadAction=[pass[@"load"] unsignedIntegerValue];d.colorAttachments[0].storeAction=[pass[@"store"] unsignedIntegerValue];d.colorAttachments[0].clearColor=MTLClearColorMake([clear[0] doubleValue],[clear[1] doubleValue],[clear[2] doubleValue],[clear[3] doubleValue]);
        id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:d];
        DVMEntry *pipeline=nil;NSMutableDictionary *vertexBuffers=[NSMutableDictionary dictionary],*fragmentBuffers=[NSMutableDictionary dictionary],*fragmentTextures=[NSMutableDictionary dictionary],*fragmentSamplers=[NSMutableDictionary dictionary];NSString *failure=nil;
        for(NSArray *op in ops){
            if(![op isKindOfClass:NSArray.class]||!op.count||![op[0] isKindOfClass:NSString.class]){failure=@"render operation shape";break;}
            NSString *name=op[0];
            if([name isEqual:@"pipeline"]&&op.count==2){pipeline=Entry(host,op[1],@"renderPipeline");if(!pipeline||![pipeline.object[@"format"] isEqual:@(target.format)]){failure=@"render pipeline target mismatch";break;}[encoder setRenderPipelineState:pipeline.object[@"state"]];}
            else if([name isEqual:@"depth"]&&op.count==2){DVMEntry *e=Entry(host,op[1],@"depthState");if(!e&&![op[1] isEqual:@0]){failure=@"depth state ownership";break;}[encoder setDepthStencilState:e.object];}
            else if(([name isEqual:@"cull"]||[name isEqual:@"winding"]||[name isEqual:@"fill"]||[name isEqual:@"stencilRef"])&&op.count==2&&RenderUInt(op[1],[name isEqual:@"stencilRef"]?UINT32_MAX:[name isEqual:@"cull"]?2:1)){
                NSUInteger value=[op[1] unsignedIntegerValue];if([name isEqual:@"cull"])[encoder setCullMode:value];else if([name isEqual:@"winding"])[encoder setFrontFacingWinding:value];else if([name isEqual:@"fill"])[encoder setTriangleFillMode:value];else [encoder setStencilReferenceValue:(uint32_t)value];
            } else if([name isEqual:@"viewport"]&&op.count==7&&RenderNumbers([op subarrayWithRange:NSMakeRange(1,6)],6)){
                MTLViewport v={[op[1] doubleValue],[op[2] doubleValue],[op[3] doubleValue],[op[4] doubleValue],[op[5] doubleValue],[op[6] doubleValue]};if(v.width<=0||v.height<=0||v.width>4096||v.height>4096||fabs(v.originX)>4096||fabs(v.originY)>4096||v.znear<0||v.zfar>1||v.znear>v.zfar){failure=@"viewport bounds";break;}[encoder setViewport:v];
            } else if([name isEqual:@"scissor"]&&op.count==5&&RenderUInt(op[1],target.width)&&RenderUInt(op[2],target.height)&&RenderUInt(op[3],target.width)&&RenderUInt(op[4],target.height)){
                MTLScissorRect s={[op[1] unsignedIntegerValue],[op[2] unsignedIntegerValue],[op[3] unsignedIntegerValue],[op[4] unsignedIntegerValue]};if(!s.width||!s.height||s.width>target.width-s.x||s.height>target.height-s.y){failure=@"scissor bounds";break;}[encoder setScissorRect:s];
            } else if([name isEqual:@"blend"]&&op.count==5&&RenderNumbers([op subarrayWithRange:NSMakeRange(1,4)],4)) [encoder setBlendColorRed:[op[1] floatValue] green:[op[2] floatValue] blue:[op[3] floatValue] alpha:[op[4] floatValue]];
            else if(([name isEqual:@"vertexBuffer"]||[name isEqual:@"fragmentBuffer"])&&op.count==4&&RenderUInt(op[3],DVM_RENDER_BUFFERS-1)){
                if([op[1] isEqual:@0]&&[op[2] isEqual:@0]){
                    if([name isEqual:@"vertexBuffer"]){[encoder setVertexBuffer:nil offset:0 atIndex:[op[3] unsignedIntegerValue]];[vertexBuffers removeObjectForKey:op[3]];}
                    else {[encoder setFragmentBuffer:nil offset:0 atIndex:[op[3] unsignedIntegerValue]];[fragmentBuffers removeObjectForKey:op[3]];}
                    continue;
                }
                DVMEntry *b=Entry(host,op[1],@"buffer");uint64_t offset;if(!b||!Number(op[2],&offset)||offset>=b.textureBytes||offset%16){failure=@"render buffer bounds";break;}
                if([name isEqual:@"vertexBuffer"]){[encoder setVertexBuffer:b.object offset:offset atIndex:[op[3] unsignedIntegerValue]];vertexBuffers[op[3]]=@[@(b.textureBytes-offset),@(b.handle)];}else {[encoder setFragmentBuffer:b.object offset:offset atIndex:[op[3] unsignedIntegerValue]];fragmentBuffers[op[3]]=@[@(b.textureBytes-offset),@(b.handle)];}
            } else if(([name isEqual:@"vertexBytes"]||[name isEqual:@"fragmentBytes"])&&op.count==3&&RenderUInt(op[2],DVM_RENDER_BUFFERS-1)){
                NSData *data=DVMBPayload(op[1]);if(!data.length||data.length>DVM_INLINE_BYTES){failure=@"render inline bounds";break;}if([name isEqual:@"vertexBytes"]){[encoder setVertexBytes:data.bytes length:data.length atIndex:[op[2] unsignedIntegerValue]];vertexBuffers[op[2]]=@[@(data.length)];}else {[encoder setFragmentBytes:data.bytes length:data.length atIndex:[op[2] unsignedIntegerValue]];fragmentBuffers[op[2]]=@[@(data.length)];}
            } else if(([name isEqual:@"fragmentTexture"]||[name isEqual:@"fragmentSampler"])&&op.count==3&&RenderUInt(op[2],[name isEqual:@"fragmentTexture"]?DVM_FRAGMENT_TEXTURES-1:DVM_FRAGMENT_SAMPLERS-1)){
                BOOL texture=[name isEqual:@"fragmentTexture"];DVMEntry *e=Entry(host,op[1],texture?@"texture":@"sampler");if((!e&&![op[1] isEqual:@0])||(texture&&e&&(!([(id<MTLTexture>)e.object usage]&MTLTextureUsageShaderRead)||e==target))){failure=@"fragment resource ownership/usage";break;}if(texture){[encoder setFragmentTexture:e.object atIndex:[op[2] unsignedIntegerValue]];fragmentTextures[op[2]]=e;}else {[encoder setFragmentSamplerState:e.object atIndex:[op[2] unsignedIntegerValue]];fragmentSamplers[op[2]]=e;}
            } else if([name isEqual:@"draw"]&&op.count==5&&RenderUInt(op[1],4)&&RenderUInt(op[2],4096)&&RenderUInt(op[3],4096)&&RenderUInt(op[4],64)&&pipeline){
                NSUInteger start=[op[2] unsignedIntegerValue],count=[op[3] unsignedIntegerValue],instances=[op[4] unsignedIntegerValue];if(!count||!instances||start+count>4096){failure=@"draw extent";break;}
                for(NSArray *layout in pipeline.object[@"layouts"]){NSUInteger elements=[layout[2] unsignedIntegerValue]==2?instances:[layout[2] unsignedIntegerValue]==0?1:start+count;NSArray *bound=vertexBuffers[layout[0]];if(!bound||elements*[layout[1] unsignedIntegerValue]>[bound[0] unsignedIntegerValue]){failure=@"vertex footprint exceeds binding";break;}}
                if(!RenderBindingsFit(pipeline,vertexBuffers,fragmentBuffers,fragmentTextures,fragmentSamplers,written))failure=@"missing or undersized render binding";
                if(failure)break;[encoder drawPrimitives:[op[1] unsignedIntegerValue] vertexStart:start vertexCount:count instanceCount:instances];draws++;
                // Apple TBDR cannot wait for fragment stores within this pass.
                // Only vertex writes are admitted; order their later consumers.
                if(written.count)[encoder memoryBarrierWithScope:MTLBarrierScopeBuffers afterStages:MTLRenderStageVertex beforeStages:MTLRenderStageVertex|MTLRenderStageFragment];
            } else if([name isEqual:@"indexed"]&&op.count==6&&RenderUInt(op[1],4)&&RenderUInt(op[2],4096)&&RenderUInt(op[3],1)&&pipeline){
                DVMEntry *b=Entry(host,op[4],@"buffer");uint64_t offset;NSUInteger count=[op[2] unsignedIntegerValue],size=[op[3] unsignedIntegerValue]?4:2;
                if(!b||[written containsObject:@(b.handle)]||!count||!Number(op[5],&offset)||offset%size||offset>b.textureBytes||count*size>b.textureBytes-offset){failure=@"index buffer bounds";break;}
                const uint8_t *data=[(id<MTLBuffer>)b.object contents];
                for(NSArray *transfer in transfers)if(transfer[0]==b)data=[(NSData *)transfer[1] bytes];
                NSUInteger maximum=0;for(NSUInteger i=0;i<count;i++){uint32_t value=0;memcpy(&value,data+offset+i*size,size);maximum=MAX(maximum,value);}
                if(maximum>=4096){failure=@"index value bounds";break;}
                for(NSArray *layout in pipeline.object[@"layouts"]){NSUInteger elements=[layout[2] unsignedIntegerValue]==1?maximum+1:1;NSArray *bound=vertexBuffers[layout[0]];if(!bound||elements*[layout[1] unsignedIntegerValue]>[bound[0] unsignedIntegerValue]){failure=@"indexed vertex footprint exceeds binding";break;}}
                if(!RenderBindingsFit(pipeline,vertexBuffers,fragmentBuffers,fragmentTextures,fragmentSamplers,written))failure=@"missing or undersized indexed binding";
                if([written containsObject:@(b.handle)])failure=@"GPU-written index input needs a separate completed submission";
                if(failure)break;[encoder drawIndexedPrimitives:[op[1] unsignedIntegerValue] indexCount:count indexType:[op[3] unsignedIntegerValue] indexBuffer:b.object indexBufferOffset:offset];draws++;
                // Apple TBDR cannot wait for fragment stores within this pass.
                // Only vertex writes are admitted; order their later consumers.
                if(written.count)[encoder memoryBarrierWithScope:MTLBarrierScopeBuffers afterStages:MTLRenderStageVertex beforeStages:MTLRenderStageVertex|MTLRenderStageFragment];
            } else {failure=[@"unsupported render operation: " stringByAppendingString:name];break;}
        }
        [encoder endEncoding];if(failure)return HostError(seq,EINVAL,failure);
    }
    NSUInteger totalWritten=0;
    for(NSNumber *handle in written){DVMEntry *b=Entry(host,handle,@"buffer");if(!b||b.textureBytes>DVM_BUFFER_BYTES-totalWritten)return HostError(seq,ENOTSUP,@"render writeback budget");totalWritten+=b.textureBytes;}
    for(NSArray *transfer in transfers){DVMEntry *e=transfer[0];NSData *data=transfer[1];if([e.kind isEqual:@"buffer"])memcpy([(id<MTLBuffer>)e.object contents],data.bytes,data.length);else [(id<MTLTexture>)e.object replaceRegion:MTLRegionMake3D(0,0,0,e.width,e.height,[(id<MTLTexture>)e.object depth]) mipmapLevel:0 slice:0 withBytes:data.bytes bytesPerRow:e.row bytesPerImage:e.row*e.height];}
    [cb commit];[cb waitUntilCompleted];host.submissions++;
    if(cb.status!=MTLCommandBufferStatusCompleted)return NSErrorReply(seq,cb.error,@"render GPU completion");
    host.renderPasses+=passes.count;host.renderDraws+=draws;
    NSMutableDictionary *result=[NSMutableDictionary dictionary];for(DVMEntry *e in reads)result[@(e.handle).stringValue]=[[NSData dataWithBytes:[(id<MTLBuffer>)e.object contents] length:e.textureBytes] base64EncodedStringWithOptions:0];
    fprintf(stderr,"DVM_DRIVER_RENDER seq=%llu passes=%lu draws=%lu status=%lu gpu_us=%.3f\n",(unsigned long long)seq,(unsigned long)passes.count,(unsigned long)draws,(unsigned long)cb.status,(cb.GPUEndTime-cb.GPUStartTime)*1e6);
    return @{@"seq":@(seq),@"ok":@YES,@"status":@(cb.status),@"buffers":result,@"passes":@(passes.count),@"draws":@(draws),@"gpu_us":@((cb.GPUEndTime-cb.GPUStartTime)*1e6),
        @"writtenBuffers":[[written allObjects] sortedArrayUsingSelector:@selector(compare:)],@"gpu_start":@(cb.GPUStartTime),@"gpu_end":@(cb.GPUEndTime),@"kernel_start":@(cb.kernelStartTime),@"kernel_end":@(cb.kernelEndTime)};
}
static NSDictionary *WriteRenderBuffer(DVMHost *host,uint64_t seq,NSDictionary *r) {
    DVMEntry *b=Entry(host,r[@"buffer"],@"buffer");uint64_t offset;NSData *data=DVMBPayload(r[@"data"]);
    if(!b||!Number(r[@"offset"],&offset)||!data.length||data.length>32768||offset>b.textureBytes||data.length>b.textureBytes-offset)return HostError(seq,EINVAL,@"buffer chunk bounds");
    memcpy((uint8_t *)[(id<MTLBuffer>)b.object contents]+offset,data.bytes,data.length);
    return @{@"seq":@(seq),@"ok":@YES};
}
static NSDictionary *ReadRenderBuffer(DVMHost *host,uint64_t seq,NSDictionary *r) {
    DVMEntry *b=Entry(host,r[@"buffer"],@"buffer");uint64_t offset,length;
    if(!b||!Number(r[@"offset"],&offset)||!Number(r[@"length"],&length)||!length||length>32768||
       offset>b.textureBytes||length>b.textureBytes-offset)return HostError(seq,EINVAL,@"buffer read chunk bounds");
    // Requests are serialized; RenderSubmit waits for native GPU completion.
    NSData *data=[NSData dataWithBytes:(uint8_t *)[(id<MTLBuffer>)b.object contents]+offset length:length];
    return @{@"seq":@(seq),@"ok":@YES,@"buffer":r[@"buffer"],@"offset":@(offset),
             @"data":[data base64EncodedStringWithOptions:0]};
}
