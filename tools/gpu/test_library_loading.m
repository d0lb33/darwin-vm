// Host file/URL entrypoint checks using an explicitly supplied MTLB file.
// File provenance is separate from this native loading result.
#define main DVMUnusedHostMain
#include "driver_host.m"
#undef main
#import "driver_api.h"
static void check(BOOL v){if(!v)abort();}
int main(void){@autoreleasepool{
 DVMHost *host=[DVMHost new];host.device=MTLCreateSystemDefaultDevice();host.queue=[host.device newCommandQueue];host.entries=[NSMutableDictionary dictionary];
 __block uint64_t seq=0;__block unsigned libraries=0;
 id<MTLDevice> d=DVMCreateMetalDevice(^NSDictionary *(NSDictionary *r,NSError **e){@synchronized(host){if([r[@"op"] isEqual:@"library"])libraries++;NSDictionary *v=ProcessRequest(host,++seq,r);if(![v[@"ok"] boolValue]){if(e)*e=[NSError errorWithDomain:@"probe" code:1 userInfo:@{NSLocalizedDescriptionKey:v[@"description"]?:@"RPC"}];return nil;}return v;}});
 NSError *e=nil;NSString *path=@(getenv("DVM_DRIVER_LIBRARY"));
 id<MTLLibrary> file=[d newLibraryWithFile:path error:&e];check(file!=nil&&!e&&file.functionNames.count>0);
 id<MTLLibrary> url=[d newLibraryWithURL:[NSURL fileURLWithPath:path] error:&e];check(url!=nil&&!e&&[file.functionNames isEqual:url.functionNames]);
 check(![d newLibraryWithFile:[path stringByAppendingString:@".absent"] error:&e]&&e&&libraries==2);
 fprintf(stderr,"LIBRARY_ENTRYPOINT_PASS file_url_native_load functions=%lu native_loads=%u missing_file_no_RPC=1\n",(unsigned long)file.functionNames.count,libraries);
}}
