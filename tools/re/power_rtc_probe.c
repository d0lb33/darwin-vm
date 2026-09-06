/*
 * One-shot native RTC and power-source inspection helper for the iOS guest.
 *
 * This program only queries public libSystem, CoreFoundation, and IOKit entry
 * points through dlsym. It neither opens an IOService nor sets a property.
 *
 * Output is line-oriented with the POWER_RTC_PROBE_ prefix so a launchd serial
 * capture can distinguish an unavailable source from a missing probe.
 *
 * It reports:
 *   - time(3) and gettimeofday(2);
 *   - the full native IOPS snapshot, list, and each source description;
 *   - the exact DeviceTree provider path for charger,passthrough;
 *   - name-matched provider entries; and
 *   - class-matched AppleARMPassthroughPowerSource and AppleARMPMUPowerSource
 *     entries, including their public IORegistry property dictionaries;
 *   - class-matched AppleSmartBattery / AppleSmartBatteryPack /
 *     AppleSmartBatteryManager entries (the SMC-backed battery chain).
 *
 * No ExternalConnected property is supplied or inferred. The accompanying DT
 * experiment therefore tests native matching before an ADT-to-OSBoolean
 * encoding is established.
 */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

typedef const void *CFTypeRef;
typedef const void *CFStringRef;
typedef const void *CFArrayRef;
typedef void *CFMutableDictionaryRef;
typedef long CFIndex;
typedef unsigned char Boolean;

typedef uint32_t kern_return_t;
typedef uint32_t mach_port_t;
typedef uint32_t io_object_t;
typedef uint32_t io_iterator_t;

enum {
    kCFStringEncodingUTF8 = 0x08000100,
    kIOMainPortDefault = 0,
    kIONameSize = 128,
    kIOPathSize = 512,
    kCFDescriptionSize = 65536,
};

struct APIs {
    void *cf;
    void *iokit;
    CFTypeRef (*ps_copy_info)(void);
    CFArrayRef (*ps_copy_list)(CFTypeRef);
    CFTypeRef (*ps_get_description)(CFTypeRef, CFTypeRef);
    CFIndex (*cf_array_count)(CFArrayRef);
    const void *(*cf_array_value)(CFArrayRef, CFIndex);
    CFStringRef (*cf_copy_description)(CFTypeRef);
    Boolean (*cf_string_get_cstring)(CFStringRef, char *, CFIndex, uint32_t);
    void (*cf_release)(CFTypeRef);
    io_object_t (*registry_entry_from_path)(mach_port_t, const char *);
    kern_return_t (*registry_entry_get_name)(io_object_t, char *);
    kern_return_t (*object_get_class)(io_object_t, char *);
    kern_return_t (*registry_entry_get_path)(io_object_t, const char *, char *);
    kern_return_t (*registry_entry_create_properties)(
        io_object_t, CFMutableDictionaryRef *, CFTypeRef, uint32_t);
    CFMutableDictionaryRef (*service_name_matching)(const char *);
    CFMutableDictionaryRef (*service_matching)(const char *);
    kern_return_t (*service_get_matching_services)(
        mach_port_t, CFMutableDictionaryRef, io_iterator_t *);
    io_object_t (*iterator_next)(io_iterator_t);
    kern_return_t (*object_release)(io_object_t);
};

static void report_error(const char *operation, const char *detail) {
    fprintf(stderr, "POWER_RTC_PROBE_ERROR operation=%s detail=%s\n", operation,
            detail ? detail : "unknown");
}

static void *required_symbol(void *handle, const char *name) {
    void *symbol = dlsym(handle, name);
    if (!symbol) {
        report_error("dlsym", name);
        exit(1);
    }
    return symbol;
}

static void load_apis(struct APIs *apis) {
    memset(apis, 0, sizeof(*apis));
    apis->cf = dlopen(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        RTLD_NOW | RTLD_LOCAL);
    apis->iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",
                        RTLD_NOW | RTLD_LOCAL);
    if (!apis->cf || !apis->iokit) {
        report_error("dlopen", dlerror() ? dlerror() : "CoreFoundation or IOKit unavailable");
        exit(1);
    }

    apis->ps_copy_info = required_symbol(apis->iokit, "IOPSCopyPowerSourcesInfo");
    apis->ps_copy_list = required_symbol(apis->iokit, "IOPSCopyPowerSourcesList");
    apis->ps_get_description =
        required_symbol(apis->iokit, "IOPSGetPowerSourceDescription");
    apis->cf_array_count = required_symbol(apis->cf, "CFArrayGetCount");
    apis->cf_array_value = required_symbol(apis->cf, "CFArrayGetValueAtIndex");
    apis->cf_copy_description = required_symbol(apis->cf, "CFCopyDescription");
    apis->cf_string_get_cstring = required_symbol(apis->cf, "CFStringGetCString");
    apis->cf_release = required_symbol(apis->cf, "CFRelease");

    apis->registry_entry_from_path =
        required_symbol(apis->iokit, "IORegistryEntryFromPath");
    apis->registry_entry_get_name =
        required_symbol(apis->iokit, "IORegistryEntryGetName");
    apis->object_get_class = required_symbol(apis->iokit, "IOObjectGetClass");
    apis->registry_entry_get_path =
        required_symbol(apis->iokit, "IORegistryEntryGetPath");
    apis->registry_entry_create_properties =
        required_symbol(apis->iokit, "IORegistryEntryCreateCFProperties");
    apis->service_name_matching = required_symbol(apis->iokit, "IOServiceNameMatching");
    apis->service_matching = required_symbol(apis->iokit, "IOServiceMatching");
    apis->service_get_matching_services =
        required_symbol(apis->iokit, "IOServiceGetMatchingServices");
    apis->iterator_next = required_symbol(apis->iokit, "IOIteratorNext");
    apis->object_release = required_symbol(apis->iokit, "IOObjectRelease");
}

static void print_cf_description(const struct APIs *apis, const char *kind,
                                 CFTypeRef value) {
    if (!value) {
        fprintf(stderr, "POWER_RTC_PROBE_%s value=NULL\n", kind);
        return;
    }

    CFStringRef description = apis->cf_copy_description(value);
    static char buffer[kCFDescriptionSize];
    if (!description ||
        !apis->cf_string_get_cstring(description, buffer, sizeof(buffer),
                                     kCFStringEncodingUTF8)) {
        fprintf(stderr, "POWER_RTC_PROBE_%s value=description-unavailable\n", kind);
    } else {
        fprintf(stderr, "POWER_RTC_PROBE_%s value=%s\n", kind, buffer);
    }
    if (description) {
        apis->cf_release(description);
    }
}

static void report_time(void) {
    struct timeval value = {0};
    errno = 0;
    int gettimeofday_result = gettimeofday(&value, NULL);
    int gettimeofday_errno = errno;
    time_t epoch = time(NULL);

    fprintf(stderr,
            "POWER_RTC_PROBE_TIME time_epoch=%" PRIdMAX
            " gettimeofday_result=%d gettimeofday_errno=%d tv_sec=%" PRIdMAX
            " tv_usec=%d\n",
            (intmax_t)epoch, gettimeofday_result, gettimeofday_errno,
            (intmax_t)value.tv_sec, (int)value.tv_usec);
}

static void report_power_sources(const struct APIs *apis) {
    CFTypeRef snapshot = apis->ps_copy_info();
    if (!snapshot) {
        report_error("IOPSCopyPowerSourcesInfo", "returned-null");
        return;
    }

    CFArrayRef sources = apis->ps_copy_list(snapshot);
    if (!sources) {
        report_error("IOPSCopyPowerSourcesList", "returned-null");
        apis->cf_release(snapshot);
        return;
    }

    CFIndex count = apis->cf_array_count(sources);
    fprintf(stderr, "POWER_RTC_PROBE_IOPS count=%ld\n", count);
    print_cf_description(apis, "IOPS_SNAPSHOT", snapshot);
    for (CFIndex index = 0; index < count; index++) {
        CFTypeRef source = apis->cf_array_value(sources, index);
        CFTypeRef description = apis->ps_get_description(snapshot, source);
        fprintf(stderr, "POWER_RTC_PROBE_IOPS_SOURCE index=%ld description=%p\n",
                index, description);
        print_cf_description(apis, "IOPS_SOURCE", description);
    }

    apis->cf_release(sources);
    apis->cf_release(snapshot);
}

static void report_entry(const struct APIs *apis, const char *query,
                         io_object_t entry) {
    char name[kIONameSize] = {0};
    char class_name[kIONameSize] = {0};
    char service_path[kIOPathSize] = {0};
    kern_return_t name_result = apis->registry_entry_get_name(entry, name);
    kern_return_t class_result = apis->object_get_class(entry, class_name);
    kern_return_t path_result =
        apis->registry_entry_get_path(entry, "IOService", service_path);
    fprintf(stderr,
            "POWER_RTC_PROBE_REGISTRY_ENTRY query=%s entry=%u name_result=%d "
            "name=%s class_result=%d class=%s path_result=%d path=%s\n",
            query, entry, (int)name_result, name_result ? "" : name,
            (int)class_result, class_result ? "" : class_name, (int)path_result,
            path_result ? "" : service_path);

    CFMutableDictionaryRef properties = NULL;
    kern_return_t properties_result =
        apis->registry_entry_create_properties(entry, &properties, NULL, 0);
    fprintf(stderr,
            "POWER_RTC_PROBE_REGISTRY_PROPERTIES query=%s entry=%u result=%d\n",
            query, entry, (int)properties_result);
    if (!properties_result && properties) {
        print_cf_description(apis, "REGISTRY_PROPERTIES", properties);
        apis->cf_release(properties);
    }
}

static void report_dt_provider(const struct APIs *apis) {
    const char *path = "IODeviceTree:/arm-io/charger,passthrough";
    io_object_t entry = apis->registry_entry_from_path(kIOMainPortDefault, path);
    fprintf(stderr, "POWER_RTC_PROBE_DT_PROVIDER path=%s entry=%u\n", path, entry);
    if (entry) {
        report_entry(apis, "dt-path", entry);
        apis->object_release(entry);
    }
}

static void report_matching(const struct APIs *apis, const char *query,
                            CFMutableDictionaryRef matching) {
    if (!matching) {
        report_error(query, "matching-dictionary-null");
        return;
    }

    io_iterator_t iterator = 0;
    kern_return_t result = apis->service_get_matching_services(
        kIOMainPortDefault, matching, &iterator);
    fprintf(stderr,
            "POWER_RTC_PROBE_MATCH query=%s result=%d iterator=%u\n",
            query, (int)result, iterator);
    if (result || !iterator) {
        return;
    }

    unsigned count = 0;
    for (;;) {
        io_object_t entry = apis->iterator_next(iterator);
        if (!entry) {
            break;
        }
        count++;
        report_entry(apis, query, entry);
        apis->object_release(entry);
    }
    fprintf(stderr, "POWER_RTC_PROBE_MATCH_COUNT query=%s count=%u\n", query, count);
    apis->object_release(iterator);
}

/* Launchd's configured stdio can be absent on this restore image. Route both
 * streams to the serial-backed console before the first diagnostic marker.
 * Four short attempts cover console-device publication without an unbounded
 * startup wait. */
static int route_stdio_to_console(void) {
    for (unsigned attempt = 1; attempt <= 4; attempt++) {
        int fd = open("/dev/console", O_WRONLY);
        if (fd >= 0 && dup2(fd, STDOUT_FILENO) >= 0 && dup2(fd, STDERR_FILENO) >= 0) {
            if (fd > STDERR_FILENO) close(fd);
            setvbuf(stdout, NULL, _IOLBF, 0);
            setvbuf(stderr, NULL, _IONBF, 0);
            fprintf(stderr, "POWER_RTC_PROBE_CONSOLE attempt=%u ready=1\n", attempt);
            return 0;
        }
        if (fd > STDERR_FILENO) close(fd);
        if (attempt < 4) {
            usleep(250000);
        }
    }
    return -1;
}

int main(void) {
    struct APIs apis;
    if (route_stdio_to_console()) {
        return 2;
    }
    fprintf(stderr, "POWER_RTC_PROBE_BEGIN\n");
    report_time();
    load_apis(&apis);
    report_power_sources(&apis);
    report_dt_provider(&apis);
    report_matching(&apis, "service-name:charger,passthrough",
                    apis.service_name_matching("charger,passthrough"));
    report_matching(&apis, "class:AppleARMPassthroughPowerSource",
                    apis.service_matching("AppleARMPassthroughPowerSource"));
    report_matching(&apis, "class:AppleARMPMUPowerSource",
                    apis.service_matching("AppleARMPMUPowerSource"));
    /* The SMC-backed battery chain (docs/re/native-battery-smc.md): the
     * IOPMPowerSource subclass powerd reads, its pack, and the manager. */
    report_matching(&apis, "class:AppleSmartBattery",
                    apis.service_matching("AppleSmartBattery"));
    report_matching(&apis, "class:AppleSmartBatteryPack",
                    apis.service_matching("AppleSmartBatteryPack"));
    report_matching(&apis, "class:AppleSmartBatteryManager",
                    apis.service_matching("AppleSmartBatteryManager"));
    sleep(1);
    report_time();
    fprintf(stderr, "POWER_RTC_PROBE_END\n");
    return 0;
}
