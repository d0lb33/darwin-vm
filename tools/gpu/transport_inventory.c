/* Exact-guest channel inventory; read-only unless built with DVM_AUX_PROBE
 * or DVM_AUX_UC_TRANSFER.
 * The optional probe writes only guarded, disposable auxiliary media.
 * No console input, network connections, driver publication, or Metal selection. */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#if defined(DVM_AUX_PROBE) || defined(DVM_AUX_UC_TRANSFER)
#include "aux_transport_probe.h"
#endif
#ifdef DVM_AUX_UC
static unsigned namespace_clients;
#endif

#define LOAD(h, name) __typeof__(&name) p_##name = dlsym(h,#name); if(!p_##name){fprintf(stderr,"GPU_LOAD_ERROR symbol=%s\n",#name);return 4;}
int main(void) {
    /* launchd's explicit StandardErrorPath already supplies the console. */
    setvbuf(stderr,NULL,_IONBF,0);
    fprintf(stderr,"GPU_LOAD_INVENTORY version=1 pid=%d\n",getpid());
    DIR *dir=opendir("/dev");struct dirent *entry;
    if(!dir)return 3;
    while((entry=readdir(dir))) {
        const char *n=entry->d_name;
        if(strstr(n,"tty")||strstr(n,"cu.")||strstr(n,"disk")||strstr(n,"mem")||strstr(n,"vsock")||strstr(n,"console"))
            fprintf(stderr,"GPU_LOAD_DEV name=%s\n",n);
    }
    closedir(dir);
    struct ifaddrs *addresses=NULL;
    int rc=getifaddrs(&addresses);
    fprintf(stderr,"GPU_LOAD_IFADDRS result=%d errno=%d\n",rc,rc?errno:0);
    for(struct ifaddrs *a=addresses;a;a=a->ifa_next)
        fprintf(stderr,"GPU_LOAD_INTERFACE name=%s family=%u flags=0x%x\n",a->ifa_name,a->ifa_addr?a->ifa_addr->sa_family:0,a->ifa_flags);
    if(addresses)freeifaddrs(addresses);
    const int domains[]={AF_INET,AF_INET6,AF_VSOCK};
    for(unsigned i=0;i<sizeof(domains)/sizeof(domains[0]);i++) {
        errno=0;int fd=socket(domains[i],SOCK_STREAM,0);int error=fd<0?errno:0;
        fprintf(stderr,"GPU_LOAD_SOCKET domain=%d fd=%d errno=%d\n",domains[i],fd,error);
        if(fd>=0)close(fd);
    }
    void *cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",RTLD_NOW|RTLD_LOCAL);
    void *io=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_LOCAL);
    if(!cf||!io){fprintf(stderr,"GPU_LOAD_ERROR dlopen=%s\n",dlerror());return 4;}
    LOAD(cf,CFStringCreateWithCString);LOAD(cf,CFStringGetCString);LOAD(cf,CFCopyDescription);LOAD(cf,CFRelease);
    LOAD(io,IOServiceMatching);LOAD(io,IOServiceGetMatchingServices);LOAD(io,IOIteratorNext);
    LOAD(io,IOObjectRelease);LOAD(io,IORegistryEntryGetPath);LOAD(io,IOObjectGetClass);LOAD(io,IORegistryEntryCreateCFProperty);
#ifdef DVM_AUX_UC
    LOAD(io,IOServiceOpen);LOAD(io,IOServiceClose);LOAD(io,IOObjectRetain);
    io_service_t namespace_service=0;
    const char *namespace_path="IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/ans@79600000/AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)/RTBuddyService/AppleANS3CGv2Controller/NS_06";
#endif
#ifdef DVM_AUX_UC_TRANSFER
    LOAD(io,IOConnectCallScalarMethod);aux_scalar=p_IOConnectCallScalarMethod;
#endif
    const char *classes[]={"IONetworkInterface","IOSerialBSDClient","IOUSBDeviceController","AppleNVMeNamespaceDevice","AppleNVMeBlockDevice","IONVMeBlockStorageDevice","AppleEmbeddedNVMeNamespaceDevice","IOMedia","IONVMeController","IOAcceleratorES","AppleParavirtIOSurface"};
    const char *keys[]={"BSD Name","Size","Leaf","IOCalloutDevice","IODialinDevice","IOInterfaceNamePrefix","NSID","NSTYPE","IOUserClientClass","IONameMatched"};
    for(unsigned i=0;i<sizeof(classes)/sizeof(classes[0]);i++) {
        io_iterator_t it=0;unsigned count=0;
        kern_return_t kr=p_IOServiceGetMatchingServices(0,p_IOServiceMatching(classes[i]),&it);
        fprintf(stderr,"GPU_LOAD_MATCH class=%s kr=0x%x\n",classes[i],kr);
        if(kr)continue;
        io_object_t object;
        while((object=p_IOIteratorNext(it))) {
            char path[512]={0},class_name[128]={0};
            p_IORegistryEntryGetPath(object,"IOService",path);p_IOObjectGetClass(object,class_name);
            fprintf(stderr,"GPU_LOAD_SERVICE match=%s class=%s path=%s\n",classes[i],class_name,path);
#ifdef DVM_AUX_UC
            if(!strcmp(classes[i],"AppleNVMeNamespaceDevice")&&
               !strcmp(class_name,"AppleNVMeNamespaceDevice")&&
               !strcmp(path,namespace_path)) {
                namespace_clients++;
                if(!namespace_service) {
                    if(p_IOObjectRetain(object))return 8;
                    namespace_service=object;
                }
            }
#endif
            for(unsigned j=0;j<sizeof(keys)/sizeof(keys[0]);j++) {
                CFStringRef key=p_CFStringCreateWithCString(NULL,keys[j],kCFStringEncodingUTF8);
                CFTypeRef value=p_IORegistryEntryCreateCFProperty(object,key,NULL,0);p_CFRelease(key);
                if(value) {
                    CFStringRef description=p_CFCopyDescription(value);char text[512]={0};
                    p_CFStringGetCString(description,text,sizeof(text),kCFStringEncodingUTF8);
                    for(char *c=text;*c;c++)if(*c=='\n'||*c=='\r')*c=' ';
                    fprintf(stderr,"GPU_LOAD_PROPERTY key=%s value=%s\n",keys[j],text);
#ifdef DVM_AUX_PROBE
                    if(!strcmp(keys[j],"BSD Name")&&strstr(path,"/NS_06@6/")&&
                       !strcmp(classes[i],"IOMedia")) {
                        char bsd[64]={0};
                        p_CFStringGetCString(value,bsd,sizeof(bsd),kCFStringEncodingUTF8);
                        aux_attempts++;
                        if(aux_attempts!=1||aux_probe_media(bsd)) {
                            fprintf(stderr,"GPU_LOAD_ERROR auxiliary-probe-failed\n");return 5;
                        }
                    }
#endif
                    p_CFRelease(description);p_CFRelease(value);
                }
            }
            p_IOObjectRelease(object);count++;
            if(count>=256){fprintf(stderr,"GPU_LOAD_ERROR inventory-truncated class=%s\n",classes[i]);return 9;}
        }
        p_IOObjectRelease(it);
        fprintf(stderr,"GPU_LOAD_MATCH_DONE class=%s count=%u\n",classes[i],count);
    }
#if defined(DVM_AUX_UC)
    if(namespace_clients!=1){fprintf(stderr,"GPU_LOAD_ERROR no-unique-namespace-client candidates=%u\n",namespace_clients);return 7;}
    /* Enumeration must finish before opening or writing the unique target.
     * This guest does not publish an NSID property; the exact observed path,
     * user-client capacity and dedicated header form the experiment guard. */
    io_connect_t client=0;
    kern_return_t opened=p_IOServiceOpen(namespace_service,mach_task_self(),0,&client);
    fprintf(stderr,"GPU_LOAD_AUX_UC_OPEN type=0 kr=0x%x client=0x%x\n",opened,client);
#ifdef DVM_AUX_UC_TRANSFER
    int failed=opened||aux_probe_client(client);
#endif
    if(!opened)fprintf(stderr,"GPU_LOAD_AUX_UC_CLOSE kr=0x%x\n",p_IOServiceClose(client));
    p_IOObjectRelease(namespace_service);
#ifdef DVM_AUX_UC_TRANSFER
    if(failed){fprintf(stderr,"GPU_LOAD_ERROR auxiliary-client-probe-failed\n");return 8;}
    fprintf(stderr,"GPU_LOAD_COMPLETE result=pass scope=auxiliary-byte-transport\n");
#else
    fprintf(stderr,"GPU_LOAD_COMPLETE result=recorded scope=auxiliary-user-client-open\n");
#endif
#elif defined(DVM_AUX_PROBE)
    if(aux_attempts!=1){fprintf(stderr,"GPU_LOAD_ERROR no-unique-aux-media attempts=%u\n",aux_attempts);return 6;}
    fprintf(stderr,"GPU_LOAD_COMPLETE result=pass scope=auxiliary-byte-transport\n");
#else
    fprintf(stderr,"GPU_LOAD_COMPLETE result=pass scope=read-only-channel-inventory\n");
#endif
    return 0;
}
