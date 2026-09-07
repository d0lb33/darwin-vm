// Test-only export for the already installed runner. The helper's exported
// DVMReport keeps these witnesses in its checked shared-RAM audit sequence.
#import "driver_api.h"
#include <dlfcn.h>
#include <stdio.h>
static int (*report)(FILE *,const char *,...) __attribute__((format(printf,2,3)));
#define fprintf report
static void fail(const char *reason){fprintf(stderr,"GPU_LOAD_ERROR package=%s\n",reason);exit(1);}
#include "consumer_probe.inc"
void DVMRunGuestTest(id<MTLDevice> device) {
    report=dlsym(RTLD_DEFAULT,"DVMReport");
    if(!report)exit(122);
    DVMRunQuartzCoreConsumer(device);
}
