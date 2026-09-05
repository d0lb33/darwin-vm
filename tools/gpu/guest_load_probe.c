#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

typedef void *Obj;
typedef void *Sel;
static Sel (*selector)(const char *);
static Obj (*named)(const char *);
static void *send;
static void *library(const char *path) {
    fprintf(stderr, "GPU_LOAD_BEGIN path=%s\n", path);
    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h) { fprintf(stderr, "GPU_LOAD_ERROR path=%s error=%s\n", path, dlerror()); exit(10); }
    fprintf(stderr, "GPU_LOAD_OK path=%s\n", path);
    return h;
}
static void *symbol(void *h, const char *name) {
    void *p = dlsym(h, name);
    if (!p) { fprintf(stderr, "GPU_LOAD_ERROR symbol=%s error=%s\n", name, dlerror()); exit(11); }
    return p;
}
static Obj call0(Obj obj, const char *sel) {
    return ((Obj (*)(Obj, Sel))send)(obj, selector(sel));
}
int main(void) {
    int console = open("/dev/console", O_WRONLY | O_NOCTTY);
    if (console < 0 || dup2(console, STDERR_FILENO) < 0) return 2;
    if (console != STDERR_FILENO) close(console);
    setvbuf(stderr, NULL, _IONBF, 0);
    fprintf(stderr, "GPU_LOAD_START version=2 pid=%d\n", getpid());
    void *objc = library("/usr/lib/libobjc.A.dylib");
    selector = symbol(objc, "sel_registerName"); named = symbol(objc, "objc_getClass");
    send = symbol(objc, "objc_msgSend");
    void *(*push)(void) = symbol(objc, "objc_autoreleasePoolPush");
    void (*pop)(void *) = symbol(objc, "objc_autoreleasePoolPop");
    void *pool = push();
    library("/System/Library/Frameworks/Foundation.framework/Foundation");
    library("/System/Library/Frameworks/Metal.framework/Metal");
    /* System enumeration is an independent contract. The version-1 control
     * spent substantial time initializing that path before testing our bundle.
     * No MTLCreateSystemDefaultDevice or MTLAddDevice call is needed here. */
    Obj path = ((Obj (*)(Obj, Sel, const char *))send)(named("NSString"),
        selector("stringWithUTF8String:"), "/usr/local/libexec/DVMProxy.bundle");
    Obj bundle = ((Obj (*)(Obj, Sel, Obj))send)(named("NSBundle"), selector("bundleWithPath:"), path);
    Obj error = NULL;
    unsigned char loaded = bundle ? ((unsigned char (*)(Obj, Sel, Obj *))send)(bundle,
        selector("loadAndReturnError:"), &error) : 0;
    fprintf(stderr, "GPU_LOAD_BUNDLE loaded=%u error=%s\n", loaded,
        error ? (const char *)call0(call0(error, "description"), "UTF8String") : "none");
    if (!loaded) return 12;
    void *plugin = library("/usr/local/libexec/DVMProxy.bundle/DVMProxy");
    Obj (*factory)(void) = symbol(plugin, "DVMCreateProbeDevice");
    Obj device = factory();
    fprintf(stderr, "GPU_LOAD_DEVICE device=%p\n", device);
    if (!device) return 13;
    fprintf(stderr, "GPU_LOAD_NAME value=%s\n", (const char *)call0(call0(device, "name"), "UTF8String"));
    call0(device, "release");
    fprintf(stderr, "GPU_LOAD_DEVICE_RELEASED\n");
    pop(pool);
    /* Keep the bundle loaded until process exit, respecting class lifetime. */
    fprintf(stderr, "GPU_LOAD_COMPLETE result=pass scope=bundle-and-local-device-only\n");
    return 0;
}
