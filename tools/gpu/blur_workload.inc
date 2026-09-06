#include "blur_reference.h"
static NSString *blurSHA(const void*p,size_t n){uint8_t d[32];char s[65];CC_SHA256(p,(CC_LONG)n,d);for(unsigned i=0;i<32;i++)snprintf(s+i*2,3,"%02x",d[i]);return @(s);}
static void blurDeliver(IOSurfaceRef surface,const DVMHalf*in,unsigned w,unsigned h){
 if(IOSurfaceLock(surface,0,NULL))fail("blur-iosurface-lock");
 uint8_t*p=IOSurfaceGetBaseAddress(surface);size_t row=IOSurfaceGetBytesPerRow(surface);
 for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++)for(unsigned c=0;c<4;c++){
  float v=in[((size_t)y*w+x)*4+(c<3?2-c:c)];p[y*row+x*4+c]=(uint8_t)(v*255.0f+.5f);
 }
 if(IOSurfaceUnlock(surface,0,NULL))fail("blur-iosurface-unlock");
}
static void blurEncode(id<MTLCommandQueue>queue,id<MTLComputePipelineState>pipeline,NSArray*ts,unsigned w,unsigned h){
 id<MTLCommandBuffer>cb=[queue commandBuffer];
 for(unsigned pass=0;pass<2;pass++){
  uint16_t u[10]={0,0,0,0,pass?0:32,pass?32:0,32,32,pass?0:1,0x3c00};DVMHalf weights[5]={.0625,.25,.375,.25,.0625};
  id<MTLComputeCommandEncoder>e=[cb computeCommandEncoder];[e setComputePipelineState:pipeline];
  [e setTexture:ts[pass] atIndex:0];[e setTexture:ts[pass+1] atIndex:1];[e setBytes:u length:20 atIndex:0];[e setBytes:weights length:10 atIndex:1];[e setImageblockWidth:32 height:32];
  [e dispatchThreadgroups:MTLSizeMake(w/32,(h+(pass?0:32))/32,1) threadsPerThreadgroup:MTLSizeMake(32,32,1)];[e endEncoding];
 }
 [cb commit];[cb waitUntilCompleted];if(cb.status!=MTLCommandBufferStatusCompleted||cb.error)fail("blur-command");
}
static void DVMRunBlur(id<MTLDevice>device,NSData*air){
 dispatch_data_t bytes=dispatch_data_create(air.bytes,air.length,dispatch_get_global_queue(QOS_CLASS_USER_INTERACTIVE,0),DISPATCH_DATA_DESTRUCTOR_DEFAULT);NSError*e=nil;
 id<MTLLibrary>library=[device newLibraryWithData:bytes error:&e];id<MTLComputePipelineState>pipeline=[device newComputePipelineStateWithFunction:[library newFunctionWithName:@"compute_simd_blur_5"] error:&e];id<MTLCommandQueue>queue=[device newCommandQueue];
 if(!pipeline||pipeline.threadExecutionWidth!=32||pipeline.maxTotalThreadsPerThreadgroup<1024)fail("blur-pipeline");
 const unsigned sizes[4][2]={{64,64},{256,256},{512,512},{1184,2560}};
 for(unsigned size=0;size<4;size++){@autoreleasepool{
  unsigned w=sizes[size][0],h=sizes[size][1];size_t ni=(size_t)(w+32)*(h+32)*8,nt=(size_t)w*(h+32)*8,no=(size_t)w*h*8;
  DVMHalf *inputs[2]={malloc(ni),malloc(ni)},*refs[2]={malloc(no),malloc(no)};
  DVMHalf *tmp=malloc(nt),*out=malloc(no),*tile=malloc(288*288*8),*tileOut=malloc(256*256*8);
  if(!inputs[0]||!inputs[1]||!refs[0]||!refs[1]||!tmp||!out||!tile||!tileOut)fail("blur-alloc");
  NSDictionary*desc=@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@(w*4),(id)kIOSurfaceAllocSize:@((size_t)w*h*4),(id)kIOSurfacePixelFormat:@(0x42475241)};
  IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)desc);if(!surface)fail("blur-iosurface-create");
  NSMutableDictionary *pools=[NSMutableDictionary dictionary];
  for(unsigned x=0;x<w;x+=256){unsigned tw=MIN(256,w-x),th=MIN(256,h);NSString*key=[NSString stringWithFormat:@"%u",tw];if(pools[key])continue;
   NSMutableArray*ts=[NSMutableArray array];for(unsigned i=0;i<3;i++){
    MTLTextureDescriptor*d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:i?tw:tw+32 height:i==2?th:th+32 mipmapped:NO];d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
    id<MTLTexture>t=[device newTextureWithDescriptor:d];if(!t)fail("blur-texture");[ts addObject:t];
   }pools[key]=ts;
  }
  fprintf(stderr,"GPU_LOAD_BLUR_SIZE width=%u height=%u frames=17 surface=%u tile_pools=%lu\n",w,h,IOSurfaceGetID(surface),(unsigned long)pools.count);
  uint32_t nonces[2]={arc4random(),arc4random()};
  for(unsigned i=0;i<2;i++)DVMBlurInput(inputs[i],w+32,h+32,nonces[i]);
  NSMutableArray*cpuTimes=[NSMutableArray array],*cpuOutput=[NSMutableArray array];
  for(unsigned run=0;run<17;run++) {
   unsigned ix=run%2;double start=now();DVMBlurCPUHalf(inputs[ix],tmp,refs[ix],w,h);double done=now();blurDeliver(surface,refs[ix],w,h);double end=now();
   [cpuTimes addObject:@((uint64_t)((done-start)*1e6))];[cpuOutput addObject:@((uint64_t)((end-done)*1e6))];
   if(size==3 && run%2==0)fprintf(stderr,"GPU_LOAD_BLUR_CPU_PROGRESS width=%u height=%u run=%u\n",w,h,run);
  }
  NSString*surfaceHashes[2];for(unsigned i=0;i<2;i++){blurDeliver(surface,refs[i],w,h);surfaceHashes[i]=blurSHA(IOSurfaceGetBaseAddress(surface),(size_t)w*h*4);}
  fprintf(stderr,"GPU_LOAD_BLUR_CPU width=%u height=%u work_us=%s output_us=%s\n",w,h,[[cpuTimes valueForKey:@"description"] componentsJoinedByString:@","].UTF8String,[[cpuOutput valueForKey:@"description"] componentsJoinedByString:@","].UTF8String);
  double batchStart=now();
  for(unsigned run=0;run<17;run++){@autoreleasepool{
   unsigned ix=run%2;uint32_t nonce=nonces[ix];DVMHalf*in=inputs[ix],*ref=refs[ix];NSString*cpuSurfaceSHA=surfaceHashes[ix];
   memset(out,0xa5,no);blurRPCus=0;blurDoorbellus=0;double begin=now();
   for(unsigned y=0;y<h;y+=256)for(unsigned x=0;x<w;x+=256){@autoreleasepool{
    unsigned tw=MIN(256,w-x),th=MIN(256,h-y);NSArray*ts=pools[[NSString stringWithFormat:@"%u",tw]];
    for(unsigned ty=0;ty<th+32;ty++)memcpy(tile+(size_t)ty*(tw+32)*4,in+((size_t)(y+ty)*(w+32)+x)*4,(tw+32)*8);
    [(id<MTLTexture>)ts[0] replaceRegion:MTLRegionMake2D(0,0,tw+32,th+32) mipmapLevel:0 withBytes:tile bytesPerRow:(tw+32)*8];
    blurEncode(queue,pipeline,ts,tw,th);
    [(id<MTLTexture>)ts[2] getBytes:tileOut bytesPerRow:tw*8 fromRegion:MTLRegionMake2D(0,0,tw,th) mipmapLevel:0];
    for(unsigned ty=0;ty<th;ty++)memcpy(out+((size_t)(y+ty)*w+x)*4,tileOut+(size_t)ty*tw*4,tw*8);
   }}
   double ready=now();blurDeliver(surface,out,w,h);double delivered=now();
   if(memcmp(out,ref,no))fail("blur-pixel-oracle");
   NSString*sha=blurSHA(out,no),*surfaceSHA=blurSHA(IOSurfaceGetBaseAddress(surface),(size_t)w*h*4);
   if(![surfaceSHA isEqual:cpuSurfaceSHA])fail("blur-surface-oracle");
   fprintf(stderr,"GPU_LOAD_BLUR_FRAME width=%u height=%u run=%u nonce=%u gpu_path_us=%.0f output_us=%.0f total_us=%.0f rpc_us=%.0f doorbell_us=%.0f verify_us=%.0f sha=%s surface_sha=%s\n",w,h,run,nonce,(ready-begin)*1e6,(delivered-ready)*1e6,(delivered-begin)*1e6,blurRPCus,blurDoorbellus,(now()-delivered)*1e6,sha.UTF8String,surfaceSHA.UTF8String);
  }}
  fprintf(stderr,"GPU_LOAD_BLUR_BATCH width=%u height=%u frames=17 wall_us=%.0f\n",w,h,(now()-batchStart)*1e6);
  CFRelease(surface);free(inputs[0]);free(inputs[1]);free(tmp);free(refs[0]);free(refs[1]);free(out);free(tile);free(tileOut);
 }}
}
