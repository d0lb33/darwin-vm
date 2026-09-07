// Native runtime observation only; no interpretation of private usage bits.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(void){@autoreleasepool{
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();
    for(NSNumber *format in @[@70,@80]) for(NSNumber *value in @[@5,@65541]){
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:format.unsignedIntegerValue width:64 height:64 mipmapped:NO];
        d.storageMode=MTLStorageModePrivate;d.usage=value.unsignedIntegerValue;
        fprintf(stderr,"DESCRIPTOR usage=%lu %s\n",(unsigned long)d.usage,d.description.UTF8String);
        id<MTLTexture> t=[device newTextureWithDescriptor:d];
        fprintf(stderr,"TEXTURE requested=%lu accepted=%u actual=%lu %s\n",(unsigned long)d.usage,t!=nil,(unsigned long)t.usage,t.description.UTF8String);
        if(!t||t.usage!=d.usage)return 1;
    }
    return 0;
}}
