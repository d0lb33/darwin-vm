/* dvm-hid: IOKit-only native input helper for the darwin-vm iOS guest.
 *
 * Protocol v2 ("DVMI2") is defined in qemu-sptm/include/xnu/darwin_input.h.
 * QEMU injects records into the console UART; this daemon reads them from
 * /dev/console and answers on the same console so QEMU's TX observer can
 * account for every record without a host relay.
 *
 * It links only libSystem and dlopens IOKit + CoreFoundation.  No Recap,
 * BackBoardServices or UIKit: initialisation cannot stall on display
 * services, and the reader thread answers pings while HID is still coming up.
 *
 * HID path (24A5430a shared cache, unslid addresses):
 *   IOHIDEventSystemClientCreateWithType      0x18efe7240 (type 4, Simple)
 *   IOHIDVirtualServiceClientCreateWithCallbacks 0x18f00e8f8
 *   IOHIDVirtualServiceClientDispatchEvent    0x18f00afa0
 * These are exactly the calls HID.framework's HIDVirtualEventService makes
 * (-[HIDVirtualEventService activate] 0x2678a8ee4, dispatchEvent: 0x2678a71d0;
 * open source: IOHIDFamily/HID/HIDVirtualEventService.m), which Recap's
 * RCPVirtualHIDService wrapped for the previous helper.  The callbacks struct
 * is IOHIDVirtualServiceClientCallbacksV2 {version=2, notify, setProperty,
 * copyProperty, copyEvent, setOutputEvent, copyMatchingEvent}.
 *
 * Service properties mirror what Recap's
 * +[RCPEventSenderProperties touchScreenDigitizerSenderForDisplayUUID:]
 * (0x29b208234) and supplyMissingStandardProperties: (0x29b1ffc30) publish:
 * displayUUID "<main>" (BKSDisplayUUIDMainKey, BackBoardServices cstring
 * 0x18a1813ad), Built-In false (kCFBooleanFalse 0x1e8ee0108), Authenticated
 * true, Transport "Recap".  Event construction is the one proven by helper v6
 * (docs/re/native-input.md): digitizer parent + finger child, options 0x40,
 * display-integrated field (11<<16)|25 and built-in field 4.
 */
#include <dlfcn.h>
#include <dispatch/dispatch.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <mach/mach_time.h>
#include <mach/mach.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/resource.h>
#include <pthread/qos.h>
#include <sys/time.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define VERSION 14
#define RECOVERY_ATTEMPTS 3
#define RECOVERY_TIMEOUT_SECONDS 20

typedef const void *CFRef;
typedef void *Ref;
static void *(*cf_string)(CFRef, const char *, uint32_t);
static void *(*cf_number)(CFRef, int, const void *);
static void *(*cf_dict)(CFRef, const void **, const void **, long, const void *, const void *);
static void *(*cf_array)(CFRef, const void **, long, const void *);
static const void *(*cf_dict_get)(CFRef, const void *);
static void (*cf_release)(CFRef);
static CFRef (*cf_retain)(CFRef);
static const void *kBoolTrue, *kBoolFalse, *kDictKeyCB, *kDictValCB, *kArrayCB;

static Ref (*client_create)(CFRef, int, CFRef);
static void (*client_set_matching)(Ref, CFRef);
static void (*client_set_queue)(Ref, dispatch_queue_t);
static void (*client_activate)(Ref);
static void (*client_cancel)(Ref);
static Ref (*vsc_create)(Ref, CFRef, void *, void *, void *);
static int (*vsc_dispatch)(Ref, Ref);
static CFRef (*svc_registry_id)(Ref);
static Ref (*digitizer)(CFRef, uint64_t, uint32_t, uint32_t, uint32_t, uint32_t,
    uint32_t, double, double, double, double, double, int, int, uint32_t);
static Ref (*finger)(CFRef, uint64_t, uint32_t, uint32_t, uint32_t, double,
    double, double, double, double, int, int, uint32_t);
static Ref (*keyboard)(CFRef, uint64_t, uint32_t, uint32_t, int, uint32_t);
static void (*set_integer)(Ref, uint32_t, intptr_t);
static void (*append_event)(Ref, Ref, uint32_t);

struct vservice {
    const char *name;
    Ref props, client, service;
    uint64_t registry_id;
};
static struct vservice touch_service = {.name = "touch"};
static struct vservice button_service = {.name = "buttons"};

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t op_cond = PTHREAD_COND_INITIALIZER;
static volatile char state = 'I';   /* I initialising, R ready, L lost */
static volatile bool need_recovery;  /* services must be re-created */
static bool initializing = true;     /* do not recover half-created services */
static unsigned recoveries;
static bool recovery_in_progress;
static unsigned recovery_generation;
static uint32_t epoch;
static bool validate_only;

struct record { unsigned epoch, seq, a, b; int c; char kind; long long host_ms; };
static uint64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static void say(const char *fmt, ...) {
    /* One write per line keeps QEMU's line parser robust against interleaved
     * kernel console output. */
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n > 0) {
        if ((size_t)n >= sizeof(buf)) n = sizeof(buf) - 1;
        write(STDERR_FILENO, buf, n);
    }
}
static void announce(void) {
    say("DVMI2R %c %d %u\n", state, getpid(), epoch);
}

/* ---------------- HID initialisation (worker thread) ---------------- */

static void *sym(void *h, const char *name) {
    void *p = dlsym(h, name);
    if (!p) say("DVM_HID_ERROR symbol=%s\n", name);
    return p;
}
static void *cfstr(const char *s) { return cf_string(NULL, s, 0x08000100); }
static void *cfnum(int v) { return cf_number(NULL, 9 /* kCFNumberSInt32Type */, &v); }
static void *make_dict(const void **keys, const void **values, long n) {
    return cf_dict(NULL, keys, values, n, kDictKeyCB, kDictValCB);
}

/* Virtual service callbacks: IOHIDFamily/HID/HIDVirtualEventService.m. */
static void cb_notify(void *target, void *ctx, Ref svc, uint32_t type, CFRef prop) {
    struct vservice *v = target;
    (void)ctx; (void)svc; (void)prop;
    /* HID.framework forwards only types 3..5 (thunk 0x2678a747c: sub #3,
     * cmp #2).  NATIVE_HID_FRESH1 observed 0, 2, 4 in that order while the
     * service was being created and then opened by backboardd, so 4 is
     * kIOHIDVirtualServiceOpenedByEventSystem; 3 and 5 are the unscheduled /
     * reset notifications that HID.framework reports as Terminated. */
    pthread_mutex_lock(&lock);
    /* Releasing a replaced client can generate its terminal notification on
     * the same serial queue.  It must not tear down the new client. */
    if (svc != v->service) {
        pthread_mutex_unlock(&lock);
        say("DVM_HID_NOTIFY_STALE service=%s type=%u\n", v->name, type);
        return;
    }
    if (type == 3 || type == 5) {
        state = 'L';
        announce();
        need_recovery = true;
        pthread_cond_signal(&op_cond);
    }
    pthread_mutex_unlock(&lock);
    say("DVM_HID_NOTIFY service=%s type=%u\n", v->name, type);
    if (type == 4) say("DVM_HID_OPENED service=%s\n", v->name);
}
static bool cb_set_property(void *target, void *ctx, Ref svc, CFRef key, CFRef value) {
    (void)target; (void)ctx; (void)svc; (void)key; (void)value;
    return true;
}
static CFRef cb_copy_property(void *target, void *ctx, Ref svc, CFRef key) {
    struct vservice *v = target;
    (void)ctx; (void)svc;
    CFRef value = cf_dict_get(v->props, key);
    return value ? cf_retain(value) : NULL;
}
static Ref cb_copy_event(void *target, void *ctx, Ref svc, uint32_t type, Ref matching, uint32_t options) {
    (void)target; (void)ctx; (void)svc; (void)type; (void)matching; (void)options;
    return NULL;
}
static int cb_set_output_event(void *target, void *ctx, Ref svc, Ref event) {
    (void)target; (void)ctx; (void)svc; (void)event;
    return 0;
}
static Ref cb_copy_matching_event(void *target, void *ctx, Ref svc, CFRef matching) {
    (void)target; (void)ctx; (void)svc; (void)matching;
    return NULL;
}
struct callbacks_v2 {
    uint64_t version;
    void *notify, *set_property, *copy_property, *copy_event, *set_output_event;
    void *copy_matching_event;
};
static struct callbacks_v2 callbacks = {2, cb_notify, cb_set_property, cb_copy_property,
                                        cb_copy_event, cb_set_output_event, cb_copy_matching_event};

static bool create_service(struct vservice *v, dispatch_queue_t queue) {
    v->client = client_create(NULL, 4 /* kIOHIDEventSystemClientTypeSimple */, NULL);
    if (!v->client) { say("DVM_HID_ERROR service=%s client-create\n", v->name); return false; }
    /* Match nothing; the client exists only to host the virtual service. */
    const void *mk[] = {cfstr("PrimaryUsagePage"), cfstr("PrimaryUsage")};
    const void *mv[] = {cfnum(-1), cfnum(-1)};
    Ref matching = make_dict(mk, mv, 2);
    client_set_matching(v->client, matching);
    cf_release(matching);
    for (unsigned i = 0; i < 2; i++) {
        cf_release(mk[i]);
        cf_release(mv[i]);
    }
    client_set_queue(v->client, queue);
    client_activate(v->client);
    Ref service = vsc_create(v->client, v->props, &callbacks, v, NULL);
    pthread_mutex_lock(&lock);
    v->service = service;
    pthread_mutex_unlock(&lock);
    if (!v->service) { say("DVM_HID_ERROR service=%s virtual-service-create\n", v->name); return false; }
    CFRef rid = svc_registry_id(v->service);
    long long id = 0;
    if (rid) {
        /* CFNumberGetValue via the number's kCFNumberSInt64Type (4). */
        static int (*get_value)(CFRef, int, void *);
        if (!get_value) get_value = dlsym(RTLD_DEFAULT, "CFNumberGetValue");
        if (get_value) get_value(rid, 4, &id);
    }
    v->registry_id = (uint64_t)id;
    say("DVM_HID_SERVICE name=%s client=%p service=%p registry_id=0x%llx\n",
        v->name, v->client, v->service, id);
    return true;
}

/* Apple IOHIDFamily's HIDVirtualEventService.m calls
 * IOHIDEventSystemClientCancel in -cancel (line 197), and its two Create
 * results are ARC-bridged as owning CF references (line 177; the client
 * wrapper releases its Create result in HIDEventSystemClient.m:51-55).
 * Follow that lifecycle here.  Clear the published pointers before cancel:
 * a terminal callback caused by teardown is stale, not a request to recover
 * the replacement service. */
static void destroy_service(struct vservice *v) {
    Ref service, client;
    pthread_mutex_lock(&lock);
    service = v->service;
    client = v->client;
    v->service = NULL;
    v->client = NULL;
    v->registry_id = 0;
    pthread_mutex_unlock(&lock);
    if (client) client_cancel(client);
    if (service) cf_release(service);
    if (client) cf_release(client);
}

static void destroy_services(void) {
    destroy_service(&touch_service);
    destroy_service(&button_service);
}

static bool initialize_hid(void) {
    void *cf = dlopen("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation", RTLD_NOW | RTLD_GLOBAL);
    void *iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit", RTLD_NOW | RTLD_GLOBAL);
    if (!cf || !iokit) { say("DVM_HID_ERROR dlopen cf=%p iokit=%p %s\n", cf, iokit, dlerror()); return false; }
    bool ok = true;
#define S(var, handle, name) do { var = sym(handle, name); ok = ok && var; } while (0)
    S(cf_string, cf, "CFStringCreateWithCString");
    S(cf_number, cf, "CFNumberCreate");
    S(cf_dict, cf, "CFDictionaryCreate");
    S(cf_array, cf, "CFArrayCreate");
    S(cf_dict_get, cf, "CFDictionaryGetValue");
    S(cf_release, cf, "CFRelease");
    S(cf_retain, cf, "CFRetain");
    S(client_create, iokit, "IOHIDEventSystemClientCreateWithType");
    S(client_set_matching, iokit, "IOHIDEventSystemClientSetMatching");
    S(client_set_queue, iokit, "IOHIDEventSystemClientSetDispatchQueue");
    S(client_activate, iokit, "IOHIDEventSystemClientActivate");
    S(client_cancel, iokit, "IOHIDEventSystemClientCancel");
    S(vsc_create, iokit, "IOHIDVirtualServiceClientCreateWithCallbacks");
    S(vsc_dispatch, iokit, "IOHIDVirtualServiceClientDispatchEvent");
    S(svc_registry_id, iokit, "IOHIDServiceClientGetRegistryID");
    S(digitizer, iokit, "IOHIDEventCreateDigitizerEvent");
    S(finger, iokit, "IOHIDEventCreateDigitizerFingerEvent");
    S(keyboard, iokit, "IOHIDEventCreateKeyboardEvent");
    S(set_integer, iokit, "IOHIDEventSetIntegerValue");
    S(append_event, iokit, "IOHIDEventAppendEvent");
#undef S
    void **p;
    if (!(p = sym(cf, "kCFBooleanTrue"))) ok = false; else kBoolTrue = *p;
    if (!(p = sym(cf, "kCFBooleanFalse"))) ok = false; else kBoolFalse = *p;
    if (!(p = sym(cf, "kCFTypeDictionaryKeyCallBacks"))) ok = false; else kDictKeyCB = p;
    if (!(p = sym(cf, "kCFTypeDictionaryValueCallBacks"))) ok = false; else kDictValCB = p;
    if (!(p = sym(cf, "kCFTypeArrayCallBacks"))) ok = false; else kArrayCB = p;
    if (!ok) return false;

    /* Touch screen digitizer bound to the main display. */
    {
        const void *pk[] = {cfstr("DeviceUsagePage"), cfstr("DeviceUsage")};
        const void *pv[] = {cfnum(0x0d), cfnum(0x04)};
        Ref pair = make_dict(pk, pv, 2);
        Ref pairs = cf_array(NULL, (const void *[]){pair}, 1, kArrayCB);
        const void *k[] = {cfstr("displayUUID"), cfstr("DisplayUUID"), cfstr("Built-In"),
                           cfstr("Authenticated"), cfstr("Transport"), cfstr("VendorID"),
                           cfstr("ProductID"), cfstr("PrimaryUsagePage"), cfstr("PrimaryUsage"),
                           cfstr("DeviceUsagePairs"), cfstr("DisplayIntegrated"),
                           cfstr("Product")};
        const void *v[] = {cfstr("<main>"), cfstr("<main>"), kBoolFalse, kBoolTrue,
                           cfstr("Recap"), cfnum(0x05ac), cfnum(0xd001), cfnum(0x0d), cfnum(0x04),
                           pairs, kBoolTrue, cfstr("dvm-hid touch")};
        touch_service.props = make_dict(k, v, 12);
    }
    /* Consumer-page buttons (Home = 0x0c/0x40, Power = 0x0c/0x30). */
    {
        const void *pk[] = {cfstr("DeviceUsagePage"), cfstr("DeviceUsage")};
        const void *pv[] = {cfnum(0x0c), cfnum(0x01)};
        Ref pair = make_dict(pk, pv, 2);
        Ref pairs = cf_array(NULL, (const void *[]){pair}, 1, kArrayCB);
        const void *k[] = {cfstr("Built-In"), cfstr("Authenticated"), cfstr("Transport"),
                           cfstr("VendorID"), cfstr("ProductID"), cfstr("PrimaryUsagePage"),
                           cfstr("PrimaryUsage"), cfstr("DeviceUsagePairs"), cfstr("Product")};
        const void *v[] = {kBoolTrue, kBoolTrue, cfstr("Recap"), cfnum(0x05ac), cfnum(0xd002),
                           cfnum(0x0c), cfnum(0x01), pairs, cfstr("built-in buttons")};
        button_service.props = make_dict(k, v, 9);
    }
    dispatch_queue_t queue = dispatch_queue_create("dvm-hid.services", DISPATCH_QUEUE_SERIAL);
    bool services_ok = create_service(&touch_service, queue) && create_service(&button_service, queue);
    /* IOHIDEventSystemClientSetDispatchQueue retains its queue; retain no
     * extra reference for every recovery attempt. */
    dispatch_release(queue);
    return services_ok;
}

static void *hid_thread(void *unused) {
    (void)unused;
    say("DVM_HID_INIT begin pid=%d\n", getpid());
    uint64_t t0 = now_us();
    bool ok = initialize_hid();
    say("DVM_HID_INIT done ok=%d ms=%llu\n", ok,
        (unsigned long long)((now_us() - t0) / 1000));
    pthread_mutex_lock(&lock);
    state = ok && !need_recovery ? 'R' : 'L';
    announce();
    initializing = false;
    pthread_cond_broadcast(&op_cond);
    pthread_mutex_unlock(&lock);
    if (!ok) {
        /* A process which stays L forever is never restarted by launchd.
         * Let KeepAlive retry after its throttle instead. */
        say("DVM_HID_INIT restart\n");
        _exit(75);
    }
    return NULL;
}

/* ---------------- event posting ----------------
 *
 * The reader thread owns the contact state machine and answers QEMU at once
 * (code Q = accepted and queued).  A worker thread performs the blocking
 * IOHIDVirtualServiceClientDispatchEvent calls in order and reports each
 * result on its own line (DVMI2D), because NATIVE_HID_FRESH2 measured single
 * dispatches of 1.4-4.0 s while backboardd was busy: with synchronous
 * dispatch an 80 ms host tap became a multi-second press.  Event timestamps
 * follow the host gesture timeline (host_ms deltas from the gesture's down),
 * so the HID stream keeps the intended timing even when delivery lags.
 */
static bool held;
static double held_x, held_y;
static bool touch_down;
static double touch_x, touch_y;
static unsigned button_down;
static bool touch_release_needed, touch_release_enqueued;
static unsigned buttons_release_needed, buttons_release_enqueued;
static uint64_t gesture_base_mach;
static long long gesture_base_host_ms;
static mach_timebase_info_data_t timebase;
static unsigned op_generation;

static uint64_t timeline_timestamp(long long host_ms) {
    uint64_t now = mach_absolute_time();
    if (!host_ms || !gesture_base_mach || host_ms < gesture_base_host_ms) return now;
    uint64_t ticks = (uint64_t)(host_ms - gesture_base_host_ms) * 1000000ull *
                     timebase.denom / timebase.numer;
    uint64_t ts = gesture_base_mach + ticks;
    return ts < now ? ts : now;   /* never post an event from the future */
}

struct op {
    uint32_t epoch, seq;
    unsigned generation;
    char kind;
    double x, y;
    bool down, edge, report;
    unsigned usage;
    uint64_t ts;
    int notches;
};
#define OP_QUEUE 32
static struct op ops[OP_QUEUE];
static unsigned op_head, op_len;
static bool dispatching;
static struct op active_op;

static bool post_touch_at(uint64_t ts, double x, double y, bool down, bool edge) {
    /* Range=1, touch=2, position=4, identity=32; transducer type 2 and
     * options 0x40 as in RCPSyntheticEventStream (0x29b1fcb74, 0x29b213a50). */
    uint32_t mask = edge ? 1 | 2 | 4 | 32 : 4;
    if (!ts) ts = mach_absolute_time();
    Ref hand = digitizer(NULL, ts, 2, 0, 0, mask, 0, x, y, 0, 0, 0, down, down, 0x40);
    Ref child = finger(NULL, ts, 1, 1, mask, x, y, 0, 0, 0, down, down, 0x40);
    if (!hand || !child) {
        if (hand) cf_release(hand);
        if (child) cf_release(child);
        return false;
    }
    set_integer(hand, (11u << 16) | 25u, 1);   /* display integrated */
    set_integer(hand, 4, 1);                   /* built in */
    append_event(hand, child, 0);
    bool ok = vsc_dispatch(touch_service.service, hand) != 0;
    cf_release(child);
    cf_release(hand);
    return ok;
}
static bool post_button_at(uint64_t ts, unsigned usage, bool down) {
    Ref event = keyboard(NULL, ts ? ts : mach_absolute_time(), 0x0c, usage, down, 0x40);
    if (!event) return false;
    set_integer(event, 4, 1);
    bool ok = vsc_dispatch(button_service.service, event) != 0;
    cf_release(event);
    return ok;
}

/* A mouse-wheel batch as one finger drag that ends at rest: down at the
 * pointer, eight 12 ms steps, a 60 ms hold so the pan velocity is zero, then
 * up.  UIKit therefore scrolls exactly the dragged distance and never flings.
 * Wheel up (positive notches) moves the finger down, i.e. content scrolls
 * up, matching AppKit's normalised delta sign in ui/cocoa.m. */
#define WHEEL_STEP_FRACTION 0.05     /* of the display height per notch */
#define WHEEL_MAX_FRACTION  0.40
/* The wheel is a miniature touch stream inside one worker operation.  Keep
 * the actual contact state current between its individual HID calls so C and
 * epoch changes can release it even though there is no queued T operation. */
static bool wheel_cancelled(unsigned generation) {
    pthread_mutex_lock(&lock);
    bool cancelled = generation != op_generation;
    pthread_mutex_unlock(&lock);
    return cancelled;
}
static void wheel_note_touch(double x, double y, bool down) {
    pthread_mutex_lock(&lock);
    touch_down = down;
    touch_x = x;
    touch_y = y;
    if (!down) touch_release_needed = false;
    pthread_mutex_unlock(&lock);
}
static bool scroll_gesture(double x, double y, int notches, unsigned generation) {
    double distance = notches * WHEEL_STEP_FRACTION;
    if (distance > WHEEL_MAX_FRACTION) distance = WHEEL_MAX_FRACTION;
    if (distance < -WHEEL_MAX_FRACTION) distance = -WHEEL_MAX_FRACTION;
    double y_end = y + distance;
    if (y_end < 0.02) y_end = 0.02;
    if (y_end > 0.98) y_end = 0.98;
    if (wheel_cancelled(generation)) return true;
    if (!post_touch_at(0, x, y, true, true)) return false;
    wheel_note_touch(x, y, true);
    bool ok = true;
    for (int i = 1; i <= 8; i++) {
        if (wheel_cancelled(generation)) {
            bool released = post_touch_at(0, x, touch_y, false, true);
            if (released) wheel_note_touch(x, touch_y, false);
            return released;
        }
        usleep(12000);
        double step_y = y + (y_end - y) * i / 8.0;
        bool posted = post_touch_at(0, x, step_y, true, false);
        if (posted) wheel_note_touch(x, step_y, true);
        ok = posted && ok;
    }
    if (wheel_cancelled(generation)) {
        bool released = post_touch_at(0, x, touch_y, false, true);
        if (released) wheel_note_touch(x, touch_y, false);
        return released;
    }
    usleep(60000);
    bool released = post_touch_at(0, x, y_end, false, true);
    if (released) wheel_note_touch(x, y_end, false);
    return released && ok;
}

/* Reader side: queue one HID operation; false when the worker is too far behind. */
static bool queue_op(const struct record *r, char kind, double x, double y,
                     bool down, bool edge, unsigned usage, bool report) {
    if (op_len >= OP_QUEUE) return false;
    struct op *o = &ops[(op_head + op_len) % OP_QUEUE];
    *o = (struct op){r->epoch, r->seq, op_generation, kind, x, y, down, edge,
                     report, usage, timeline_timestamp(r->host_ms), r->c};
    op_len++;
    pthread_cond_signal(&op_cond);
    return true;
}
static bool queue_op_front(const struct record *r, char kind, double x, double y,
                           bool down, bool edge, unsigned usage, bool report) {
    if (op_len >= OP_QUEUE) return false;
    op_head = (op_head + OP_QUEUE - 1) % OP_QUEUE;
    ops[op_head] = (struct op){r->epoch, r->seq, op_generation, kind, x, y, down,
                               edge, report, usage, timeline_timestamp(r->host_ms), r->c};
    op_len++;
    pthread_cond_signal(&op_cond);
    return true;
}
static unsigned button_bit(unsigned usage) {
    return usage == 0x40 ? 1u : usage == 0x30 ? 2u : 0;
}
/* Caller holds lock.  Recovery/cancel releases are placed ahead of fresh
 * records, including records the reader accepted while an old dispatch was
 * still blocked. */
static bool schedule_releases(const struct record *r) {
    bool ok = true;
    if (touch_release_needed && touch_down && !touch_release_enqueued) {
        if (queue_op_front(r, 'T', touch_x, touch_y, false, true, 0, false)) {
            touch_release_enqueued = true;
        } else {
            ok = false;
        }
    }
    static const unsigned usages[] = {0x40, 0x30};
    for (unsigned i = 0; i < sizeof(usages) / sizeof(usages[0]); i++) {
        unsigned bit = button_bit(usages[i]);
        if (!(buttons_release_needed & button_down & bit) ||
            (buttons_release_enqueued & bit)) continue;
        if (queue_op_front(r, 'B', 0, 0, false, true, usages[i], false)) {
            buttons_release_enqueued |= bit;
        } else {
            ok = false;
        }
    }
    return ok;
}

/* Cancel once for a packet, whether it arrived with a new epoch or as C in
 * the current epoch.  Queued work is discarded; a down already in the worker
 * is detected through active_op and balanced by the worker before new input. */
static bool cancel_input(const struct record *r) {
    op_generation++;
    op_head = op_len = 0;
    touch_release_enqueued = false;
    buttons_release_enqueued = 0;
    held = false;
    if (touch_down || (dispatching && active_op.kind == 'T' && active_op.down) ||
        (dispatching && active_op.kind == 'W'))
        touch_release_needed = true;
    buttons_release_needed |= button_down;
    if (dispatching && active_op.kind == 'B' && active_op.down)
        buttons_release_needed |= button_bit(active_op.usage);
    /* A running wheel observes its generation and posts its own release before
     * returning.  Do not queue another up behind it. */
    if (dispatching && active_op.kind == 'W') return true;
    return schedule_releases(r);
}

/* Re-creation can itself block while the event system is being replaced.  A
 * helper process is disposable (launchd has KeepAlive), whereas an unbounded
 * dispatch worker leaves the host permanently at I. */
static void *recovery_watchdog(void *argument) {
    unsigned generation = (unsigned)(uintptr_t)argument;
    sleep(RECOVERY_TIMEOUT_SECONDS);
    pthread_mutex_lock(&lock);
    bool expired = recovery_in_progress && generation == recovery_generation;
    pthread_mutex_unlock(&lock);
    if (expired) {
        say("DVM_HID_RECOVER timeout=%us generation=%u restart\n",
            RECOVERY_TIMEOUT_SECONDS, generation);
        _exit(75);
    }
    return NULL;
}

/* NATIVE_HID_RESTORE1/2: about a minute after a snapshot restore every
 * IOHIDVirtualServiceClientDispatchEvent started returning false while the
 * UI stayed live, so the event system had dropped our services.  Replacement
 * services own fresh client references.  If that cannot complete promptly,
 * exit for launchd instead of pinning this helper in I forever. */
static void recover_services(void) {
    pthread_mutex_lock(&lock);
    state = 'I';
    announce();
    recoveries++;
    recovery_in_progress = true;
    unsigned generation = ++recovery_generation;
    say("DVM_HID_RECOVER begin count=%u\n", recoveries);
    pthread_mutex_unlock(&lock);
    pthread_t watchdog;
    if (pthread_create(&watchdog, NULL, recovery_watchdog, (void *)(uintptr_t)generation)) {
        say("DVM_HID_RECOVER watchdog-create restart\n");
        _exit(75);
    }
    pthread_detach(watchdog);
    destroy_services();
    for (unsigned attempt = 1; attempt <= RECOVERY_ATTEMPTS; attempt++) {
        pthread_mutex_lock(&lock);
        need_recovery = false;
        pthread_mutex_unlock(&lock);
        dispatch_queue_t queue = dispatch_queue_create("dvm-hid.services", DISPATCH_QUEUE_SERIAL);
        bool ok = create_service(&touch_service, queue) && create_service(&button_service, queue);
        dispatch_release(queue);
        pthread_mutex_lock(&lock);
        if (ok && !need_recovery) {
            need_recovery = false;
            held = false;
            touch_down = touch_release_needed = touch_release_enqueued = false;
            button_down = buttons_release_needed = buttons_release_enqueued = 0;
            recovery_in_progress = false;
            state = 'R';
            announce();
            say("DVM_HID_RECOVER done attempt=%u\n", attempt);
            pthread_mutex_unlock(&lock);
            return;
        }
        pthread_mutex_unlock(&lock);
        destroy_services();
        say("DVM_HID_RECOVER retry attempt=%u\n", attempt);
        if (attempt != RECOVERY_ATTEMPTS) sleep(2);
    }
    say("DVM_HID_RECOVER failed attempts=%u restart\n", RECOVERY_ATTEMPTS);
    _exit(75);
}

static void *dispatch_thread(void *unused) {
    (void)unused;
    unsigned failures = 0;
    /* NATIVE_HID_FRESH3 froze a 1.5 s "dispatch" inside malloc in
     * IOHIDEventCreateDigitizerEvent: the worker was runnable but starved
     * while SpringBoard/backboardd saturated the TCG vCPUs.  Input threads
     * must outrank the UI work they feed. */
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    pthread_mutex_lock(&lock);
    for (;;) {
        while (initializing || (!op_len && !need_recovery))
            pthread_cond_wait(&op_cond, &lock);
        if (need_recovery) {
            op_head = op_len = 0;           /* stale gestures die with the service */
            pthread_mutex_unlock(&lock);
            recover_services();
            failures = 0;
            pthread_mutex_lock(&lock);
            continue;
        }
        struct op o = ops[op_head];
        op_head = (op_head + 1) % OP_QUEUE;
        op_len--;
        dispatching = true;
        active_op = o;
        if (!o.report && o.kind == 'T' && !o.down) touch_release_enqueued = false;
        if (!o.report && o.kind == 'B' && !o.down) buttons_release_enqueued &= ~button_bit(o.usage);
        pthread_mutex_unlock(&lock);
        uint64_t t0 = now_us();
        bool ok = o.kind == 'B' ? post_button_at(o.ts, o.usage, o.down)
                : o.kind == 'W' ? scroll_gesture(o.x, o.y, o.notches, o.generation)
                : o.kind == 'T' ? post_touch_at(o.ts, o.x, o.y, o.down, o.edge)
                                : false;
        uint64_t elapsed = now_us() - t0;
        pthread_mutex_lock(&lock);
        dispatching = false;
        /* A cancel or an epoch transition may have cleared the operation
         * while it was already executing.  Balance any edge it did submit,
         * but do not emit a second completion for the later record. */
        if (ok && o.kind == 'T') {
            touch_down = o.down;
            touch_x = o.x;
            touch_y = o.y;
            if (!o.down) touch_release_needed = false;
        } else if (ok && o.kind == 'B' && button_bit(o.usage)) {
            if (o.down) button_down |= button_bit(o.usage);
            else {
                button_down &= ~button_bit(o.usage);
                buttons_release_needed &= ~button_bit(o.usage);
            }
        }
        if (!ok && o.kind == 'W' && touch_down) {
            /* A failed final wheel up is a real held contact.  Queue one
             * release now; do not wait for an unrelated second failure. */
            touch_release_needed = true;
            struct record cleanup = {.epoch = epoch};
            schedule_releases(&cleanup);
        }
        if (o.generation != op_generation) {
            struct record cleanup = {.epoch = epoch};
            schedule_releases(&cleanup);
        } else if (!ok && !o.report) {
            /* An internal release failed.  Retry immediately; two failures
             * still take the normal service-recovery path below. */
            struct record cleanup = {.epoch = epoch};
            schedule_releases(&cleanup);
        }
        failures = ok ? 0 : failures + 1;
        if (failures >= 2) need_recovery = true;
        pthread_mutex_unlock(&lock);
        if (o.report) say("DVMI2D %u %u %c %llu\n", o.epoch, o.seq, ok ? 'S' : 'F',
                          (unsigned long long)elapsed);
        pthread_mutex_lock(&lock);
    }
}

/* ---------------- console reader ---------------- */

static bool parse(const char *line, struct record *r) {
    int consumed = 0;
    if (sscanf(line, "DVMI2 %u %u %c %u %u %d %lld %n", &r->epoch, &r->seq, &r->kind,
               &r->a, &r->b, &r->c, &r->host_ms, &consumed) != 7 || !consumed) return false;
    if (line[consumed]) return false;
    switch (r->kind) {
    case 'D': case 'M': case 'U': return r->a <= 32767 && r->b <= 32767 && !r->c;
    case 'W': return r->a <= 32767 && r->b <= 32767 && r->c && r->c >= -8 && r->c <= 8;
    case 'C': case 'P': return !r->a && !r->b && !r->c;
    case 'B': return r->a <= 0xffff && r->b <= 1 && !r->c;
    default: return false;
    }
}


static char handle(const struct record *r) {
    if (validate_only) return 'Q';
    if (r->epoch != epoch) {
        /* UART records already in flight from an old host epoch must never
         * turn a later reset into a stale press.  QEMU epochs only increase
         * during one helper lifetime (wrap is not a practical concern). */
        if (epoch && r->epoch < epoch) return 'E';
        epoch = r->epoch;
        /* C itself is the one cancellation request for a new-epoch C. */
        if (state == 'R' && r->kind != 'C') cancel_input(r);
        else if (state != 'R') {
            held = false;
            touch_down = touch_release_needed = touch_release_enqueued = false;
            button_down = buttons_release_needed = buttons_release_enqueued = 0;
        }
    }
    /* Reserve room for the touch/Home/Power cleanup even with an older
     * host that only waits for queue acceptance. Never drop a release while
     * retaining the down that it was meant to balance. */
    if (state == 'R' && strchr("DMUBW", r->kind) && op_len >= OP_QUEUE - 3) {
        cancel_input(r);
        return 'F';
    }
    switch (r->kind) {
    case 'P': return 'Q';
    case 'C':
        if (state != 'R') return 'Q';
        /* C is host-side flow control, not an HID operation.  Its internal
         * releases deliberately have no DVMI2D record for the C sequence. */
        return cancel_input(r) ? 'Q' : 'F';
    case 'B':
        if (state != 'R') return 'N';
        if (r->b) {
            gesture_base_mach = mach_absolute_time();
            gesture_base_host_ms = r->host_ms;
        }
        return queue_op(r, 'B', 0, 0, r->b, true, r->a, true) ? 'Q' : 'F';
    case 'W':
        if (state != 'R') return 'N';
        if (held) return 'E';                /* a real drag owns the finger */
        return queue_op(r, 'W', r->a / 32767.0, r->b / 32767.0,
                        false, false, 0, true) ? 'Q' : 'F';
    default: break;
    }
    if (state != 'R') { held = false; return 'N'; }
    double x = r->a / 32767.0, y = r->b / 32767.0;
    if (r->kind == 'D') {
        if (held) cancel_input(r);           /* never stack two contacts */
        gesture_base_mach = mach_absolute_time();
        gesture_base_host_ms = r->host_ms;
        held = true; held_x = x; held_y = y;
        return queue_op(r, 'T', x, y, true, true, 0, true) ? 'Q' : 'F';
    }
    if (!held) return 'E';                   /* stale motion or release */
    held_x = x; held_y = y;
    if (r->kind == 'U') held = false;
    return queue_op(r, 'T', x, y, r->kind == 'M', r->kind == 'U', 0, true) ? 'Q' : 'F';
}

static void reader(void) {
    char line[200];
    while (fgets(line, sizeof(line), stdin)) {
        if (!strchr(line, '\n')) {
            int c;
            while ((c = getchar()) != '\n' && c != EOF) {}
            continue;                       /* overlong or torn record */
        }
        line[strcspn(line, "\r\n")] = '\0';
        const char *p = strstr(line, "DVMI2 ");
        if (!p) continue;                   /* console chatter, not ours */
        struct record r;
        if (!parse(p, &r)) { say("DVM_HID_REJECT %s\n", p); continue; }
        uint64_t t0 = now_us();
        /* QEMU stamps host monotonic time while this guest carries its own
         * clock.  Cross-clock subtraction is not a latency metric and can
         * underflow an unsigned dispatch duration, so leave it unavailable. */
        long long delivery_ms = -1;
        pthread_mutex_lock(&lock);
        char code = handle(&r);
        char s = state;
        /* The worker cannot publish DVMI2D until this mutex is released, so
         * the host always sees Q before the completion it authorises. */
        say("DVMI2A %u %u %c %c %llu %lld\n", r.epoch, r.seq, code, s,
            (unsigned long long)(now_us() - t0), delivery_ms);
        pthread_mutex_unlock(&lock);
    }
    pthread_mutex_lock(&lock);
    if (state == 'R') {
        struct record eof = {.epoch = epoch};
        cancel_input(&eof);
    }
    pthread_mutex_unlock(&lock);
    say("DVM_HID_EOF error=%d errno=%d\n", ferror(stdin), errno);
}

/* The first helper instance died silently in three of five fresh boots
 * (no EOF, no error line, no corpse).  This handler uses only async-signal-
 * safe operations.  The old snprintf/signal/raise sequence could deadlock in
 * an allocator crash, which is exactly when this evidence matters most. */
static void on_signal(int number) {
    static const char term[] = "DVM_HID_SIGNAL TERM\n";
    static const char intr[] = "DVM_HID_SIGNAL INT\n";
    static const char hup[] = "DVM_HID_SIGNAL HUP\n";
    static const char segv[] = "DVM_HID_SIGNAL SEGV\n";
    static const char bus[] = "DVM_HID_SIGNAL BUS\n";
    static const char abrt[] = "DVM_HID_SIGNAL ABRT\n";
    static const char ill[] = "DVM_HID_SIGNAL ILL\n";
    static const char pipe[] = "DVM_HID_SIGNAL PIPE\n";
    const char *message = NULL;
    size_t length = 0;
#define SIGNAL_MESSAGE(sig, text) case sig: message = text; length = sizeof(text) - 1; break
    switch (number) {
    SIGNAL_MESSAGE(SIGTERM, term);
    SIGNAL_MESSAGE(SIGINT, intr);
    SIGNAL_MESSAGE(SIGHUP, hup);
    SIGNAL_MESSAGE(SIGSEGV, segv);
    SIGNAL_MESSAGE(SIGBUS, bus);
    SIGNAL_MESSAGE(SIGABRT, abrt);
    SIGNAL_MESSAGE(SIGILL, ill);
    SIGNAL_MESSAGE(SIGPIPE, pipe);
    default: break;
    }
#undef SIGNAL_MESSAGE
    if (message) write(STDERR_FILENO, message, length);
    kill(getpid(), number);  /* SA_RESETHAND has restored the default action. */
}

static void install_signal_handlers(void) {
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_handler = on_signal;
    action.sa_flags = SA_RESETHAND;
    sigemptyset(&action.sa_mask);
    for (int *sig = (int[]){SIGTERM, SIGINT, SIGHUP, SIGSEGV, SIGBUS,
                            SIGABRT, SIGILL, SIGPIPE, 0}; *sig; sig++)
        sigaction(*sig, &action, NULL);
}

int main(int argc, char **argv) {
    validate_only = argc == 2 && !strcmp(argv[1], "--validate");
    if (argc != 1 && !validate_only) return 2;
    setvbuf(stderr, NULL, _IONBF, 0);
    if (validate_only) { state = 'R'; reader(); return 0; }
    /* Same lock as the v6 helper so the two can never both read the console. */
    int lock_fd = open("/var/run/dvm-input.lock", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB)) { perror("dvm-hid singleton lock"); return 1; }
    say("DVM_HID_START version=%d pid=%d\n", VERSION, getpid());
    if (!freopen("/dev/console", "r", stdin)) { perror("dvm-hid open console"); return 1; }
    install_signal_handlers();
    sigset_t signals;
    sigemptyset(&signals);
    pthread_sigmask(SIG_SETMASK, &signals, NULL);
    struct termios attributes;
    if (tcgetattr(STDIN_FILENO, &attributes) == 0) {
        cfmakeraw(&attributes);
        attributes.c_cc[VMIN] = 1;
        attributes.c_cc[VTIME] = 0;
        tcsetattr(STDIN_FILENO, TCSANOW, &attributes);
    }
    announce();
    mach_timebase_info(&timebase);
    if (setpriority(PRIO_PROCESS, 0, -20)) perror("dvm-hid setpriority");
    pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
    pthread_t thread, worker;
    if (pthread_create(&thread, NULL, hid_thread, NULL)) return 1;
    if (pthread_create(&worker, NULL, dispatch_thread, NULL)) return 1;
    /* The reader owns the main thread; GCD services the HID client queue. */
    reader();
    return 0;
}
