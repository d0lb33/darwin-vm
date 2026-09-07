// Native host view contract probe; not an exact-guest execution claim.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static void check(BOOL v,const char *s){if(!v){fprintf(stderr,"FAIL %s\n",s);exit(1);}}
int main(void){@autoreleasepool{
 id<MTLDevice> d=MTLCreateSystemDefaultDevice();
 for(unsigned mode=0;mode<3;mode++){
  MTLTextureDescriptor *td=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA16Float width:64 height:48 mipmapped:NO];td.usage=mode==2?65541:5;td.storageMode=mode?MTLStorageModePrivate:MTLStorageModeShared;td.mipmapLevelCount=mode?4:1;
  id<MTLBuffer> b=mode?nil:[d newBufferWithLength:32768 options:0];
  id<MTLTexture> t=mode?[d newTextureWithDescriptor:td]:[b newTextureWithDescriptor:td offset:0 bytesPerRow:512];
  check(t!=nil,"parent");NSUInteger base=mode?1:0;
  id<MTLTexture> v=[t newTextureViewWithPixelFormat:t.pixelFormat textureType:t.textureType levels:NSMakeRange(base,mode?2:1) slices:NSMakeRange(0,1)];check(v!=nil,"view");
  id<MTLTexture> child=[v newTextureViewWithPixelFormat:v.pixelFormat textureType:v.textureType levels:NSMakeRange(mode?1:0,1) slices:NSMakeRange(0,1)];check(child!=nil,"nested view");
  MTLPurgeableState old=[t setPurgeableState:MTLPurgeableStateVolatile],state=[t setPurgeableState:MTLPurgeableStateKeepCurrent],childState=[child setPurgeableState:MTLPurgeableStateKeepCurrent],prior=[t setPurgeableState:MTLPurgeableStateNonVolatile];fprintf(stderr,"NATIVE_VIEW_PURGE root_old=%lu root_current=%lu child_current=%lu root_previous=%lu\n",(unsigned long)old,(unsigned long)state,(unsigned long)childState,(unsigned long)prior);
  fprintf(stderr,"NATIVE_VIEW mode=%u view=%lux%lu levels=%lu parent_is_source=%u relative=%lu child_parent_is_view=%u child_parent_is_root=%u child_relative=%lu allocated=%lu/%lu/%lu buffers=%u/%u/%u offsets=%lu/%lu/%lu rows=%lu/%lu/%lu\n",mode,(unsigned long)v.width,(unsigned long)v.height,(unsigned long)v.mipmapLevelCount,v.parentTexture==t,(unsigned long)v.parentRelativeLevel,child.parentTexture==v,child.parentTexture==t,(unsigned long)child.parentRelativeLevel,(unsigned long)t.allocatedSize,(unsigned long)v.allocatedSize,(unsigned long)child.allocatedSize,t.buffer==b,v.buffer==b,child.buffer==b,(unsigned long)t.bufferOffset,(unsigned long)v.bufferOffset,(unsigned long)child.bufferOffset,(unsigned long)t.bufferBytesPerRow,(unsigned long)v.bufferBytesPerRow,(unsigned long)child.bufferBytesPerRow);
 }
}}
