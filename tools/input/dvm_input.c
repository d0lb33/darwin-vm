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
#include <fcntl.h>
#include <inttypes.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
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
static Obj touch_properties, display_uuid;
static void *(*instance_variable)(Obj, const char *);
static Obj (*get_ivar)(Obj, void *);

static Obj msg0(Obj object, const char *name) {
    return ((Obj (*)(Obj, Sel))send_message)(object, selector(name));
}
static Obj msg1(Obj object, const char *name, Obj arg) {
    return ((Obj (*)(Obj, Sel, Obj))send_message)(object, selector(name), arg);
}
static Obj event_service(Obj service) {
    return msg0(service, "eventService");
}
static bool service_ready(Obj service) {
    return service && msg0(event_service(service), "serviceClient");
}
static bool post_event(Obj service, Obj event) {
    /* HIDVirtualEventService dispatchEvent: returns BOOL. Recap's wrapper
     * returns void and can silently call a service with no registered client
     * (24A5430a 0x2678a7204..0x2678a7238, NATIVE_INPUT_BOOT2). Report the
     * actual submission result, not merely successful event allocation.
     */
    return ((bool (*)(Obj, Sel, Obj))send_message)(
        event_service(service), selector("dispatchEvent:"), event);
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
    instance_variable = symbol(objc, "class_getInstanceVariable");
    get_ivar = symbol(objc, "object_getIvar");
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
    display_uuid = uuid;
    touch_properties = msg0(properties, "retain");
    touch_service = msg0(msg1(service_pool, "deliveryServiceForSenderProperties:", properties), "retain");
    button_service = msg0(msg1(service_pool, "deliveryServiceForSenderProperties:",
                              msg0(properties_class, "phoneButtonSender")), "retain");
    if (!service_ready(touch_service) || !service_ready(button_service)) {
        fprintf(stderr, "DVM_INPUT_ERROR native-service-registration touch=%d buttons=%d\n",
                service_ready(touch_service), service_ready(button_service)); exit(1);
    }
    fprintf(stderr, "DVM_INPUT_READY protocol=1 touch=%p buttons=%p\n", touch_service, button_service);
}

struct packet { uint64_t sequence; unsigned down, x, y; char kind; };
static bool parse(const char *line, struct packet *packet) {
    int consumed = 0;
    if (sscanf(line, "DVMINPUT1 %" SCNu64 " %c %u %u %u %n", &packet->sequence,
        &packet->kind, &packet->down, &packet->x, &packet->y, &consumed) != 5 || !consumed) return false;
    if (line[consumed] || packet->down > 1) return false;
    return ((packet->kind == 'T' || packet->kind == 'R') &&
            packet->x <= 32767 && packet->y <= 32767) ||
           (packet->kind == 'H' && packet->x == 0 && packet->y == 0) ||
           (packet->kind == 'S' && !packet->down && !packet->x && !packet->y);
}

static bool post_touch(const struct packet *packet, bool previous_down) {
    uint64_t timestamp = mach_absolute_time();
    /* Match 24A5430a RCPSyntheticEventStream: default transducer type 2
     * (init at 0x29b1fcb74), constructor options 0x40 for parent and child
     * (0x29b213a50, 0x29b213ca8). Range=1, touch=2, position=4, identity=32.
     * Parent/child coordinates are normalized, as native digitizer reports.
     */
    uint32_t mask = previous_down == !!packet->down ? 4 : 1 | 2 | 4 | 32;
    double x = packet->x / 32767.0, y = packet->y / 32767.0;
    Obj hand = digitizer(NULL, timestamp, 2, 0, 0, mask, 0,
                         x, y, 0, 0, 0, packet->down, packet->down, 0x40);
    Obj child = finger(NULL, timestamp, 1, 1, mask, x, y, 0, 0, 0,
                       packet->down, packet->down, 0x40);
    if (!hand || !child) {
        if (hand) release_cf(hand);
        if (child) release_cf(child);
        return false;
    }
    set_integer(hand, (11u << 16) | 25u, 1); /* display integrated */
    set_integer(hand, 4, 1); /* built in: native Recap 0x29b213b58 */
    append_event(hand, child, 0);
    bool submitted = post_event(touch_service, hand);
    release_cf(child);
    release_cf(hand);
    return submitted;
}
static bool post_home(const struct packet *packet) {
    /* Recap's native button constructor, 24A5430a 0x29b213868..0x29b2138d0. */
    Obj event = keyboard(NULL, mach_absolute_time(), 0x0c, 0x40, packet->down, 0x40);
    if (!event) return false;
    set_integer(event, 4, 1);
    bool submitted = post_event(button_service, event);
    release_cf(event);
    return submitted;
}

/* Diagnostic alternative matching touch_bridge.py's verified Recap calls.
 * R packets collect a gesture, then queue native playback on release. This
 * is deliberately separate from T's immediate HID submission; an R ACK
 * means buffering/queueing, not UI delivery. No debugger or fixed addresses.
 */
struct point { double x, y; };
static Obj recap_stream;
static unsigned recap_points;
static bool post_recap(const struct packet *p) {
    Obj cls = class_named("RCPSyntheticEventStream");
    if (!recap_stream) {
        if (!p->down) return true;
        recap_stream = msg0(msg0(cls, "alloc"), "init");
        if (!recap_stream) return false;
        msg1(recap_stream, "setSenderProperties:", touch_properties);
        recap_points = 0;
    }
    struct point size = ((struct point (*)(Obj, Sel))send_message)(
        recap_stream, selector("screenSize"));
    if (!(size.x > 0 && size.x <= 16384 && size.y > 0 && size.y <= 16384)) return false;
    struct point point = {p->x * size.x / 32767., p->y * size.y / 32767.};
    if (!recap_points) {
        ((void (*)(Obj, Sel, struct point))send_message)(recap_stream,
            selector("touchDown:"), point);
    } else if (p->down && recap_points < 512) {
        ((void (*)(Obj, Sel, struct point, double))send_message)(recap_stream,
            selector("moveToPoint:duration:"), point, .02);
    }
    recap_points++;
    if (p->down) return true;
    ((void (*)(Obj, Sel, struct point))send_message)(recap_stream,
        selector("liftUp:"), point);
    msg0(recap_stream, "_finalizeProcessingEventBuffer");
    void *ivar = instance_variable(cls, "_processingEventBuffer");
    if (!ivar) return false;
    msg1(recap_stream, "setEvents:", get_ivar(recap_stream, ivar));
    Obj options = msg0(msg0(class_named("RCPPlayerPlaybackOptions"), "alloc"), "init");
    msg1(options, "setDisplayUUIDOverride:", display_uuid);
    ((void (*)(Obj, Sel, Obj, Obj, Obj))send_message)(class_named("RCPInlinePlayer"),
        selector("playEventStream:options:completion:"), recap_stream, options, NULL);
    msg0(options, "release");
    msg0(recap_stream, "release");
    recap_stream = NULL;
    return true;
}

static bool validate_only;
static void *input_loop(void *unused) {
    (void)unused;
    if (!validate_only) {
        fprintf(stderr, "DVM_INPUT_INITIALIZING pid=%d\n", getpid());
        initialize();
    }
    char line[160];
    bool down = false;
    uint64_t last = 0;
    while (fgets(line, sizeof(line), stdin)) {
        if (!strcmp(line, "\n")) continue; /* transport resynchronization */
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
        bool ok = validate_only || p.kind == 'S' || (p.kind == 'H' ? post_home(&p) :
                  p.kind == 'R' ? post_recap(&p) : post_touch(&p, down));
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
    fprintf(stderr, "DVM_INPUT_EOF error=%d errno=%d\n", ferror(stdin), errno);
    exit(0);
}
int main(int argc, char **argv) {
    validate_only = argc == 2 && !strcmp(argv[1], "--validate");
    if (argc != 1 && !validate_only) return 2;
    setvbuf(stderr, NULL, _IONBF, 0);
    if (validate_only) { input_loop(NULL); return 0; }
    /* Two readers of /dev/console split packets byte by byte. Keep this
     * descriptor open for the process lifetime; the kernel releases the lock
     * on exit, including SIGKILL. Do this before changing terminal settings.
     */
    int lock = open("/var/run/dvm-input.lock", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
        perror("dvm-input singleton lock"); return 1;
    }
    fprintf(stderr, "DVM_INPUT_START version=4 pid=%d\n", getpid());
    /* Direct children of launchd can inherit ignored/blocked signals. */
    signal(SIGTERM, SIG_DFL);
    signal(SIGINT, SIG_DFL);
    sigset_t signals;
    sigemptyset(&signals);
    if (pthread_sigmask(SIG_SETMASK, &signals, NULL)) return 1;
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
