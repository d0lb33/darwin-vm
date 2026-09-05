/*
 * Process-scoped virtual power source for a disposable iOS guest.
 *
 * This uses the private but exported IOPS publisher ABI recovered from
 * Apple's PowerManagement source: IOPSCreatePowerSource(IOPSPowerSourceID *),
 * IOPSSetPowerSourceDetails(IOPSPowerSourceID, CFDictionaryRef), and
 * IOPSReleasePowerSource(IOPSPowerSourceID).  It does not write IORegistry,
 * mutate the device tree, or pretend to model a battery controller.
 *
 * The service remains alive only after it has verified that the native IOPS
 * snapshot contains exactly the source it asked powerd to publish.  It removes
 * the source on SIGTERM/SIGINT.  Launching is intentionally owned by the
 * caller; this program does not install a launchd job.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <dispatch/dispatch.h>

#include <dlfcn.h>
#include <fcntl.h>
#include <notify.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct OpaqueIOPSPowerSourceID *IOPSPowerSourceID;
typedef int32_t IOReturn;

/*
 * 24A5430a's _createPowerSource at 0x18f023a0c passes source + 8 as the
 * output psid slot to _io_ps_new_pspowersource (0x18f060e84).  This is the
 * same layout Apple documents in its PowerManagement pmtool:
 * { CFMachPortRef configdConnection; int psid; }.  The returned source ID is
 * then published as ((pid & 0xffff) << 16) | (psid & 0xffff).  The Power
 * Source ID key is system-owned, so it is deliberately read back, never set.
 */
struct IOPSPowerSourceIDLayout {
    void *connection;
    int32_t psid;
};

_Static_assert(offsetof(struct IOPSPowerSourceIDLayout, psid) == 8,
               "24A5430a IOPSPowerSourceID psid offset");

enum {
    kIOReturnSuccess = 0,
    kConsoleAttempts = 4,
    kConsoleRetryUsecs = 250000,
    kCFDescriptionSize = 4096,
};

static const char kName[] = "Darwin VM";
static const char kType[] = "InternalBattery";
static const char kTransport[] = "Internal";
static const char kState[] = "AC Power";
static const char kPercentNotification[] =
    "com.apple.system.powersources.percent";
/* IOPowerSourcesPrivate.h and native IOPSGetPercentRemaining at 0x18f0092e0:
 * bits 19 valid, 16 external power, 21 fully charged; low byte percent.
 * Bit 17 means charging, so it remains clear for our full AC-backed source.
 * powerd's hardware aggregate ignores user-published InternalBattery sources.
 * POWER_PERCENT_ROOT4 proved root publication supplies a valid native reading;
 * SpringBoard's unprivileged notify_set_state returned success but did not.
 */
static const uint64_t kPercentState = (UINT64_C(1) << 19) |
    (UINT64_C(1) << 16) | (UINT64_C(1) << 21) | 100;
/* Exact 24A5430a cache literal used by SmartPowerNap for powerd restart resync. */
static const char kPowerdResyncNotification[] =
    "com.apple.system.powermanagement.assertionresync";

struct APIs {
    void *cf;
    void *iokit;
    IOReturn (*create)(IOPSPowerSourceID *);
    IOReturn (*set_details)(IOPSPowerSourceID, CFDictionaryRef);
    IOReturn (*release_source)(IOPSPowerSourceID);
    CFTypeRef (*copy_info)(void);
    CFArrayRef (*copy_list)(CFTypeRef);
    CFDictionaryRef (*get_description)(CFTypeRef, CFTypeRef);
    IOReturn (*get_percent)(int *, bool *, bool *);
    CFMutableDictionaryRef (*dictionary_create_mutable)(
        CFAllocatorRef, CFIndex, const CFDictionaryKeyCallBacks *,
        const CFDictionaryValueCallBacks *);
    void (*dictionary_set_value)(CFMutableDictionaryRef, const void *,
                                 const void *);
    const void *(*dictionary_get_value)(CFDictionaryRef, const void *);
    CFStringRef (*string_create)(CFAllocatorRef, const char *, CFStringEncoding);
    CFNumberRef (*number_create)(CFAllocatorRef, CFNumberType, const void *);
    Boolean (*number_get_value)(CFNumberRef, CFNumberType, void *);
    CFIndex (*array_count)(CFArrayRef);
    const void *(*array_value)(CFArrayRef, CFIndex);
    Boolean (*equal)(CFTypeRef, CFTypeRef);
    CFStringRef (*copy_description)(CFTypeRef);
    Boolean (*string_get_cstring)(CFStringRef, char *, CFIndex, CFStringEncoding);
    void (*cf_release)(CFTypeRef);
    const CFDictionaryKeyCallBacks *key_callbacks;
    const CFDictionaryValueCallBacks *value_callbacks;
    CFBooleanRef true_value;
    CFBooleanRef false_value;
};

struct Publisher {
    const struct APIs *apis;
    CFDictionaryRef details;
    IOPSPowerSourceID source;
    dispatch_queue_t queue;
    int resync_token;
    int percent_token;
    Boolean percent_owned;
    unsigned retry_seconds;
    uint64_t retry_generation;
    Boolean ready;
    Boolean stopping;
    Boolean initial_publish_complete;
    Boolean resync_pending;
};

static struct Publisher publisher;
/* Framework initialization may replace stderr; keep our console descriptor. */
static int console_fd = STDERR_FILENO;

static void console_redirect(void) {
    for (unsigned attempt = 1; attempt <= kConsoleAttempts; attempt++) {
        int console = open("/dev/console", O_WRONLY | O_APPEND | O_CLOEXEC);
        if (console >= 0) {
            int retained = fcntl(console, F_DUPFD_CLOEXEC, 3);
            if (retained >= 0) console_fd = retained;
            int stdout_result = dup2(console, STDOUT_FILENO);
            int stderr_result = dup2(console, STDERR_FILENO);
            if (console > STDERR_FILENO) {
                close(console);
            }
            if (stdout_result == STDOUT_FILENO && stderr_result == STDERR_FILENO) {
                clearerr(stdout);
                clearerr(stderr);
                setvbuf(stdout, NULL, _IONBF, 0);
                setvbuf(stderr, NULL, _IONBF, 0);
                dprintf(console_fd, "POWER_PV_CONSOLE attempt=%u ready=1\n", attempt);
            return;
            }
        }
        if (attempt < kConsoleAttempts) {
            usleep(kConsoleRetryUsecs);
        }
    }
}

static void fatal(const char *operation, const char *detail) {
    dprintf(console_fd, "POWER_PV_FATAL operation=%s detail=%s\n", operation,
            detail ? detail : "unknown");
    exit(1);
}

static void *required_symbol(void *handle, const char *name) {
    void *symbol = dlsym(handle, name);
    if (!symbol) {
        fatal("dlsym", name);
    }
    return symbol;
}

static CFBooleanRef required_boolean(void *handle, const char *name) {
    const CFBooleanRef *storage = required_symbol(handle, name);
    if (!*storage) {
        fatal("boolean-global", name);
    }
    return *storage;
}

static void load_apis(struct APIs *apis) {
    memset(apis, 0, sizeof(*apis));
    apis->cf = dlopen(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        RTLD_NOW | RTLD_LOCAL);
    apis->iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",
                        RTLD_NOW | RTLD_LOCAL);
    if (!apis->cf || !apis->iokit) {
        fatal("dlopen", dlerror());
    }

    apis->create = required_symbol(apis->iokit, "IOPSCreatePowerSource");
    apis->set_details = required_symbol(apis->iokit, "IOPSSetPowerSourceDetails");
    apis->release_source = required_symbol(apis->iokit, "IOPSReleasePowerSource");
    apis->copy_info = required_symbol(apis->iokit, "IOPSCopyPowerSourcesInfo");
    apis->copy_list = required_symbol(apis->iokit, "IOPSCopyPowerSourcesList");
    apis->get_description =
        required_symbol(apis->iokit, "IOPSGetPowerSourceDescription");
    apis->get_percent = required_symbol(apis->iokit, "IOPSGetPercentRemaining");

    apis->dictionary_create_mutable =
        required_symbol(apis->cf, "CFDictionaryCreateMutable");
    apis->dictionary_set_value = required_symbol(apis->cf, "CFDictionarySetValue");
    apis->dictionary_get_value = required_symbol(apis->cf, "CFDictionaryGetValue");
    apis->string_create = required_symbol(apis->cf, "CFStringCreateWithCString");
    apis->number_create = required_symbol(apis->cf, "CFNumberCreate");
    apis->number_get_value = required_symbol(apis->cf, "CFNumberGetValue");
    apis->array_count = required_symbol(apis->cf, "CFArrayGetCount");
    apis->array_value = required_symbol(apis->cf, "CFArrayGetValueAtIndex");
    apis->equal = required_symbol(apis->cf, "CFEqual");
    apis->copy_description = required_symbol(apis->cf, "CFCopyDescription");
    apis->string_get_cstring = required_symbol(apis->cf, "CFStringGetCString");
    apis->cf_release = required_symbol(apis->cf, "CFRelease");
    apis->key_callbacks = required_symbol(apis->cf, "kCFTypeDictionaryKeyCallBacks");
    apis->value_callbacks = required_symbol(apis->cf, "kCFTypeDictionaryValueCallBacks");
    apis->true_value = required_boolean(apis->cf, "kCFBooleanTrue");
    apis->false_value = required_boolean(apis->cf, "kCFBooleanFalse");
}

static CFStringRef make_string(const struct APIs *apis, const char *value) {
    CFStringRef result = apis->string_create(NULL, value, kCFStringEncodingUTF8);
    if (!result) {
        fatal("CFStringCreateWithCString", value);
    }
    return result;
}

static CFNumberRef make_int(const struct APIs *apis, int value) {
    CFNumberRef result = apis->number_create(NULL, kCFNumberIntType, &value);
    if (!result) {
        fatal("CFNumberCreate", "100");
    }
    return result;
}

static void set_value(const struct APIs *apis, CFMutableDictionaryRef dictionary,
                      const char *key, const void *value) {
    CFStringRef key_ref = make_string(apis, key);
    apis->dictionary_set_value(dictionary, key_ref, value);
    apis->cf_release(key_ref);
}

static CFDictionaryRef create_details(const struct APIs *apis) {
    CFMutableDictionaryRef details = apis->dictionary_create_mutable(
        NULL, 0, apis->key_callbacks, apis->value_callbacks);
    if (!details) {
        fatal("CFDictionaryCreateMutable", "details");
    }

    CFStringRef name = make_string(apis, kName);
    CFStringRef type = make_string(apis, kType);
    CFStringRef transport = make_string(apis, kTransport);
    CFStringRef state = make_string(apis, kState);
    CFNumberRef current = make_int(apis, 100);
    CFNumberRef maximum = make_int(apis, 100);

    set_value(apis, details, "Name", name);
    set_value(apis, details, "Type", type);
    set_value(apis, details, "Transport Type", transport);
    set_value(apis, details, "Power Source State", state);
    set_value(apis, details, "Current Capacity", current);
    set_value(apis, details, "Max Capacity", maximum);
    set_value(apis, details, "Is Present", apis->true_value);
    set_value(apis, details, "Is Charging", apis->false_value);
    set_value(apis, details, "Is Charged", apis->true_value);

    apis->cf_release(name);
    apis->cf_release(type);
    apis->cf_release(transport);
    apis->cf_release(state);
    apis->cf_release(current);
    apis->cf_release(maximum);
    return details;
}

static uint32_t power_source_id(IOPSPowerSourceID source) {
    const struct IOPSPowerSourceIDLayout *layout =
        (const struct IOPSPowerSourceIDLayout *)source;
    if (!layout || layout->psid <= 0) {
        fatal("IOPSPowerSourceID", "missing psid");
    }
    return (((uint32_t)getpid() & UINT32_C(0xffff)) << 16) |
           ((uint32_t)layout->psid & UINT32_C(0xffff));
}

static Boolean dictionary_matches(const struct APIs *apis, CFDictionaryRef dictionary,
                                  CFStringRef name, CFStringRef type,
                                  CFStringRef transport, CFStringRef state,
                                  uint32_t expected_power_source_id) {
    const char *keys[] = {"Name", "Type", "Transport Type", "Power Source State"};
    const CFStringRef values[] = {name, type, transport, state};
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); i++) {
        CFStringRef key = make_string(apis, keys[i]);
        const void *actual = apis->dictionary_get_value(dictionary, key);
        apis->cf_release(key);
        if (!actual || !apis->equal(actual, values[i])) {
            return 0;
        }
    }

    const char *numeric_keys[] = {"Current Capacity", "Max Capacity"};
    for (size_t i = 0; i < sizeof(numeric_keys) / sizeof(numeric_keys[0]); i++) {
        CFStringRef key = make_string(apis, numeric_keys[i]);
        CFNumberRef number = apis->dictionary_get_value(dictionary, key);
        int actual = -1;
        apis->cf_release(key);
        if (!number || !apis->number_get_value(number, kCFNumberIntType, &actual) ||
            actual != 100) {
            return 0;
        }
    }

    const char *boolean_keys[] = {"Is Present", "Is Charging", "Is Charged"};
    const CFBooleanRef expected[] = {
        apis->true_value, apis->false_value, apis->true_value,
    };
    for (size_t i = 0; i < sizeof(boolean_keys) / sizeof(boolean_keys[0]); i++) {
        CFStringRef key = make_string(apis, boolean_keys[i]);
        const void *actual = apis->dictionary_get_value(dictionary, key);
        apis->cf_release(key);
        if (!actual || !apis->equal(actual, expected[i])) {
            return 0;
        }
    }

    CFStringRef id_key = make_string(apis, "Power Source ID");
    CFNumberRef id = apis->dictionary_get_value(dictionary, id_key);
    int32_t actual_id = -1;
    apis->cf_release(id_key);
    if (!id || !apis->number_get_value(id, kCFNumberSInt32Type, &actual_id) ||
        (uint32_t)actual_id != expected_power_source_id) {
        return 0;
    }
    return 1;
}

static void print_description(const struct APIs *apis, CFTypeRef value) {
    CFStringRef description = apis->copy_description(value);
    char buffer[kCFDescriptionSize] = {0};
    if (description && apis->string_get_cstring(description, buffer, sizeof(buffer),
                                                 kCFStringEncodingUTF8)) {
        dprintf(console_fd, "POWER_PV_SNAPSHOT description=%s\n", buffer);
    } else {
        dprintf(console_fd, "POWER_PV_SNAPSHOT description=unavailable\n");
    }
    if (description) {
        apis->cf_release(description);
    }
}

static Boolean verify_publication(const struct APIs *apis, IOPSPowerSourceID source) {
    CFStringRef name = make_string(apis, kName);
    CFStringRef type = make_string(apis, kType);
    CFStringRef transport = make_string(apis, kTransport);
    CFStringRef state = make_string(apis, kState);
    CFTypeRef snapshot = apis->copy_info();
    CFArrayRef list = snapshot ? apis->copy_list(snapshot) : NULL;
    uint32_t expected_power_source_id = power_source_id(source);
    CFIndex matches = 0;
    CFIndex count = 0;

    if (!snapshot || !list) {
        dprintf(console_fd,
                "POWER_PV_VERIFY snapshot=%p list=%p expected_id=%u matches=0\n",
                snapshot, list, expected_power_source_id);
        goto done;
    }

    count = apis->array_count(list);
    for (CFIndex index = 0; index < count; index++) {
        CFTypeRef source = apis->array_value(list, index);
        CFDictionaryRef description = apis->get_description(snapshot, source);
        if (description && dictionary_matches(apis, description, name, type, transport,
                                              state, expected_power_source_id)) {
            matches++;
            print_description(apis, description);
        }
    }
    dprintf(console_fd,
            "POWER_PV_VERIFY snapshot=%p list=%p expected_id=%u count=%ld matches=%ld\n",
            snapshot, list, expected_power_source_id, (long)count, (long)matches);

done:
    if (list) {
        apis->cf_release(list);
    }
    if (snapshot) {
        apis->cf_release(snapshot);
    }
    apis->cf_release(name);
    apis->cf_release(type);
    apis->cf_release(transport);
    apis->cf_release(state);
    /* The aggregate below belongs to this sole VM source. Do not take over
     * the system percentage if another source appears in the native list. */
    return matches == 1 && count == 1;
}

static void withdraw_percent(const char *reason) {
    if (!publisher.percent_owned || publisher.percent_token < 0) return;
    uint64_t state = 0;
    int result = notify_get_state(publisher.percent_token, &state);
    if (result == 0 && state == kPercentState) {
        result = notify_set_state(publisher.percent_token, 0);
        if (result == 0) result = notify_post(kPercentNotification);
    }
    dprintf(console_fd, "POWER_PV_PERCENT_WITHDRAW reason=%s result=%d previous=0x%llx\n",
            reason, result, (unsigned long long)state);
    publisher.percent_owned = 0;
}

static Boolean publish_percent(void) {
    uint64_t state = 0;
    int percent = -1;
    bool charging = false, full = false;
    int set_result = notify_set_state(publisher.percent_token, kPercentState);
    int post_result = set_result == 0 ? notify_post(kPercentNotification) : -1;
    int state_result = notify_get_state(publisher.percent_token, &state);
    IOReturn read_result = publisher.apis->get_percent(&percent, &charging, &full);
    publisher.percent_owned = set_result == 0 && state_result == 0 && state == kPercentState;
    Boolean valid = publisher.percent_owned && post_result == 0 &&
        read_result == kIOReturnSuccess && percent == 100 && !charging && full;
    dprintf(console_fd,
            "POWER_PV_PERCENT set=%d post=%d state_result=%d state=0x%llx read=0x%08x percent=%d charging=%d full=%d verified=%d\n",
            set_result, post_result, state_result, (unsigned long long)state,
            (unsigned)read_result, percent, charging, full, valid);
    return valid;
}

static IOReturn release_current_source(const char *reason) {
    withdraw_percent(reason);
    if (!publisher.source) {
        return kIOReturnSuccess;
    }
    IOReturn result = publisher.apis->release_source(publisher.source);
    dprintf(console_fd, "POWER_PV_RELEASE reason=%s result=0x%08x\n", reason,
            (unsigned)result);
    /* A restarted server may reject this opaque client handle; never reuse it. */
    publisher.source = NULL;
    publisher.ready = 0;
    return result;
}

static void schedule_retry(const char *reason);

/* Must run only on publisher.queue. */
static Boolean publish_source(const char *reason, Boolean retry_on_failure) {
    if (publisher.stopping || !publisher.details) {
        return 0;
    }
    if (publisher.source) {
        (void)release_current_source("superseded");
    }
    IOPSPowerSourceID source = NULL;
    IOReturn create_result = publisher.apis->create(&source);
    dprintf(console_fd, "POWER_PV_CREATE reason=%s result=0x%08x source=%p\n", reason,
            (unsigned)create_result, source);
    if (create_result != kIOReturnSuccess || !source) {
        if (source) {
            IOReturn release_result = publisher.apis->release_source(source);
            dprintf(console_fd, "POWER_PV_RELEASE reason=create-failed result=0x%08x\n",
                    (unsigned)release_result);
        }
        goto failed;
    }

    IOReturn set_result = publisher.apis->set_details(source, publisher.details);
    dprintf(console_fd, "POWER_PV_SET reason=%s result=0x%08x\n", reason,
            (unsigned)set_result);
    if (set_result == kIOReturnSuccess &&
        verify_publication(publisher.apis, source) && publish_percent()) {
        publisher.source = source;
        publisher.ready = 1;
        publisher.retry_seconds = 1;
        publisher.retry_generation++;
        dprintf(console_fd,
                "POWER_PV_READY reason=%s name=%s type=%s capacity=100/100 state=%s charging=false\n",
                reason, kName, kType, kState);
        return 1;
    }

    withdraw_percent("validation-failed");
    IOReturn release_result = publisher.apis->release_source(source);
    dprintf(console_fd, "POWER_PV_RELEASE reason=validation-failed result=0x%08x\n",
            (unsigned)release_result);

failed:
    publisher.source = NULL;
    publisher.ready = 0;
    dprintf(console_fd, "POWER_PV_UNREADY reason=%s\n", reason);
    if (retry_on_failure && !publisher.stopping) {
        schedule_retry(reason);
    }
    return 0;
}

static void retry_publish(uint64_t generation) {
    if (publisher.stopping || publisher.ready ||
        generation != publisher.retry_generation) {
        return;
    }
    (void)publish_source("retry", 1);
}

static void schedule_retry(const char *reason) {
    unsigned delay = publisher.retry_seconds ? publisher.retry_seconds : 1;
    if (delay > 30) {
        delay = 30;
    }
    publisher.retry_seconds = delay < 30 ? delay * 2 : 30;
    uint64_t generation = ++publisher.retry_generation;
    dprintf(console_fd, "POWER_PV_RETRY reason=%s delay_seconds=%u generation=%llu\n",
            reason, delay, (unsigned long long)generation);
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                 (int64_t)delay * NSEC_PER_SEC),
                   publisher.queue, ^{
        retry_publish(generation);
    });
}

static void powerd_resync(int token) {
    if (publisher.stopping || token != publisher.resync_token) {
        return;
    }
    if (!publisher.initial_publish_complete) {
        publisher.resync_pending = 1;
        dprintf(console_fd, "POWER_PV_RESYNC deferred=startup token=%d\n", token);
        return;
    }
    dprintf(console_fd, "POWER_PV_RESYNC token=%d\n", token);
    ++publisher.retry_generation;  /* supersede a pending delayed retry */
    release_current_source("powerd-resync");
    (void)publish_source("powerd-resync", 1);
}

/* Must run only on publisher.queue.  Does not return. */
static void shutdown_on_queue(int signal_number) {
    if (publisher.stopping) {
        return;
    }
    publisher.stopping = 1;
    ++publisher.retry_generation;
    if (publisher.resync_token >= 0) {
        notify_cancel(publisher.resync_token);
    }
    IOReturn release_result = release_current_source("signal");
    if (publisher.percent_token >= 0) notify_cancel(publisher.percent_token);
    int exit_status = release_result == kIOReturnSuccess ? 0 : 1;
    dprintf(console_fd,
            "POWER_PV_SHUTDOWN signal=%d release_result=0x%08x exit_status=%d\n",
            signal_number, (unsigned)release_result, exit_status);
    if (publisher.details) {
        publisher.apis->cf_release(publisher.details);
        publisher.details = NULL;
    }
    fflush(stderr);
    _exit(exit_status);
}

static void install_signal_source(int signal_number) {
    if (signal(signal_number, SIG_IGN) == SIG_ERR) {
        fatal("signal", "SIGTERM/SIGINT");
    }
    dispatch_source_t source = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL,
                                                       (uintptr_t)signal_number, 0,
                                                       publisher.queue);
    if (!source) {
        fatal("dispatch_source_create", "signal");
    }
    dispatch_source_set_event_handler(source, ^{
        shutdown_on_queue(signal_number);
    });
    dispatch_resume(source);
}

int main(void) {
    console_redirect();
    /* dispatch_main may retire the main thread; callbacks outlive its stack. */
    static struct APIs apis;
    load_apis(&apis);

    publisher.apis = &apis;
    publisher.details = create_details(&apis);
    publisher.queue = dispatch_queue_create("Darwin VM power source", NULL);
    if (!publisher.queue) {
        fatal("dispatch_queue_create", "Darwin VM power source");
    }
    publisher.resync_token = -1;
    publisher.percent_token = -1;
    int percent_result = notify_register_check(kPercentNotification, &publisher.percent_token);
    if (percent_result != 0 || publisher.percent_token < 0) {
        fatal("notify_register_check", kPercentNotification);
    }
    install_signal_source(SIGTERM);
    install_signal_source(SIGINT);
    int notify_result = notify_register_dispatch(kPowerdResyncNotification,
                                                 &publisher.resync_token,
                                                 publisher.queue, ^(int token) {
        powerd_resync(token);
    });
    dprintf(console_fd, "POWER_PV_RESYNC_REGISTER name=%s result=0x%08x token=%d\n",
            kPowerdResyncNotification, (unsigned)notify_result,
            publisher.resync_token);
    if (notify_result != 0 || publisher.resync_token < 0) {
        apis.cf_release(publisher.details);
        return 1;
    }

    __block Boolean initially_ready = 0;
    dispatch_sync(publisher.queue, ^{
        initially_ready = publish_source("initial", 0);
        publisher.initial_publish_complete = 1;
        if (initially_ready && publisher.resync_pending) {
            publisher.resync_pending = 0;
            powerd_resync(publisher.resync_token);
            initially_ready = publisher.ready;
        }
    });
    if (!initially_ready) {
        /* Serialize stopping and drain an already queued notify callback before
         * releasing the immutable dictionary it could otherwise dereference. */
        dispatch_sync(publisher.queue, ^{
            publisher.stopping = 1;
            ++publisher.retry_generation;
            (void)release_current_source("initial-failed");
        });
        notify_cancel(publisher.resync_token);
        dispatch_sync(publisher.queue, ^{});
        apis.cf_release(publisher.details);
        publisher.details = NULL;
        return 1;
    }

    dispatch_main();
}
