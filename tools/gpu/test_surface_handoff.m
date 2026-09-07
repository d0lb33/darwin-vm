// Native host IPC test, not exact-guest evidence. Both children exec afresh.
#import <Foundation/Foundation.h>
#include "surface_handoff.h"
#include <spawn.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include <unistd.h>
static void require(BOOL value,const char *why){if(!value){fprintf(stderr,"FAIL %s\n",why);exit(1);}}
int main(int argc,char **argv){@autoreleasepool {
    DVMSurfacePortAPI api;require(DVMSurfacePortLoad(&api),"symbols");
    if(argc==2){
        kern_return_t kr;IOSurfaceRef surface=DVMSurfacePortsReceive(&api,&kr);
        fprintf(stderr,"CHILD pid=%d receive=%x surface=%p\n",getpid(),kr,surface);require(!kr&&surface,"child receive");
        require(!IOSurfaceLock(surface,0,NULL),"child lock");
        unsigned *bytes=IOSurfaceGetBaseAddress(surface);unsigned expected=(unsigned)atoi(argv[1]);
        require(bytes&&bytes[0]==expected,"original shared bytes");bytes[1]=expected^0xabcdef;
        require(!IOSurfaceUnlock(surface,0,NULL),"child unlock");
        IOSurfaceRef duplicate=DVMSurfacePortsReceive(&api,&kr);
        require(!duplicate&&kr,"child no longer holds registered export capability");
        CFRelease(surface);return 0;
    }
    size_t length=16384;void *bytes=mmap(NULL,length,PROT_READ|PROT_WRITE,MAP_ANON|MAP_SHARED,-1,0);
    require(bytes!=MAP_FAILED,"mapping");
    CFStringRef *address=dlsym(RTLD_DEFAULT,"kIOSurfaceClientAddress");require(address&&*address,"address property");
    IOSurfaceRef surface=IOSurfaceCreate((__bridge CFDictionaryRef)@{(__bridge id)*address:@((uintptr_t)bytes),
        (id)kIOSurfaceWidth:@64,(id)kIOSurfaceHeight:@64,(id)kIOSurfaceBytesPerElement:@4,(id)kIOSurfaceBytesPerRow:@256,
        (id)kIOSurfaceAllocSize:@16384,(id)kIOSurfacePixelFormat:@(0x42475241)});
    require(surface&&IOSurfaceGetBaseAddress(surface)==bytes,"original surface alias");
    for(unsigned i=1;i<=2;i++){
        require(!IOSurfaceLock(surface,0,NULL),"parent lock");((unsigned *)bytes)[0]=i;
        require(!IOSurfaceUnlock(surface,0,NULL),"parent unlock");
        DVMSurfacePortScope scope;int kr=DVMSurfacePortsBegin(&api,surface,&scope);
        fprintf(stderr,"PARENT begin=%x iteration=%u\n",kr,i);require(!kr,"register surface");
        DVMSurfacePortScope conflict;require(DVMSurfacePortsBegin(&api,surface,&conflict)==KERN_INVALID_ARGUMENT,"reject occupied registered slot");
        char count[8];snprintf(count,sizeof(count),"%u",i);char *args[]={argv[0],count,NULL};char *env[]={NULL};pid_t pid;
        int rc=posix_spawn(&pid,argv[0],NULL,NULL,args,env);
        require(!DVMSurfacePortsEnd(&api,&scope),"restore original registered ports");require(!rc,"spawn");
        int status;require(waitpid(pid,&status,0)==pid&&WIFEXITED(status)&&!WEXITSTATUS(status),"fresh child completion");
        require(!IOSurfaceLock(surface,kIOSurfaceLockReadOnly,NULL),"parent verify lock");
        require(((unsigned *)bytes)[1]==(i^0xabcdef),"child write visibility");
        require(!IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,NULL),"parent verify unlock");
    }
    CFRelease(surface);munmap(bytes,length);fprintf(stderr,"PASS two fresh processes shared original IOSurface bytes via inherited Mach right\n");return 0;
}}
