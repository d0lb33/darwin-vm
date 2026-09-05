/* Native development-image input daemon. No fixed guest addresses or LLDB.
 *
 * 24A5430a Recap evidence: RCPEventDeliveryServicePool deliveryServiceFor-
 * SenderProperties: 0x29b1fc1e8; RCPVirtualHIDService postHIDEvent:
 * 0x29b1fff38; touchscreen properties with display UUID 0x29b208234.
 * Keep that virtual service alive instead of creating a player for each tap.
 * The digitizer constructors are exported by the device IOKit framework.
 * See docs/re/native-input.md for ABI sources and runtime status.
 */
#include <dlfcn.h>
#include <dispatch/dispatch.h>
#include <errno.h>
#include <inttypes.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

typedef void *Obj;
typedef void *Sel;
static Sel (*selector)(const char *);
static Obj (*class_named)(const char *);
static void *send_message;
static void (*release_cf)(const void *);
static void *(*pool_push)(void);
static void (*pool_pop)(void *);
static Obj (*digitizer)(const void *, uint64_t, uint32_t, uint32_t,
    uint32_t, uint32_t, uint32_t, double, double, double, double, double,
    int, int, uint32_t);
static Obj (*finger)(const void *, uint64_t, uint32_t, uint32_t, uint32_t,
    double, double, double, double, double, int, int, uint32_t);
static Obj (*keyboard)(const void *, uint64_t, uint32_t, uint32_t, int, uint32_t);
static void (*set_integer)(Obj, uint32_t, intptr_t);
static void (*append_event)(Obj, Obj, uint32_t);
static Obj service_pool, touch_service, button_service;

static Obj msg0(Obj object, const char *name) {
    return ((Obj (*)(Obj, Sel))send_message)(object, selector(name));
}
static Obj msg1(Obj object, const char *name, Obj arg) {
    return ((Obj (*)(Obj, Sel, Obj))send_message)(object, selector(name), arg);
}
static void *symbol(void *handle, const char *name) {
    void *result = dlsym(handle, name);
    if (!result) { fprintf(stderr, "DVM_INPUT_ERROR symbol=%s\n", name); exit(1); }
    return result;
}
static void *framework(const char *path) {
    void *handle = dlopen(path, RTLD_NOW | RTLD_GLOBAL);
    if (!handle) { fprintf(stderr, "DVM_INPUT_ERROR dlopen=%s error=%s\n", path, dlerror()); exit(1); }
    return handle;
}
static void initialize(void) {
    void *objc = framework("/usr/lib/libobjc.A.dylib");
    framework("/System/Library/Frameworks/Foundation.framework/Foundation");
    void *cf = framework("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation");
    void *iokit = framework("/System/Library/Frameworks/IOKit.framework/IOKit");
    void *bks = framework("/System/Library/PrivateFrameworks/BackBoardServices.framework/BackBoardServices");
    framework("/System/Library/PrivateFrameworks/Recap.framework/Recap");
    selector = symbol(objc, "sel_registerName");
    class_named = symbol(objc, "objc_getClass");
    send_message = symbol(objc, "objc_msgSend");
    pool_push = symbol(objc, "objc_autoreleasePoolPush");
    pool_pop = symbol(objc, "objc_autoreleasePoolPop");
    release_cf = symbol(cf, "CFRelease");
    digitizer = symbol(iokit, "IOHIDEventCreateDigitizerEvent");
    finger = symbol(iokit, "IOHIDEventCreateDigitizerFingerEvent");
    keyboard = symbol(iokit, "IOHIDEventCreateKeyboardEvent");
    set_integer = symbol(iokit, "IOHIDEventSetIntegerValue");
    append_event = symbol(iokit, "IOHIDEventAppendEvent");
    Obj uuid = *(Obj *)symbol(bks, "BKSDisplayUUIDMainKey");
    Obj properties_class = class_named("RCPEventSenderProperties");
    Obj pool_class = class_named("RCPEventDeliveryServicePool");
    if (!uuid || !properties_class || !pool_class) {
        fprintf(stderr, "DVM_INPUT_ERROR missing-native-class-or-display\n"); exit(1);
    }
    service_pool = msg0(msg0(pool_class, "alloc"), "init");
    Obj properties = msg1(properties_class, "touchScreenDigitizerSenderForDisplayUUID:", uuid);
    touch_service = msg0(msg1(service_pool, "deliveryServiceForSenderProperties:", properties), "retain");
    button_service = msg0(msg1(service_pool, "deliveryServiceForSenderProperties:",
                              msg0(properties_class, "phoneButtonSender")), "retain");
    if (!touch_service || !button_service) {
        fprintf(stderr, "DVM_INPUT_ERROR native-service-creation\n"); exit(1);
    }
    fprintf(stderr, "DVM_INPUT_READY protocol=1 touch=%p buttons=%p\n", touch_service, button_service);
}

struct packet { uint64_t sequence; unsigned down, x, y; char kind; };
static bool parse(const char *line, struct packet *packet) {
    int consumed = 0;
    if (sscanf(line, "DVMINPUT1 %" SCNu64 " %c %u %u %u %n", &packet->sequence,
        &packet->kind, &packet->down, &packet->x, &packet->y, &consumed) != 5 || !consumed) return false;
    if (line[consumed] || packet->down > 1) return false;
    return (packet->kind == 'T' && packet->x <= 32767 && packet->y <= 32767) ||
           (packet->kind == 'H' && packet->x == 0 && packet->y == 0);
}

static bool post_touch(const struct packet *packet, bool previous_down) {
    uint64_t timestamp = mach_absolute_time();
    /* IOHIDEventTypes: range=1, touch=2, position=4, identity=32; hand=1.
     * Parent/child coordinates are normalized, as native digitizer reports.
     */
    uint32_t mask = previous_down == !!packet->down ? 4 : 1 | 2 | 4 | 32;
    double x = packet->x / 32767.0, y = packet->y / 32767.0;
    Obj hand = digitizer(NULL, timestamp, 1, 0, 0, mask, 0,
                         x, y, 0, 0, 0, packet->down, packet->down, 0);
    Obj child = finger(NULL, timestamp, 1, 1, mask, x, y, 0, 0, 0,
                       packet->down, packet->down, 0);
    if (!hand || !child) {
        if (hand) release_cf(hand);
        if (child) release_cf(child);
        return false;
    }
    set_integer(hand, (11u << 16) | 25u, 1); /* display integrated */
    append_event(hand, child, 0);
    ((void (*)(Obj, Sel, Obj))send_message)(touch_service, selector("postHIDEvent:"), hand);
    release_cf(child);
    release_cf(hand);
    return true;
}
static bool post_home(const struct packet *packet) {
    Obj event = keyboard(NULL, mach_absolute_time(), 0x0c, 0x40, packet->down, 0);
    if (!event) return false;
    ((void (*)(Obj, Sel, Obj))send_message)(button_service, selector("postHIDEvent:"), event);
    release_cf(event);
    return true;
}

static bool validate_only;
static void *input_loop(void *unused) {
    (void)unused;
    if (!validate_only) initialize();
    char line[160];
    bool down = false;
    uint64_t last = 0;
    while (fgets(line, sizeof(line), stdin)) {
        /* A long or incomplete record is discarded through its newline. */
        if (!strchr(line, '\n')) {
            int c; while ((c = getchar()) != '\n' && c != EOF) {}
            fprintf(stderr, "DVM_INPUT_REJECT truncated\n"); continue;
        }
        struct packet p;
        if (!parse(line, &p) || p.sequence <= last) {
            fprintf(stderr, "DVM_INPUT_REJECT malformed-or-old\n"); continue;
        }
        void *pool = validate_only ? NULL : pool_push();
        bool ok = validate_only || (p.kind == 'H' ? post_home(&p) : post_touch(&p, down));
        if (p.kind == 'T' && ok) down = p.down;
        last = p.sequence;
        if (pool) pool_pop(pool);
        fprintf(stderr, "DVM_INPUT_ACK %" PRIu64 " %d\n", p.sequence, ok);
    }
    /* Ensure a disconnected writer cannot leave a finger held down. */
    if (down && !validate_only) {
        struct packet p = {.kind = 'T'};
        post_touch(&p, true);
    }
    exit(0);
}
int main(int argc, char **argv) {
    validate_only = argc == 2 && !strcmp(argv[1], "--validate");
    if (argc != 1 && !validate_only) return 2;
    setvbuf(stderr, NULL, _IONBF, 0);
    if (validate_only) { input_loop(NULL); return 0; }
    struct termios attributes;
    if (tcgetattr(STDIN_FILENO, &attributes) == 0) {
        cfmakeraw(&attributes);
        attributes.c_cc[VMIN] = 1;
        attributes.c_cc[VTIME] = 0;
        if (tcsetattr(STDIN_FILENO, TCSANOW, &attributes)) { perror("tcsetattr"); return 1; }
    }
    pthread_t thread;
    if (pthread_create(&thread, NULL, input_loop, NULL)) return 1;
    dispatch_main();
}
