// Exact 24A5430a, opt-in development image only. Three gates: owned service,
// dedicated open type, and an AMFI-resolved entitlement on the current process.
// Stock cs_allow_invalid owns flags, locks, map references and TXM interaction.
// Monitor return is logged separately; a flags transition is not loading proof.
#include <IOKit/IOUserClient.h>

extern "C" void *dvm_current_proc();
extern "C" int dvm_entitled(void *,const char *,bool *);
extern "C" int dvm_cs_allow_invalid(void *);
extern "C" unsigned long long dvm_csflags(void *);
extern "C" int dvm_pid();
extern "C" int dvm_developer_mode();
extern "C" int dvm_printf(const char *,...);
extern "C" int dvm_original_policy(void *);
extern "C" int dvm_original_monitor(void *);
extern "C" IOReturn dvm_transport_uc(IOService *,task_t,void *,UInt32,OSDictionary *,IOUserClient **);

static bool entitled(void *proc) {
    bool value=false;
    return proc && proc==dvm_current_proc() &&
        !dvm_entitled(proc,"org.darwin-vm.development-loader",&value) && value;
}
#if DVM_LOADER_FIX >= 1
extern "C" unsigned dvm_blob_flags(void *);
extern "C" int dvm_original_mmap(void *,void *,int,int,unsigned long long,int *);
#if DVM_LOADER_FIX >= 4
extern "C" const unsigned char *dvm_blob_hash(void *,unsigned char *);
extern "C" void dvm_authorize_hash(const unsigned char *,unsigned long);
extern "C" bool dvm_match_hash(const unsigned char *,unsigned long);
#endif
static bool stagedPath(const char *path) {
    const char *prefix="/private/var/tmp/dvm-gpu-runner/";
    if(!path)return false;
    while(*prefix)if(*path++!=*prefix++)return false;
    unsigned digits=0;
    while(*path>='0'&&*path<='9'){path++;if(++digits>20)return false;}
    if(!digits)return false;
    const char *suffix="/DVMProxy.bundle/DVMProxy";
    do {if(*path++!=*suffix)return false;}while(*suffix++);
    return true;
}
// The ad-hoc signature has already been parsed. Modes 1/2 retain subsequent
// AMFI policy checks; mode 3 selects the existing completion path for this
// scoped test bundle. Real CT bits and the callback's return remain intact.
extern "C" bool dvm_development_ct(const char *path,void *blob,unsigned long long policy,unsigned *outputFlags) {
    void *proc=dvm_current_proc();
    if(policy||!stagedPath(path)||!entitled(proc)||
       !(dvm_csflags(proc)&0x10000000)||!dvm_developer_mode())return false;
    unsigned flags=dvm_blob_flags(blob);
    bool allow=(flags&2)!=0;
    dvm_printf("DVM_DEV_LOADER ct pid=%d flags=0x%x policy=0x%llx allow=%d path=%s\n",dvm_pid(),flags,policy,allow,path);
#if DVM_LOADER_FIX >= 4
    if(allow){
        unsigned char length=0;
        const unsigned char *hash=dvm_blob_hash(blob,&length);
        if(!hash||length<20||length>64)return false;
        bool hasEntitlement=false;
        if(dvm_entitled(proc,"com.apple.private.amfi.can-load-cdhash",&hasEntitlement)||!hasEntitlement)return false;
        // Stock wrappers own the compilation-service lock and TXM calls.
        // This replaces the monitor's single authorized hash slot, not a TC.
        dvm_authorize_hash(hash,length);
        bool matched=dvm_match_hash(hash,length);
        char hex[129];const char *digits="0123456789abcdef";
        for(unsigned i=0;i<length;i++){hex[2*i]=digits[hash[i]>>4];hex[2*i+1]=digits[hash[i]&15];}
        hex[2*length]=0;
        dvm_printf("DVM_DEV_LOADER compilation_hash pid=%d length=%u matched=%d hash=%s\n",dvm_pid(),length,matched,hex);
        if(!matched)return false;
    }
#endif
#if DVM_LOADER_FIX >= 3
    if(allow){
        // Same privilege-bit removal as AMFI's non-platform continuation.
        unsigned before=*outputFlags;*outputFlags&=0xf3ffbfff;
        dvm_printf("DVM_DEV_LOADER completion_policy pid=%d flags_before=%x flags_after=%x\n",dvm_pid(),before,*outputFlags);
    }
#endif
    return allow;
}
extern "C" int dvm_development_mmap(void *cred,void *fg,int prot,int flags,
        unsigned long long offset,int *maxprot) {
    int before=*maxprot;
    int result=dvm_original_mmap(cred,fg,prot,flags,offset,maxprot);
    void *proc=dvm_current_proc();
    if((prot&4)&&entitled(proc)&&(dvm_csflags(proc)&0x10000000)) {
        int original=result;
#if DVM_LOADER_FIX >= 2
        // Exact observed dyld RX request only. Signature validation remains
        // upstream; no writable-executable mapping or expanded max protection.
        // This is process-scoped, not pathname-scoped: only the opted-in,
        // specially entitled disposable test child receives the exception.
        if(result==1&&prot==5&&flags==0x40012&&offset==0&&
           before==7&&*maxprot==7&&dvm_developer_mode())result=0;
#endif
        dvm_printf("DVM_DEV_LOADER mmap pid=%d result=%d returned=%d prot=%x flags=%x offset=%llx max_before=%x max_after=%x fg=%p\n",dvm_pid(),original,result,prot,flags,offset,before,*maxprot,fg);
    }
    return result;
}
#if DVM_LOADER_FIX >= 3
// Exact callback prologue and its two stack arguments match the public MAC
// signature-check ABI. Observe the original return/output, never replace it.
extern "C" int dvm_original_signature(void *,void *,int,void *,unsigned *,unsigned *,int,unsigned,char **,unsigned long *);
extern "C" int dvm_development_signature(void *vp,void *label,int cpu,void *blob,
        unsigned *flags,unsigned *signer,int options,unsigned platform,char **fatal,unsigned long *fatalLength) {
    unsigned before=*flags;
    int result=dvm_original_signature(vp,label,cpu,blob,flags,signer,options,platform,fatal,fatalLength);
    void *proc=dvm_current_proc();
    if(entitled(proc)&&(dvm_csflags(proc)&0x10000000))
        dvm_printf("DVM_DEV_LOADER signature pid=%d result=%d flags_before=%x flags_after=%x signer=%x fatal_length=%lu\n",dvm_pid(),result,before,*flags,signer?*signer:0,fatalLength?*fatalLength:0);
    return result;
}
#endif
#endif
extern "C" int dvm_development_policy(void *proc) {
    return entitled(proc)?0:dvm_original_policy(proc);
}
extern "C" int dvm_development_monitor(void *pmap) {
    int result=dvm_original_monitor(pmap);
    // The stock caller holds p_mlock here. Do not resolve entitlements (which
    // may take that lock) inside this observational monitor wrapper.
    dvm_printf("DVM_DEV_LOADER monitor pid=%d result=0x%x\n",dvm_pid(),result);
    return result;
}
extern "C" IOReturn dvm_development_uc(IOService *service,task_t task,void *security,
        UInt32 type,OSDictionary *properties,IOUserClient **result) {
    if(type!=0x44564d4c)return dvm_transport_uc(service,task,security,type,properties,result);
    *result=nullptr;
    const char *name=service?service->getName():nullptr,*expected="dvm-transport";
    if(!name)return kIOReturnNotPermitted;
    for(unsigned i=0;i<sizeof("dvm-transport");i++)if(name[i]!=expected[i])return kIOReturnNotPermitted;
    void *proc=dvm_current_proc();
    // Exact proc_task inline layout: cs_allow_invalid +0x60/+0x64/+0x68.
    // Reject a user-supplied task port other than the caller's embedded task.
    if(!entitled(proc)||!(*(const unsigned char *)((char *)proc+0x448)&2)||
       (char *)task!=(char *)proc+0x768)return kIOReturnNotPermitted;
    IOReturn kr=dvm_transport_uc(service,task,security,0x44564d54,properties,result);
    if(kr||!*result)return kr?kr:kIOReturnNoMemory;
    if(!(*result)->setProperty("IOUserClientEntitlements","org.darwin-vm.development-loader"))kr=kIOReturnNoMemory;
    if(!kr){
        bool taskAllowed=false;
        int entitlementResult=dvm_entitled(proc,"get-task-allow",&taskAllowed);
        dvm_printf("DVM_DEV_LOADER prerequisites pid=%d developer_mode=%d task_allow_result=0x%x task_allow=%d\n",dvm_pid(),dvm_developer_mode(),entitlementResult,taskAllowed);
        auto before=dvm_csflags(proc);int transitioned=dvm_cs_allow_invalid(proc);
        dvm_printf("DVM_DEV_LOADER flags pid=%d before=0x%llx transition=%d after=0x%llx\n",dvm_pid(),before,transitioned,dvm_csflags(proc));
        if(!transitioned)kr=kIOReturnNotPermitted;
    }
    if(kr){(*result)->terminate();(*result)->release();*result=nullptr;}
    return kr;
}
