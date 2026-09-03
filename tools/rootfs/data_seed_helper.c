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
 * &error) at 0x1000042b4..cc.  See docs/re/data-volume.md.
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
#define CF_STRING_ENCODING_UTF8 0x08000100U

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
    unsigned long files;
};

static void fail(const char *stage) {
    fprintf(stderr, "DVM_SEED_%s_ERRNO=%d (%s)\n", stage, errno, strerror(errno));
    exit(1);
}

static void fail_rc(const char *stage, int rc) {
    fprintf(stderr, "DVM_SEED_%s_RC=%d\n", stage, rc);
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

static void initialise_data(const struct apis *a, const char *dest) {
    int rc = a->aks_bootstrap_fs(dest, 2);
    printf("DVM_SEED_AKS_RC=%d\n", rc);
    if (rc) fail_rc("AKS", rc);
    rc = a->mkb_create(NULL, dest);
    printf("DVM_SEED_MKB_RC=%d\n", rc);
    if (rc) fail_rc("MKB", rc);
}

static int copy_status(int what, int stage, copyfile_state_t state,
                       const char *src, const char *dst, void *opaque) {
    struct copy_context *ctx = opaque;
    (void)state;
    (void)dst;
    if (src && exact(src, ctx->skip_source)) {
        printf("DVM_SEED_SKIP_HARDWARE=1\n");
        return COPYFILE_SKIP;
    }
    if (what == COPYFILE_RECURSE_FILE && stage == COPYFILE_START) ctx->files++;
    return COPYFILE_CONTINUE;
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

static void run_full(const struct apis *a, const char *source, const char *dest) {
    struct copy_context ctx;
    CFStringRef root, mobile;
    CFErrorRef error = NULL;
    int fd;
    initialise_data(a, dest);
    memset(&ctx, 0, sizeof(ctx)); ctx.skip_source = "/private/var/hardware/private/var/hardware";
    if (copy_tree(source, dest, &ctx)) fail("COPY");
    if (!ctx.files) fail_rc("COPY_FILES", 0);
    printf("DVM_SEED_COPY_FILES=%lu\n", ctx.files);
    root = a->cf_string(NULL, "/", CF_STRING_ENCODING_UTF8);
    mobile = a->cf_string(NULL, "/private/var/mobile", CF_STRING_ENCODING_UTF8);
    if (!root || !mobile) fail_rc("UML_CFSTRING", 0);
    if (!a->uml_create(root, mobile, 0, &error)) fail_rc("UML", 0);
    a->cf_release(root); a->cf_release(mobile);
    printf("DVM_SEED_UML_RC=0\n");
    fd = open(COMPLETE_MARKER, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) fail("MARKER_OPEN");
    if (write(fd, "dvm data seed complete\n", 23) != 23 || fsync(fd) || close(fd)) fail("MARKER_WRITE");
    printf("DVM_SEED_MARKER_RC=0\n");
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
        run_full(&a, argv[2], argv[3]);
        return 0;
    }
    {
        fprintf(stderr, "usage: %s --probe REL %s %s | --full %s %s\n", argv[0], SOURCE_ROOT, DATA_MOUNT, SOURCE_ROOT, DATA_MOUNT);
        return 2;
    }
}
