/* Read-only 24A5430a activation-state probe.
 *
 * This intentionally calls only query APIs.  It neither creates an activation
 * request nor writes a record, preference, DataArk value, or network request.
 * Results go to /dev/console so the launch context need not preserve stdio.
 *
 * ABI evidence: docs/re/setup-activation-contract.md and
 * /tmp/dvm/SETUP_ACTIVATION1/MobileActivation.otool.txt show
 * MAEGetActivationStateWithError(CFErrorRef *) and MAEGetBrickState(void).
 * The extracted liblockdown disassembly shows both copy functions enter with
 * no argument before calling lockdown_connect and send_get_value.
 * MobileKeyBag's MKBGetDeviceLockState(NULL) returns selector-17's `ls` word;
 * MKBDeviceUnlockedSinceBoot(void) returns selector-7's first-unlock bit.
 */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

extern int sysctlbyname(const char *name, void *oldp, size_t *oldlenp,
                        void *newp, size_t newlen);

/* Keep CoreFoundation dynamically loaded, like the private query frameworks.
 * This permits a plain iOS-targeted link without importing host SDK stubs. */
typedef const void *CFTypeRef;
typedef const void *CFStringRef;
typedef const void *CFErrorRef;
typedef long CFIndex;
typedef unsigned long CFTypeID;
typedef unsigned int CFStringEncoding;
typedef unsigned int IOOptionBits;
typedef unsigned int io_registry_entry_t;
enum {
    DVM_CF_STRING_ENCODING_UTF8 = 0x08000100,
    DVM_CF_NUMBER_SINT32 = 3,
};

typedef CFStringRef (*mae_copy_activation_state_fn)(CFErrorRef *error);
typedef bool (*mae_get_brick_state_fn)(void);
typedef CFTypeRef (*lockdown_copy_value_fn)(void);
typedef int (*mkb_get_device_lock_state_fn)(const void *options);
typedef bool (*mkb_device_unlocked_since_boot_fn)(void);
static CFStringRef (*cf_string_create_with_cstring)(CFTypeRef, const char *,
                                                     CFStringEncoding);
static CFStringRef (*cf_copy_description)(CFTypeRef);
static bool (*cf_string_get_cstring)(CFStringRef, char *, CFIndex, CFStringEncoding);
static CFTypeID (*cf_get_type_id)(CFTypeRef);
static CFTypeID (*cf_data_get_type_id)(void);
static CFIndex (*cf_data_get_length)(CFTypeRef);
static const uint8_t *(*cf_data_get_byte_ptr)(CFTypeRef);
static CFTypeID (*cf_number_get_type_id)(void);
static bool (*cf_number_get_value)(CFTypeRef, int, void *);
static void (*cf_release)(CFTypeRef);

/* Keep a descriptor independent of stdout/stderr: framework initialization can
 * alter stdio state, but it cannot redirect this already-open console handle. */
static int console_log_fd = -1;

static void probe_log_printf(const char *format, ...)
{
    va_list arguments;

    if (console_log_fd < 0) {
        return;
    }
    va_start(arguments, format);
    (void)vdprintf(console_log_fd, format, arguments);
    va_end(arguments);
}

static void console_stdio(void) {
    int console = open("/dev/console", O_WRONLY | O_NOCTTY);
    int retained;

    if (console < 0) {
        dprintf(STDERR_FILENO,
                "DVM_ACTIVATION_PROBE_ERROR open-console errno=%d\n", errno);
        exit(2);
    }
    retained = fcntl(console, F_DUPFD, STDERR_FILENO + 1);
    if (retained < 0) {
        dprintf(console, "DVM_ACTIVATION_PROBE_ERROR retain-console errno=%d\n",
                errno);
        close(console);
        exit(2);
    }
    if (retained != console) {
        close(console);
    }
    console_log_fd = retained;
    if (dup2(console_log_fd, STDOUT_FILENO) < 0 ||
        dup2(console_log_fd, STDERR_FILENO) < 0) {
        probe_log_printf("DVM_ACTIVATION_PROBE_ERROR dup2-console errno=%d\n",
                         errno);
        close(console_log_fd);
        console_log_fd = -1;
        exit(2);
    }
    setvbuf(stdout, NULL, _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);
}

static void print_cf_description(const char *field, CFTypeRef value) {
    if (!value) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=<null>\n", field);
        return;
    }
    CFStringRef description = cf_copy_description(value);
    char text[2048];
    if (description && cf_string_get_cstring(description, text, sizeof(text),
                                              DVM_CF_STRING_ENCODING_UTF8)) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=%s\n", field, text);
    } else {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=<description-unavailable>\n", field);
    }
    if (description) cf_release(description);
}

static void *open_required(const char *path) {
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!handle) {
        probe_log_printf("DVM_ACTIVATION_PROBE_ERROR dlopen=%s error=%s\n", path, dlerror());
        exit(1);
    }
    return handle;
}

static void *required_symbol(void *handle, const char *name) {
    dlerror();
    void *result = dlsym(handle, name);
    const char *error = dlerror();
    if (error || !result) {
        probe_log_printf("DVM_ACTIVATION_PROBE_ERROR dlsym=%s error=%s\n", name,
               error ? error : "missing");
        exit(1);
    }
    return result;
}

static void *optional_symbol(void *handle, const char *name)
{
    dlerror();
    return dlsym(handle, name);
}

static void print_environment_value(const char *field, CFTypeRef value)
{
    CFStringRef description;
    char text[1024];

    if (!value) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=<absent>\n", field);
        return;
    }
    if (cf_get_type_id && cf_data_get_type_id && cf_data_get_length &&
        cf_data_get_byte_ptr && cf_get_type_id(value) == cf_data_get_type_id()) {
        CFIndex length = cf_data_get_length(value);
        const uint8_t *bytes = cf_data_get_byte_ptr(value);
        probe_log_printf("DVM_ACTIVATION_PROBE %s.type=CFData len=%ld raw=", field, length);
        if (!bytes && length) {
            probe_log_printf("<unavailable>\n");
            return;
        }
        for (CFIndex i = 0; i < length; i++) {
            probe_log_printf("%02x", bytes[i]);
        }
        if (length == 4) {
            uint32_t little = (uint32_t)bytes[0] | (uint32_t)bytes[1] << 8 |
                              (uint32_t)bytes[2] << 16 | (uint32_t)bytes[3] << 24;
            probe_log_printf(" le32=%u", little);
        }
        probe_log_printf("\n");
        return;
    }
    if (cf_get_type_id && cf_number_get_type_id && cf_number_get_value &&
        cf_get_type_id(value) == cf_number_get_type_id()) {
        int32_t number = 0;
        if (cf_number_get_value(value, DVM_CF_NUMBER_SINT32, &number)) {
            probe_log_printf("DVM_ACTIVATION_PROBE %s.type=CFNumber sint32=%d\n", field,
                   number);
            return;
        }
    }
    description = cf_copy_description(value);
    if (description && cf_string_get_cstring(description, text, sizeof(text),
                                              DVM_CF_STRING_ENCODING_UTF8)) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s.type=other description=%s\n", field, text);
    } else {
        probe_log_printf("DVM_ACTIVATION_PROBE %s.type=other\n", field);
    }
    if (description) {
        cf_release(description);
    }
}

static void print_device_tree_property(const char *path, const char *key)
{
    void *iokit = dlopen("/System/Library/Frameworks/IOKit.framework/IOKit",
                         RTLD_NOW | RTLD_LOCAL);
    io_registry_entry_t (*entry_from_path)(unsigned int, const char *);
    CFTypeRef (*copy_property)(io_registry_entry_t, CFStringRef, CFTypeRef,
                               IOOptionBits);
    int (*object_release)(io_registry_entry_t);
    io_registry_entry_t entry;
    CFStringRef cf_key;
    CFTypeRef value;
    char field[160];

    snprintf(field, sizeof(field), "ioreg.%s.%s", path, key);
    if (!iokit) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=<iokit-unavailable>\n", field);
        return;
    }
    entry_from_path = (io_registry_entry_t (*)(unsigned int, const char *))
        optional_symbol(iokit, "IORegistryEntryFromPath");
    copy_property = (CFTypeRef (*)(io_registry_entry_t, CFStringRef, CFTypeRef,
                                   IOOptionBits))
        optional_symbol(iokit, "IORegistryEntryCreateCFProperty");
    object_release = (int (*)(io_registry_entry_t))optional_symbol(iokit,
        "IOObjectRelease");
    if (!entry_from_path || !copy_property || !object_release ||
        !cf_string_create_with_cstring) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=<iokit-symbol-unavailable>\n", field);
        dlclose(iokit);
        return;
    }
    entry = entry_from_path(0, path);
    if (!entry) {
        probe_log_printf("DVM_ACTIVATION_PROBE %s=<entry-missing>\n", field);
        dlclose(iokit);
        return;
    }
    cf_key = cf_string_create_with_cstring(NULL, key, DVM_CF_STRING_ENCODING_UTF8);
    value = cf_key ? copy_property(entry, cf_key, NULL, 0) : NULL;
    print_environment_value(field, value);
    if (value) cf_release(value);
    if (cf_key) cf_release(cf_key);
    object_release(entry);
    dlclose(iokit);
}

static void print_kernel_boot_args(void)
{
    char boot_args[0x400];
    size_t length = sizeof(boot_args) - 1;
    int result;

    memset(boot_args, 0, sizeof(boot_args));
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.kern-bootargs.begin\n");
    errno = 0;
    result = sysctlbyname("kern.bootargs", boot_args, &length, NULL, 0);
    if (result == 0) {
        /* Reserve one byte for a terminator even if the kernel filled its
         * reported buffer length.  The native method supplies 0x400 bytes. */
        size_t printable = length < sizeof(boot_args) ? length : sizeof(boot_args) - 1;
        boot_args[printable] = '\0';
        probe_log_printf("DVM_ACTIVATION_PROBE sysctl.kern.bootargs.len=%zu value=%s\n",
               length, boot_args);
    } else {
        probe_log_printf("DVM_ACTIVATION_PROBE sysctl.kern.bootargs=<error> errno=%d\n",
               errno);
    }
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.kern-bootargs.returned\n");
}

static void activation_environment_report(void)
{
    void *foundation;
    void *objc;
    void *(*objc_get_class)(const char *);
    void *(*objc_alloc)(void *);
    void *(*objc_sel_register_name)(const char *);
    void *(*objc_msg_send_object)(void *, void *, CFTypeRef);
    void *defaults_class;
    void *defaults;
    void *domain_dictionary;
    void *preference;
    void *persistent_domain_selector;
    void *object_subscript_selector;
    CFStringRef key;
    CFStringRef domain;
    int marker;
    int marker_errno;

    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.begin uid=%u euid=%u\n",
           (unsigned)getuid(), (unsigned)geteuid());
    print_device_tree_property("IODeviceTree:/product", "allow-hactivation");
    print_device_tree_property("IODeviceTree:/chosen", "enable-avp-fairplay");
    print_kernel_boot_args();
    errno = 0;
    marker = access("/AppleInternal/Lockdown/.hactivateoff", F_OK);
    marker_errno = errno;
    probe_log_printf("DVM_ACTIVATION_PROBE marker.hactivateoff.exists=%d errno=%d\n",
           marker == 0 ? 1 : 0, marker == 0 ? 0 : marker_errno);

    /* This exactly follows mobileactivationd DeviceType init at
     * 0x1002ebc34..0x1002ebcb8: objc_alloc(NSUserDefaults),
     * persistentDomainForName:, then objectForKeyedSubscript:.  The result is
     * necessarily the probe process's user preference domain; uid/euid above
     * identify whether it is the same user context as mobileactivationd. */
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.foundation-dlopen.begin\n");
    foundation = dlopen("/System/Library/Frameworks/Foundation.framework/Foundation",
                        RTLD_NOW | RTLD_LOCAL);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.foundation-dlopen.returned available=%d\n",
           foundation ? 1 : 0);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.objc-dlopen.begin\n");
    objc = dlopen("/usr/lib/libobjc.A.dylib", RTLD_NOW | RTLD_LOCAL);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.objc-dlopen.returned available=%d\n",
           objc ? 1 : 0);
    if (!foundation || !objc) {
        probe_log_printf("DVM_ACTIVATION_PROBE preferences.mobileactivationd.DisableHactivation=<runtime-unavailable>\n");
        if (foundation) dlclose(foundation);
        if (objc) dlclose(objc);
        probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.returned\n");
        return;
    }
    objc_get_class = (void *(*)(const char *))optional_symbol(objc, "objc_getClass");
    objc_alloc = (void *(*)(void *))optional_symbol(objc, "objc_alloc");
    objc_sel_register_name = (void *(*)(const char *))optional_symbol(objc,
        "sel_registerName");
    objc_msg_send_object = (void *(*)(void *, void *, CFTypeRef))optional_symbol(objc,
        "objc_msgSend");
    defaults_class = objc_get_class ? objc_get_class("NSUserDefaults") : NULL;
    persistent_domain_selector = objc_sel_register_name ?
        objc_sel_register_name("persistentDomainForName:") : NULL;
    object_subscript_selector = objc_sel_register_name ?
        objc_sel_register_name("objectForKeyedSubscript:") : NULL;
    key = cf_string_create_with_cstring(NULL, "DisableHactivation",
                                        DVM_CF_STRING_ENCODING_UTF8);
    domain = cf_string_create_with_cstring(NULL, "com.apple.mobileactivationd",
                                           DVM_CF_STRING_ENCODING_UTF8);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.objc-alloc.begin\n");
    defaults = (objc_alloc && defaults_class) ? objc_alloc(defaults_class) : NULL;
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.objc-alloc.returned available=%d\n",
           defaults ? 1 : 0);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.persistent-domain.begin\n");
    domain_dictionary = (defaults && persistent_domain_selector && domain &&
                         objc_msg_send_object) ?
        objc_msg_send_object(defaults, persistent_domain_selector, domain) : NULL;
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.persistent-domain.returned available=%d\n",
           domain_dictionary ? 1 : 0);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.dictionary-subscript.begin\n");
    preference = (domain_dictionary && object_subscript_selector && key &&
                  objc_msg_send_object) ?
        objc_msg_send_object(domain_dictionary, object_subscript_selector, key) : NULL;
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.dictionary-subscript.returned available=%d\n",
           preference ? 1 : 0);
    probe_log_printf("DVM_ACTIVATION_PROBE preferences.scope=NSUserDefaults.persistent-domain probe-user\n");
    print_environment_value("preferences.mobileactivationd.DisableHactivation",
                            preference);
    if (key) cf_release(key);
    if (domain) cf_release(domain);
    dlclose(objc);
    dlclose(foundation);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE environment.returned\n");
}

int main(void) {
    console_stdio();
    probe_log_printf("DVM_ACTIVATION_PROBE_BEGIN version=3 mode=read-only\n");

    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE corefoundation.load\n");
    void *core_foundation = open_required(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation");
    cf_string_create_with_cstring = (CFStringRef (*)(CFTypeRef, const char *,
                                                       CFStringEncoding))
        required_symbol(core_foundation, "CFStringCreateWithCString");
    cf_copy_description = (CFStringRef (*)(CFTypeRef))
        required_symbol(core_foundation, "CFCopyDescription");
    cf_string_get_cstring = (bool (*)(CFStringRef, char *, CFIndex, CFStringEncoding))
        required_symbol(core_foundation, "CFStringGetCString");
    cf_get_type_id = (CFTypeID (*)(CFTypeRef))optional_symbol(core_foundation,
        "CFGetTypeID");
    cf_data_get_type_id = (CFTypeID (*)(void))optional_symbol(core_foundation,
        "CFDataGetTypeID");
    cf_data_get_length = (CFIndex (*)(CFTypeRef))optional_symbol(core_foundation,
        "CFDataGetLength");
    cf_data_get_byte_ptr = (const uint8_t *(*)(CFTypeRef))optional_symbol(
        core_foundation, "CFDataGetBytePtr");
    cf_number_get_type_id = (CFTypeID (*)(void))optional_symbol(core_foundation,
        "CFNumberGetTypeID");
    cf_number_get_value = (bool (*)(CFTypeRef, int, void *))optional_symbol(
        core_foundation, "CFNumberGetValue");
    cf_release = (void (*)(CFTypeRef))required_symbol(core_foundation, "CFRelease");
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE corefoundation.ready\n");

    /* These local reads execute before MKB and every activation/lockdown XPC
     * query. The preference scope is the probe's current user; compare the
     * printed uid with mobileactivationd before treating it as daemon state. */
    activation_environment_report();

    /* MKB is intentionally first: a MobileActivation XPC query can block
     * while its daemon starts, but the KeyBag observations are independently
     * useful to the passcode/first-unlock diagnosis. */
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mobilekeybag.load\n");
    void *mobile_keybag = open_required(
        "/System/Library/PrivateFrameworks/MobileKeyBag.framework/MobileKeyBag");
    mkb_get_device_lock_state_fn mkb_lock_state = (mkb_get_device_lock_state_fn)
        required_symbol(mobile_keybag, "MKBGetDeviceLockState");
    mkb_device_unlocked_since_boot_fn mkb_first_unlock = (mkb_device_unlocked_since_boot_fn)
        required_symbol(mobile_keybag, "MKBDeviceUnlockedSinceBoot");
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mobilekeybag.ready\n");
    /* MKBGetDeviceLockState enters __get_device_lock_state with x0 supplied
     * by its public options argument.  NULL follows the documented no-options
     * query path; it performs only AppleKeyStore selector-17 state retrieval.
     */
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mkb.device-lock-state.begin\n");
    int lock_state = mkb_lock_state(NULL);
    probe_log_printf("DVM_ACTIVATION_PROBE mkb.device-lock-state=%d\n", lock_state);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mkb.device-lock-state.returned\n");
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mkb.unlocked-since-boot.begin\n");
    bool first_unlock = mkb_first_unlock();
    probe_log_printf("DVM_ACTIVATION_PROBE mkb.unlocked-since-boot=%d\n", first_unlock ? 1 : 0);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mkb.unlocked-since-boot.returned\n");

    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mobileactivation.load\n");
    void *mobile_activation = open_required(
        "/System/Library/PrivateFrameworks/MobileActivation.framework/MobileActivation");
    mae_copy_activation_state_fn mae_state = (mae_copy_activation_state_fn)
        required_symbol(mobile_activation, "MAEGetActivationStateWithError");
    mae_get_brick_state_fn mae_brick = (mae_get_brick_state_fn)
        required_symbol(mobile_activation, "MAEGetBrickState");
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mobileactivation.ready\n");

    /* lockdownd passes NULL at 0x10002a890 in the extracted 24A5430a binary.
     * Supplying a valid out pointer is the same ABI and lets this diagnostic
     * report any CFError without making another activation-side call.
     * The wrapper retains its returned state at 0x28f91ffe4, so that object
     * is +1 and is released below.  The extracted code does not establish an
     * ownership transfer for the NSError-style out parameter; retain neither
     * nor release it in this short-lived diagnostic.
     */
    CFErrorRef error = NULL;
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mae.activation-state.begin\n");
    CFStringRef state = mae_state(&error);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mae.activation-state.returned\n");
    print_cf_description("mae.activation-state", state);
    print_cf_description("mae.activation-error", error);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mae.brick-state.begin\n");
    bool brick_state = mae_brick();
    probe_log_printf("DVM_ACTIVATION_PROBE mae.brick-state=%d\n", brick_state ? 1 : 0);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE mae.brick-state.returned\n");
    if (state) cf_release(state);

    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE lockdown.load\n");
    void *lockdown = open_required("/usr/lib/liblockdown.dylib");
    lockdown_copy_value_fn lockdown_state = (lockdown_copy_value_fn)
        required_symbol(lockdown, "lockdown_copy_activationState");
    lockdown_copy_value_fn lockdown_brick = (lockdown_copy_value_fn)
        required_symbol(lockdown, "lockdown_copy_brickState");
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE lockdown.ready\n");
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE lockdown.activation-state.begin\n");
    CFTypeRef lockdown_state_value = lockdown_state();
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE lockdown.activation-state.returned\n");
    print_cf_description("lockdown.activation-state", lockdown_state_value);
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE lockdown.brick-state.begin\n");
    CFTypeRef lockdown_brick_value = lockdown_brick();
    probe_log_printf("DVM_ACTIVATION_PROBE_STAGE lockdown.brick-state.returned\n");
    print_cf_description("lockdown.brick-state", lockdown_brick_value);
    if (lockdown_state_value) cf_release(lockdown_state_value);
    if (lockdown_brick_value) cf_release(lockdown_brick_value);

    /* Keep query frameworks resident until process exit.  This avoids making
     * lifetime assumptions about an error returned through the borrowed out
     * pointer and mirrors ordinary one-shot diagnostic process behavior.
     */
    probe_log_printf("DVM_ACTIVATION_PROBE_END\n");
    return 0;
}
