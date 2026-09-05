// Execute the exact guest QuartzCore read_write_surf_compute AIR on host Metal.
// Oracle follows the extracted AIR: bounded per-pixel half4 read and write.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <IOSurface/IOSurface.h>
static void fail(NSString *s) { fprintf(stderr,"FAIL %s\n",s.UTF8String); exit(1); }
int main(int argc, const char **argv) { @autoreleasepool {
 if(argc != 3) return 2;
 NSData *bytes=[NSData dataWithContentsOfFile:@(argv[1])]; if(!bytes) fail(@"read library");
 id<MTLDevice> d=MTLCreateSystemDefaultDevice(); if(!d) fail(@"no Metal device");
 NSError *error=nil;
 dispatch_data_t data=dispatch_data_create(bytes.bytes,bytes.length,nil,DISPATCH_DATA_DESTRUCTOR_DEFAULT);
 id<MTLLibrary> lib=[d newLibraryWithData:data error:&error]; if(!lib) fail(error.description);
 id<MTLFunction> fn=[lib newFunctionWithName:@"read_write_surf_compute"]; if(!fn) fail(@"function missing");
 MTLComputePipelineReflection *ref=nil;
 id<MTLComputePipelineState> ps=[d newComputePipelineStateWithFunction:fn options:MTLPipelineOptionArgumentInfo reflection:&ref error:&error];
 if(!ps) fail(error.description);
 id<MTLCommandQueue> q=[d newCommandQueue];
 const NSUInteger w=64,h=48,row=w*4,total=row*h;
 NSMutableData *input=[NSMutableData dataWithLength:total],*observed=[NSMutableData dataWithLength:total];
 NSMutableArray *runs=[NSMutableArray array];
 for(unsigned generation=0;generation<3;generation++) {
  IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(id)kIOSurfaceWidth:@(w),(id)kIOSurfaceHeight:@(h),(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@(row),(id)kIOSurfaceAllocSize:@(total),(id)kIOSurfacePixelFormat:@((uint32_t)'BGRA')});
  if(!surface) fail(@"IOSurfaceCreate");
  MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
  td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsageShaderWrite;
  id<MTLTexture> src=[d newTextureWithDescriptor:td];
  id<MTLTexture> dst=[d newTextureWithDescriptor:td iosurface:surface plane:0];
  if(!src||!dst) fail(@"texture creation");
  for(unsigned pass=0;pass<3;pass++) {
   unsigned char *in=input.mutableBytes;
   for(NSUInteger y=0;y<h;y++) for(NSUInteger x=0;x<w;x++) {
    NSUInteger i=y*row+4*x; in[i]=(x*3+y*7+pass*31+generation*11)&255;
    in[i+1]=(x*11+y*5+pass*13+generation*23)&255;in[i+2]=(x^y^pass*47^generation*59)&255;in[i+3]=255;
   }
   [src replaceRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0 withBytes:in bytesPerRow:row];
   if(IOSurfaceLock(surface,0,NULL)) fail(@"surface lock");
   memset(IOSurfaceGetBaseAddress(surface),0xa5,total);
   if(IOSurfaceUnlock(surface,0,NULL)) fail(@"surface unlock");
   // Negative control: output must differ before GPU work.
   NSUInteger preEqual=0; const unsigned char *before=IOSurfaceGetBaseAddress(surface);
   for(NSUInteger i=0;i<total;i++) preEqual+=(before[i]==in[i]);
   if(preEqual==total) fail(@"vacuous oracle");
   id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLComputeCommandEncoder> enc=[cb computeCommandEncoder];
   [enc setComputePipelineState:ps];[enc setTexture:src atIndex:0];[enc setTexture:dst atIndex:1];
   // Deliberately oversize dispatch exercises the guest shader's bounds checks.
   [enc dispatchThreadgroups:MTLSizeMake(9,7,1) threadsPerThreadgroup:MTLSizeMake(8,8,1)];[enc endEncoding];
   [cb commit];[cb waitUntilCompleted]; if(cb.status!=MTLCommandBufferStatusCompleted) fail(cb.error.description);
   [dst getBytes:observed.mutableBytes bytesPerRow:row fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
   NSUInteger bad=0; const unsigned char *got=observed.bytes;
   for(NSUInteger i=0;i<total;i++) bad+=(in[i]!=got[i]);
   if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL)) fail(@"read lock");
   BOOL surfaceEqual=memcmp(in,IOSurfaceGetBaseAddress(surface),total)==0;
   if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL)) fail(@"read unlock");
   [runs addObject:@{@"generation":@(generation),@"pass":@(pass),@"bytes":@(total),@"mismatches":@(bad),@"surfaceEqual":@(surfaceEqual),@"preEqualBytes":@(preEqual),@"commandStatus":@(cb.status),@"gpuSeconds":@(cb.GPUEndTime-cb.GPUStartTime)}];
   if(bad||!surfaceEqual) fail(@"pixel oracle mismatch");
  }
  if(generation==2) [observed writeToFile:@(argv[2]) atomically:YES];
  // Release only after completion; subsequent generations reconstruct all textures/surfaces.
  dst=nil;src=nil;CFRelease(surface);
 }
 NSDictionary *r=@{@"device":d.name,@"library":@(argv[1]),@"function":@"read_write_surf_compute",@"width":@(w),@"height":@(h),@"format":@"BGRA8Unorm",@"runs":runs,@"reflection":ref.description,@"passed":@YES};
 NSData *json=[NSJSONSerialization dataWithJSONObject:r options:NSJSONWritingPrettyPrinted error:&error];fwrite(json.bytes,1,json.length,stdout);puts("");return 0;
}}
