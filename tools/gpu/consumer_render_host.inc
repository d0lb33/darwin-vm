#include <math.h>
static BOOL RenderNumbers(id raw,NSUInteger count) {
    if(![raw isKindOfClass:NSArray.class]||[raw count]!=count)return NO;
    for(id x in raw)if(![x isKindOfClass:NSNumber.class]||!isfinite([x doubleValue]))return NO;
    return YES;
}
static BOOL RenderUInt(id raw,uint64_t max) {uint64_t n;return Number(raw,&n)&&n<=max;}
#include "consumer_blit_host.inc"
#include "consumer_compute_host.inc"
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
        if([ResourceRoot(texture).kind isEqual:@"buffer"]&&[written containsObject:@(ResourceRoot(texture).handle)])return NO;
    return YES;
}
static NSDictionary *RenderPipeline(DVMHost *host,uint64_t seq,NSDictionary *r) {
    DVMEntry *vl=Entry(host,r[@"vertexLibrary"],@"library"),*fl=Entry(host,r[@"fragmentLibrary"],@"library");
    if(!vl||!fl||![r[@"vertex"] isKindOfClass:NSString.class]||![r[@"fragment"] isKindOfClass:NSString.class])return HostError(seq,EINVAL,@"render function ownership");
    id<MTLFunction> v=[r[@"vertexHandle"] unsignedLongLongValue]?Entry(host,r[@"vertexHandle"],@"function").object:[vl.object newFunctionWithName:r[@"vertex"]];
    id<MTLFunction> f=[r[@"fragmentHandle"] unsignedLongLongValue]?Entry(host,r[@"fragmentHandle"],@"function").object:[fl.object newFunctionWithName:r[@"fragment"]];
    if(!v||!f||v.functionType!=MTLFunctionTypeVertex||f.functionType!=MTLFunctionTypeFragment)return HostError(seq,EINVAL,@"render function stages");
    NSArray *c=r[@"color"],*attrs=r[@"attributes"],*layouts=r[@"layouts"];
    if(!RenderNumbers(c,9)||!RenderUInt(c[0],554)||!DVMColorFormat([c[0] unsignedIntegerValue])||[c[1] unsignedIntValue]>1||
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
           (arg.type==MTLArgumentTypeTexture&&arg.textureType!=MTLTextureType1D&&arg.textureType!=MTLTextureType2D&&arg.textureType!=MTLTextureType3D))return HostError(seq,ENOTSUP,[NSString stringWithFormat:@"shader binding outside render contract: stage=%u name=%@ index=%lu type=%lu access=%lu array=%lu textureType=%lu",stage,arg.name,(unsigned long)arg.index,(unsigned long)arg.type,(unsigned long)arg.access,(unsigned long)arg.arrayLength,(unsigned long)(arg.type==MTLArgumentTypeTexture?arg.textureType:0)]);
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
#ifdef DVM_RENDER_DIAGNOSTIC
#include "render_debug_capture.inc"
#endif
static NSDictionary *RenderSubmitPart(DVMHost *host,uint64_t seq,NSDictionary *r,BOOL preflight) {
    NSArray *tasks=r[@"guestTaskIDs"]?:@[];
    if(![tasks isKindOfClass:NSArray.class]||tasks.count>16)return HostError(seq,EINVAL,@"guest attribution extent");
    for(id task in tasks)if(!RenderUInt(task,UINT32_MAX))return HostError(seq,EINVAL,@"guest task ID");
    NSArray *passes=r[@"commands"],*uploads=r[@"uploads"],*readbacks=r[@"readbacks"];
    if(![passes isKindOfClass:NSArray.class]||passes.count>DVM_ORDERED_COMMANDS||![uploads isKindOfClass:NSArray.class]||uploads.count>128||![readbacks isKindOfClass:NSArray.class]||readbacks.count>128)return HostError(seq,EINVAL,@"render batch extent");
    NSMutableArray *transfers=[NSMutableArray array],*reads=[NSMutableArray array];NSMutableSet *seen=[NSMutableSet set];NSUInteger totalRead=0;
    for(NSDictionary *u in uploads){
        if(![u isKindOfClass:NSDictionary.class])return HostError(seq,EINVAL,@"render upload shape");
        BOOL buffer=u[@"buffer"]!=nil;DVMEntry *e=Entry(host,u[buffer?@"buffer":@"texture"],buffer?@"buffer":@"texture");NSData *data=DVMBPayload(u[@"data"]);
        if(!e||e.sharedRender||e.importedPages||(!buffer&&[(id<MTLTexture>)e.object storageMode]==MTLStorageModePrivate)||!data||data.length!=e.textureBytes||(buffer&&u[@"texture"])||(!buffer&&![u[@"row"] isEqual:@(e.row)])||[seen containsObject:@(e.handle)])return HostError(seq,EINVAL,@"render upload extent/ownership");
        [seen addObject:@(e.handle)];[transfers addObject:@[e,data]];
    }
    [seen removeAllObjects];
    for(id h in readbacks){DVMEntry *e=Entry(host,h,@"buffer");if(!e||[seen containsObject:h]||e.textureBytes>DVM_BUFFER_BYTES-totalRead)return HostError(seq,EINVAL,@"render readback extent");[seen addObject:h];totalRead+=e.textureBytes;[reads addObject:e];}
    // Encode into an uncommitted command buffer. Validate every pass before any
    // upload or commit, so a bad later operation cannot execute an earlier pass.
    id<MTLCommandBuffer> cb=[host.queue commandBuffer];NSUInteger draws=0,renderPasses=0,blitPasses=0,computePasses=0;
#ifdef DVM_RENDER_DIAGNOSTIC
    NSMutableArray *debugCaptures=[NSMutableArray array];NSUInteger debugPass=0;
#endif
    NSMutableSet *written=[NSMutableSet set],*sharedTargets=[NSMutableSet set];BOOL serialIndices=NO;
    if(!preflight){
        // Host-only completion fault injection; the normal reply must wait for
        // the GPU, not merely for command encoding or shared-memory mapping.
        for(NSDictionary *pass in passes){DVMSharedRender *shared=Entry(host,pass[@"target"],@"texture").sharedRender;
            if(shared.completionDelayUS){id<MTLSharedEvent> event=[host.device newSharedEvent];if(!event)return HostError(seq,ENOMEM,@"shared render delay event");
                [cb encodeWaitForEvent:event value:1];unsigned delay=shared.completionDelayUS;
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)delay*1000),dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE,0),^{event.signaledValue=1;});break;}
        }
    }
    for(NSDictionary *pass in passes){
        if([pass isKindOfClass:NSDictionary.class]&&[pass[@"kind"] isEqual:@"compute"]){
            NSString *failure=EncodeCompute(host,cb,pass,written,sharedTargets);if(failure)return HostError(seq,EINVAL,failure);
            computePasses++;continue;
        }
        if([pass isKindOfClass:NSDictionary.class]&&[pass[@"kind"] isEqual:@"blit"]){
            NSString *failure=EncodeBlit(host,cb,pass,written);if(failure)return HostError(seq,EINVAL,failure);
            blitPasses++;continue;
        }
        if(![pass isKindOfClass:NSDictionary.class]||![pass[@"kind"] isEqual:@"render"])return HostError(seq,EINVAL,@"mixed render batch");
        renderPasses++;
        DVMEntry *target=Entry(host,pass[@"target"],@"texture");NSArray *clear=pass[@"clear"],*ops=pass[@"operations"];
        if(target.importedPages.failed)return HostError(seq,EBUSY,@"imported target quarantined");
        if(target.sharedRender){if(target.sharedRender.state!=DVMSharedAcquired)return HostError(seq,EBUSY,@"shared render target is not acquired");[sharedTargets addObject:target.sharedRender];}
        if(!target||[(id<MTLTexture>)target.object textureType]!=MTLTextureType2D||!([(id<MTLTexture>)target.object usage]&MTLTextureUsageRenderTarget)||!RenderUInt(pass[@"load"],2)||!RenderUInt(pass[@"store"],1)||!RenderNumbers(clear,4)||![ops isKindOfClass:NSArray.class]||ops.count>DVM_ENCODER_OPERATIONS)return HostError(seq,EINVAL,@"render pass descriptor");
        uint64_t level;
        if(!Number(pass[@"level"]?:@0,&level)||level>=[(id<MTLTexture>)target.object mipmapLevelCount])return HostError(seq,EINVAL,@"render target mip level");
        NSUInteger targetWidth=MAX((NSUInteger)1,target.width>>level),targetHeight=MAX((NSUInteger)1,target.height>>level);
        // Native Metal accepts reading earlier mip subresources while writing
        // a later mip in this allocation (test_native_mip_alias, validation on).
        // Reflection cannot identify a shader's dynamic source LOD. As in
        // native Metal, the application must keep read/write subresources
        // disjoint. This does not authorize sampling the destination mip.
        BOOL disjointMipCandidate=level>0&&[(id<MTLTexture>)target.object storageMode]==MTLStorageModePrivate;
#ifdef DVM_RENDER_DIAGNOSTIC
        if(!preflight){
            NSMutableSet *inputs=[NSMutableSet set];
            for(NSArray *op in ops)if(op.count==3&&[op[0] isEqual:@"fragmentTexture"]&&![op[1] isEqual:@0])[inputs addObject:op[1]];
            for(NSNumber *handle in [[inputs allObjects] sortedArrayUsingSelector:@selector(compare:)]){
                DVMEntry *input=Entry(host,handle,@"texture");
                for(NSUInteger mip=0;mip<[(id<MTLTexture>)input.object mipmapLevelCount];mip++)
                    DVMDebugSnapshot(host,cb,debugCaptures,input,mip,seq,debugPass,@"input-before-pass");
            }
        }
#endif
        MTLRenderPassDescriptor *d=[MTLRenderPassDescriptor renderPassDescriptor];d.colorAttachments[0].level=level;d.colorAttachments[0].texture=target.object;d.colorAttachments[0].loadAction=[pass[@"load"] unsignedIntegerValue];d.colorAttachments[0].storeAction=[pass[@"store"] unsignedIntegerValue];d.colorAttachments[0].clearColor=MTLClearColorMake([clear[0] doubleValue],[clear[1] doubleValue],[clear[2] doubleValue],[clear[3] doubleValue]);
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
            } else if([name isEqual:@"scissor"]&&op.count==5&&RenderUInt(op[1],targetWidth)&&RenderUInt(op[2],targetHeight)&&RenderUInt(op[3],targetWidth)&&RenderUInt(op[4],targetHeight)){
                MTLScissorRect s={[op[1] unsignedIntegerValue],[op[2] unsignedIntegerValue],[op[3] unsignedIntegerValue],[op[4] unsignedIntegerValue]};if(!s.width||!s.height||s.width>targetWidth-s.x||s.height>targetHeight-s.y){failure=@"scissor bounds";break;}[encoder setScissorRect:s];
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
                BOOL texture=[name isEqual:@"fragmentTexture"];DVMEntry *e=Entry(host,op[1],texture?@"texture":@"sampler");if((!e&&![op[1] isEqual:@0])||(texture&&e&&(!([(id<MTLTexture>)e.object usage]&MTLTextureUsageShaderRead)||(TextureRoot(e)==TextureRoot(target)&&TextureBaseLevel(target)+level>=TextureBaseLevel(e)&&TextureBaseLevel(target)+level<TextureBaseLevel(e)+[(id<MTLTexture>)e.object mipmapLevelCount]&&!(e==target&&disjointMipCandidate))||(e.importedPages&&(e.importedPages.failed||(e!=target&&e.importedPages==target.importedPages)))||(e.sharedRender&&e.sharedRender.state!=DVMSharedAcquired)))){failure=@"fragment resource ownership/usage";break;}if(texture){[encoder setFragmentTexture:e.object atIndex:[op[2] unsignedIntegerValue]];fragmentTextures[op[2]]=e;}else {[encoder setFragmentSamplerState:e.object atIndex:[op[2] unsignedIntegerValue]];fragmentSamplers[op[2]]=e;}
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
                if(!b||!count||!Number(op[5],&offset)||offset%size||offset>b.textureBytes||count*size>b.textureBytes-offset){failure=@"index buffer bounds";break;}
                BOOL priorWrite=[written containsObject:@(b.handle)];
                const uint8_t *data=[(id<MTLBuffer>)b.object contents];
                for(NSArray *transfer in transfers)if(transfer[0]==b)data=[(NSData *)transfer[1] bytes];
                NSUInteger maximum=0;for(NSUInteger i=0;i<count;i++){uint32_t value=0;memcpy(&value,data+offset+i*size,size);maximum=MAX(maximum,value);}
                if(!(preflight&&priorWrite)) {
                    if(maximum>=4096){failure=@"index value bounds";break;}
                    for(NSArray *layout in pipeline.object[@"layouts"]){NSUInteger elements=[layout[2] unsignedIntegerValue]==1?maximum+1:1;NSArray *bound=vertexBuffers[layout[0]];if(!bound||elements*[layout[1] unsignedIntegerValue]>[bound[0] unsignedIntegerValue]){failure=@"indexed vertex footprint exceeds binding";break;}}
                }
                if(!RenderBindingsFit(pipeline,vertexBuffers,fragmentBuffers,fragmentTextures,fragmentSamplers,written))failure=@"missing or undersized indexed binding";
                serialIndices|=[written containsObject:@(b.handle)];
                if(failure)break;
                // Resolve indices after previous dependent draws complete. A
                // native immutable snapshot prevents this draw's writable
                // buffer aliases from racing the index fetch or CPU validation.
                id<MTLBuffer> indices=b.object;NSUInteger indexOffset=offset;
                if(!preflight&&serialIndices){
                    indices=[host.device newBufferWithBytes:data+offset length:count*size options:MTLResourceStorageModeShared];indexOffset=0;
                    if(!indices){failure=@"index snapshot allocation";break;}
                }
                [encoder drawIndexedPrimitives:[op[1] unsignedIntegerValue] indexCount:count indexType:[op[3] unsignedIntegerValue] indexBuffer:indices indexBufferOffset:indexOffset];draws++;
                // Apple TBDR cannot wait for fragment stores within this pass.
                // Only vertex writes are admitted; order their later consumers.
                if(written.count)[encoder memoryBarrierWithScope:MTLBarrierScopeBuffers afterStages:MTLRenderStageVertex beforeStages:MTLRenderStageVertex|MTLRenderStageFragment];
            } else {failure=[@"unsupported render operation: " stringByAppendingString:name];break;}
        }
        [encoder endEncoding];if(failure)return HostError(seq,EINVAL,failure);
#ifdef DVM_RENDER_DIAGNOSTIC
        if(!preflight&&[pass[@"store"] unsignedIntegerValue]==MTLStoreActionStore)
            DVMDebugSnapshot(host,cb,debugCaptures,target,level,seq,debugPass,@"target-after-pass");
        debugPass++;
#endif
    }
    NSUInteger totalWritten=0;
    for(NSNumber *handle in written){DVMEntry *b=Entry(host,handle,@"buffer");if(!b||b.textureBytes>DVM_BUFFER_BYTES-totalWritten)return HostError(seq,ENOTSUP,@"render writeback budget");totalWritten+=b.textureBytes;}
    if(preflight)return @{@"ok":@YES,@"serialIndices":@(serialIndices),@"writtenBuffers":[[written allObjects] sortedArrayUsingSelector:@selector(compare:)]};
    for(NSArray *transfer in transfers){DVMEntry *e=transfer[0];NSData *data=transfer[1];if([e.kind isEqual:@"buffer"])memcpy([(id<MTLBuffer>)e.object contents],data.bytes,data.length);else ReplaceTextureBytes(e.object,data.bytes,e.row);}
    [cb commit];[cb waitUntilCompleted];host.submissions++;
    if(cb.status!=MTLCommandBufferStatusCompleted){for(DVMSharedRender *shared in sharedTargets)shared.state=DVMSharedFailed;for(DVMImportedPages *m in host.imports.allValues)m.failed=YES;return NSErrorReply(seq,cb.error,@"render GPU completion");}
#ifdef DVM_RENDER_DIAGNOSTIC
    DVMDebugSave(debugCaptures);
#endif
    for(DVMSharedRender *shared in sharedTargets)shared.completedWrites++;
    host.renderPasses+=renderPasses;host.renderDraws+=draws;host.blitPasses+=blitPasses;host.computePasses+=computePasses;
    NSMutableDictionary *result=[NSMutableDictionary dictionary];for(DVMEntry *e in reads)result[@(e.handle).stringValue]=[[NSData dataWithBytes:[(id<MTLBuffer>)e.object contents] length:e.textureBytes] base64EncodedStringWithOptions:0];
    fprintf(stderr,"DVM_DRIVER_RENDER seq=%llu passes=%lu draws=%lu status=%lu gpu_us=%.3f\n",(unsigned long long)seq,(unsigned long)passes.count,(unsigned long)draws,(unsigned long)cb.status,(cb.GPUEndTime-cb.GPUStartTime)*1e6);
    return @{@"seq":@(seq),@"ok":@YES,@"status":@(cb.status),@"buffers":result,@"passes":@(passes.count),@"draws":@(draws),@"gpu_us":@((cb.GPUEndTime-cb.GPUStartTime)*1e6),
        @"renderPasses":@(renderPasses),@"blitPasses":@(blitPasses),@"computePasses":@(computePasses),
        @"writtenBuffers":[[written allObjects] sortedArrayUsingSelector:@selector(compare:)],@"gpu_start":@(cb.GPUStartTime),@"gpu_end":@(cb.GPUEndTime),@"kernel_start":@(cb.kernelStartTime),@"kernel_end":@(cb.kernelEndTime)};
}
static NSDictionary *RenderSubmit(DVMHost *host,uint64_t seq,NSDictionary *r) {
    NSDictionary *check=RenderSubmitPart(host,seq,r,YES);
    if(![check[@"ok"] boolValue])return check;
    BOOL split=[check[@"serialIndices"] boolValue];
#ifdef DVM_RENDER_DIAGNOSTIC
    // Diagnostic only: reuse the existing store/load split for per-draw
    // snapshots. Extra nativeCommandBuffers remains visible in the reply.
    split=split||getenv("DVM_RENDER_CAPTURE_DRAWS")!=NULL;
#endif
    if(!split)return RenderSubmitPart(host,seq,r,NO);
    // This bounded profile has one single-sample color target and no depth,
    // tile shaders or visibility queries. Store/load preserves it across native
    // command buffers. Split only batches with writable/index aliases; retain
    // encoded state and wait before inspecting GPU-produced index bytes.
    NSMutableArray *parts=[NSMutableArray array];
    for(NSDictionary *pass in r[@"commands"]){
        if([pass[@"kind"] isEqual:@"blit"]||[pass[@"kind"] isEqual:@"compute"]){[parts addObject:pass];continue;}
        NSMutableArray *state=[NSMutableArray array],*passParts=[NSMutableArray array];
        for(NSArray *op in pass[@"operations"]){
            if([op[0] isEqual:@"draw"]||[op[0] isEqual:@"indexed"]){
                NSMutableDictionary *part=[pass mutableCopy];part[@"operations"]=[state arrayByAddingObject:op];
                part[@"load"]=passParts.count?@(MTLLoadActionLoad):pass[@"load"];
                part[@"store"]=@(MTLStoreActionStore);[passParts addObject:part];
            }else [state addObject:op];
        }
        if(!passParts.count)[passParts addObject:[pass mutableCopy]];
        [passParts lastObject][@"store"]=pass[@"store"];
        [parts addObjectsFromArray:passParts];
    }
    NSDictionary *last=nil;double gpuUs=0;id gpuStart=nil,kernelStart=nil;
    uint64_t initialPasses=host.renderPasses,initialBlits=host.blitPasses,initialDraws=host.renderDraws,initialSubmissions=host.submissions,initialCompute=host.computePasses;
    for(NSUInteger i=0;i<parts.count;i++){
        NSMutableDictionary *part=[r mutableCopy];part[@"commands"]=@[parts[i]];
        part[@"uploads"]=i?@[]:r[@"uploads"];part[@"readbacks"]=i+1==parts.count?r[@"readbacks"]:@[];
        last=RenderSubmitPart(host,seq,part,NO);
        if(![last[@"ok"] boolValue]){
            // A GPU-generated invalid index can fail only after earlier draws
            // executed. Such a partially rendered frame must not be sealed.
            if(host.submissions!=initialSubmissions){
                // A partially executed mixed batch cannot safely resume reuse.
                // Include imported allocations and compute targets, which have
                // no render-pass "target" field. Match GPU-failure quarantine.
                for(DVMEntry *entry in host.entries.allValues)if(entry.sharedRender)entry.sharedRender.state=DVMSharedFailed;
                for(DVMImportedPages *mapping in host.imports.allValues)mapping.failed=YES;
            }
            return last;
        }
        if(!i){gpuStart=last[@"gpu_start"];kernelStart=last[@"kernel_start"];}
        gpuUs+=[last[@"gpu_us"] doubleValue];
    }
    NSUInteger renderCount=0;for(NSDictionary *pass in r[@"commands"])renderCount+=[pass[@"kind"] isEqual:@"render"];
    host.renderPasses=initialPasses+renderCount;
    NSMutableDictionary *result=[last mutableCopy];result[@"passes"]=@([r[@"commands"] count]);
    result[@"renderPasses"]=@(renderCount);result[@"blitPasses"]=@(host.blitPasses-initialBlits);
    result[@"computePasses"]=@(host.computePasses-initialCompute);
    result[@"draws"]=@(host.renderDraws-initialDraws);result[@"writtenBuffers"]=check[@"writtenBuffers"];
    result[@"gpu_us"]=@(gpuUs);result[@"gpu_start"]=gpuStart;result[@"kernel_start"]=kernelStart;
    result[@"nativeCommandBuffers"]=@(parts.count);return result;
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
