// Controlled backboardd driver-revision session: announce this process to the
// host, optionally load a host-staged revision and let the host retire this
// generation. Nothing here unloads a module or replaces a live device; the
// only lifecycle boundary is process replacement by launchd.
#include "session_control.h"
#include <errno.h>
#include <objc/message.h>
#include <pthread.h>
#include <sys/stat.h>

typedef void (*DVMEnableSurfaceImportsFn)(id<MTLDevice>, DVMMetalSurfaceProvider);
typedef struct {
    DVMCreateSharedMetalDeviceFn create;
    DVMEnableSurfaceImportsFn enable;
    uint64_t revision, job;
} DVMSessionStaged;

static volatile uint8_t *sessionRAM;
static Namespace *sessionNamespace;
static uint64_t sessionGeneration, sessionRevision, sessionJob;
static NSString *sessionPackageDigest;

static void sessionPut(unsigned offset, uint64_t value) {
    if (!sessionRAM) return;
    mmioBarrier();
    *(volatile uint64_t *)(sessionRAM + offset) = value;
    mmioBarrier();
}
static uint64_t sessionGet(unsigned offset) {
    return sessionRAM ? *(volatile uint64_t *)(sessionRAM + offset) : 0;
}
static void sessionReportPath(const char *path) {
    struct stat st;
    int rc = stat(path, &st);
    DVMReport(stderr, "GPU_LOAD_SESSION_PATH path=%.90s rc=%d errno=%d mode=%o uid=%d gid=%d\n",
        path, rc, rc ? errno : 0, rc ? 0u : (unsigned)st.st_mode, rc ? -1 : (int)st.st_uid, rc ? -1 : (int)st.st_gid);
}
// CA_SYSTEM_LOAD_GUEST1 failed here with errno 2 and no further evidence.
// Create every component explicitly and record the exact failing one.
static BOOL sessionMakeDirectory(const char *path) {
    int (*mk)(const char *, mode_t) = dlsym(RTLD_DEFAULT, "mkdir");
    if (!mk) return NO;
    if (!mk(path, 0700)) return YES;
    int failure = errno;
    struct stat st;
    int rc = stat(path, &st);
    BOOL present = !rc && S_ISDIR(st.st_mode);
    DVMReport(stderr, "GPU_LOAD_SESSION_MKDIR path=%.90s errno=%d stat=%d directory=%d mode=%o uid=%d\n",
        path, failure, rc ? errno : 0, present, rc ? 0u : (unsigned)st.st_mode, rc ? -1 : (int)st.st_uid);
    return failure == EEXIST && present;
}

// The category on the built-in DVMDevice cannot reach a staged revision's
// distinctly named class. Attach the identical behaviour at load time.
static uint64_t DVMSessionRegistryIMP(id self, SEL cmd) { (void)self; (void)cmd; return systemRegistryID; }
static void DVMSessionCapabilityIMP(id self, SEL cmd) {
    (void)cmd;
    if ([self respondsToSelector:@selector(contractCapabilities)])
        ((id (*)(id, SEL))objc_msgSend)(self, @selector(contractCapabilities));
}
static void DVMSessionNoopIMP(id self, SEL cmd) { (void)self; (void)cmd; }
static void DVMSessionMissingIMP(id self, SEL cmd, SEL missing) {
    (void)cmd;
    DVMReport(stderr, "GPU_LOAD_SYSTEM_MISSING class=%s selector=%s\n", class_getName([self class]), sel_getName(missing));
    [NSException raise:NSInvalidArgumentException format:@"-[%s %s]: unrecognized selector sent to instance %p",
        class_getName([self class]), sel_getName(missing), (void *)self];
}
// BBRELOAD_SESSION1 observed /private/var (0755) and /private/var/tmp (1777)
// but ENOENT for the installed staging root inside it, which the installer had
// just verified on the Data volume. List the parent and try the process's own
// temporary directory before concluding anything about that difference.
static void sessionReportDirectory(NSString *path) {
    NSError *error = nil;
    NSArray<NSString *> *entries = [NSFileManager.defaultManager contentsOfDirectoryAtPath:path error:&error];
    DVMReport(stderr, "GPU_LOAD_SESSION_LIST path=%.70s count=%ld error=%ld\n",
        path.UTF8String, (long)entries.count, (long)(entries ? 0 : error.code));
    for (NSUInteger i = 0; i < entries.count && i < 12; i++)
        DVMReport(stderr, "GPU_LOAD_SESSION_ENTRY index=%lu name=%.70s\n", (unsigned long)i, entries[i].UTF8String);
}
// The first writable candidate wins. TMPDIR is this process's own sandboxed
// temporary directory and needs no path exception; the installed root and the
// mobile home directory are the fallbacks the installer also prepares.
static NSString *sessionStageRoot(void) {
    static NSString *chosen;
    static BOOL resolved;
    if (resolved) return chosen;
    resolved = YES;
    NSMutableArray<NSString *> *candidates = [NSMutableArray array];
    const char *temporary = getenv("TMPDIR");
    if (temporary && *temporary) [candidates addObject:[@(temporary) stringByAppendingPathComponent:@"dvm-gpu-runner"]];
    [candidates addObject:@DVM_SESSION_STAGE_ROOT];
    [candidates addObject:@"/private/var/mobile/dvm-gpu-runner"];
    for (NSString *candidate in candidates) {
        if (sessionMakeDirectory(candidate.UTF8String)) { chosen = candidate; break; }
    }
    DVMReport(stderr, "GPU_LOAD_SESSION_STAGE_ROOT path=%.90s tmpdir=%.70s\n",
        chosen.UTF8String ?: "(none)", temporary ?: "(unset)");
    return chosen;
}

static void DVMSessionAdoptDeviceClass(Class device) {
    Protocol *spi = objc_getProtocol("MTLDeviceSPI");
    if (!device || !spi) fail("session-adopt-protocol");
    class_addMethod(device, @selector(registryID), (IMP)DVMSessionRegistryIMP, "Q@:");
    class_addMethod(device, @selector(initLimits), (IMP)DVMSessionCapabilityIMP, "v@:");
    class_addMethod(device, @selector(initFeatureQueries), (IMP)DVMSessionCapabilityIMP, "v@:");
    class_addMethod(device, @selector(initWorkarounds), (IMP)DVMSessionNoopIMP, "v@:");
    class_addMethod(device, @selector(doesNotRecognizeSelector:), (IMP)DVMSessionMissingIMP, "v@::");
    if (!class_conformsToProtocol(device, spi) && !class_addProtocol(device, spi)) fail("session-adopt-registration-protocol");
    DVMReport(stderr, "GPU_LOAD_SESSION_ADOPT class=%s registry=%llu\n", class_getName(device), systemRegistryID);
}

// Give up the registry ownership this process claimed. Without it the provider
// keeps the dead IOUserClient in DVMSurfaceOwner/DVMManagedOwner and every
// registration by a replacement returns kIOReturnNotPermitted, as
// BBRELOAD_SESSION2 generation 2 observed (kr=e00002e2).
static BOOL DVMSessionRelinquishRegistry(Namespace *ns) {
    uint64_t input[3] = {1, 0, 0}, output[4] = {0};
    uint32_t count = 4;
    kern_return_t kr = IOConnectCallScalarMethod(ns.client, 0x44565303, input, 3, output, &count);
    BOOL ok = !kr && count == 4 && output[0] == 1;
    DVMReport(stderr, "GPU_LOAD_SESSION_RELINQUISH kr=%x count=%u ok=%d owned=%llu\n", kr, count, ok, output[1]);
    return ok;
}

static void DVMSessionRetire(void) {
    Namespace *ns = sessionNamespace;
    // Recursive: the retiring thread owns the transport for the rest of this
    // process's life, so no compositor thread can start another request and
    // none is outstanding. Host GPU work is synchronous inside a request
    // (driver_host.m [cb commit]/[cb waitUntilCompleted]), so holding this
    // lock is also the GPU quiescence point.
    @synchronized(ns) {
        sessionPut(DVM_SESSION_GUEST_STATE, DVM_SESSION_STATE_RETIRING);
        uint64_t registered = 0, alreadyRetired = 0, retired = 0;
        NSArray<NSNumber *> *outstanding = @[];
#ifdef DVM_SURFACE_IMPORT
        DVMImportedSurfaceCounts(&registered, &alreadyRetired);
        outstanding = DVMImportedOutstandingIDs();
#endif
        NSError *error = nil;
        // The host destroys the Metal worker that could alias these pages and
        // only then writes each retirement tombstone. Nothing is unpinned
        // because a process disappeared: the tombstone is the host's proof
        // that its aliases are gone, and the kernel unpin below is this
        // process's own explicit act while it is still alive.
        NSDictionary *quiesce = [ns call:@{@"op":@"sessionQuiesce", @"generation":@(sessionGeneration),
            @"pid":@(getpid()), @"imports":outstanding} error:&error];
        NSArray<NSNumber *> *tombstones = quiesce[@"tombstones"];
        DVMReport(stderr, "GPU_LOAD_SESSION_QUIESCE generation=%llu outstanding=%lu tombstones=%lu\n",
            sessionGeneration, (unsigned long)outstanding.count, (unsigned long)tombstones.count);
#ifdef DVM_SURFACE_IMPORT
        for (NSNumber *identifier in tombstones)
            if (DVMImportedRetireID(identifier.unsignedLongLongValue)) retired++;
        DVMImportedSurfaceCounts(&registered, &alreadyRetired);
        retired = alreadyRetired;
#endif
        BOOL relinquished = DVMSessionRelinquishRegistry(ns);
        NSDictionary *reply = [ns call:@{@"op":@"sessionRetire", @"generation":@(sessionGeneration),
            @"pid":@(getpid()), @"revision":@(sessionRevision), @"job":@(sessionJob),
            @"imports":@(registered), @"importsRetired":@(retired),
            @"relinquished":@(relinquished), @"requests":@(ns.sequence)} error:&error];
        sessionPut(DVM_SESSION_GUEST_REQUESTS, ns.sequence);
        sessionPut(DVM_SESSION_GUEST_IMPORTS, registered);
        sessionPut(DVM_SESSION_GUEST_IMPORTS_DONE, retired);
        DVMReport(stderr, "GPU_LOAD_SESSION_RETIRE generation=%llu pid=%d revision=%llu requests=%llu imports=%llu retired=%llu quarantined=%llu relinquished=%d acknowledged=%d\n",
            sessionGeneration, getpid(), sessionRevision, ns.sequence, registered, retired,
            registered - retired, relinquished, reply != nil);
        sessionPut(DVM_SESSION_GUEST_STATE, DVM_SESSION_STATE_RETIRED);
        // Any thread that reaches the transport after this point must fail
        // rather than reach the replacement worker with this generation's
        // handles. It cannot: this thread holds the lock until _exit.
        ns.failed = YES;
        fflush(NULL);
        // launchd KeepAlive respawns this job. Whether a deliberate zero exit
        // is recovered the way a crash was in BBRELOAD_SESSION2 is a guest
        // hypothesis this session has to measure, not an established fact.
        _exit(0);
    }
}
static void *DVMSessionControlThread(void *unused) {
    (void)unused;
    for (;;) {
        usleep(100000);
        if (sessionGet(DVM_SESSION_MAGIC_OFFSET) != DVM_SESSION_MAGIC) continue;
        uint64_t requested = sessionGet(DVM_SESSION_RETIRE_GENERATION);
        if (requested && requested == sessionGeneration) DVMSessionRetire();
    }
    return NULL;
}

static void DVMSessionBegin(Namespace *ns) {
    sessionNamespace = ns;
    sessionRAM = ns.shared;
    if (sessionGet(DVM_SESSION_MAGIC_OFFSET) != DVM_SESSION_MAGIC) fail("session-control-magic");
    NSError *error = nil;
    NSDictionary *hello = [ns call:@{@"op":@"sessionHello", @"pid":@(getpid()),
        @"sequence":@(ns.sequence), @"unix":@((uint64_t)time(NULL))} error:&error];
    if (!hello || ![hello[@"generation"] unsignedLongLongValue]) fail("session-hello");
    sessionGeneration = [hello[@"generation"] unsignedLongLongValue];
    sessionRevision = DVM_SESSION_BUILTIN_REVISION;
    sessionPut(DVM_SESSION_GUEST_GENERATION, sessionGeneration);
    sessionPut(DVM_SESSION_GUEST_REVISION, sessionRevision);
    sessionPut(DVM_SESSION_GUEST_PID, (uint64_t)getpid());
    sessionPut(DVM_SESSION_GUEST_STATE, DVM_SESSION_STATE_STARTING);
    DVMReport(stderr, "GPU_LOAD_SESSION_BEGIN generation=%llu pid=%d resumed_sequence=%llu\n",
        sessionGeneration, getpid(), ns.sequence);
    pthread_t thread;
    pthread_attr_t attributes;
    if (pthread_attr_init(&attributes)) fail("session-control-attributes");
    pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&thread, &attributes, DVMSessionControlThread, NULL)) fail("session-control-thread");
    pthread_attr_destroy(&attributes);
}

static void DVMSessionStagedResult(Namespace *ns, uint64_t job, BOOL loaded, const char *reason, Class device) {
    NSError *error = nil;
    (void)[ns call:@{@"op":@"sessionStaged", @"generation":@(sessionGeneration), @"job":@(job),
        @"pid":@(getpid()), @"loaded":@(loaded ? YES : NO), @"revision":@(sessionRevision),
        @"class":device ? @(class_getName(device)) : @"", @"reason":@(reason),
        @"sha256":sessionPackageDigest ?: @"", @"stageRoot":sessionStageRoot() ?: @""} error:&error];
}

// Returns NULL on success. Every failure names the exact contract that failed;
// ARC forbids jumping past initialised strong locals, so this reports by
// return value rather than by a shared error label.
static const char *DVMSessionFetchStaged(Namespace *ns, NSDictionary *job, DVMSessionStaged *out) {
    NSError *error = nil;
    uint64_t jobID = [job[@"job"] unsignedLongLongValue];
    NSUInteger length = [job[@"bytes"] unsignedIntegerValue];
    if (!jobID || !length || length > 8 * 1024 * 1024 || ![job[@"mode"] isEqual:@"data"]) return "package";
    NSMutableData *data = [NSMutableData dataWithCapacity:length];
    while (data.length < length) {
        NSDictionary *chunk = [ns call:@{@"op":@"runnerFetch", @"job":@(jobID), @"offset":@(data.length)} error:&error];
        NSData *part = [[NSData alloc] initWithBase64EncodedString:chunk[@"data"] options:0];
        if (!part.length || part.length > 32768 || part.length > length - data.length) return "chunk";
        [data appendData:part];
    }
    uint8_t digest[32];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    char hex[65];
    for (unsigned i = 0; i < 32; i++) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    if (![@(hex) isEqual:job[@"sha256"]]) return "digest";
    sessionPackageDigest = @(hex);
    // Thin arm64e is required by this exact backboardd, independently of the
    // arm64 payload the old development runner accepted.
    const uint32_t *header = data.bytes;
    if (data.length < 32 || header[0] != 0xfeedfacf || header[1] != 0x100000c || (header[2] & 0xffffff) != 2) return "arm64e";
    NSString *root = sessionStageRoot();
    if (!root) return "stage-root";
    NSString *directory = [root stringByAppendingPathComponent:[NSString stringWithFormat:@"%llu", jobID]];
    NSString *bundle = [directory stringByAppendingPathComponent:@"DVMProxy.bundle"];
    NSString *signature = [bundle stringByAppendingPathComponent:@"_CodeSignature"];
    if (!sessionMakeDirectory(directory.UTF8String)) return "stage-directory";
    if (!sessionMakeDirectory(bundle.UTF8String)) return "stage-bundle";
    if (!sessionMakeDirectory(signature.UTF8String)) return "stage-signature";
    NSData *info = [[NSData alloc] initWithBase64EncodedString:job[@"info"] options:0];
    NSData *resources = [[NSData alloc] initWithBase64EncodedString:job[@"resources"] options:0];
    if (!info.length || !resources.length || info.length + resources.length > 16384) return "signing-metadata";
    NSDictionary *files = @{@"DVMProxy":data, @"Info.plist":info, @"_CodeSignature/CodeResources":resources};
    for (NSString *name in files) {
        if (![files[name] writeToFile:[bundle stringByAppendingPathComponent:name] options:NSDataWritingAtomic error:&error]) {
            DVMReport(stderr, "GPU_LOAD_SESSION_WRITE name=%.60s errno=%d\n", name.UTF8String, errno);
            return "write";
        }
    }
    DVMReport(stderr, "GPU_LOAD_SYSTEM_REVISION_STAGED pid=%d job=%llu bytes=%lu sha256=%s\n",
        getpid(), jobID, (unsigned long)length, hex);
    if (![[NSBundle bundleWithPath:bundle] loadAndReturnError:&error]) {
        const char *text = error.description.UTF8String;
        for (unsigned i = 0; text && *text && i < 12; i++) {
            unsigned n = 0;
            while (n < 280 && text[n]) n++;
            DVMReport(stderr, "GPU_LOAD_SYSTEM_REVISION_LOAD_ERROR part=%u text=%.*s\n", i, n, text);
            text += n;
        }
        return "bundle-load";
    }
    void *handle = dlopen([bundle stringByAppendingPathComponent:@"DVMProxy"].UTF8String, RTLD_NOW | RTLD_LOCAL);
    uint64_t (*revision)(void) = handle ? dlsym(handle, "DVMRevisionNumber") : NULL;
    DVMCreateSharedMetalDeviceFn create = handle ? dlsym(handle, "DVMCreateSharedMetalDevice") : NULL;
    DVMEnableSurfaceImportsFn enable = handle ? dlsym(handle, "DVMEnableSurfaceImports") : NULL;
    if (!revision || !create || !enable) return "exports";
    sessionRevision = revision();
    sessionPut(DVM_SESSION_GUEST_REVISION, sessionRevision);
    if (!sessionRevision || sessionRevision != [job[@"revision"] unsignedLongLongValue]) return "revision-identity";
    out->create = create;
    out->enable = enable;
    out->revision = sessionRevision;
    out->job = jobID;
    // The loaded image is retained for this process's whole life on purpose.
    return NULL;
}

static DVMSessionStaged DVMSessionLoadStagedRevision(Namespace *ns) {
    DVMSessionStaged staged = {0};
    NSError *error = nil;
    sessionReportPath("/private/var");
    sessionReportPath("/private/var/tmp");
    sessionReportPath(DVM_SESSION_STAGE_ROOT);
    sessionReportPath("/private/var/mobile");
    sessionReportDirectory(@"/private/var/tmp");
    (void)sessionStageRoot();
    NSDictionary *job = [ns call:@{@"op":@"runnerNext"} error:&error];
    if (!job) fail("session-stage-query");
    if (![job[@"action"] isEqual:@"stage"]) {
        DVMReport(stderr, "GPU_LOAD_SESSION_STAGE_NONE generation=%llu action=%.24s\n",
            sessionGeneration, [job[@"action"] UTF8String] ?: "(absent)");
        return staged;
    }
    sessionJob = [job[@"job"] unsignedLongLongValue];
    const char *reason = DVMSessionFetchStaged(ns, job, &staged);
    if (reason) {
        DVMReport(stderr, "GPU_LOAD_SESSION_STAGE_FAILED generation=%llu job=%llu reason=%s errno=%d\n",
            sessionGeneration, sessionJob, reason, errno);
        sessionRevision = DVM_SESSION_BUILTIN_REVISION;
        sessionPut(DVM_SESSION_GUEST_REVISION, sessionRevision);
        DVMSessionStagedResult(ns, sessionJob, NO, reason, Nil);
        // Registering the built-in driver keeps this boot usable and makes the
        // failure explicit to the host rather than respawning into a loop.
        staged.create = NULL;
        staged.enable = NULL;
    }
    return staged;
}
