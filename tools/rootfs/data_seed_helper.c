/*
 * Populate a freshly guest-formatted APFS Data volume from the System-volume
 * /private/var template.  This program runs only in the iOS restore guest;
 * the host never mounts or writes the filesystems involved.
 *
 * API evidence, iOS 27 restore ramdisk (24A5430a): mobile_obliterator calls
 * aks_bootstrap_fs(data, 2) at 0x100006d54..60, then
 * MKBKeyBagCreateSystemWithACM(NULL, data) at 0x100006dd4..e0, before its
 * content restore at 0x100006e4c..80.  /sbin/mount calls
 * UMLCreatePrimaryUserLayout(CFSTR("/"), CFSTR("/private/var/mobile"), 0,
 * &error) at 0x1000042b4..cc.  --full preserves that observed default.
 * --full-aks-setup is the separate, explicitly labelled experiment for the
 * reverse-engineered withAKSSetup option bit.  See docs/re/data-volume.md.
 *
 * The source must be the System volume mounted read-only at
 * /private/var/hardware; it is not caller-selectable.  Data must be mounted
 * at /private/var.  Those exact paths make a typo fail before a private API
 * or a write can run.  Do NOT add mobile_obliterator --init here: that mode
 * is destructive and is explicitly outside this helper's design.
 */
#include <copyfile.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/attr.h>
#include <sys/mount.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

#define DATA_MOUNT "/private/var"
#define SOURCE_ROOT "/private/var/hardware/private/var"
#define PROBE_DIR "/private/var/.dvm-data-seed-probe"
#define STAGING_DIR "/private/var/.dvm-data-seed"
#define STAGING_HELPER "/private/var/.dvm-data-seed/data_seed_helper"
#define COMPLETE_MARKER "/private/var/.dvm-data-seed-complete"
#define USER_MOUNT "/private/var/hardware"
#define USER_DEVICE "/dev/disk1s5"
#define SYSTEM_MOUNT "/private/var"
#define SYSTEMBAG_PATH DATA_MOUNT "/keybags/systembag.kb"
#define SYSTEMBAG_WRITING_PATH DATA_MOUNT "/keybags/systembag.kb.writing"
#define TIMEZONE_DIR_REL "db/timezone"
#define TIMEZONE_LOCALTIME_REL TIMEZONE_DIR_REL "/localtime"
#define CF_STRING_ENCODING_UTF8 0x08000100U
#define DIAG_XATTR_FLAGS (XATTR_NOFOLLOW | XATTR_SHOWCOMPRESSION)

typedef const void *CFStringRef;
typedef void *CFErrorRef;
typedef unsigned long CFOptionFlags;
typedef int (*aks_bootstrap_fs_fn)(const char *, int);
typedef int (*mkb_create_fn)(void *, const char *);
typedef CFStringRef (*cf_string_fn)(const void *, const char *, unsigned int);
typedef void (*cf_release_fn)(const void *);
typedef unsigned char (*uml_create_fn)(CFStringRef, CFStringRef, CFOptionFlags,
                                       CFErrorRef *);
typedef void *objc_id;
typedef void *objc_sel;
typedef objc_id (*objc_send0_fn)(objc_id, objc_sel);
typedef objc_id (*objc_send_create_fn)(objc_id, objc_sel, objc_id, void **);
typedef unsigned char (*objc_send_update_fn)(objc_id, objc_sel, objc_id, objc_id, objc_id, objc_id, objc_id, void **);
typedef unsigned char (*objc_send_layout_fn)(objc_id, objc_sel, objc_id, objc_id, void **);
typedef unsigned long (*objc_send_ulong_fn)(objc_id, objc_sel);
typedef const char *(*objc_send_cstr_fn)(objc_id, objc_sel);

struct apis {
    aks_bootstrap_fs_fn aks_bootstrap_fs;
    mkb_create_fn mkb_create;
    cf_string_fn cf_string;
    cf_release_fn cf_release;
    uml_create_fn uml_create;
};

struct copy_context {
    const char *skip_source;
    const char *skip_timezone_source;
    const char *tag;
    int diagnostic;
    int abort_on_error;
    int allow_sandbox_errors;
    int saw_error;
    int error_what;
    int error_stage;
    int error_errno;
    char last_source[1024];
    char last_destination[1024];
    char error_source[1024];
    char error_destination[1024];
    unsigned long files;
    unsigned long allowed_errors;
};

static void fail(const char *stage) {
    fprintf(stderr, "DVM_SEED_%s_ERRNO=%d (%s)\n", stage, errno, strerror(errno));
    fflush(stderr);
    exit(1);
}

static void fail_rc(const char *stage, int rc) {
    fprintf(stderr, "DVM_SEED_%s_RC=%d\n", stage, rc);
    fflush(stderr);
    exit(1);
}

/* This is only used on a failed UMLManager query.  The fixed prototypes keep
 * the Objective-C arguments in x0/x1 (rather than the stack, as the old
 * variadic objc_msgSend declaration did).  Report the NSError before deciding
 * whether its nil result is merely "no existing primary user". */
static void report_nserror(const char *stage, objc_id error, void *raw_send,
                           void *(*sel)(const char *)) {
    objc_send0_fn send0 = (objc_send0_fn)raw_send;
    objc_send_ulong_fn send_ulong = (objc_send_ulong_fn)raw_send;
    objc_send_cstr_fn send_cstr = (objc_send_cstr_fn)raw_send;
    objc_id domain, description;
    const char *domain_text = NULL, *description_text = NULL;
    if (!error) {
        printf("DVM_SEED_%s_ERROR_PRESENT=0\n", stage);
        fflush(stdout);
        return;
    }
    domain = send0(error, sel("domain"));
    description = send0(error, sel("localizedDescription"));
    if (domain) domain_text = send_cstr(domain, sel("UTF8String"));
    if (description) description_text = send_cstr(description, sel("UTF8String"));
    printf("DVM_SEED_%s_ERROR_PRESENT=1 CODE=%lu DOMAIN=%s DESCRIPTION=%s\n",
           stage, send_ulong(error, sel("code")),
           domain_text ? domain_text : "<unavailable>",
           description_text ? description_text : "<unavailable>");
    fflush(stdout);
}

static int exact(const char *got, const char *want) {
    return got && strcmp(got, want) == 0;
}

static void require_layout(const char *source, const char *dest) {
    struct stat st;
    if (!exact(source, SOURCE_ROOT) || !exact(dest, DATA_MOUNT)) {
        fprintf(stderr, "DVM_SEED_REFUSED=paths must be %s -> %s\n",
                SOURCE_ROOT, DATA_MOUNT);
        exit(2);
    }
    if (lstat(dest, &st) || !S_ISDIR(st.st_mode)) fail("DEST");
    if (lstat(source, &st) || !S_ISDIR(st.st_mode)) fail("SOURCE");
}

/* Refuse to seed over an existing Data tree.  `hardware` is the sole staging
 * mountpoint deliberately created by the caller before this helper starts.
 * This makes the full restore safe to retry only on a fresh disposable
 * overlay, rather than silently merging two independent seeds. */
static void require_empty_data(const char *dest) {
    DIR *dir = opendir(dest);
    struct dirent *entry;
    if (!dir) fail("DEST_OPEN");
    while ((entry = readdir(dir))) {
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..") ||
            !strcmp(entry->d_name, "hardware") ||
            !strcmp(entry->d_name, ".dvm-data-seed")) continue;
        fprintf(stderr, "DVM_SEED_REFUSED=Data not empty: %s\n", entry->d_name);
        closedir(dir);
        exit(2);
    }
    if (closedir(dir)) fail("DEST_CLOSE");
}

/* Transport lives below Data only for one restore boot: protected inodes may
 * not unwrap after a reboot, while the restore ramdisk's /tmp is read-only.
 * The uploader removes this exact target before each transfer; make its
 * post-marker cleanup idempotent for a same-boot upload/execute/cleanup
 * lifecycle.  Only --final-marker reaches it after its durable marker check. */
static void remove_staging_helper(void) {
    if (unlink(STAGING_HELPER) && errno != ENOENT) fail("STAGING_CLEANUP_UNLINK");
    if (rmdir(STAGING_DIR) && errno != ENOENT) fail("STAGING_CLEANUP_RMDIR");
    printf("DVM_SEED_STAGING_CLEANUP_RC=0\n");
}

static void *load_symbol(const char *image, const char *symbol, void **handle) {
    void *value;
    const char *error;
    *handle = dlopen(image, RTLD_NOW | RTLD_LOCAL);
    if (!*handle) {
        fprintf(stderr, "DVM_SEED_DLOPEN=%s %s\n", image, dlerror());
        exit(1);
    }
    dlerror();
    value = dlsym(*handle, symbol);
    error = dlerror();
    if (error || !value) {
        fprintf(stderr, "DVM_SEED_DLSYM=%s %s\n", symbol,
                error ? error : "missing");
        exit(1);
    }
    return value;
}

static struct apis load_apis(void) {
    struct apis a;
    void *h1, *h2, *h3, *h4;
    a.aks_bootstrap_fs = (aks_bootstrap_fs_fn)load_symbol(
        "/System/Library/PrivateFrameworks/AppleKeyStore.framework/AppleKeyStore",
        "aks_bootstrap_fs", &h1);
    a.mkb_create = (mkb_create_fn)load_symbol(
        "/System/Library/PrivateFrameworks/MobileKeyBag.framework/MobileKeyBag",
        "MKBKeyBagCreateSystemWithACM", &h2);
    a.uml_create = (uml_create_fn)load_symbol(
        "/System/Library/PrivateFrameworks/UserManagementLayout.framework/UserManagementLayout",
        "UMLCreatePrimaryUserLayout", &h3);
    a.cf_string = (cf_string_fn)load_symbol(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation",
        "CFStringCreateWithCString", &h4);
    a.cf_release = (cf_release_fn)dlsym(h4, "CFRelease");
    if (!a.cf_release) {
        fprintf(stderr, "DVM_SEED_DLSYM=CFRelease\n");
        exit(1);
    }
    return a;
}

/* On legacy APFS MKB persists these files.  Enhanced APFS deliberately makes
 * MKBKeyBagCreateSystemWithACM a no-op success, so absence is evidence about
 * the active storage mode, not a reason to misreport a failed private call.
 * In either case record the exact stat result before copy work begins. */
static int report_keybag_file(const char *stage, const char *path) {
    struct stat st;
    if (stat(path, &st)) {
        int saved_errno = errno;
        printf("DVM_SEED_%s_PATH=%s EXISTS=0 ERRNO=%d (%s)\n", stage, path,
                saved_errno, strerror(saved_errno));
        fflush(stdout);
        return 0;
    }
    printf("DVM_SEED_%s_PATH=%s EXISTS=1 MODE=%o SIZE=%lld\n", stage, path,
           st.st_mode & 07777, (long long)st.st_size);
    fflush(stdout);
    if (!S_ISREG(st.st_mode)) {
        fprintf(stderr, "DVM_SEED_%s_NOT_REGULAR=1\n", stage);
        fflush(stderr);
        exit(1);
    }
    return 1;
}

static void initialise_data(const struct apis *a, const char *dest) {
    int rc, bag, writing;
    rc = a->aks_bootstrap_fs(dest, 2);
    printf("DVM_SEED_AKS_RC=%d\n", rc);
    fflush(stdout);
    if (rc) fail_rc("AKS", rc);
    rc = a->mkb_create(NULL, dest);
    printf("DVM_SEED_MKB_RC=%d\n", rc);
    fflush(stdout);
    if (rc) fail_rc("MKB", rc);
    bag = report_keybag_file("SYSTEMBAG", SYSTEMBAG_PATH);
    writing = report_keybag_file("SYSTEMBAG_WRITING", SYSTEMBAG_WRITING_PATH);
    printf("DVM_SEED_SYSTEMBAG_FILES_PERSISTED=%d\n", bag || writing);
    fflush(stdout);
}

/* These are the only restore-sandbox denials classified in the guest.  They
 * are empty or placeholder template directories (apart from the absent/
 * dangling Lockdown source), so allowing any broader prefix would hide a
 * real copy omission.  OOPJit has its 58-byte regular `anchor` child, and a
 * denied directory cannot exist for copyfile to create its children; permit
 * only the exact root or a slash-bounded descendant of each classified root. */
static int allowed_sandbox_copy_destination(const char *dst) {
    static const char *const allowed[] = {
        DATA_MOUNT "/root/Library/Lockdown",
        DATA_MOUNT "/MobileDevice/ProvisioningProfiles",
        DATA_MOUNT "/mobile/Library/WebClips",
        DATA_MOUNT "/mobile/Library/Safari",
        DATA_MOUNT "/OOPJit",
        DATA_MOUNT "/protected/trustd",
    };
    size_t i;
    for (i = 0; i < sizeof(allowed) / sizeof(allowed[0]); i++) {
        size_t root_len = strlen(allowed[i]);
        if (exact(dst, allowed[i]) ||
            (dst && !strncmp(dst, allowed[i], root_len) && dst[root_len] == '/'))
            return 1;
    }
    return 0;
}

static int copy_status(int what, int stage, copyfile_state_t state,
                       const char *src, const char *dst, void *opaque) {
    struct copy_context *ctx = opaque;
    if (ctx->diagnostic) {
        /* COPYFILE_RECURSE_ERROR/COPYFILE_ERR are the only guest-visible
         * attribution copyfile offers for a failed named stream. */
        printf("DVM_SEED_DIAG_CB=%s what=%d stage=%d errno=%d src=%s dst=%s\n",
               ctx->tag, what, stage, errno, src ? src : "-", dst ? dst : "-");
    }
    (void)state;
    if (src && strlen(src) < sizeof(ctx->last_source))
        strcpy(ctx->last_source, src);
    if (dst && strlen(dst) < sizeof(ctx->last_destination))
        strcpy(ctx->last_destination, dst);
    if (src && exact(src, ctx->skip_source)) {
        printf("DVM_SEED_SKIP_HARDWARE=1\n");
        return COPYFILE_SKIP;
    }
    if (src && exact(src, ctx->skip_timezone_source)) {
        printf("DVM_SEED_SKIP_TIMEZONE_LOCALTIME=1\n");
        return COPYFILE_SKIP;
    }
    if (stage == COPYFILE_ERR && ctx->abort_on_error) {
        if (ctx->allow_sandbox_errors && allowed_sandbox_copy_destination(dst)) {
            ctx->allowed_errors++;
            printf("DVM_SEED_ALLOWED_COPY_ERROR=what=%d stage=%d errno=%d src=%s dst=%s count=%lu\n",
                   what, stage, errno, src ? src : "-", dst,
                   ctx->allowed_errors);
            fflush(stdout);
            return COPYFILE_CONTINUE;
        }
        if (ctx->saw_error) return COPYFILE_QUIT;
        ctx->saw_error = 1;
        ctx->error_what = what;
        ctx->error_stage = stage;
        ctx->error_errno = errno;
        if (src && strlen(src) < sizeof(ctx->error_source))
            strcpy(ctx->error_source, src);
        if (dst && strlen(dst) < sizeof(ctx->error_destination))
            strcpy(ctx->error_destination, dst);
        /* Normal seeding is deliberately fail-closed too.  The diagnostic
         * prefix is retained for the existing --diagnose consumer, while the
         * COPY_ERROR line is the concise production-stage witness. */
        printf("DVM_SEED_COPY_ERROR=%s what=%d stage=%d errno=%d src=%s dst=%s last_src=%s last_dst=%s\n",
               ctx->tag ? ctx->tag : "copy", what, stage, errno,
               src ? src : "-", dst ? dst : "-",
               ctx->last_source[0] ? ctx->last_source : "-",
               ctx->last_destination[0] ? ctx->last_destination : "-");
        if (ctx->diagnostic) printf("DVM_SEED_DIAG_FIRST_ERROR=%s what=%d stage=%d errno=%d src=%s dst=%s last_src=%s last_dst=%s\n",
               ctx->tag, what, stage, errno, src ? src : "-", dst ? dst : "-",
               ctx->last_source[0] ? ctx->last_source : "-",
               ctx->last_destination[0] ? ctx->last_destination : "-");
        return COPYFILE_QUIT;
    }
    if (what == COPYFILE_RECURSE_FILE && stage == COPYFILE_START) ctx->files++;
    return COPYFILE_CONTINUE;
}

static unsigned long long fnv1a_bytes(const void *data, size_t size) {
    const unsigned char *p = data;
    unsigned long long h = 1469598103934665603ULL;
    size_t i;
    for (i = 0; i < size; i++) { h ^= p[i]; h *= 1099511628211ULL; }
    return h;
}

static void report_xattrs(const char *tag, const char *path) {
    ssize_t n = listxattr(path, NULL, 0, DIAG_XATTR_FLAGS);
    char *names, *p;
    if (n < 0) fail("DIAG_XATTR_LIST");
    printf("DVM_SEED_DIAG_XATTR_LIST=%s bytes=%zd flags=0x%x path=%s\n",
           tag, n, DIAG_XATTR_FLAGS, path);
    if (!n) return;
    names = malloc((size_t)n);
    if (!names) fail("DIAG_XATTR_ALLOC");
    if (listxattr(path, names, (size_t)n, DIAG_XATTR_FLAGS) != n) fail("DIAG_XATTR_NAMES");
    for (p = names; p < names + n; p += strlen(p) + 1) {
        ssize_t size = getxattr(path, p, NULL, 0, 0, DIAG_XATTR_FLAGS);
        void *data;
        if (size < 0) fail("DIAG_XATTR_SIZE");
        data = malloc((size_t)size ?: 1);
        if (!data) fail("DIAG_XATTR_ALLOC");
        if (size && getxattr(path, p, data, (size_t)size, 0, DIAG_XATTR_FLAGS) != size)
            fail("DIAG_XATTR_READ");
        printf("DVM_SEED_DIAG_XATTR=%s name=%s bytes=%zd fnv1a64=%016llx\n",
               tag, p, size, fnv1a_bytes(data, (size_t)size));
        free(data);
    }
    free(names);
}

static void report_file(const char *tag, const char *path) {
    struct stat st;
    int fd, protection;
    if (lstat(path, &st)) fail("DIAG_STAT");
    fd = open(path, O_RDONLY);
    if (fd < 0) fail("DIAG_OPEN");
    protection = fcntl(fd, F_GETPROTECTIONCLASS);
    close(fd);
    printf("DVM_SEED_DIAG_FILE=%s path=%s uid=%u gid=%u mode=%o protection=%d\n",
           tag, path, st.st_uid, st.st_gid, st.st_mode & 07777, protection);
    report_xattrs(tag, path);
}

static void report_parent(const char *tag, const char *path) {
    char parent[1024];
    char *slash;
    if (snprintf(parent, sizeof(parent), "%s", path) >= (int)sizeof(parent))
        fail_rc("DIAG_PARENT_PATH", 0);
    slash = strrchr(parent, '/');
    if (!slash || slash == parent) fail_rc("DIAG_PARENT_PATH", 0);
    *slash = '\0';
    report_file(tag, parent);
}

static int copy_tree(const char *src, const char *dst, struct copy_context *ctx) {
    copyfile_state_t state = copyfile_state_alloc();
    int one = 1;
    int rc;
    if (!state) fail("COPY_STATE");
    if (copyfile_state_set(state, COPYFILE_STATE_FORBID_CROSS_MOUNT, &one) ||
        copyfile_state_set(state, COPYFILE_STATE_STATUS_CTX, ctx) ||
        copyfile_state_set(state, COPYFILE_STATE_STATUS_CB, copy_status)) {
        fail("COPY_STATE_SET");
    }
    /* Do not dereference a template symlink while recursively copying it.
     * In particular, db/timezone/localtime points back into /var and must be
     * reproduced as that symlink on Data, not copied as its referent. */
    rc = copyfile(src, dst, state,
                  COPYFILE_RECURSIVE | COPYFILE_ALL | COPYFILE_NOFOLLOW_SRC);
    copyfile_state_free(state);
    return rc;
}

/* copyfile(source_root, data_mount) creates data_mount/var because source_root
 * itself is named "var".  Apple /sbin/mount copies the contents of the
 * System template into the existing Data mountpoint instead.  Enumerating the
 * immediate children is deliberately boring but makes the mapping auditable:
 * source_root/foo always becomes data_mount/foo, never data_mount/var/foo. */
static int copy_tree_contents(const char *source_root, const char *dest,
                              struct copy_context *ctx) {
    DIR *dir = opendir(source_root);
    struct dirent *entry;
    int rc = 0;
    if (!dir) fail("COPY_SOURCE_OPEN");
    while ((entry = readdir(dir))) {
        char src[1024], dst[1024];
        if (!strcmp(entry->d_name, ".") || !strcmp(entry->d_name, "..")) continue;
        if (snprintf(src, sizeof(src), "%s/%s", source_root, entry->d_name) >= (int)sizeof(src) ||
            snprintf(dst, sizeof(dst), "%s/%s", dest, entry->d_name) >= (int)sizeof(dst)) {
            closedir(dir);
            fail_rc("COPY_PATH", 0);
        }
        rc = copy_tree(src, dst, ctx);
        if (rc) break;
    }
    if (closedir(dir)) fail("COPY_SOURCE_CLOSE");
    return rc;
}

/* This is intentionally before aks_bootstrap_fs(): the restore guest can
 * create the Data-side timezone link on the fresh mounted volume, while the
 * identical operation hangs after AKS has initialized Data.  require_empty_data
 * has already excluded a pre-existing db tree, so every mkdir here is strict.
 * Preserve directory mode/ownership and link ownership from the read-only
 * System template; no host filesystem ever observes either side. */
static void precreate_timezone_directory(const char *src, const char *dst,
                                         const char *stage) {
    struct stat st;
    if (lstat(src, &st)) fail("TIMEZONE_PRECREATE_SOURCE_LSTAT");
    if (!S_ISDIR(st.st_mode)) fail_rc("TIMEZONE_PRECREATE_SOURCE_NOT_DIR", 0);
    if (mkdir(dst, st.st_mode & 07777)) fail(stage);
    if (chmod(dst, st.st_mode & 07777)) fail("TIMEZONE_PRECREATE_CHMOD");
    if (lchown(dst, st.st_uid, st.st_gid)) fail("TIMEZONE_PRECREATE_LCHOWN");
}

static void precreate_timezone_link(const char *source_root, const char *dest) {
    char src_db[1024], dst_db[1024], src_dir[1024], dst_dir[1024];
    char src_link[1024], dst_link[1024], target[1024];
    struct stat st;
    ssize_t target_len;

    if (snprintf(src_db, sizeof(src_db), "%s/db", source_root) >= (int)sizeof(src_db) ||
        snprintf(dst_db, sizeof(dst_db), "%s/db", dest) >= (int)sizeof(dst_db) ||
        snprintf(src_dir, sizeof(src_dir), "%s/%s", source_root, TIMEZONE_DIR_REL) >= (int)sizeof(src_dir) ||
        snprintf(dst_dir, sizeof(dst_dir), "%s/%s", dest, TIMEZONE_DIR_REL) >= (int)sizeof(dst_dir) ||
        snprintf(src_link, sizeof(src_link), "%s/%s", source_root, TIMEZONE_LOCALTIME_REL) >= (int)sizeof(src_link) ||
        snprintf(dst_link, sizeof(dst_link), "%s/%s", dest, TIMEZONE_LOCALTIME_REL) >= (int)sizeof(dst_link))
        fail_rc("TIMEZONE_PRECREATE_PATH", 0);

    precreate_timezone_directory(src_db, dst_db, "TIMEZONE_PRECREATE_DB_MKDIR");
    printf("DVM_SEED_TIMEZONE_PRECREATE_DB_RC=0\n"); fflush(stdout);
    precreate_timezone_directory(src_dir, dst_dir, "TIMEZONE_PRECREATE_DIR_MKDIR");
    printf("DVM_SEED_TIMEZONE_PRECREATE_DIR_RC=0\n"); fflush(stdout);

    if (lstat(src_link, &st)) fail("TIMEZONE_PRECREATE_SOURCE_LINK_LSTAT");
    if (!S_ISLNK(st.st_mode)) fail_rc("TIMEZONE_PRECREATE_SOURCE_NOT_SYMLINK", 0);
    target_len = readlink(src_link, target, sizeof(target) - 1);
    if (target_len < 0 || target_len >= (ssize_t)sizeof(target) - 1)
        fail("TIMEZONE_PRECREATE_READLINK");
    target[target_len] = '\0';
    if (symlink(target, dst_link)) fail("TIMEZONE_PRECREATE_SYMLINK");
    if (lchown(dst_link, st.st_uid, st.st_gid)) fail("TIMEZONE_PRECREATE_LINK_LCHOWN");
    printf("DVM_SEED_TIMEZONE_PRECREATE_LINK_RC=0 target=%s\n", target); fflush(stdout);
    printf("DVM_SEED_TIMEZONE_PRECREATE_RC=0\n"); fflush(stdout);
}

/* tzinit needs the template's localtime link.  Comparing lstat metadata and
 * readlink text is intentionally narrower than a tree hash: it proves the
 * symlink survived recursive COPYFILE_ALL without following either endpoint,
 * and makes an absent db/timezone hierarchy fail at the copy boundary. */
static void verify_timezone_symlink(const char *source_root, const char *dest) {
    char src[1024], dst[1024], src_link[1024], dst_link[1024];
    struct stat src_st, dst_st;
    ssize_t src_len, dst_len;

    if (snprintf(src, sizeof(src), "%s/%s", source_root, TIMEZONE_LOCALTIME_REL) >= (int)sizeof(src) ||
        snprintf(dst, sizeof(dst), "%s/%s", dest, TIMEZONE_LOCALTIME_REL) >= (int)sizeof(dst))
        fail_rc("TIMEZONE_PATH", 0);
    if (lstat(src, &src_st)) fail("TIMEZONE_SOURCE_LSTAT");
    if (lstat(dst, &dst_st)) fail("TIMEZONE_DEST_LSTAT");
    if (!S_ISLNK(src_st.st_mode)) fail_rc("TIMEZONE_SOURCE_NOT_SYMLINK", 0);
    if (!S_ISLNK(dst_st.st_mode)) fail_rc("TIMEZONE_DEST_NOT_SYMLINK", 0);
    src_len = readlink(src, src_link, sizeof(src_link) - 1);
    if (src_len < 0 || src_len >= (ssize_t)sizeof(src_link) - 1) fail("TIMEZONE_SOURCE_READLINK");
    dst_len = readlink(dst, dst_link, sizeof(dst_link) - 1);
    if (dst_len < 0 || dst_len >= (ssize_t)sizeof(dst_link) - 1) fail("TIMEZONE_DEST_READLINK");
    src_link[src_len] = '\0';
    dst_link[dst_len] = '\0';
    if (src_len != dst_len || memcmp(src_link, dst_link, (size_t)src_len))
        fail_rc("TIMEZONE_LINK_TARGET", 0);
    if ((src_st.st_mode & 07777) != (dst_st.st_mode & 07777) ||
        src_st.st_uid != dst_st.st_uid || src_st.st_gid != dst_st.st_gid ||
        src_st.st_size != dst_st.st_size) {
        printf("DVM_SEED_TIMEZONE_SYMLINK_METADATA=src_mode=%o dst_mode=%o src_uid=%u dst_uid=%u src_gid=%u dst_gid=%u src_size=%lld dst_size=%lld\n",
               src_st.st_mode & 07777, dst_st.st_mode & 07777,
               src_st.st_uid, dst_st.st_uid, src_st.st_gid, dst_st.st_gid,
               (long long)src_st.st_size, (long long)dst_st.st_size);
        fflush(stdout);
        fail_rc("TIMEZONE_SYMLINK_METADATA", 0);
    }
    printf("DVM_SEED_TIMEZONE_SYMLINK=src=%s dst=%s target=%s mode=%o uid=%u gid=%u\n",
           src, dst, src_link, src_st.st_mode & 07777, src_st.st_uid, src_st.st_gid);
    printf("DVM_SEED_TIMEZONE_SYMLINK_RC=0\n");
    fflush(stdout);
}

static unsigned long long fnv1a_file(const char *path, unsigned long long *bytes) {
    unsigned char buf[16384];
    unsigned long long h = 1469598103934665603ULL;
    ssize_t n;
    int fd = open(path, O_RDONLY);
    if (fd < 0) fail("HASH_OPEN");
    *bytes = 0;
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        ssize_t i;
        for (i = 0; i < n; i++) { h ^= buf[i]; h *= 1099511628211ULL; }
        *bytes += (unsigned long long)n;
    }
    if (n < 0) fail("HASH_READ");
    if (close(fd)) fail("HASH_CLOSE");
    return h;
}

static void compare_xattrs(const char *src, const char *dst) {
    ssize_t nsrc, ndst;
    char *names, *p;
    nsrc = listxattr(src, NULL, 0, XATTR_NOFOLLOW);
    ndst = listxattr(dst, NULL, 0, XATTR_NOFOLLOW);
    if (nsrc < 0 || ndst < 0) fail("XATTR_LIST");
    if (nsrc != ndst) fail_rc("XATTR_COUNT", (int)(ndst - nsrc));
    if (!nsrc) return;
    names = malloc((size_t)nsrc);
    if (!names) fail("XATTR_ALLOC");
    if (listxattr(src, names, (size_t)nsrc, XATTR_NOFOLLOW) != nsrc) fail("XATTR_NAMES");
    for (p = names; p < names + nsrc; p += strlen(p) + 1) {
        ssize_t ss = getxattr(src, p, NULL, 0, 0, XATTR_NOFOLLOW);
        ssize_t ds = getxattr(dst, p, NULL, 0, 0, XATTR_NOFOLLOW);
        void *a, *b;
        if (ss < 0 || ds != ss) fail_rc("XATTR_VALUE", (int)ds);
        a = malloc((size_t)ss ?: 1); b = malloc((size_t)ss ?: 1);
        if (!a || !b) fail("XATTR_ALLOC");
        if (ss && (getxattr(src, p, a, (size_t)ss, 0, XATTR_NOFOLLOW) != ss ||
                   getxattr(dst, p, b, (size_t)ss, 0, XATTR_NOFOLLOW) != ss ||
                   memcmp(a, b, (size_t)ss))) fail_rc("XATTR_VALUE", -1);
        free(a); free(b);
    }
    free(names);
}

static void verify_probe(const char *src, const char *dst) {
    struct stat a, b;
    unsigned long long asz, bsz, ah, bh;
    int af, bf, ac, bc;
    if (lstat(src, &a) || lstat(dst, &b)) fail("PROBE_STAT");
    ah = fnv1a_file(src, &asz); bh = fnv1a_file(dst, &bsz);
    af = open(src, O_RDONLY); bf = open(dst, O_RDONLY);
    if (af < 0 || bf < 0) fail("PROBE_OPEN");
    ac = fcntl(af, F_GETPROTECTIONCLASS); bc = fcntl(bf, F_GETPROTECTIONCLASS);
    close(af); close(bf);
    printf("DVM_SEED_PROBE_BYTES=%llu\n", asz);
    printf("DVM_SEED_PROBE_FNV1A64=%016llx/%016llx\n", ah, bh);
    printf("DVM_SEED_PROBE_META=%u:%u:%o/%u:%u:%o\n", a.st_uid, a.st_gid,
           a.st_mode & 07777, b.st_uid, b.st_gid, b.st_mode & 07777);
    printf("DVM_SEED_PROBE_PROTECTION=%d/%d\n", ac, bc);
    if (asz != bsz || ah != bh || a.st_uid != b.st_uid || a.st_gid != b.st_gid ||
        (a.st_mode & 07777) != (b.st_mode & 07777) || bc < 0) fail_rc("PROBE_VERIFY", -1);
    compare_xattrs(src, dst);
    printf("DVM_SEED_PROBE_RC=0\n");
}

static void make_dir(const char *path) {
    if (mkdir(path, 0700) && errno != EEXIST) fail("MKDIR");
}

static void make_parent_dirs(const char *path) {
    char parent[1024], *p;
    if (snprintf(parent, sizeof(parent), "%s", path) >= (int)sizeof(parent))
        fail_rc("DIAG_PARENT_PATH", 0);
    p = strrchr(parent, '/');
    if (!p || p == parent) fail_rc("DIAG_PARENT_PATH", 0);
    *p = '\0';
    for (p = parent + 1; *p; p++) {
        if (*p != '/') continue;
        *p = '\0';
        if (mkdir(parent, 0755) && errno != EEXIST) fail("DIAG_MKDIR");
        *p = '/';
    }
    if (mkdir(parent, 0755) && errno != EEXIST) fail("DIAG_MKDIR");
}

static void make_relative_path(const char *source, const char *relative,
                               char *path, size_t path_size) {
    if (!relative || !*relative || relative[0] == '/' || strstr(relative, "..") ||
        snprintf(path, path_size, "%s/%s", source, relative) >= (int)path_size) {
        fprintf(stderr, "DVM_SEED_REFUSED=unsafe diagnostic relative path\n");
        exit(2);
    }
}

static void run_probe(const struct apis *a, const char *source, const char *dest,
                      const char *relative) {
    char src[1024], dst[1024];
    const char *base;
    struct copy_context ctx;
    if (!relative || relative[0] == '/' || strstr(relative, "..")) {
        fprintf(stderr, "DVM_SEED_REFUSED=unsafe probe relative path\n"); exit(2);
    }
    if (snprintf(src, sizeof(src), "%s/%s", source, relative) >= (int)sizeof(src)) exit(2);
    base = strrchr(relative, '/'); base = base ? base + 1 : relative;
    if (snprintf(dst, sizeof(dst), "%s/%s", PROBE_DIR, base) >= (int)sizeof(dst)) exit(2);
    make_dir(PROBE_DIR);
    initialise_data(a, dest);
    memset(&ctx, 0, sizeof(ctx)); ctx.skip_source = "";
    if (copy_tree(src, dst, &ctx)) fail("COPY");
    printf("DVM_SEED_COPY_RC=0\n");
    verify_probe(src, dst);
}

static void run_full(const struct apis *a, const char *source, const char *dest,
                     int with_aks_setup) {
    struct copy_context ctx;
    CFStringRef root, mobile;
    CFErrorRef error = NULL;
    int fd;
    initialise_data(a, dest);
    memset(&ctx, 0, sizeof(ctx)); ctx.skip_source = "/private/var/hardware/private/var/hardware";
    if (copy_tree_contents(source, dest, &ctx)) fail("COPY");
    if (!ctx.files) fail_rc("COPY_FILES", 0);
    printf("DVM_SEED_COPY_FILES=%lu\n", ctx.files);
    root = a->cf_string(NULL, "/", CF_STRING_ENCODING_UTF8);
    mobile = a->cf_string(NULL, "/private/var/mobile", CF_STRING_ENCODING_UTF8);
    if (!root || !mobile) fail_rc("UML_CFSTRING", 0);
    /* Option zero is the observed /sbin/mount call.  The nonzero branch is
     * deliberately opt-in: RE identifies bit 0 as withAKSSetup, and this
     * helper records which branch it actually asked the private API to take. */
    printf("DVM_SEED_UML_AKS_SETUP=%d\n", with_aks_setup ? 1 : 0);
    fflush(stdout);
    if (!a->uml_create(root, mobile, with_aks_setup ? 1 : 0, &error))
        fail_rc("UML", 0);
    a->cf_release(root); a->cf_release(mobile);
    printf("DVM_SEED_UML_RC=0\n");
    /* The volume root is writable on a just-seeded Data volume; root/ is
     * subject to the restore sandbox's directory creation policy.  Stage
     * witnesses are flushed before every syscall so a post-UML termination
     * cannot be mistaken for a successful marker. */
    printf("DVM_SEED_MARKER_STAGE=OPEN path=%s\n", COMPLETE_MARKER);
    fflush(stdout);
    fd = open(COMPLETE_MARKER, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) fail("MARKER_OPEN");
    printf("DVM_SEED_MARKER_STAGE=WRITE\n");
    fflush(stdout);
    if (write(fd, "dvm data seed complete\n", 23) != 23) fail("MARKER_WRITE");
    printf("DVM_SEED_MARKER_STAGE=FSYNC\n");
    fflush(stdout);
    if (fsync(fd)) fail("MARKER_FSYNC");
    printf("DVM_SEED_MARKER_STAGE=CLOSE\n");
    fflush(stdout);
    if (close(fd)) fail("MARKER_CLOSE");
    printf("DVM_SEED_MARKER_RC=0\n");
    fflush(stdout);
}

static void run_copy_data(const struct apis *a, const char *source, const char *dest) {
    struct copy_context ctx;
    precreate_timezone_link(source, dest);
    initialise_data(a, dest);
    memset(&ctx, 0, sizeof(ctx));
    ctx.skip_source = "/private/var/hardware/private/var/hardware";
    ctx.skip_timezone_source = SOURCE_ROOT "/" TIMEZONE_LOCALTIME_REL;
    ctx.tag = "copy-data";
    ctx.abort_on_error = 1;
    ctx.allow_sandbox_errors = 1;
    if (copy_tree_contents(source, dest, &ctx)) fail("COPY");
    verify_timezone_symlink(source, dest);
    if (!ctx.files) fail_rc("COPY_FILES", 0);
    printf("DVM_SEED_ALLOWED_COPY_ERRORS=%lu\n", ctx.allowed_errors);
    printf("DVM_SEED_COPY_FILES=%lu\nDVM_SEED_COPY_DATA_RC=0\n", ctx.files);
    fflush(stdout);
}

static void final_marker(void) {
    int fd;
    printf("DVM_SEED_MARKER_STAGE=OPEN path=%s\n", COMPLETE_MARKER); fflush(stdout);
    fd=open(COMPLETE_MARKER,O_WRONLY|O_CREAT|O_EXCL,0600); if(fd<0) fail("MARKER_OPEN");
    printf("DVM_SEED_MARKER_STAGE=WRITE\n"); fflush(stdout);
    if(write(fd,"dvm data seed complete\n",23)!=23) fail("MARKER_WRITE");
    printf("DVM_SEED_MARKER_STAGE=FSYNC\n"); fflush(stdout);
    if(fsync(fd)) fail("MARKER_FSYNC");
    printf("DVM_SEED_MARKER_STAGE=CLOSE\n"); fflush(stdout);
    if(close(fd)) fail("MARKER_CLOSE");
    if(access(COMPLETE_MARKER,R_OK)) fail("MARKER_STAT");
    printf("DVM_SEED_MARKER_RC=0\n"); fflush(stdout);
}

/* Run just the destructive-to-an-empty-volume initialisation boundary.  It is
 * deliberately passed the same exact mount paths as --full: callers cannot
 * point a private API at an arbitrary volume, and staging cleanup still
 * happens before AKS/MKB. */
static void run_init_only(const struct apis *a, const char *dest) {
    initialise_data(a, dest);
    printf("DVM_SEED_INIT_ONLY_RC=0\n");
    fflush(stdout);
}

static void user_manifest(const struct apis *a) {
    struct attrlist attrs = {0};
    struct { unsigned int length; unsigned char uuid[16]; } reply = {0};
    char uuid[37];
    void *objc, *(*look_up)(const char *), *(*sel)(const char *);
    void *raw_send;
    objc_send0_fn send0;
    objc_send_create_fn send_create;
    objc_send_update_fn send_update;
    objc_id manager, user;
    CFStringRef data, device, uuid_string, name;
    CFErrorRef error = NULL;
    int i;
    struct statfs fs;

    attrs.bitmapcount = ATTR_BIT_MAP_COUNT;
    attrs.volattr = ATTR_VOL_UUID;
    if (statfs(USER_MOUNT, &fs) || strcmp(fs.f_mntfromname, USER_DEVICE)) fail("USER_MOUNT_SOURCE");
    printf("DVM_SEED_USER_MOUNT_SOURCE=%s\n", fs.f_mntfromname); fflush(stdout);
    printf("DVM_SEED_USER_STAGE=UUID_QUERY path=%s\n", USER_MOUNT); fflush(stdout);
    if (getattrlist(USER_MOUNT, &attrs, &reply, sizeof(reply), 0) || reply.length < sizeof(reply))
        fail("USER_UUID");
    for (i = 0; i < 16; i++) if (reply.uuid[i]) break;
    if (i == 16) fail_rc("USER_UUID_ZERO", 0);
    snprintf(uuid, sizeof(uuid),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        reply.uuid[0],reply.uuid[1],reply.uuid[2],reply.uuid[3],reply.uuid[4],reply.uuid[5],reply.uuid[6],reply.uuid[7],
        reply.uuid[8],reply.uuid[9],reply.uuid[10],reply.uuid[11],reply.uuid[12],reply.uuid[13],reply.uuid[14],reply.uuid[15]);
    printf("DVM_SEED_USER_UUID=%s device=%s mount=%s\n", uuid, USER_DEVICE, USER_MOUNT); fflush(stdout);
    objc = dlopen("/usr/lib/libobjc.A.dylib", RTLD_NOW | RTLD_LOCAL);
    if (!objc) fail("USER_OBJC_DLOPEN");
    look_up = dlsym(objc, "objc_lookUpClass"); sel = dlsym(objc, "sel_registerName"); raw_send = dlsym(objc, "objc_msgSend");
    if (!look_up || !sel || !raw_send) fail("USER_OBJC_SYMBOL");
    send0=(objc_send0_fn)raw_send; send_create=(objc_send_create_fn)raw_send; send_update=(objc_send_update_fn)raw_send;
    manager = send0(look_up("UMLManager"), sel("sharedManager"));
    if (!manager) fail_rc("USER_MANAGER", 0);
    data=a->cf_string(NULL,DATA_MOUNT,CF_STRING_ENCODING_UTF8); device=a->cf_string(NULL,USER_DEVICE,CF_STRING_ENCODING_UTF8);
    uuid_string=a->cf_string(NULL,uuid,CF_STRING_ENCODING_UTF8); name=a->cf_string(NULL,"User",CF_STRING_ENCODING_UTF8);
    if (!data||!device||!uuid_string||!name) fail_rc("USER_CFSTRING",0);
    printf("DVM_SEED_USER_STAGE=LOOKUP_PRIMARY\n"); fflush(stdout);
    error = NULL;
    user=send_create(manager,sel("primaryUserOnSharedDataVolumePath:withError:"),data,&error);
    if (user) {
        printf("DVM_SEED_USER_LOOKUP_FOUND=1\nDVM_SEED_USER_EXISTING=1\n"); fflush(stdout);
    } else {
        printf("DVM_SEED_USER_LOOKUP_FOUND=0\n"); fflush(stdout);
        report_nserror("USER_LOOKUP_PRIMARY", error, raw_send, sel);
        /* The accessor is diagnostic only: the production create selector is
         * authoritative for a freshly made User volume.  Preserve the error
         * witness, then clear it rather than conflating absence with a failed
         * create. */
        error = NULL;
    }
    if (!user) {
        printf("DVM_SEED_USER_STAGE=CREATE_PRIMARY\n"); fflush(stdout);
        error = NULL;
        user=send_create(manager,sel("createPrimaryUserOnSharedDataVolumePath:withError:"),data,&error);
        if (!user || error) {
            report_nserror("USER_CREATE_PRIMARY", error, raw_send, sel);
            fail_rc("USER_CREATE_PRIMARY",0);
        }
        printf("DVM_SEED_USER_CREATED=1\n"); fflush(stdout);
    }
    printf("DVM_SEED_USER_STAGE=UPDATE_PRIMARY\n"); fflush(stdout);
    error = NULL;
    if (!send_update(manager,sel("updatePrimaryUser:onSharedDataVolumePath:withDiskNode:withVolumeuuid:withVolumeName:withError:"),user,data,device,uuid_string,name,&error) || error) {
        report_nserror("USER_UPDATE_PRIMARY", error, raw_send, sel);
        fail_rc("USER_UPDATE_PRIMARY",0);
    }
    /* Do not run ObjC/CF teardown after this opaque private call. */
    if (write(STDOUT_FILENO, "DVM_SEED_USER_MANIFEST_RC=0\n", 28) != 28) _exit(1);
    _exit(0);
}

static void user_layout(const struct apis *a) {
    void *objc, *(*look_up)(const char *), *(*sel)(const char *);
    void *raw_send;
    objc_send0_fn send0;
    objc_send_layout_fn send_layout;
    objc_id manager;
    CFStringRef user_mount, system_mount;
    CFErrorRef error = NULL;
    struct statfs fs;
    if (statfs(USER_MOUNT, &fs) || strcmp(fs.f_mntfromname, USER_DEVICE)) fail("USER_MOUNT_SOURCE");
    objc = dlopen("/usr/lib/libobjc.A.dylib", RTLD_NOW | RTLD_LOCAL);
    if (!objc) fail("USER_OBJC_DLOPEN");
    look_up=dlsym(objc,"objc_lookUpClass"); sel=dlsym(objc,"sel_registerName"); raw_send=dlsym(objc,"objc_msgSend");
    if (!look_up||!sel||!raw_send) fail("USER_OBJC_SYMBOL");
    send0=(objc_send0_fn)raw_send; send_layout=(objc_send_layout_fn)raw_send;
    manager=send0(look_up("UMLManager"),sel("sharedManager"));
    user_mount=a->cf_string(NULL,USER_MOUNT,CF_STRING_ENCODING_UTF8); system_mount=a->cf_string(NULL,SYSTEM_MOUNT,CF_STRING_ENCODING_UTF8);
    if (!manager||!user_mount||!system_mount) fail_rc("USER_LAYOUT_ARGS",0);
    printf("DVM_SEED_USER_STAGE=POPULATE\n"); fflush(stdout);
    error = NULL;
    if (!send_layout(manager,sel("createPrimaryUserLayoutWithOnUserVolumePath:fromSystemVolumePath:withError:"),user_mount,system_mount,&error) || error) {
        report_nserror("USER_POPULATE", error, raw_send, sel);
        fail_rc("USER_POPULATE",0);
    }
    if (write(STDOUT_FILENO, "DVM_SEED_USER_LAYOUT_RC=0\n", 26) != 26) _exit(1);
    _exit(0);
}

/* First identify the actual source that makes COPYFILE_ALL fail.  A template
 * named stream can be materialized through com.apple.decmpfs rather than
 * appearing as a directly enumerable ResourceFork xattr, so this deliberately
 * aborts at the first copyfile error instead of guessing a source from a
 * pre-scan.  A second, single-file diagnostic is only valid after this one
 * gives us an exact source path. */
static void run_diagnose(const struct apis *a, const char *source, const char *dest) {
    const char *ordinary_rel = "MobileAsset/PreinstalledAssetsV2/InstallWithOs/"
        "com_apple_MobileAsset_SoundScapesPickerAssets/"
        "76ac0db885bfc20859eb82059482f5b2c1c439a9.asset/Info.plist";
    char ordinary[1024], ordinary_dst[1024];
    struct copy_context ctx;
    if (snprintf(ordinary, sizeof(ordinary), "%s/%s", source, ordinary_rel) >= (int)sizeof(ordinary) ||
        snprintf(ordinary_dst, sizeof(ordinary_dst), "%s/ordinary-info.plist", PROBE_DIR) >= (int)sizeof(ordinary_dst))
        fail_rc("DIAG_PATH", 0);
    make_dir(PROBE_DIR);
    initialise_data(a, dest);
    report_file("ordinary-source", ordinary);
    report_parent("ordinary-source-parent", ordinary);
    memset(&ctx, 0, sizeof(ctx)); ctx.skip_source = ""; ctx.tag = "ordinary"; ctx.diagnostic = 1;
    errno = 0;
    if (copy_tree(ordinary, ordinary_dst, &ctx)) fail("DIAG_ORDINARY_COPY");
    report_file("ordinary-dest", ordinary_dst);
    report_parent("ordinary-dest-parent", ordinary_dst);
    verify_probe(ordinary, ordinary_dst);
    printf("DVM_SEED_DIAG_ORDINARY_RC=0\n");
    memset(&ctx, 0, sizeof(ctx));
    ctx.skip_source = "/private/var/hardware/private/var/hardware";
    ctx.tag = "first-error";
    ctx.diagnostic = 1;
    ctx.abort_on_error = 1;
    errno = 0;
    if (!copy_tree_contents(source, dest, &ctx)) fail_rc("DIAG_EXPECTED_COPY_ERROR", 0);
    if (!ctx.saw_error) {
        printf("DVM_SEED_DIAG_COPY_FAILED_WITHOUT_CALLBACK=1 last_src=%s last_dst=%s\n",
               ctx.last_source[0] ? ctx.last_source : "-",
               ctx.last_destination[0] ? ctx.last_destination : "-");
        fail_rc("DIAG_ERROR_CALLBACK", 0);
    }
    printf("DVM_SEED_DIAG_FIRST_ERROR_RC=0\n");
}

/* Copy one source leaf to its real Data-relative target after the ordinary
 * control.  This is the follow-up to --diagnose: it never changes a source
 * xattr and it does not claim success unless the data, metadata, and every
 * xattr compare equal. */
static void run_diagnose_file(const struct apis *a, const char *source,
                              const char *dest, const char *relative) {
    const char *ordinary_rel = "MobileAsset/PreinstalledAssetsV2/InstallWithOs/"
        "com_apple_MobileAsset_SoundScapesPickerAssets/"
        "76ac0db885bfc20859eb82059482f5b2c1c439a9.asset/Info.plist";
    char ordinary_src[1024], ordinary_dst[1024], named_src[1024], named_dst[1024];
    struct copy_context ctx;
    ssize_t xattr_bytes;

    make_relative_path(source, ordinary_rel, ordinary_src, sizeof(ordinary_src));
    make_relative_path(dest, ordinary_rel, ordinary_dst, sizeof(ordinary_dst));
    make_relative_path(source, relative, named_src, sizeof(named_src));
    make_relative_path(dest, relative, named_dst, sizeof(named_dst));
    make_dir(PROBE_DIR);
    initialise_data(a, dest);

    printf("DVM_SEED_DIAG_ORDINARY_TARGET=%s\n", ordinary_dst);
    report_file("ordinary-source", ordinary_src);
    report_parent("ordinary-source-parent", ordinary_src);
    make_parent_dirs(ordinary_dst);
    report_parent("ordinary-dest-parent", ordinary_dst);
    memset(&ctx, 0, sizeof(ctx));
    ctx.skip_source = ""; ctx.tag = "ordinary"; ctx.diagnostic = 1;
    if (copy_tree(ordinary_src, ordinary_dst, &ctx)) fail("DIAG_ORDINARY_COPY");
    report_file("ordinary-dest", ordinary_dst);
    verify_probe(ordinary_src, ordinary_dst);
    printf("DVM_SEED_DIAG_ORDINARY_RC=0\n");

    xattr_bytes = listxattr(named_src, NULL, 0, DIAG_XATTR_FLAGS);
    if (xattr_bytes <= 0) fail_rc("DIAG_NAMED_SOURCE_XATTR_BYTES", (int)xattr_bytes);
    printf("DVM_SEED_DIAG_NAMED_TARGET=%s\n", named_dst);
    report_file("named-source", named_src);
    report_parent("named-source-parent", named_src);
    make_parent_dirs(named_dst);
    report_parent("named-dest-parent", named_dst);
    memset(&ctx, 0, sizeof(ctx));
    ctx.skip_source = ""; ctx.tag = "named"; ctx.diagnostic = 1;
    ctx.abort_on_error = 1;
    errno = 0;
    if (copy_tree(named_src, named_dst, &ctx)) {
        if (!ctx.saw_error) fail("DIAG_NAMED_COPY");
        if (!lstat(named_dst, &(struct stat){0})) report_file("named-dest-partial", named_dst);
        printf("DVM_SEED_DIAG_NAMED_EXPECTED_ERROR_RC=0\n");
        return;
    }
    report_file("named-dest", named_dst);
    verify_probe(named_src, named_dst);
    printf("DVM_SEED_DIAG_NAMED_RC=0\n");
}

int main(int argc, char **argv) {
    struct apis a;
    if (argc == 5 && exact(argv[1], "--probe")) {
        require_layout(argv[3], argv[4]);
        require_empty_data(argv[4]);
        a = load_apis();
        run_probe(&a, argv[3], argv[4], argv[2]);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--full")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        run_full(&a, argv[2], argv[3], 0);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--copy-data")) {
        require_layout(argv[2], argv[3]); require_empty_data(argv[3]);
        a=load_apis(); run_copy_data(&a,argv[2],argv[3]); return 0;
    }
    if (argc == 4 && exact(argv[1], "--full-aks-setup")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        run_full(&a, argv[2], argv[3], 1);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--init-only")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        run_init_only(&a, argv[3]);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--user-manifest")) {
        if (!exact(argv[3], DATA_MOUNT)) { fprintf(stderr,"DVM_SEED_REFUSED=manifest Data path\n"); return 2; }
        a = load_apis();
        user_manifest(&a);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--user-layout")) {
        if (!exact(argv[3], DATA_MOUNT)) { fprintf(stderr,"DVM_SEED_REFUSED=layout System path\n"); return 2; }
        a = load_apis();
        user_layout(&a);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--final-marker")) {
        require_layout(argv[2], argv[3]);
        final_marker(); remove_staging_helper(); return 0;
    }
    if (argc == 4 && exact(argv[1], "--diagnose")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        run_diagnose(&a, argv[2], argv[3]);
        return 0;
    }
    if (argc == 5 && exact(argv[1], "--diagnose-file")) {
        require_layout(argv[3], argv[4]);
        require_empty_data(argv[4]);
        a = load_apis();
        run_diagnose_file(&a, argv[3], argv[4], argv[2]);
        return 0;
    }
    {
        fprintf(stderr, "usage: %s --probe REL %s %s | --diagnose %s %s | --diagnose-file REL %s %s | --full %s %s | --full-aks-setup %s %s | --init-only %s %s | --user-manifest %s %s | --user-layout %s %s\n",
                argv[0], SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT,
                SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT,
                SOURCE_ROOT, DATA_MOUNT,
                SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT);
        return 2;
    }
}
