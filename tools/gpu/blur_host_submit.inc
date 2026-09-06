// Validate the complete two-pass contract before uploads or execution.
static NSDictionary *BlurSubmit(DVMHost*host,uint64_t seq,NSDictionary*r) {
 unsigned w=[r[@"w"] unsignedIntValue],h=[r[@"h"] unsignedIntValue];
 DVMEntry*p=Entry(host,r[@"pipeline"],@"pipeline"),*in=Entry(host,r[@"input"],@"texture"),*tmp=Entry(host,r[@"tmp"],@"texture"),*out=Entry(host,r[@"output"],@"texture");
 if(!p||![p.object[@"function"] isEqual:@"compute_simd_blur_5"]||!in||!tmp||!out||in==tmp||in==out||tmp==out)return HostError(seq,EINVAL,@"blur resource handles");
 if(!w||!h||w%32||h%32||w>256||h>256)return HostError(seq,EINVAL,@"blur dimensions");
 NSArray*textures=@[in,tmp,out];for(unsigned i=0;i<3;i++) {
  DVMEntry*t=textures[i];if(t.width!=(i?w:w+32)||t.height!=(i==2?h:h+32)||t.format!=MTLPixelFormatRGBA16Float||((id<MTLTexture>)t.object).usage!=(MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite))return HostError(seq,EINVAL,@"blur texture geometry or usage");
 }
 NSData*state=r[@"state"],*pixels=r[@"pixels"];
 if(![state isKindOfClass:NSData.class]||state.length!=128||![pixels isKindOfClass:NSData.class]||pixels.length!=in.textureBytes)return HostError(seq,EINVAL,@"blur state or upload size");
 DVMBlurCommand c[2];memcpy(c,state.bytes,128);
 for(unsigned pass=0;pass<2;pass++) {
  uint16_t u[10]={0,0,0,0,pass?0:32,pass?32:0,32,32,pass?0:1,0x3c00};
  uint16_t weights[5]={0x2c00,0x3400,0x3600,0x3400,0x2c00};
  if(memcmp(c[pass].uniform,u,20)||memcmp(c[pass].weights,weights,10)||!DVMBZero(c[pass].reserved,2)||c[pass].groups[0]!=w/32||c[pass].groups[1]!=(h+(pass?0:32))/32||c[pass].groups[2]!=1||c[pass].threads[0]!=32||c[pass].threads[1]!=32||c[pass].threads[2]!=1||c[pass].imageblock[0]!=32||c[pass].imageblock[1]!=32)return HostError(seq,EINVAL,@"blur shader state outside audited envelope");
 }
 id<MTLComputePipelineState>pipeline=p.object[@"state"];
 if(pipeline.threadExecutionWidth!=32||pipeline.maxTotalThreadsPerThreadgroup<1024)return HostError(seq,ENOTSUP,@"blur SIMD width or thread capacity");
 [(id<MTLTexture>)in.object replaceRegion:MTLRegionMake2D(0,0,in.width,in.height) mipmapLevel:0 withBytes:pixels.bytes bytesPerRow:in.row];
 id<MTLCommandBuffer>cb=[host.queue commandBuffer];
 for(unsigned pass=0;pass<2;pass++) {
  id<MTLComputeCommandEncoder>e=[cb computeCommandEncoder];[e setComputePipelineState:pipeline];
  [e setTexture:pass?tmp.object:in.object atIndex:0];[e setTexture:pass?out.object:tmp.object atIndex:1];
  [e setBytes:c[pass].uniform length:20 atIndex:0];[e setBytes:c[pass].weights length:10 atIndex:1];[e setImageblockWidth:32 height:32];
  [e dispatchThreadgroups:MTLSizeMake(c[pass].groups[0],c[pass].groups[1],1) threadsPerThreadgroup:MTLSizeMake(32,32,1)];[e endEncoding];
 }
 [cb commit];[cb waitUntilCompleted];if(cb.status!=MTLCommandBufferStatusCompleted)return NSErrorReply(seq,cb.error,@"blur GPU failure");
 host.submissions++;uint64_t gpu=(uint64_t)((cb.GPUEndTime-cb.GPUStartTime)*1e9);
 NSMutableData*result=[NSMutableData dataWithLength:out.textureBytes];[(id<MTLTexture>)out.object getBytes:result.mutableBytes bytesPerRow:out.row fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
 fprintf(stderr,"DVM_DRIVER_BLUR seq=%llu dispatches=2 status=4 gpu_ns=%llu\n",(unsigned long long)seq,(unsigned long long)gpu);fflush(stderr);
 return @{@"seq":@(seq),@"ok":@YES,@"status":@4,@"w":@(w),@"h":@(h),@"output":@(out.handle),@"gpu_ns":@(gpu),@"texture":result};
}
