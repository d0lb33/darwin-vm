// Test-only export for the already installed runner. The helper's exported
// DVMReport keeps these witnesses in its checked shared-RAM audit sequence.
#import "driver_api.h"
#include <dlfcn.h>
#include <stdio.h>
#include <mach/mach.h>
#include <signal.h>
#include <execinfo.h>
#include <unistd.h>
#include <sys/ucontext.h>
static int (*report)(FILE *,const char *,...) __attribute__((format(printf,2,3)));
#define fprintf report
static void fail(const char *reason){fprintf(stderr,"GPU_LOAD_ERROR package=%s\n",reason);exit(1);}
#include "consumer_probe.inc"
#ifdef DVM_CA_REGIONS
#include "consumer_region_probe.inc"
#endif
// Test-package-only diagnosis. No production driver signal interception.
// The supervisor still bounds/reaps a failed child. Prewarm the unwinder;
// if diagnostic unwinding itself fails, the original failed run stays failed.
static void abortReport(int signal,siginfo_t *info,void *context){
    (void)info;ucontext_t *u=context;void *frames[24];int n=backtrace(frames,24);
    char line[480];int len=snprintf(line,sizeof(line),"GPU_LOAD_ABORT signal=%d pc=%llx lr=%llx frames=%d\n",signal,(unsigned long long)u->uc_mcontext->__ss.__pc,(unsigned long long)u->uc_mcontext->__ss.__lr,n);
    if(len>0&&len<(int)sizeof(line))write(2,line,len);
    for(int i=0;i<n;i++){len=snprintf(line,sizeof(line),"GPU_LOAD_ABORT_FRAME index=%d address=%llx\n",i,(unsigned long long)(uintptr_t)frames[i]);if(len>0&&len<(int)sizeof(line))write(2,line,len);}
    _exit(128+signal);
}
static void exceptionReport(NSException *exception){
    fprintf(stderr,"GPU_LOAD_ERROR uncaught_name=%s reason=%.280s\n",exception.name.UTF8String,exception.reason.UTF8String);
}
static void memoryReport(const char *phase){
    task_vm_info_data_t info={0};mach_msg_type_number_t count=TASK_VM_INFO_COUNT;
    kern_return_t status=task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&info,&count);
    if(status!=KERN_SUCCESS||count<TASK_VM_INFO_REV1_COUNT){fprintf(stderr,"GPU_LOAD_MEMORY phase=%s status=%d count=%u available=0\n",phase,status,count);return;}
    fprintf(stderr,"GPU_LOAD_MEMORY phase=%s status=0 resident=%llu resident_peak=%llu footprint=%llu\n",phase,(unsigned long long)info.resident_size,(unsigned long long)info.resident_size_peak,(unsigned long long)info.phys_footprint);
}
void DVMRunGuestTest(id<MTLDevice> device) {
    report=dlsym(RTLD_DEFAULT,"DVMReport");
    if(!report)exit(122);
    void *warm[4];backtrace(warm,4);
    struct sigaction action={0};action.sa_sigaction=abortReport;action.sa_flags=SA_SIGINFO|SA_RESETHAND;
    if(sigaction(SIGABRT,&action,NULL))fail("abort-handler");
    NSSetUncaughtExceptionHandler(exceptionReport);
    memoryReport("before");
    @autoreleasepool {DVMRunQuartzCoreConsumer(device);}
#ifdef DVM_CA_REGIONS
    @autoreleasepool {DVMRunRegionProbe(device);}
#endif
    memoryReport("after-scene");
}
