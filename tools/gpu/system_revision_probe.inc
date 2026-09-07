// Bounded loading-contract probe inside the actual backboardd process. Uses
// the existing runner package wire/signature/path and development-open route.
// It does not replace the boot device, unload a module, or restart the service.
static void DVMSystemRevisionProbe(Namespace *ns) {
    NSError *error=nil;NSDictionary *job=nil;double deadline=now()+60;
    do {
        job=[ns call:@{@"op":@"runnerNext"} error:&error];
        if(!job)fail("system-revision-next");
        if([job[@"action"] isEqual:@"stage"])break;
        if(![job[@"action"] isEqual:@"idle"]||now()>deadline)fail("system-revision-stage-deadline");
        usleep(100000);
    }while(1);
    uint64_t jobID=[job[@"job"] unsignedLongLongValue];NSUInteger length=[job[@"bytes"] unsignedIntegerValue];
    if(!jobID||!length||length>8*1024*1024||![job[@"mode"] isEqual:@"data"])fail("system-revision-package");
    NSMutableData *data=[NSMutableData dataWithCapacity:length];
    while(data.length<length){
        NSDictionary *chunk=[ns call:@{@"op":@"runnerFetch",@"job":@(jobID),@"offset":@(data.length)} error:&error];
        NSData *part=[[NSData alloc] initWithBase64EncodedString:chunk[@"data"] options:0];
        if(!part.length||part.length>32768||part.length>length-data.length)fail("system-revision-chunk");
        [data appendData:part];
    }
    uint8_t digest[32];CC_SHA256(data.bytes,(CC_LONG)data.length,digest);char hex[65];
    for(unsigned i=0;i<32;i++)snprintf(hex+i*2,3,"%02x",digest[i]);
    if(![@(hex) isEqual:job[@"sha256"]])fail("system-revision-digest");
    // Thin arm64e is required by this exact backboardd, independently of
    // whether an arm64 payload worked in the old development runner.
    const uint32_t *header=data.bytes;
    if(data.length<32||header[0]!=0xfeedfacf||header[1]!=0x100000c||(header[2]&0xffffff)!=2)fail("system-revision-arm64e");
    NSString *directory=[NSString stringWithFormat:@"/private/var/tmp/dvm-gpu-runner/%llu",jobID];
    NSString *bundle=[directory stringByAppendingPathComponent:@"DVMProxy.bundle"];
    int (*mk)(const char *,mode_t)=dlsym(RTLD_DEFAULT,"mkdir");
    if(!mk||mk(directory.UTF8String,0700)||mk(bundle.UTF8String,0700))
        [NSException raise:NSInternalInconsistencyException format:@"system-revision directory errno=%d",errno];
    NSData *info=[[NSData alloc] initWithBase64EncodedString:job[@"info"] options:0];
    NSData *resources=[[NSData alloc] initWithBase64EncodedString:job[@"resources"] options:0];
    if(!info.length||!resources.length||info.length+resources.length>16384)fail("system-revision-signing-metadata");
    NSString *signature=[bundle stringByAppendingPathComponent:@"_CodeSignature"];
    if(mk(signature.UTF8String,0700))fail("system-revision-signature-directory");
    NSDictionary *files=@{@"DVMProxy":data,@"Info.plist":info,@"_CodeSignature/CodeResources":resources};
    for(NSString *name in files)if(![files[name] writeToFile:[bundle stringByAppendingPathComponent:name] options:NSDataWritingAtomic error:&error])
        [NSException raise:NSInternalInconsistencyException format:@"system-revision write %@: %@",name,error];
    DVMReport(stderr,"GPU_LOAD_SYSTEM_REVISION_STAGED pid=%d job=%llu bytes=%lu sha256=%s\n",getpid(),jobID,(unsigned long)length,hex);
    if(![[NSBundle bundleWithPath:bundle] loadAndReturnError:&error]){
        const char *p=error.description.UTF8String;
        for(unsigned i=0;p&&*p&&i<12;i++){unsigned n=0;while(n<280&&p[n])n++;DVMReport(stderr,"GPU_LOAD_SYSTEM_REVISION_LOAD_ERROR part=%u text=%.*s\n",i,n,p);p+=n;}
        fail("system-revision-bundle-load");
    }
    void *handle=dlopen([bundle stringByAppendingPathComponent:@"DVMProxy"].UTF8String,RTLD_NOW|RTLD_LOCAL);
    uint64_t (*revision)(void)=handle?dlsym(handle,"DVMRevisionNumber"):NULL;
    DVMCreateMetalDeviceFn create=handle?dlsym(handle,"DVMCreateMetalDevice"):NULL;
    if(!create||!revision)fail("system-revision-exports");
    @autoreleasepool{
        id<MTLDevice> candidate=create(^NSDictionary *(NSDictionary *r,NSError **e){return [ns call:r error:e];});
        if(!candidate||![(DVMDevice *)candidate contractCapabilities])fail("system-revision-factory-contract");
        DVMReport(stderr,"GPU_LOAD_SYSTEM_REVISION_LOADED pid=%d job=%llu revision=%llu class=%s width=%lu\n",getpid(),jobID,revision(),class_getName([candidate class]),(unsigned long)[(DVMDevice *)candidate maxTextureWidth2D]);
    }
    // Intentionally retain the loaded image for this process's entire life.
    // The normal constructor below still registers the proven boot driver.
}
