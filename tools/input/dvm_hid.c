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
#include <unistd.h>

#define VERSION 12

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
static unsigned recoveries;
static uint32_t epoch;
static bool validate_only;

struct record { unsigned epoch, seq, a, b; int c; char kind; long long host_ms; };
static uint64_t now_us(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (uint64_t)tv.tv_sec * 1000000 + tv.tv_usec;
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
    say("DVM_HID_NOTIFY service=%s type=%u\n", v->name, type);
    /* HID.framework forwards only types 3..5 (thunk 0x2678a747c: sub #3,
     * cmp #2).  NATIVE_HID_FRESH1 observed 0, 2, 4 in that order while the
     * service was being created and then opened by backboardd, so 4 is
     * kIOHIDVirtualServiceOpenedByEventSystem; 3 and 5 are the unscheduled /
     * reset notifications that HID.framework reports as Terminated. */
    if (type == 3 || type == 5) {
        state = 'L';
        announce();
        pthread_mutex_lock(&lock);
        need_recovery = true;
        pthread_cond_signal(&op_cond);
        pthread_mutex_unlock(&lock);
    } else if (type == 4) {
        say("DVM_HID_OPENED service=%s\n", v->name);
    }
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
    client_set_queue(v->client, queue);
    client_activate(v->client);
    v->service = vsc_create(v->client, v->props, &callbacks, v, NULL);
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
    return create_service(&touch_service, queue) && create_service(&button_service, queue);
}

static void *hid_thread(void *unused) {
    (void)unused;
    say("DVM_HID_INIT begin pid=%d\n", getpid());
    struct timeval t0, t1;
    gettimeofday(&t0, NULL);
    bool ok = initialize_hid();
    gettimeofday(&t1, NULL);
    say("DVM_HID_INIT done ok=%d ms=%lld\n", ok,
        (long long)((t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_usec - t0.tv_usec) / 1000));
    pthread_mutex_lock(&lock);
    state = ok ? 'R' : 'L';
    announce();
    pthread_mutex_unlock(&lock);
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
static uint64_t gesture_base_mach;
static long long gesture_base_host_ms;
static mach_timebase_info_data_t timebase;

static uint64_t timeline_timestamp(long long host_ms) {
    uint64_t now = mach_absolute_time();
    if (!host_ms || !gesture_base_mach || host_ms < gesture_base_host_ms) return now;
    uint64_t ticks = (uint64_t)(host_ms - gesture_base_host_ms) * 1000000ull *
                     timebase.denom / timebase.numer;
    uint64_t ts = gesture_base_mach + ticks;
    return ts < now ? ts : now;   /* never post an event from the future */
}

struct op { uint32_t epoch, seq; char kind; double x, y; bool down, edge; unsigned usage; uint64_t ts; int notches; };
#define OP_QUEUE 32
static struct op ops[OP_QUEUE];
static unsigned op_head, op_len;

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
static bool scroll_gesture(double x, double y, int notches) {
    double distance = notches * WHEEL_STEP_FRACTION;
    if (distance > WHEEL_MAX_FRACTION) distance = WHEEL_MAX_FRACTION;
    if (distance < -WHEEL_MAX_FRACTION) distance = -WHEEL_MAX_FRACTION;
    double y_end = y + distance;
    if (y_end < 0.02) y_end = 0.02;
    if (y_end > 0.98) y_end = 0.98;
    if (!post_touch_at(0, x, y, true, true)) return false;
    bool ok = true;
    for (int i = 1; i <= 8; i++) {
        usleep(12000);
        ok = post_touch_at(0, x, y + (y_end - y) * i / 8.0, true, false) && ok;
    }
    usleep(60000);
    return post_touch_at(0, x, y_end, false, true) && ok;
}

/* Reader side: queue one HID operation; false when the worker is too far behind. */
static bool queue_op(const struct record *r, char kind, double x, double y, bool down, bool edge, unsigned usage) {
    if (op_len >= OP_QUEUE) return false;
    struct op *o = &ops[(op_head + op_len) % OP_QUEUE];
    *o = (struct op){r->epoch, r->seq, kind, x, y, down, edge, usage, timeline_timestamp(r->host_ms), r->c};
    op_len++;
    pthread_cond_signal(&op_cond);
    return true;
}
static bool release_contact(const struct record *r) {
    if (!held) return true;
    held = false;
    return queue_op(r, 'T', held_x, held_y, false, true, 0);
}
/* NATIVE_HID_RESTORE1/2: about a minute after a snapshot restore every
 * IOHIDVirtualServiceClientDispatchEvent started returning false while the
 * UI stayed live, so the event system had dropped our services.  Rebuild
 * them with fresh clients instead of staying dead; the reader answers
 * pings with state I meanwhile and QEMU drops input until R returns. */
static void recover_services(void) {
    pthread_mutex_lock(&lock);
    state = 'I';
    announce();
    recoveries++;
    say("DVM_HID_RECOVER begin count=%u\n", recoveries);
    pthread_mutex_unlock(&lock);
    for (unsigned attempt = 1;; attempt++) {
        dispatch_queue_t queue = dispatch_queue_create("dvm-hid.services", DISPATCH_QUEUE_SERIAL);
        touch_service.client = touch_service.service = NULL;
        button_service.client = button_service.service = NULL;
        bool ok = create_service(&touch_service, queue) && create_service(&button_service, queue);
        pthread_mutex_lock(&lock);
        if (ok) {
            need_recovery = false;
            held = false;
            state = 'R';
            announce();
            say("DVM_HID_RECOVER done attempt=%u\n", attempt);
            pthread_mutex_unlock(&lock);
            return;
        }
        pthread_mutex_unlock(&lock);
        say("DVM_HID_RECOVER retry attempt=%u\n", attempt);
        sleep(2);
    }
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
        while (!op_len && !need_recovery) pthread_cond_wait(&op_cond, &lock);
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
        pthread_mutex_unlock(&lock);
        uint64_t t0 = now_us();
        bool ok = o.kind == 'B' ? post_button_at(o.ts, o.usage, o.down)
                : o.kind == 'W' ? scroll_gesture(o.x, o.y, o.notches)
                                : post_touch_at(o.ts, o.x, o.y, o.down, o.edge);
        say("DVMI2D %u %u %c %llu\n", o.epoch, o.seq, ok ? 'S' : 'F',
            (unsigned long long)(now_us() - t0));
        pthread_mutex_lock(&lock);
        failures = ok ? 0 : failures + 1;
        if (failures >= 2) need_recovery = true;
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
        /* Host state was reset (restore, helper restart, overflow): whatever
         * finger we still hold belongs to a gesture nobody can finish. */
        if (state == 'R') release_contact(r);
        held = false;
        epoch = r->epoch;
    }
    switch (r->kind) {
    case 'P': return 'Q';
    case 'C':
        if (state != 'R') { held = false; return 'Q'; }
        return release_contact(r) ? 'Q' : 'F';
    case 'B':
        if (state != 'R') return 'N';
        gesture_base_mach = mach_absolute_time();
        gesture_base_host_ms = r->host_ms;
        return queue_op(r, 'B', 0, 0, r->b, true, r->a) ? 'Q' : 'F';
    case 'W':
        if (state != 'R') return 'N';
        if (held) return 'Q';               /* a real drag owns the finger */
        return queue_op(r, 'W', r->a / 32767.0, r->b / 32767.0, false, false, 0) ? 'Q' : 'F';
    default: break;
    }
    if (state != 'R') { held = false; return 'N'; }
    double x = r->a / 32767.0, y = r->b / 32767.0;
    if (r->kind == 'D') {
        if (held) release_contact(r);       /* never stack two contacts */
        gesture_base_mach = mach_absolute_time();
        gesture_base_host_ms = r->host_ms;
        held = true; held_x = x; held_y = y;
        return queue_op(r, 'T', x, y, true, true, 0) ? 'Q' : 'F';
    }
    if (!held) return 'Q';                  /* motion or release without a contact */
    held_x = x; held_y = y;
    if (r->kind == 'U') held = false;
    return queue_op(r, 'T', x, y, r->kind == 'M', r->kind == 'U', 0) ? 'Q' : 'F';
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
        /* Delivery latency: QEMU stamps host wall-clock ms; the guest clock
         * is host-synchronised through the RTC leaf (DARWIN_RTC_PV), so the
         * difference measures UART FIFO -> kernel console -> fgets. */
        long long delivery_ms = r.host_ms ? (long long)(t0 / 1000) - r.host_ms : -1;
        pthread_mutex_lock(&lock);
        char code = handle(&r);
        char s = state;
        pthread_mutex_unlock(&lock);
        say("DVMI2A %u %u %c %c %llu %lld\n", r.epoch, r.seq, code, s,
            (unsigned long long)(now_us() - t0), delivery_ms);
    }
    pthread_mutex_lock(&lock);
    if (state == 'R') { struct record eof = {.epoch = epoch}; release_contact(&eof); }
    pthread_mutex_unlock(&lock);
    say("DVM_HID_EOF error=%d errno=%d\n", ferror(stdin), errno);
}

/* The first helper instance died silently in three of five fresh boots
 * (no EOF, no error line, no corpse).  Log the terminating signal, then
 * let the default action run so launchd's KeepAlive restarts us. */
static void on_signal(int number) {
    char buf[32];
    int n = snprintf(buf, sizeof(buf), "DVM_HID_SIGNAL %d\n", number);
    if (n > 0) write(STDERR_FILENO, buf, n);
    signal(number, SIG_DFL);
    raise(number);
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
    for (int *sig = (int[]){SIGTERM, SIGINT, SIGHUP, SIGSEGV, SIGBUS, SIGABRT, SIGILL, SIGPIPE, 0}; *sig; sig++)
        signal(*sig, on_signal);
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
