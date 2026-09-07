// Additional exact-guest public API transfer check, outside the CA render.
// GPU-written surrounding-pixel preservation is covered by the host test.
static void DVMRunRegionProbe(id<MTLDevice> device){
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:8 height:6 mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> texture=[device newTextureWithDescriptor:d];if(!texture)fail("region-allocation");
    uint32_t full[48];for(unsigned i=0;i<48;i++)full[i]=0xffff0000;
    [texture replaceRegion:MTLRegionMake2D(0,0,8,6) mipmapLevel:0 withBytes:full bytesPerRow:32];
    uint32_t patch[9]={0xff00ff00,0xff00ff00,0xa5a5a5a5,0xff00ff00,0xff00ff00,0xa5a5a5a5,0xff00ff00,0xff00ff00,0xa5a5a5a5};
    MTLRegion r=MTLRegionMake2D(3,2,2,3);
    [texture replaceRegion:r mipmapLevel:0 withBytes:patch bytesPerRow:12];
    uint32_t pixels[48];[texture getBytes:pixels bytesPerRow:32 fromRegion:MTLRegionMake2D(0,0,8,6) mipmapLevel:0];
    for(unsigned y=0;y<6;y++)for(unsigned x=0;x<8;x++)if(pixels[y*8+x]!=(x>=3&&x<5&&y>=2&&y<5?0xff00ff00:0xffff0000))fail("region-full-pixels");
    uint32_t partial[9];memset(partial,0xa5,sizeof(partial));[texture getBytes:partial bytesPerRow:12 fromRegion:r mipmapLevel:0];
    if(memcmp(partial,patch,sizeof(partial)))fail("region-partial-read-padding");
    fprintf(stderr,"GPU_LOAD_TEXTURE_REGIONS width=8 height=6 x=3 y=2 width_region=2 height_region=3 bad_pixels=0 padding_preserved=1\n");
}
