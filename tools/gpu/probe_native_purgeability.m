// Host-native behavior probe, not exact-guest resource policy evidence.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(void){@autoreleasepool{
    id<MTLDevice> device=MTLCreateSystemDefaultDevice();if(!device)return 1;
    NSMutableArray *resources=[NSMutableArray array],*names=[NSMutableArray array];
    for(NSNumber *mode in @[@0,@2]){
        id<MTLBuffer> b=[device newBufferWithLength:65536 options:mode.unsignedIntegerValue<<4];
        if(!b)return 2;[resources addObject:b];[names addObject:[NSString stringWithFormat:@"buffer-%@",mode]];
        for(NSNumber *extent in @[@16,@64]){
        MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:80 width:extent.unsignedIntegerValue height:extent.unsignedIntegerValue mipmapped:NO];d.storageMode=mode.unsignedIntegerValue;d.usage=5;
        id<MTLTexture> t=[device newTextureWithDescriptor:d];if(!t)return 3;[resources addObject:t];[names addObject:[NSString stringWithFormat:@"texture-%@",mode]];
        names[names.count-1]=[NSString stringWithFormat:@"texture-%@-%@x%@",mode,extent,extent];}
    }
    for(NSUInteger i=0;i<resources.count;i++){
        id<MTLResource> r=resources[i];NSMutableArray *states=[NSMutableArray array];
        for(NSNumber *state in @[@1,@3,@1,@2,@1,@4,@1,@2,@1]){
            MTLPurgeableState result=[r setPurgeableState:state.unsignedIntegerValue];
            [states addObject:@[@(state.unsignedIntegerValue),@(result)]];
        }
        NSData *json=[NSJSONSerialization dataWithJSONObject:@{@"resource":names[i],@"requested_returned":states} options:NSJSONWritingSortedKeys error:nil];puts([[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding].UTF8String);
    }
}}
