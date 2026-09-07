// Isolated development supervisor. Host sends only driver bytes plus an opaque
// job ID. No shell, executable path or command line is accepted over the wire.
// Each child execs this pinned trusted helper and lets dyld/kernel validate the
// staged driver's actual signature. A payload digest is not code-signing trust.
static NSString *DVMRunnerDigest(NSData *data) {
    uint8_t digest[32];CC_SHA256(data.bytes,(CC_LONG)data.length,digest);
    char hex[65];for(unsigned i=0;i<32;i++)snprintf(hex+i*2,3,"%02x",digest[i]);
    return @(hex);
}
static void DVMRunnerFail(const char *contract) {
    fprintf(stderr,"GPU_LOAD_RUNNER_FATAL contract=%s\n",contract);
    fail(contract);
}
static int DVMRunTestSupervisor(const char *executable) {
    void *io=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_LOCAL);
    if(!io)DVMRunnerFail("runner-iokit");Namespace *ns=openMMIO(io);
    int (*spawn)(pid_t *,const char *,const posix_spawn_file_actions_t *,const posix_spawnattr_t *,char *const[],char *const[])=dlsym(RTLD_DEFAULT,"posix_spawn");
    int (*mkdirFn)(const char *,mode_t)=dlsym(RTLD_DEFAULT,"mkdir");
    if(!spawn||!mkdirFn)DVMRunnerFail("runner-symbols");
    const char *base="/private/var/tmp/dvm-gpu-runner";
    if(mkdirFn(base,0700)&&errno!=EEXIST)DVMRunnerFail("runner-directory");
    BOOL systemWritable=NO;
    // Stable owner across exec'd workers. Only the supervisor maps the pool;
    // each worker gets a scoped IOSurface capability, never the service owner.
    id<DVMMetalOwnedMapping> pool=nil;id surfaceObject=nil;
    DVMSurfacePortAPI portAPI;
    fprintf(stderr,"GPU_LOAD_RUNNER_READY pid=%d\n",getpid());
    while(1)@autoreleasepool {
        NSError *error=nil;NSDictionary *job=[ns call:@{@"op":@"runnerNext"} error:&error];
        if(!job)DVMRunnerFail("runner-next");
        if([job[@"action"] isEqual:@"stop"]){fprintf(stderr,"GPU_LOAD_RUNNER_COMPLETE\n");return 0;}
        if([job[@"action"] isEqual:@"idle"]){usleep(100000);continue;}
        uint64_t jobID=[job[@"job"] unsignedLongLongValue];NSUInteger length=[job[@"bytes"] unsignedIntegerValue];
        if(![job[@"action"] isEqual:@"stage"]||!jobID||!length||length>8*1024*1024||![job[@"sha256"] isKindOfClass:NSString.class])DVMRunnerFail("runner-job");
        double staged=now();NSMutableData *data=[NSMutableData dataWithCapacity:length];
        while(data.length<length){
            NSDictionary *chunk=[ns call:@{@"op":@"runnerFetch",@"job":@(jobID),@"offset":@(data.length)} error:&error];
            NSData *part=[[NSData alloc] initWithBase64EncodedString:chunk[@"data"] options:0];
            if(!part.length||part.length>32768||part.length>length-data.length)DVMRunnerFail("runner-chunk");
            [data appendData:part];
        }
        if(![DVMRunnerDigest(data) isEqual:job[@"sha256"]])DVMRunnerFail("runner-payload-digest");
        NSString *bundle=nil;NSString *mode=job[@"mode"]?:@"data";
        if([mode isEqual:@"installed"]){
            bundle=@"/usr/local/libexec/DVMProxy.bundle";
            NSData *installed=[NSData dataWithContentsOfFile:[bundle stringByAppendingPathComponent:@"DVMProxy"]];
            if(!installed||![DVMRunnerDigest(installed) isEqual:job[@"sha256"]])DVMRunnerFail("runner-installed-preimage");
        } else {
        const char *stageBase=base;
        if([mode isEqual:@"system"]){
            if(!systemWritable){
                pid_t mountPID=0;char *mountArgs[]={"/sbin/mount","-uw","/",NULL};char *mountEnv[]={NULL};
                int mountStatus=0,mountRC=spawn(&mountPID,"/sbin/mount",NULL,NULL,mountArgs,mountEnv);pid_t mounted=-1;
                if(!mountRC){
                    double mountStarted=now();
                    do {mounted=waitpid(mountPID,&mountStatus,WNOHANG);if(!mounted)usleep(10000);}while((!mounted||(mounted<0&&errno==EINTR))&&now()-mountStarted<15);
                    if(!mounted){int (*killFn)(pid_t,int)=dlsym(RTLD_DEFAULT,"kill");if(!killFn)DVMRunnerFail("runner-kill-symbol");killFn(mountPID,SIGKILL);do {mounted=waitpid(mountPID,&mountStatus,0);}while(mounted<0&&errno==EINTR);}
                }
                fprintf(stderr,"GPU_LOAD_RUNNER_MOUNT pid=%d spawn=%d waited=%d status=%x\n",mountPID,mountRC,mounted,mountStatus);
                if(mountRC||mounted!=mountPID||!WIFEXITED(mountStatus)||WEXITSTATUS(mountStatus))DVMRunnerFail("runner-system-remount");
                systemWritable=YES;
            }
            stageBase="/usr/local/libexec/dvm-gpu-runner";
            if(mkdirFn(stageBase,0700)&&errno!=EEXIST)DVMRunnerFail("runner-system-directory");
        } else if(![mode isEqual:@"data"])DVMRunnerFail("runner-stage-mode");
        NSString *directory=[NSString stringWithFormat:@"%s/%llu",stageBase,jobID];
        if(mkdirFn(directory.UTF8String,0700))DVMRunnerFail("runner-job-directory-exists");
        bundle=[directory stringByAppendingPathComponent:@"DVMProxy.bundle"];
        if(mkdirFn(bundle.UTF8String,0700))DVMRunnerFail("runner-bundle-directory");
        if(![data writeToFile:[bundle stringByAppendingPathComponent:@"DVMProxy"] atomically:YES])DVMRunnerFail("runner-driver-write");
        NSData *info=[[NSData alloc] initWithBase64EncodedString:job[@"info"] options:0];
        NSData *resources=[[NSData alloc] initWithBase64EncodedString:job[@"resources"] options:0];
        if(!info.length||!resources.length||info.length+resources.length>16384)DVMRunnerFail("runner-signing-metadata");
        if(![info writeToFile:[bundle stringByAppendingPathComponent:@"Info.plist"] atomically:YES])DVMRunnerFail("runner-info-write");
        NSString *signature=[bundle stringByAppendingPathComponent:@"_CodeSignature"];
        if(mkdirFn(signature.UTF8String,0700)||![resources writeToFile:[signature stringByAppendingPathComponent:@"CodeResources"] atomically:YES])DVMRunnerFail("runner-resources-write");
        }
        fprintf(stderr,"GPU_LOAD_RUNNER_STAGED job=%llu bytes=%lu sha256=%s staging_us=%.3f mode=%s\n",jobID,(unsigned long)length,[job[@"sha256"] UTF8String],(now()-staged)*1e6,mode.UTF8String);
        NSString *env=[@"DVM_DRIVER_BUNDLE_PATH=" stringByAppendingString:bundle];
        BOOL package=[job[@"test"] isEqual:@"package"],development=[job[@"development"] isEqual:@YES];
        BOOL shared=[job[@"shared_surface"] isEqual:@YES];DVMSurfacePortScope portScope={0};
        if(shared){
            if(!pool){pool=DVMMapDriverPool(ns,&error);if(!pool)DVMRunnerFail("supervisor-pool");
                surfaceObject=CFBridgingRelease(DVMCreatePoolSurface(pool));
                if(!DVMSurfacePortLoad(&portAPI))DVMRunnerFail("supervisor-port-symbols");
                fprintf(stderr,"GPU_LOAD_SURFACE_OWNER pid=%d surface=%u bytes=%lu\n",getpid(),IOSurfaceGetID((__bridge IOSurfaceRef)surfaceObject),(unsigned long)pool.length);
            }
            IOSurfaceRef surface=(__bridge IOSurfaceRef)surfaceObject;
            if(IOSurfaceLock(surface,0,NULL))DVMRunnerFail("supervisor-lock");
            uint64_t *witness=(uint64_t *)((uint8_t *)pool.bytes+DVM_PRESENT_BYTES);witness[0]=jobID;witness[1]=0;
            if(IOSurfaceUnlock(surface,0,NULL))DVMRunnerFail("supervisor-unlock");
            kern_return_t portRC=DVMSurfacePortsBegin(&portAPI,surface,&portScope);
            fprintf(stderr,"GPU_LOAD_SURFACE_SEND job=%llu result=%x surface=%u\n",jobID,portRC,IOSurfaceGetID(surface));
            if(portRC)DVMRunnerFail("supervisor-port-register");
        }
        char *args[]={(char *)executable,"--worker",NULL};char *environment[]={(char *)env.UTF8String,package?"DVM_RUNNER_TEST=package":"DVM_RUNNER_TEST=builtin",development?"DVM_RUNNER_DEVELOPMENT=1":"DVM_RUNNER_DEVELOPMENT=0",shared?"DVM_RUNNER_SURFACE=1":"DVM_RUNNER_SURFACE=0",NULL};
        pid_t child=0;double started=now();int rc=spawn(&child,executable,NULL,NULL,args,environment);
        if(shared&&DVMSurfacePortsEnd(&portAPI,&portScope))DVMRunnerFail("supervisor-port-restore");
        int status=0;pid_t waited=-1;
        if(!rc){
            do {waited=waitpid(child,&status,WNOHANG);if(waited==0)usleep(10000);}while((waited==0||(waited<0&&errno==EINTR))&&now()-started<90);
            if(waited==0){int (*killFn)(pid_t,int)=dlsym(RTLD_DEFAULT,"kill");if(!killFn)DVMRunnerFail("runner-kill-symbol");killFn(child,SIGKILL);do {waited=waitpid(child,&status,0);}while(waited<0&&errno==EINTR);}
        }
        // Child was the sole shared-RAM producer until waitpid completed.
        ns.sequence=*(volatile uint64_t *)(ns.regs+DVM_GPU_REG_DONE);
        mmioReportSequence=*(volatile uint64_t *)(ns.shared+0x180);
        BOOL sharedSafe=YES;
        if(shared){
            IOSurfaceRef surface=(__bridge IOSurfaceRef)surfaceObject;
            int lockRC=IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL);
            uint64_t *witness=(uint64_t *)((uint8_t *)pool.bytes+DVM_PRESENT_BYTES);
            sharedSafe=!rc&&waited==child&&WIFEXITED(status)&&!WEXITSTATUS(status)&&!lockRC&&witness[0]==jobID&&witness[1]==(jobID^0x44564d48414e4446ULL);
            if(!lockRC&&IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL))sharedSafe=NO;
            fprintf(stderr,"GPU_LOAD_SURFACE_RETURN job=%llu pid=%d surface=%u alias_verified=%u\n",jobID,child,IOSurfaceGetID(surface),sharedSafe);
        }
        fprintf(stderr,"GPU_LOAD_RUNNER_EXIT job=%llu pid=%d spawn=%d waited=%d status=%x elapsed_us=%.3f\n",jobID,child,rc,waited,status,(now()-started)*1e6);
        NSDictionary *result=@{@"op":@"runnerResult",@"job":@(jobID),@"pid":@(child),@"spawn":@(rc),@"waited":@(waited),@"exit":@(rc||waited!=child?-1:WIFEXITED(status)?WEXITSTATUS(status):-1),@"signal":@(rc||waited!=child?0:WIFSIGNALED(status)?WTERMSIG(status):0),@"elapsed_us":@((now()-started)*1e6)};
        if(![ns call:result error:&error])DVMRunnerFail("runner-result");
        // Failure can leave GPU/display work outstanding. Do not hand the pool
        // to another child merely because waitpid or client close succeeded.
        if(!sharedSafe)DVMRunnerFail("shared-child-failed-no-reuse");
    }
}
