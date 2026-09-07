#pragma once
// One transport-owned allocation, retained by its texture entry. The host maps
// only the model's registered page list; no guest address/path is accepted.
// A lease permits multiple completed Metal submissions before presentation.
// Retirement is a caller-reported native wait, not proof of DCP completion;
// the exact-guest acceptance test must independently correlate that completion.
typedef NS_ENUM(NSUInteger,DVMSharedRenderState) {
    DVMSharedIdle, DVMSharedAcquired, DVMSharedSealed, DVMSharedFailed
};
@interface DVMSharedRender : NSObject
@property void *mapping;
@property int fd;
@property id<MTLBuffer> buffer;
@property id<MTLTexture> texture;
@property DVMSharedRenderState state;
@property uint64_t epoch,completedWrites;
@property unsigned completionDelayUS;
@end
@implementation DVMSharedRender
- (void)dealloc {
    // Native submissions are synchronous; all aliases must die before unmap.
    _texture=nil;_buffer=nil;
    if(_mapping){munmap(_mapping,DVM_PRESENT_BUFFER_BYTES);close(_fd);}
}
@end

static DVMSharedRender *DVMOpenSharedRender(id<MTLDevice> device) {
    const char *path=getenv("DVM_DRIVER_PRESENT_RAM");
    if(!path)return nil;
    int fd=open(path,O_RDONLY|O_NOFOLLOW);struct stat st;uint8_t header[32]={0};
    if(fd<0)return nil;
    BOOL valid=!fstat(fd,&st)&&S_ISREG(st.st_mode)&&st.st_uid==getuid()&&!(st.st_mode&077)&&
        st.st_size==0x1000000&&pread(fd,header,sizeof(header),0)==sizeof(header);
    close(fd);uint32_t magic=0,version=0;memcpy(&magic,header,4);memcpy(&version,header+4,4);
    if(!valid||magic!=0x44564d31||version!=1)return nil;
    DVMSharedRender *resource=[DVMSharedRender new];
    resource.mapping=DVMMapManaged(header+16,&fd);if(!resource.mapping)return nil;
    resource.fd=fd;
    resource.buffer=[device newBufferWithBytesNoCopy:resource.mapping length:DVM_PRESENT_BUFFER_BYTES options:MTLResourceStorageModeShared deallocator:nil];
    if(!resource.buffer||resource.buffer.contents!=resource.mapping)return nil;
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:DVM_PRESENT_WIDTH height:DVM_PRESENT_HEIGHT mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
    resource.texture=[resource.buffer newTextureWithDescriptor:d offset:0 bytesPerRow:DVM_PRESENT_ROW];
    if(!resource.texture)return nil;
    const char *delay=getenv("DVM_DRIVER_MANAGED_DELAY_US");
    if(delay){char *end=NULL;unsigned long us=strtoul(delay,&end,10);if(!*delay||*end||us>250000)return nil;resource.completionDelayUS=(unsigned)us;}
    return resource;
}
