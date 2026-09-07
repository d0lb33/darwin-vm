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
