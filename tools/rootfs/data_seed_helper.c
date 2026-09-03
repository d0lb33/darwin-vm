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
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

#define DATA_MOUNT "/private/var"
#define SOURCE_ROOT "/private/var/hardware/private/var"
#define PROBE_DIR "/private/var/.dvm-data-seed-probe"
#define STAGING_DIR "/private/var/.dvm-data-seed"
#define STAGING_HELPER "/private/var/.dvm-data-seed/data_seed_helper"
#define COMPLETE_MARKER "/private/var/root/.dvm-data-seed-complete"
#define SYSTEMBAG_PATH DATA_MOUNT "/keybags/systembag.kb"
#define SYSTEMBAG_WRITING_PATH DATA_MOUNT "/keybags/systembag.kb.writing"
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

struct apis {
    aks_bootstrap_fs_fn aks_bootstrap_fs;
    mkb_create_fn mkb_create;
    cf_string_fn cf_string;
    cf_release_fn cf_release;
    uml_create_fn uml_create;
};

struct copy_context {
    const char *skip_source;
    const char *tag;
    int diagnostic;
    int abort_on_error;
    int saw_error;
    int error_what;
    int error_stage;
    int error_errno;
    char last_source[1024];
    char last_destination[1024];
    char error_source[1024];
    char error_destination[1024];
    unsigned long files;
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

/* serial.py can only upload below this narrow staging directory.  Delete the
 * executable after all of our libraries are loaded so it cannot become an
 * accidental, persistent part of the seeded volume. */
static void remove_staging_helper(void) {
    if (unlink(STAGING_HELPER) || rmdir(STAGING_DIR)) fail("STAGING_CLEANUP");
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
    if (stage == COPYFILE_ERR && ctx->abort_on_error) {
        if (ctx->saw_error) return COPYFILE_QUIT;
        ctx->saw_error = 1;
        ctx->error_what = what;
        ctx->error_stage = stage;
        ctx->error_errno = errno;
        if (src && strlen(src) < sizeof(ctx->error_source))
            strcpy(ctx->error_source, src);
        if (dst && strlen(dst) < sizeof(ctx->error_destination))
            strcpy(ctx->error_destination, dst);
        printf("DVM_SEED_DIAG_FIRST_ERROR=%s what=%d stage=%d errno=%d src=%s dst=%s last_src=%s last_dst=%s\n",
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
    rc = copyfile(src, dst, state, COPYFILE_RECURSIVE | COPYFILE_ALL);
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
    fd = open(COMPLETE_MARKER, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) fail("MARKER_OPEN");
    if (write(fd, "dvm data seed complete\n", 23) != 23) fail("MARKER_WRITE");
    if (fsync(fd)) fail("MARKER_FSYNC");
    if (close(fd)) fail("MARKER_CLOSE");
    printf("DVM_SEED_MARKER_RC=0\n");
    fflush(stdout);
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
        remove_staging_helper();
        run_probe(&a, argv[3], argv[4], argv[2]);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--full")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        remove_staging_helper();
        run_full(&a, argv[2], argv[3], 0);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--full-aks-setup")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        remove_staging_helper();
        run_full(&a, argv[2], argv[3], 1);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--init-only")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        remove_staging_helper();
        run_init_only(&a, argv[3]);
        return 0;
    }
    if (argc == 4 && exact(argv[1], "--diagnose")) {
        require_layout(argv[2], argv[3]);
        require_empty_data(argv[3]);
        a = load_apis();
        remove_staging_helper();
        run_diagnose(&a, argv[2], argv[3]);
        return 0;
    }
    if (argc == 5 && exact(argv[1], "--diagnose-file")) {
        require_layout(argv[3], argv[4]);
        require_empty_data(argv[4]);
        a = load_apis();
        remove_staging_helper();
        run_diagnose_file(&a, argv[3], argv[4], argv[2]);
        return 0;
    }
    {
        fprintf(stderr, "usage: %s --probe REL %s %s | --diagnose %s %s | --diagnose-file REL %s %s | --full %s %s | --full-aks-setup %s %s | --init-only %s %s\n",
                argv[0], SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT,
                SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT,
                SOURCE_ROOT, DATA_MOUNT,
                SOURCE_ROOT, DATA_MOUNT);
        return 2;
    }
}
