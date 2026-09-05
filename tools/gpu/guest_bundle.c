/* Process-local load/initializer probe. No registry publication or MTLAddDevice.
 * Runtime calls keep the executable linked only to libSystem, as the existing
 * guest input helper does. All classes/frameworks come from the exact guest.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

typedef void *Obj;
typedef void *Sel;
static Sel (*selector)(const char *);
static Obj (*named)(const char *);
static void *send;
static void *send_super;
static Obj proxy_class;
static Obj call0(Obj obj, const char *sel) {
    return ((Obj (*)(Obj, Sel))send)(obj, selector(sel));
}
static void *sym(void *handle, const char *name) {
    void *p = dlsym(handle, name);
    if (!p) { fprintf(stderr, "GPU_BUNDLE_ERROR symbol=%s error=%s\n", name, dlerror()); exit(20); }
    return p;
}
static Obj device_init(Obj self, Sel cmd, uint32_t port) {
    (void)cmd;
    fprintf(stderr, "GPU_BUNDLE_INIT_ENTER port=%u\n", port);
    /* objc_msgSendSuper2 starts lookup above the supplied current class. */
    struct { Obj receiver; Obj current_class; } super = {self, proxy_class};
    Obj result = ((Obj (*)(void *, Sel))send_super)(&super, selector("init"));
    fprintf(stderr, "GPU_BUNDLE_INIT_RETURN device=%p\n", result);
    return result;
}
static Obj device_name(Obj self, Sel cmd) {
    (void)self; (void)cmd;
    return ((Obj (*)(Obj, Sel, const char *))send)(named("NSString"),
        selector("stringWithUTF8String:"), "DVM process-local feasibility device");
}
__attribute__((visibility("default"))) Obj DVMCreateProbeDevice(void) {
    void *objc = dlopen("/usr/lib/libobjc.A.dylib", RTLD_NOW | RTLD_LOCAL);
    if (!objc) return NULL;
    selector = sym(objc, "sel_registerName"); named = sym(objc, "objc_getClass");
    send = sym(objc, "objc_msgSend"); send_super = sym(objc, "objc_msgSendSuper2");
    Obj base = named("_MTLDevice");
    fprintf(stderr, "GPU_BUNDLE_BASE class=%p\n", base);
    if (!base) return NULL;
    Obj (*allocate)(Obj, const char *, size_t) = sym(objc, "objc_allocateClassPair");
    unsigned char (*add)(Obj, Sel, void *, const char *) = sym(objc, "class_addMethod");
    void (*register_class)(Obj) = sym(objc, "objc_registerClassPair");
    proxy_class = allocate(base, "DVMFeasibilityDevice", 0);
    if (!proxy_class || !add(proxy_class, selector("initWithAcceleratorPort:"),
        device_init, "@24@0:8I16") || !add(proxy_class, selector("name"), device_name, "@16@0:8"))
        return NULL;
    register_class(proxy_class);
    fprintf(stderr, "GPU_BUNDLE_CLASS_REGISTERED class=%p scope=process-local\n", proxy_class);
    return ((Obj (*)(Obj, Sel, uint32_t))send)(call0(proxy_class, "alloc"),
        selector("initWithAcceleratorPort:"), 0);
}
