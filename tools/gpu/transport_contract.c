/* Bounded guest-side prerequisites for a shared-RAM/MMIO transport.
 * No executable kext, storage I/O or mapped-memory access. The explicit
 * management variant publishes only its own inert IOService personality in
 * a unique matching category. All mappings read-only and immediately unmapped.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <dlfcn.h>
#include <dirent.h>
#include <errno.h>
#include <mach/mach.h>
#include <mach/host_priv.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define LOAD(h, n) __typeof__(&n) p_##n = dlsym(h, #n); if (!p_##n) { fprintf(stderr,"GPU_LOAD_ERROR symbol=%s\n",#n); return 4; }

static void request(const char *label, const void *bytes, unsigned length) {
    vm_offset_t response=0, log=0;
    mach_msg_type_number_t response_n=0, log_n=0;
    kern_return_t op=0x7fffffff;
    host_t host=mach_host_self();
    fprintf(stderr,"GPU_LOAD_CONTRACT_BEGIN request=%s bytes=%u\n",label,length);
    kern_return_t kr=kext_request(host, 0, (vm_offset_t)bytes, length,
        &response, &response_n, &log, &log_n, &op);
    fprintf(stderr,"GPU_LOAD_CONTRACT_KEXT request=%s kr=0x%x op=0x%x response_bytes=%u log_bytes=%u\n",
        label,kr,op,response_n,log_n);
    /* Bound console traffic; no kernel addresses or response blob is needed. */
    if (response && response_n) {
        fprintf(stderr,"GPU_LOAD_CONTRACT_RESPONSE request=%s text=%.*s\n",label,
            (int)(response_n<1024?response_n:1024),(const char *)response);
        vm_deallocate(mach_task_self(),response,response_n);
    }
    if (log && log_n) vm_deallocate(mach_task_self(),log,log_n);
    mach_port_deallocate(mach_task_self(),host);
}

int main(void) {
    setvbuf(stderr,NULL,_IONBF,0);
    fprintf(stderr,"GPU_LOAD_CONTRACT version=1 pid=%d uid=%d\n",getpid(),getuid());
    const char query[]="<dict><key>Kext Request Predicate</key><string>DaemonReady</string></dict>";
    request("daemon-ready",query,sizeof(query));
    const char invalid[]="<dict/>";
    request("xml-no-predicate-control",invalid,sizeof(invalid));
    /* Recognizable mkext envelope ONLY. Deliberately no driver code: exact
     * 24A5430a rejects magic+signature before checksum/plist validation.
     * This tests the loader gate, not whether a real kext is well formed. */
    const unsigned char envelope[48]={'M','K','X','T','M','O','S','X'};
    request("mkext-recognition-gate",envelope,sizeof(envelope));
#ifdef DVM_CONTRACT_MANAGE
    /* A unique category on IOResources cannot replace hardware/UI drivers.
     * The existing IOService class has no custom executable or MMIO method. */
    const char codeless[]=
        "<dict><key>Kext Request Predicate</key><string>LoadCodelessKext</string>"
        "<key>Kext Request Arguments</key><dict>"
        "<key>CFBundleIdentifier</key><string>org.darwin-vm.transport-contract</string>"
        "<key>Codeless Kext Info</key><dict>"
        "<key>CFBundleIdentifier</key><string>org.darwin-vm.transport-contract</string>"
        "<key>CFBundleVersion</key><string>1.0</string>"
        "<key>CFBundlePackageType</key><string>KEXT</string>"
        "<key>_CodelessKextBundlePath</key><string>/usr/local/libexec/DVMTransportContract.kext</string>"
        "<key>IOKitPersonalities</key><dict><key>DVMTransportContract</key><dict>"
        "<key>CFBundleIdentifier</key><string>com.apple.kernel.iokit</string>"
        "<key>IOClass</key><string>IOService</string>"
        "<key>IOProviderClass</key><string>IOResources</string>"
        "<key>IOResourceMatch</key><string>IOKit</string>"
        "<key>IOMatchCategory</key><string>org.darwin-vm.transport-contract</string>"
        "<key>DVMTransportContract</key><string>v1</string>"
        "</dict></dict></dict></dict></dict>";
    request("load-inert-codeless-personality",codeless,sizeof(codeless));
#endif
    const char *paths[]={"/dev/mem","/dev/kmem","/usr/libexec/driverkitd",
        "/System/Library/DriverExtensions"};
    for(unsigned i=0;i<sizeof(paths)/sizeof(paths[0]);i++) {
        struct stat st; errno=0; int rc=stat(paths[i],&st);
        fprintf(stderr,"GPU_LOAD_CONTRACT_PATH path=%s rc=%d errno=%d\n",paths[i],rc,rc?errno:0);
    }
    DIR *directory=opendir("/System/Library/DriverExtensions");
    if(directory) {
        struct dirent *entry;unsigned count=0;
        while((entry=readdir(directory))) {
            if(entry->d_name[0]=='.')continue;
            fprintf(stderr,"GPU_LOAD_CONTRACT_DEXT name=%s\n",entry->d_name);
            if(++count>=64)break;
        }
        closedir(directory);
    }
    void *io=dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",RTLD_NOW|RTLD_LOCAL);
    if(!io) {fprintf(stderr,"GPU_LOAD_ERROR dlopen=%s\n",dlerror());return 4;}
    LOAD(io,IOServiceMatching);LOAD(io,IOServiceGetMatchingServices);LOAD(io,IOIteratorNext);
    LOAD(io,IORegistryEntryGetPath);LOAD(io,IOObjectGetClass);LOAD(io,IOObjectRelease);
    LOAD(io,IOServiceOpen);LOAD(io,IOServiceClose);
    LOAD(io,IOConnectMapMemory64);LOAD(io,IOConnectUnmapMemory64);
#ifdef DVM_CONTRACT_MANAGE
    void *cf=dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",RTLD_NOW|RTLD_LOCAL);
    if(!cf)return 4;
    LOAD(cf,CFStringCreateWithCString);LOAD(cf,CFDictionaryCreateMutable);
    LOAD(cf,CFDictionarySetValue);LOAD(cf,CFRelease);
    CFStringRef property=p_CFStringCreateWithCString(NULL,"DVMTransportContract",kCFStringEncodingUTF8);
    CFStringRef value=p_CFStringCreateWithCString(NULL,"v1",kCFStringEncodingUTF8);
    CFStringRef match_key=p_CFStringCreateWithCString(NULL,"IOPropertyMatch",kCFStringEncodingUTF8);
    CFMutableDictionaryRef properties=p_CFDictionaryCreateMutable(NULL,0,NULL,NULL);
    p_CFDictionarySetValue(properties,property,value);
    CFMutableDictionaryRef matching=p_IOServiceMatching("IOService");
    p_CFDictionarySetValue(matching,match_key,properties);
    io_iterator_t own_it=0;
    kern_return_t own_kr=p_IOServiceGetMatchingServices(0,matching,&own_it);
    unsigned own_count=0;
    io_service_t own;
    if(!own_kr)while((own=p_IOIteratorNext(own_it))) {
        char own_path[512]={0};p_IORegistryEntryGetPath(own,"IOService",own_path);
        fprintf(stderr,"GPU_LOAD_CONTRACT_OWN_SERVICE path=%s\n",own_path);
        own_count++;p_IOObjectRelease(own);
        if(own_count>=4)break;
    }
    if(own_it)p_IOObjectRelease(own_it);
    fprintf(stderr,"GPU_LOAD_CONTRACT_OWN_MATCH kr=0x%x count=%u\n",own_kr,own_count);
    p_CFRelease(properties);p_CFRelease(match_key);p_CFRelease(value);p_CFRelease(property);
#endif
    const char *classes[]={"DVMTransport","IOUserServer","IOPlatformExpertDevice","AppleNVMeNamespaceDevice"};
    for(unsigned i=0;i<sizeof(classes)/sizeof(classes[0]);i++) {
        io_iterator_t it=0; unsigned count=0;
        kern_return_t kr=p_IOServiceGetMatchingServices(0,p_IOServiceMatching(classes[i]),&it);
        fprintf(stderr,"GPU_LOAD_CONTRACT_MATCH class=%s kr=0x%x\n",classes[i],kr);
        if(kr)continue;
        io_service_t service;
        while((service=p_IOIteratorNext(it))) {
            char path[512]={0},class_name[128]={0};
            p_IORegistryEntryGetPath(service,"IOService",path);
            p_IOObjectGetClass(service,class_name);
            fprintf(stderr,"GPU_LOAD_CONTRACT_SERVICE class=%s path=%s\n",class_name,path);
            /* One justified generic user-client type and memory index only.
             * NS6 is our disposable auxiliary namespace; never open NS1–5. */
            const char *ns6="IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/ans@79600000/AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)/RTBuddyService/AppleANS3CGv2Controller/NS_06";
            if(!strcmp(classes[i],"IOPlatformExpertDevice") || !strcmp(path,ns6)) {
                io_connect_t client=0;
                kr=p_IOServiceOpen(service,mach_task_self(),0,&client);
                fprintf(stderr,"GPU_LOAD_CONTRACT_OPEN class=%s type=0 kr=0x%x\n",class_name,kr);
                if(!kr) {
                    mach_vm_address_t address=0;mach_vm_size_t size=0;
                    kr=p_IOConnectMapMemory64(client,0,mach_task_self(),&address,&size,kIOMapAnywhere|kIOMapReadOnly);
                    fprintf(stderr,"GPU_LOAD_CONTRACT_MAP class=%s memory_type=0 read_only=1 kr=0x%x bytes=%llu\n",class_name,kr,size);
                    if(!kr)fprintf(stderr,"GPU_LOAD_CONTRACT_UNMAP kr=0x%x\n",p_IOConnectUnmapMemory64(client,0,mach_task_self(),address));
                    fprintf(stderr,"GPU_LOAD_CONTRACT_CLOSE kr=0x%x\n",p_IOServiceClose(client));
                }
            }
            p_IOObjectRelease(service); count++;
            if(count>=64) {fprintf(stderr,"GPU_LOAD_ERROR service-limit\n");return 5;}
        }
        p_IOObjectRelease(it);
        fprintf(stderr,"GPU_LOAD_CONTRACT_MATCH_DONE class=%s count=%u\n",classes[i],count);
    }
    fprintf(stderr,"GPU_LOAD_COMPLETE result=recorded scope=mmio-loading-mapping-contracts\n");
    return 0;
}
