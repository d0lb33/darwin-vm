// Native CoreGraphics oracle for sRGB-tagged RGBA16Float -> SDR BGRA8.
// This proves the host color conversion, not arbitrary DCP color processing.
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#include <assert.h>
int main(int argc,const char **argv){@autoreleasepool{
    assert(argc==2);NSString *out=@(argv[1]);assert([[NSFileManager defaultManager] createDirectoryAtPath:out withIntermediateDirectories:NO attributes:nil error:nil]);
    const unsigned width=256,height=256,row=width*8+64;
    NSMutableData *source=[NSMutableData dataWithLength:row*height];memset(source.mutableBytes,0xa5,source.length);
    for(unsigned i=0;i<65536;i++){
        uint16_t *p=(uint16_t *)((uint8_t *)source.mutableBytes+(i/width)*row+(i%width)*8);
        // Cover every finite half value; nonfinite rejection is a separate test.
        uint16_t h=(i&0x7c00)==0x7c00?0:i;
        p[0]=h;p[1]=0x3400;p[2]=0x3a00;p[3]=0x3c00;
    }
    CGColorSpaceRef color=CGColorSpaceCreateWithName(kCGColorSpaceSRGB);assert(color);
    CGDataProviderRef provider=CGDataProviderCreateWithCFData((__bridge CFDataRef)source);assert(provider);
    CGImageRef image=CGImageCreate(width,height,16,64,row,color,kCGBitmapByteOrder16Little|kCGBitmapFloatComponents|kCGImageAlphaPremultipliedLast,provider,NULL,false,kCGRenderingIntentDefault);assert(image);
    NSMutableData *dest=[NSMutableData dataWithLength:width*height*4];
    CGContextRef context=CGBitmapContextCreate(dest.mutableBytes,width,height,8,width*4,color,kCGBitmapByteOrder32Little|kCGImageAlphaPremultipliedFirst);assert(context);
    CGContextSetBlendMode(context,kCGBlendModeCopy);CGContextSetInterpolationQuality(context,kCGInterpolationNone);
    CGContextDrawImage(context,CGRectMake(0,0,width,height),image);
    assert([source writeToFile:[out stringByAppendingPathComponent:@"source.rgha"] atomically:NO]);
    assert([dest writeToFile:[out stringByAppendingPathComponent:@"expected.bgra"] atomically:NO]);
    NSDictionary *report=@{@"scope":@"native host CoreGraphics sRGB half-float conversion",@"width":@(width),@"height":@(height),@"source_row":@(row),@"output_row":@(width*4),@"finite_binary16_patterns":@63488};
    [[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil] writeToFile:[out stringByAppendingPathComponent:@"oracle.json"] atomically:NO];
    CGContextRelease(context);CGImageRelease(image);CGDataProviderRelease(provider);CGColorSpaceRelease(color);
    puts("NATIVE_RGBA16F_SRGB_ORACLE finite_half_patterns=63488 padded_rows=1");
}}
