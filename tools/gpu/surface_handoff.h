// Development supervisor IPC: inherit one IOSurface send right through an
// otherwise unused registered Mach-port slot. Never replace bootstrap ports or
// publish a global IOSurface ID. Restore the parent's registered ports after
// posix_spawn; retain the backing mapping until every child has exited.
#pragma once
#import <IOSurface/IOSurface.h>
#include <mach/mach.h>
#include <dlfcn.h>
typedef struct {
    __typeof__(&mach_ports_lookup) lookup;
    __typeof__(&mach_ports_register) registerPorts;
    __typeof__(&mach_port_deallocate) drop;
    __typeof__(&vm_deallocate) freeArray;
    __typeof__(&IOSurfaceCreateMachPort) create;
    __typeof__(&IOSurfaceLookupFromMachPort) surface;
} DVMSurfacePortAPI;
static inline BOOL DVMSurfacePortLoad(DVMSurfacePortAPI *a) {
#define DVM_PORT_LOAD(field,name) a->field=dlsym(RTLD_DEFAULT,#name);if(!a->field)return NO
    DVM_PORT_LOAD(lookup,mach_ports_lookup);DVM_PORT_LOAD(registerPorts,mach_ports_register);
    DVM_PORT_LOAD(drop,mach_port_deallocate);DVM_PORT_LOAD(freeArray,vm_deallocate);
    DVM_PORT_LOAD(create,IOSurfaceCreateMachPort);DVM_PORT_LOAD(surface,IOSurfaceLookupFromMachPort);
#undef DVM_PORT_LOAD
    return YES;
}
static inline void DVMSurfacePortsRelease(DVMSurfacePortAPI *a,mach_port_array_t ports,mach_msg_type_number_t n) {
    for(mach_msg_type_number_t i=0;i<n;i++)if(MACH_PORT_VALID(ports[i]))a->drop(mach_task_self(),ports[i]);
    if(ports)a->freeArray(mach_task_self(),(vm_address_t)ports,n*sizeof(*ports));
}
typedef struct {
    mach_port_array_t original;
    mach_msg_type_number_t count;
    mach_port_t surface;
} DVMSurfacePortScope;
static inline kern_return_t DVMSurfacePortsBegin(DVMSurfacePortAPI *a,IOSurfaceRef surface,DVMSurfacePortScope *s) {
    *s=(DVMSurfacePortScope){0};
    kern_return_t kr=a->lookup(mach_task_self(),&s->original,&s->count);
    if(kr)return kr;
    if(s->count!=3||s->original[2]!=MACH_PORT_NULL){
        DVMSurfacePortsRelease(a,s->original,s->count);*s=(DVMSurfacePortScope){0};return KERN_INVALID_ARGUMENT;
    }
    s->surface=a->create(surface);
    if(!MACH_PORT_VALID(s->surface))kr=KERN_FAILURE;
    else {mach_port_t ports[]={s->original[0],s->original[1],s->surface};kr=a->registerPorts(mach_task_self(),ports,3);}
    if(kr){if(MACH_PORT_VALID(s->surface))a->drop(mach_task_self(),s->surface);DVMSurfacePortsRelease(a,s->original,s->count);*s=(DVMSurfacePortScope){0};}
    return kr;
}
static inline kern_return_t DVMSurfacePortsEnd(DVMSurfacePortAPI *a,DVMSurfacePortScope *s) {
    kern_return_t kr=a->registerPorts(mach_task_self(),s->original,s->count);
    a->drop(mach_task_self(),s->surface);DVMSurfacePortsRelease(a,s->original,s->count);*s=(DVMSurfacePortScope){0};return kr;
}
static inline IOSurfaceRef DVMSurfacePortsReceive(DVMSurfacePortAPI *a,kern_return_t *error) {
    mach_port_array_t ports=NULL;mach_msg_type_number_t n=0;
    *error=a->lookup(mach_task_self(),&ports,&n);if(*error)return NULL;
    IOSurfaceRef surface=n==3&&MACH_PORT_VALID(ports[2])?a->surface(ports[2]):NULL;
    // Do not propagate this capability to a grandchild by accident.
    if(n==3){mach_port_t clean[]={ports[0],ports[1],MACH_PORT_NULL};*error=a->registerPorts(mach_task_self(),clean,3);}
    else *error=KERN_INVALID_ARGUMENT;
    DVMSurfacePortsRelease(a,ports,n);
    if(!surface&&!*error)*error=KERN_FAILURE;
    if(*error&&surface){CFRelease(surface);return NULL;}return surface;
}
