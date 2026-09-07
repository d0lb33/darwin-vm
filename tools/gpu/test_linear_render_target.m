// Isolated host allocation contract probe, not guest shader/QuartzCore evidence.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdlib.h>
int main(int argc,const char **argv){@autoreleasepool {
    if(argc!=4)return 2;
    NSUInteger w=strtoul(argv[1],NULL,10),h=strtoul(argv[2],NULL,10),row=strtoul(argv[3],NULL,10);
    if(!w||!h||w>4096||h>4096||row<w*4||row>32768||row%256)return 2;
    NSUInteger length=(row*h+16383)&~16383ul;void *memory=NULL;
    if(posix_memalign(&memory,16384,length))return 3;memset(memory,0xa5,length);
    @autoreleasepool {
        id<MTLDevice> device=MTLCreateSystemDefaultDevice();
        id<MTLBuffer> buffer=[device newBufferWithBytesNoCopy:memory length:length options:MTLResourceStorageModeShared deallocator:nil];
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:w height:h mipmapped:NO];
        d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        fprintf(stderr,"LINEAR_TARGET create width=%lu height=%lu row=%lu bytes=%lu\n",(unsigned long)w,(unsigned long)h,(unsigned long)row,(unsigned long)length);fflush(stderr);
        id<MTLTexture> texture=[buffer newTextureWithDescriptor:d offset:0 bytesPerRow:row];
        if(!texture){fprintf(stderr,"LINEAR_TARGET rejected=nil\n");return 4;}
        MTLRenderPassDescriptor *pass=[MTLRenderPassDescriptor renderPassDescriptor];pass.colorAttachments[0].texture=texture;
        pass.colorAttachments[0].loadAction=MTLLoadActionClear;pass.colorAttachments[0].storeAction=MTLStoreActionStore;pass.colorAttachments[0].clearColor=MTLClearColorMake(1,0,0,1);
        id<MTLCommandBuffer> cb=[[device newCommandQueue] commandBuffer];id<MTLRenderCommandEncoder> encoder=[cb renderCommandEncoderWithDescriptor:pass];[encoder endEncoding];[cb commit];[cb waitUntilCompleted];
        if(cb.status!=MTLCommandBufferStatusCompleted){fprintf(stderr,"LINEAR_TARGET failed=%s\n",cb.error.description.UTF8String);return 5;}
        unsigned bad=0;for(NSUInteger y=0;y<h;y++)for(NSUInteger x=0;x<w;x++){uint8_t *p=(uint8_t *)memory+y*row+x*4;if(p[0]||p[1]||p[2]!=255||p[3]!=255)bad++;}
        fprintf(stderr,"LINEAR_TARGET completion=%lu bad_pixels=%u pointer_alias=%u\n",(unsigned long)cb.status,bad,buffer.contents==memory);
        if(bad||buffer.contents!=memory)return 6;
    }
    free(memory);return 0;
}}
