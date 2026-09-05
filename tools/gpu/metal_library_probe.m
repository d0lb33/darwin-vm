// Host-only loading/reflection of unmodified guest UI libraries. No guest code executes.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
static id err(NSError *e) { return e ? @{ @"domain":e.domain, @"code":@(e.code), @"description":e.localizedDescription, @"userInfo":e.userInfo.description } : (id)[NSNull null]; }
int main(int argc, const char **argv) { @autoreleasepool {
 if(argc != 2) return 2;
 id<MTLDevice> d = MTLCreateSystemDefaultDevice();
 NSMutableDictionary *r = [@{@"path":@(argv[1]), @"device":d.name ?: @"nil", @"os":[NSProcessInfo processInfo].operatingSystemVersionString} mutableCopy];
 NSData *bytes = [NSData dataWithContentsOfFile:@(argv[1])];
 if(!bytes || !d) return 3;
 dispatch_data_t data = dispatch_data_create(bytes.bytes, bytes.length, nil, DISPATCH_DATA_DESTRUCTOR_DEFAULT);
 NSError *e = nil;
 id<MTLLibrary> lib = [d newLibraryWithData:data error:&e];
 r[@"load"]=@(lib != nil); r[@"loadError"]=err(e);
 if(!lib) {
  MTLBinaryArchiveDescriptor *ad=[MTLBinaryArchiveDescriptor new];ad.url=[NSURL fileURLWithPath:@(argv[1])];e=nil;
  id<MTLBinaryArchive> ar=[d newBinaryArchiveWithDescriptor:ad error:&e];
  r[@"binaryArchiveLoad"]=@(ar!=nil);r[@"binaryArchiveError"]=err(e);
 }
 NSMutableArray *functions = [NSMutableArray array];
 for(NSString *name in lib.functionNames) {
  id<MTLFunction> f=[lib newFunctionWithName:name];
  NSMutableDictionary *v=[@{@"name":name, @"created":@(f!=nil), @"type":@(f.functionType), @"constants":f.functionConstantsDictionary.description ?: @"", @"attributes":f.vertexAttributes.description ?: @""} mutableCopy];
  if(f.functionType==MTLFunctionTypeKernel && f.functionConstantsDictionary.count == 0) {
   MTLComputePipelineReflection *ref=nil; e=nil;
   id<MTLComputePipelineState> ps=[d newComputePipelineStateWithFunction:f options:MTLPipelineOptionArgumentInfo|MTLPipelineOptionBufferTypeInfo reflection:&ref error:&e];
   v[@"pipeline"]=@(ps!=nil); v[@"pipelineError"]=err(e); v[@"reflection"]=ref.description ?: @"";
   if(!ps) {
    MTLTileRenderPipelineDescriptor *tile=[MTLTileRenderPipelineDescriptor new];
    tile.tileFunction=f;tile.rasterSampleCount=1;tile.colorAttachments[0].pixelFormat=MTLPixelFormatBGRA8Unorm;
    MTLRenderPipelineReflection *tr=nil;e=nil;
    id<MTLRenderPipelineState> tp=[d newRenderPipelineStateWithTileDescriptor:tile options:MTLPipelineOptionArgumentInfo reflection:&tr error:&e];
    v[@"tilePipeline"]=@(tp!=nil);v[@"tileError"]=err(e);v[@"tileReflection"]=tr.description ?: @"";
   }
  }
  [functions addObject:v];
 }
 r[@"functions"]=functions;
 NSData *out=[NSJSONSerialization dataWithJSONObject:r options:NSJSONWritingPrettyPrinted error:&e];
 if(!out) { fprintf(stderr,"JSON: %s\n",e.description.UTF8String); return 4; }
 fwrite(out.bytes,1,out.length,stdout); puts("");
 return lib?0:1;
}}
