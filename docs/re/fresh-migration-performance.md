# Fresh migration and interactive performance investigation

Active goal, 2026-09-05: fix SEP failures on the fresh bootstrap path and
achieve a meaningful measured improvement in both migration time and VM
responsiveness. Reaching SpringBoard alone is not completion. Keep six CPUs
as the preferred configuration. Validate the complete cold-bootstrap-to-apps
and display path after diagnosis with saved RAM/device checkpoints.

The fresh seed is `/tmp/dvm/APP_FRESH1/install/disk.qcow2`, sealed before its
first normal boot. `APP_FIRST_MIGRATION_WAIT1` is a healthy checkpoint during
the initial LaunchServices migration, before the newly observed SEP failure;
it is not yet the requested earlier pre-migration checkpoint.
`APP_DATA34_REJECTED1` preserves the failed state and must not be used as a
healthy provisioning parent.

`APP_MIGRATION_R1/qemu.stderr.log:96321` rejects a 164-byte opcode 0x0f
Data-volume class transfer 3 -> 4 and sends no reply. Its DART-recovered
request has SHA256
`ec1fd20016257e9851cbf476238e58c687a5959b7b57a0429d179c662b026fe7`.
The source/target fields are at +0x68/+0x6c, length 28 at +0x84, and the
existing Data-volume tag at +0x90. The zero length in the rejection message
is a diagnostic limitation. Native class plumbing is established at
AppleSEPKeyStore static 0xfffffff0095730a0..0x957311c.
Container Manager pids 83 and 125 and lsd pid 119 wait on event
0xffffffea63b3e3e0 through file writes/APFS/AppleSEPKeyStore; saved process
and kernel-frame evidence is under `/tmp/dvm/APP_MIGRATION_R1/`.

Comparison requirements: same checkpoint, milestone and completed-bundle
count; active wall time excludes pauses and capture/restore; no LLDB or
breakpoints for performance controls; separate host-display and tracing
variables; per-thread samples; native plugin timing and guest-clock checks.
Device request counts alone are not completed-app counts. Record absolute
times, repeatability, and display/input latency before claiming improvement.

All code work is isolated in superproject branch `codex/app-install-debug`
and QEMU branch `codex/sep-migration-perf`, initially based on b0f7bc6 so
the captured SEP fix can be compared independently of later HID changes.

## Rejected framebuffer candidate and timing controls

The first symmetric Cocoa replay used `APP_MIGRATION_NATIVE_180` and the v3
milestone plugin, six CPUs, no LLDB connection or breakpoints. Both stopped
at 100 successful `LS_SCAN_RETURN` events (`x0 == 1`):

| Run | Host framebuffer behavior | Active wall seconds |
| --- | --- | ---: |
| `APP_LSPERF_BASE1` | Existing full refresh | 127.159 |
| `APP_LSPERF_DIRTY1` | Per-row memcmp against a host shadow | 145.375 |

Sources: each run's `/tmp/dvm/<run>/timing.json` and `milestones.tsv`.
The candidate was 14.3% slower in this pair and was removed from the worktree.
This is not a demonstrated optimization. The v3 observer also decoded
migration log strings and bundle objects, so these are preliminary control
measurements, not the final low-overhead benchmark. The v4 observer adds
`scans-only=on`: only the scan-return instruction gets a callback, which reads
x0 and does no guest-memory reads. Scan success is not installed-app success.

The first v4 launch `APP_SCAN_MIN_COCOA1` exited before restore because its
plugin output directory did not exist. It supplies no runtime measurement.
`APP_SCAN_MIN_COCOA2` specifies the run output directory explicitly.

The restore helper's display override now removes every original `-display`
argument before appending the selected backend. Previously a later Cocoa
argument could override an earlier `none`, invalidating a headless control.
The checkpoint tool regressions pass 12 tests; log:
`/tmp/dvm/APP_CHECKPOINT_TESTS-latest.log`.

## Next credential-service request

After the exact SKS Data 3 -> 4 parser extension, `APP_SCAN_COCOA_BASE1`
reaches another missing reply at `qemu.stderr.log:7374`: endpoint SCRD,
command 0x24, request body 40 bytes. Its captured body is
`/tmp/dvm/APP_SCAN_COCOA_BASE1/scrd24-request.bin`, SHA256
`2e9047c7ab17157633dd56832d26950558883c38d6bbf1ab87f554dfc1e695d5`.
`APP_SCRD24_WAIT1` preserves this failed/pending request state; it is not a
healthy provisioning checkpoint. Earlier fresh-boot serial output
`/tmp/dvm/APP_FIRST_BOOT2/APP_FIRST_BOOT2.serial.log:4693,5936,7176` records command 36 waits around 5002 ms. The watch
now records the actual no-reply line and labels its stop `SEP-no-reply`,
rather than implying every missing reply is an SKS rejection.

Static evidence in the extracted AppleSEPCredentialManager kext:

- `0xfffffff00952d270..0xfffffff00952d29c` selects command 0x24 for a tracked,
  auto-disposable context and calls the transport with output capacity 21.
- `0xfffffff00952d2a4..0xfffffff00952d2ac` requires exactly 21 response bytes.
- `0xfffffff00952921c..0xfffffff0095292b4` removes the response envelope
  header and copies the remaining payload to the caller. A 12-byte header
  would therefore require a 33-byte total OOL response for this command.
- `0xfffffff00952d3dc..0xfffffff00952d3fc` copies payload bytes 0..15 into
  the context, bytes 17..20 into context offset 16, and optionally returns
  payload byte 16 separately.
- `0xfffffff00952d9b4..0xfffffff00952d9d4` sends the first 16 context bytes
  back to SEP for command 2 (context deletion).
- `0xfffffff009533640..0xfffffff009533688` also includes those 16 bytes in
  the command 0x2e context-info request.

Thus a zero-filled success payload cannot yet be justified as a complete
credential-context model: the handle participates in later SEP operations.
The framing and first consumer are established; handle lifecycle and the
meaning of the extra five bytes remain open. No SCRD success behavior was
added on the strength of length checks alone.

### Minimal observer, initial display comparison

Both v4 runs use the same checkpoint, six-core QEMU `-O2` build, PAuth cache
on, and the scans-only observer, without LLDB or thread sampling:

| Run | Display | Stop active wall seconds | Successful scans at stop |
| --- | --- | ---: | ---: |
| `APP_SCAN_MIN_COCOA2` | Cocoa | 60.124 | 100 |
| `APP_SCAN_MIN_HEADLESS1` | none | 57.301 | 101 |

The watcher polls every 0.25 seconds, so the headless run passed the
100-scan boundary by one event. Future runs also record the exact host
monotonic timestamp of the 100th success. These initial values suggest a
small display-backend effect; repeatability is not yet established.
The Cocoa v4 run's first-to-100th scan interval is 20.900954 seconds,
compared with 80.356277 for the richer v3 baseline. Observer overhead is
therefore a material confounder, not evidence of an improvement to a normal
plugin-free VM. The existing guest-model behavior is identical in these
baseline runs. The other agent's QEMU (PID 96635) was idle at 0.0% CPU during
the headless control; no LLDB process was present in the host process list.

Build options inspected from `qemu-sptm/build/meson-info/intro-buildoptions.json`:
optimization 2, LTO false, assertions retained (`b_ndebug=false`), debug true.
An O3/LTO comparison remains actionable using `tools/build_qemu_fast.sh` in
this isolated worktree, with all owned VMs stopped during the build.

`APP_SCAN_MIN_COCOA3` repeated Cocoa and reached exactly 100 successful scans
at 55.002638 active seconds (stop acknowledged at 55.069). This is faster
than the headless value and establishes that the initial 4.7% headless
advantage is smaller than observed run-to-run variation. Do not claim a
Cocoa bottleneck from this display comparison. The O3/LTO build started
only after this run's QEMU was quit; build log `/tmp/dvm/APP_O3LTO-build.log`.

The captured `APP_FIRST_BOOT2/APP_FIRST_BOOT2.serial.log` contains three
command-36 waits totalling 15.006 seconds and one command-51 wait of 5.002
seconds. Those observed waits matter for correctness and latency, but this
partial log does not explain 40 minutes by itself; no aggregate attribution
of the full migration delay to SEP has been established.

### Clock audit still requires a guest witness

The PMU source uses `rtc_clock` for its 32768-Hz upcount
(`darwin_pmu.c:123`) and serializes `clock_base_ns` in VMState. QEMU defaults
that clock to `QEMU_CLOCK_HOST` (`system/rtc.c:146`), so wall time continues
across pauses/restores by design. This does not prove the behavior of guest
mach-time-based migration deadlines. The checkpoint helper's serial hex-clock
continuity check is also narrower than a guest wall/monotonic-clock comparison.
Keep that explicit validation gate open; these source facts alone cannot rule
out deadline artifacts in restored migration runs.

`APP_SCAN_FAST_COCOA1` (O3/LTO, Cocoa, same v4 scans-only observer) reached
100 successful scans at 53.204019 active seconds; stop at 53.249. Compared
with the immediately preceding O2 Cocoa repeat (55.002638), the single-run
reduction is 3.27%, within the variation already observed between O2 runs.
No meaningful performance gain has yet been demonstrated. Both build
binaries remain available for repeated/profiling controls, and this QEMU was
quit after collection. The SEP context lifecycle and end-to-end app migration
remain unresolved; these scan timings are not whole-migration timings.

## Stateful SCRD progress after the performance controls

The opt-in `DARWIN_SCRD_CONTEXTS=1` model now completes the observed
0x24 create -> 0x13 export -> 0x02 delete sequence on an empty credential
context. It generates unique opaque handles, records owner and tracking
number, rejects unissued/mismatched-owner handles, and reclaims deleted slots.
The guest's explicit follow-on requests establish acceptance of each reply;
see `acm-scrd-response-contract.md` for request hashes and consumer addresses.
`APP_SCRD_CONTEXT4` runs without LLDB/plugins for 120.048 active seconds
without another missing SEP reply. DataMigrator remains alive with XPC and
dispatch waits; there is no completed-migration or performance claim yet.

`APP_SCRD_LIFECYCLE1` is the new later checkpoint for continuing diagnosis.
`APP_SCRD_STATE_V2` loads its optional version-2 context table and restores
the checkpoint PC while paused; stderr line 54 reports one table slot and
last tracking number 1 (the slot has been deleted/reclaimed).

## Later migration: dependency queue and signature validation

`APP_MIGRATION_AFTER_CONTEXT1` resumes the lifecycle checkpoint for 180.117
active seconds without another SEP no-reply. Its diagnostic 3-second host
sample assigns CPU0..3 observations as follows: guest execution/helpers
42.0%, TB lookup 17.0%, MMU 12.4%, pointer authentication 9.7%, mutex wait
9.7%, with storage command stacks 0.3%. CPU4/5 spend about 96% in condition
waits. These are sampled thread observations, not CPU-time attribution.
Source: `host-profile.txt`, parsed explicitly with `smp_storage_report.sample_tree`
into `profile-summary.json` (the directory CLI does not match this filename).

The DataMigrator dependency block at runtime 0x7634c09630 has invoke address
0x10417ef18. Static 0x10000eff0..0x10000f024 checks each pending plugin's
`identifierOfDependency` against its captured completed set; 0x10000f15c..
0x10000f17c waits while pending entries remain. The exact `__NSSetM`
enumerator at CoreFoundation 0x1806206cc..0x180620748 supplies storage
geometry, capacity table `__NSSetSizes` at 0x18097aa58, and deleted-marker
address 0x1e8d94790. The read-only decoder uses those bounds and skips the
actual tombstone, rather than guessing bucket count.

At the frozen end of this run, the pending set contains only
`com.apple.sbmigrator`, dependent on `com.apple.WiFiDataMigrator`. Its captured
completed set contains seven identifiers. This does not prove Wi-Fi is the
current bottleneck: the active wrapper process (pid 442) is in
SystemAppMigrator's signature-validation call at runtime 0x104432c98/static
0x6c98. The exact call and return instruction bytes match the extracted
SystemAppMigrator image. Its stack passes through MISValidateSignature,
Security BundleDiskRep::checkMoved, and getattrlist; the current syscall path
is `/private/var/containers/Bundle`. The source at 0x6c50..0x6c98 selects
signature-only/trust-cache-only options. This is active validation work,
not evidence of a Wi-Fi timeout.

Evidence: `APP_MIGRATION_AFTER_CONTEXT1/dependencies3/wait.json`,
`processes/migration-processes.json`, and `signature/signature.json`.
`APP_SYSAPP_SIGNATURE1` captures this later state. A v5 minimal observer adds
only call/return callbacks at the verified image PCs to count signature
results and duration. Signature success is still distinct from app install
success. `APP_SYSAPP_TRACE1` is the next bounded observation.

`APP_SYSAPP_TRACE1` observes 19 remaining signature returns, all status 0,
with 18 paired calls. All returns span 0.742634 seconds; the first call was
already in progress at checkpoint capture. The native checks therefore do
not explain a multi-minute delay at this point. At the 60-second stop, the
old wrapper pid 442 has exited, a new wrapper pid 700 is active, and the
DataMigrator dependency-wait frame is absent. The earlier pending SpringBoard
entry is not proof that Wi-Fi was stalled. New process and stack evidence is
under `APP_SYSAPP_TRACE1/processes/` and `dependencies/wait.json`.

### Concrete five-minute launch-image wait

The new wrapper pid 700 is SpringBoard.migrator, not WiFiDataMigrator.
Its saved frame at runtime 0x102c1e034 matches static 0x6034 in the extracted
SpringBoard.migrator image (image base 0x102c18000). At 0x5f58 the enclosing
method is `-[SBSplashBoardMigrationController performSystemAppMigrationRecreating:]`.
It calls `captureOrUpdateLaunchImagesForApplications:firstImageIsReady:completion:`
at 0x600c, then constructs dispatch_time delta 0x45d964b800 (300,000,000,000
ns = five minutes) at 0x6014..0x6024 and waits on a semaphore at 0x6030.
This is a concrete long-deadline candidate, not yet proof the full timeout
expires. It warrants tracing the launch-image completion before changing
hardware or skipping migration. `APP_SPLASHBOARD_WAIT1` preserves this state.
The two relevant binaries were extracted read-only with 7zz from the original
OS image into `APP_FRESH1/migrator-payloads/`; extraction log is adjacent.

## Continued clean replay: context data and launch-image wait

`APP_SPLASH_TRACE1` restored `APP_SPLASHBOARD_WAIT1` with six CPUs,
headless display, no plugins, and no LLDB. The condition watcher paused it
at 4.083 active seconds after SCRD command 0x28, 73 bytes, received no reply
(`qemu.stderr.log:1695`; `timing.json`). The preceding create at lines
1680–1681 issued CS[2] to owner 501; environment query 0x19 followed.
This is another unresolved SEP operation, not evidence of a completed migration
or a measured performance gain. `APP_SCRD28_WAIT1` preserves the failed state;
use the earlier `APP_SPLASHBOARD_WAIT1` for replay before the request.

Captured `APP_SPLASH_TRACE1/scrd28.bin` has SHA256
`9fbadc3fe842d7c8e08f1ab1fa227a6347c2165ceabe52a158c9487ebac9902c`.
Kernel `LibCall_ACMContextSetData` selects command 0x28 at
`0xfffffff009532b24..0xfffffff009532b44`, with no output payload.
Its serializer at `0xfffffff0095092ac..0xfffffff0095092e0` writes handle16,
data type u32, data length u32, data bytes, then serialized parameters.
The captured payload has type 5 and zero data length, followed by one parameter
(type 14, length 1, byte 0). Semantics of that data type and parameter still
need confirmation before modeling a successful mutation. LocalAuthenticationCore
`___43-[LACACMHelper setData:type:encoded:error:]_block_invoke` at
`0x20628c988..0x20628ca2c` supplies one parameter to ACMContextSetDataEx;
its byte is 2 only for an encoded, nonempty value, otherwise 0. This identifies
framing, not the semantic purpose of this particular type-5 request.

The same paused process inspection (`processes/migration-processes.json`)
finds SpringBoard.migrator pid 700 still waiting at runtime 0x102c1e034,
its outer 300-second launch-image deadline. Another thread is at runtime
0x2c22d8834 = SplashBoard static 0x2ac044834 (slide 0x16294000), inside
`XBLaunchImageProvider`'s capture block. At static 0x2ac044824 it invokes
`_generateImageForSnapshot:inManifest:withContext:asyncImageData:dataProvider:scheduleAsyncGeneration:completion:`
(selector stub 0x2b00498d0), then calls dispatch_semaphore_wait with
DISPATCH_TIME_FOREVER at 0x2ac044828..0x2ac044830 (stub 0x2b00d7490).
This gives an inner image-generation completion wait to trace. Neither the
five-minute deadline expiring nor a causal connection to SCRD is established.
The original main-thread compatibility-info decoding has advanced to its
runloop, so that earlier single stack was not proof of a decoding hang.

`APP_SCRD28_FIXED1` replays the healthy SplashBoard checkpoint with the
empty-data reset handler, six CPUs, headless, no plugins/LLDB. At stderr
1737 command 0x28 receives its 12-byte reply; 1738 exports CS[2], 1745 creates
CS[3], and 1748–1749 delete CS[2]. Native progression beyond the former wait
is therefore observed. The watcher stops at 4.340 active seconds on the next
unknown command, 0x29/61 bytes (line 1756). This is not a whole-migration timing
comparison. Build passed; 69 host, 4 SCRD parser, and 26 SKS tests passed.

The next body is `APP_SCRD28_FIXED1/scrd29.bin`, SHA256
`b9df0d259b92be15e5b66655cba58b148b87f1fb64bf8a7f0f449f799e6b732e`.
`APP_SCRD29_WAIT1` preserves this failed request. Kext
`LibCall_ACMContextGetData` selects 0x29 at 0xfffffff009532eb8..0xfffffff009532ed4.
Payload starts with the newly issued token, then u32 13, u32 1, byte 0;
request serializer 0xfffffff0095096f8 and the native caller/response consumer
remain to be traced. Do not assume all context data queries mean empty output.

## Launch-image ASTC work persists after the seed-query failure

`APP_SCRD29_CALLER1` resumed the failed 0x29 checkpoint for 60.083 active
seconds with six CPUs, headless, no plugin/LLDB, and a three-second host
sample beginning at active second 1. The watcher saw no additional no-reply
requests. Context CS[3] was deleted at stderr 1736–1737. This is a diagnostic
failure-path observation, not a healthy provisioning parent or success proof.

After the observation, pid 700 remains at outer migration wait 0x102c1e034
and inner image wait 0x2c22d8834. Its active image worker thread
0xffffffe89f52c180 has PC 0x2599ce674, static vImage 0x24373a674,
`vConvert_ARGBFFFFToPlanarF_CV_vec +296`. The stack includes ImageIO
`IIOIOSurfaceWrapper_CIF10::copyImageBlockSet_8bit` (static 0x18795b998),
`ATXWritePlugin::writeASTCData` (0x18793e59c),
`IIOImageDestination::finalizeDestination` (0x187917570), and
`CGImageDestinationFinalizeEx` (0x18791635c). SplashBoard callers are
`+[XBApplicationSnapshot dataForImage:withFormat:]` at static 0x2ac03cd40,
`_generate_imageFromNewDataProvider:forSnapshot:imageDataHandler:` block at
0x2ac02dd38, and `performImageDataGenerationAsyncAndWait:withHandler:` at
0x2ac039f90. Runtime slide is 0x16294000.

Evidence lives in `APP_SCRD29_CALLER1/after/migration-processes.json` and
`host-profile.txt`; checkpoint `APP_SPLASH_ASTC1` preserves the later diagnostic
state. This identifies ASTC snapshot encoding on the migration dependency
path. A single stack does not establish its total cost, whether it is the same
image as before, or the performance benefit of changing its format. Next:
count completed snapshots, time encoding calls, and inspect native format
selection before considering any compatibility override.

## Sparse snapshot timing and native format gate

`APP_ASTC_TIMING1` restores `APP_SPLASH_ASTC1`, six CPUs/headless, no LLDB,
with v6 snapshots-only TCG callbacks. These callbacks read registers only at
SplashBoard encoding entry/return, ImageIO finalization call/return, and the
migration semaphore return. No guest-memory string decoding occurs. The watch
runs 60.240 active seconds and sees no further SEP no-reply message.

The observed sequential ASTC finalization pairs consume 52.619 seconds over
55.855 seconds of callback span (19 complete pairs, no overlapping calls;
median 2.671 seconds). This is a large measured part of this migration slice,
not yet a whole-boot attribution. All observed encoding entries request format
1. Exact final totals and raw events are `APP_ASTC_TIMING1/encoding-timing.json`
and `milestones.tsv`; one initial finalization was already in progress at
restore, so it cannot supply a complete paired duration. A plugin-free control
and same-image format comparison remain required before claiming improvement.

Native format selection is `+[XBApplicationSnapshotManifestImpl
_outputFormatForSnapshot:]`, static 0x2ac02de34. Its once block at
0x2ac02e1f0..0x2ac02e214 calls MGGetBoolAnswer for `DeviceSupportsASTC` and
`HasExtendedColorDisplay`, caching bytes at 0x2ce2f4380 and 0x2ce2f4379.
The dispatch at 0x2ac02de60..0x2ac02deb4 returns format zero when ASTC support
is false. `dataForImage:withFormat:` format-zero branch at
0x2ac03cb18..0x2ac03cb24 invokes UIImagePNGRepresentation (obfuscated stub
0x2b00d1880 resolves to 0x184da1608). This is an existing native PNG path.
The ASTC branch invokes CGImageDestinationFinalizeEx via 0x2ac03cd3c and
returns at 0x2ac03cd40. The capability derives from MobileGestalt, not a live
Metal device query here. Next test: same checkpoint, capability false, native
PNG output and equal completed-image count, then identify the persistent
hardware-capability source for cold-bootstrap validation.

## PNG candidate rejected by equal-work replay

`APP_PNG_TIMING1` restores the identical `APP_SPLASH_ASTC1` checkpoint,
with the same six-core/headless/v6 sparse observer configuration. Before
execution, `snapshot_format_probe.py` verifies pid 700, native selector bytes,
initialized once-token and ASTC byte 1, then changes only that process's cached
ASTC capability at VA 0x2e4588380 / PA 0x1016e77c380 to 0. Raw GDB physical
packets perform the one-byte write and readback while paused, then disconnect;
there are no breakpoints or debugger connections during timing. The mutation
is diagnostic only, recorded in `format-probe/mutation.json`, not a bootstrap
or QEMU model change. The VM was quit after measurement.

The first complete post-restore encoding begins after the in-flight initial
ASTC operation. Native calls then carry format 0 (UIImagePNGRepresentation),
where the control carries format 1. `snapshot_timing.py --count 12` compares
12 sequential non-null encoding returns, refusing overlapping calls:

| Format | First entry to twelfth return | Sum of encoding calls | Median call |
| --- | ---: | ---: | ---: |
| ASTC | 35.998 s | 34.133 s | 2.752 s |
| PNG | 37.638 s | 36.057 s | 2.927 s |

PNG is 4.55% slower in this pair's wall interval. No improvement is established;
do not change the bootstrap capability based on this candidate. Both runs
start from the same queued snapshot state, but the observer does not record
bundle IDs or image hashes, and these returns are not app-install milestones.
PNG's full observation is 60.156 active seconds with no new SEP no-reply line.
The format investigation isolates expensive work on the dependency path; it
does not prove ASTC itself is responsible for the cost, since native PNG is
similarly slow. Next profiling should separate shared image conversion,
encoding, and translated execution/helper cost before another format change.

## Helper attribution and rejected jump-cache candidate

`helper_profile.py` refines guest-execution observations by deepest QEMU
helper. In ASTC's four active CPU threads: translated execution/unnamed work
31.24%, TB lookup 15.20%, pointer authentication 9.62%, MMU translation 9.06%,
mutex wait 5.35%, condition wait 3.75%. Vector float max/min account for
3.72%/2.85%; no individual float helper dominates. PNG similarly records
lookup 15.96%, pointer authentication 10.19%, MMU 8.99%. These are sample
observations, not CPU-time fractions. CPU4/5 are predominantly waiting.

A host-only candidate enlarged `TB_JMP_CACHE_BITS` from 12 to 14 (4,096 to
16,384 entries per CPU). `APP_CACHE14_TIMING1` used the identical ASTC
checkpoint, six CPUs/headless/v6 observer. Its first 12 encodings take
35.660 s versus baseline 35.998 s, with call sum 33.177 s versus 34.133 s.
The roughly 0.94% wall reduction is too small to separate from run variation;
the change was reverted and the ordinary binary rebuilt after quitting the
candidate VM. No cache-size optimization is being promoted. The baseline
binary is retained at `/tmp/dvm/qemu-app-cache12`; all launch manifests pin
binary hashes. The candidate's full bounded run was 60.011 active seconds
with no new SEP no-reply message.

## Fresh-seed early checkpoint and clean cold measurement

`APP_COLD_SEP1` cold-boots a new read/write child of sealed, read-only
`APP_FRESH1/install/disk.qcow2`, using the isolated current O2 binary with
SKS Data 3->4 and opt-in SCRD context fixes. `fresh_migration_boot.py` reuses
the original firmware/device arguments but removes all debugger, plugin,
restored-RAM, and duplicate display arguments. The initial launcher hit an
incorrect wait_for_path signature after starting QEMU; pid 57533 was verified
alive and paused, then reused without restarting. The signature was corrected.
No guest execution occurred before the watcher began timing.

The serial Early boot complete marker occurs by 9.496 active seconds
(serial.log:677), and the VM is stopped there. `APP_EARLY_BOOT_SEED1` records
PC fffffff02aa654c8, 657,127,337-byte VM state, and 11.083-second checkpoint
creation. This is an early-boot seed checkpoint; a process inventory is still
required before claiming the migrator has not yet started. Creation terminates
the source VM, so that run is not end-to-end cold validation.

The watcher now supports a literal serial stop condition and an explicit
ignore-SEP observation mode. Its missing-reply stop predicate is narrowed to
SEP lines: normal DCP report/tracekit messages saying no reply awaited are not
failures. Two captured-line regressions cover that distinction; all 71 host
tests pass. Previously timed late checkpoints saw actual SEP missing replies,
so those diagnostic stops remain valid.

`APP_COLD_CLEAN1` starts a separate fresh child for the clean cold measurement,
with the same binary/model configuration and no plugin or debugger. Initial
bounded observation is 180 active seconds, sampling the host at 30 seconds and
recording (but not stopping on) SEP failures. This allows their native timeout
cost and startup progression to be observed. Runtime evidence, including seed
and binary hashes, is under that tag. This partial observation is not yet a
completed migration or end-to-end validation.

### Independent cold boot, 180 active seconds (APP_COLD_CLEAN1)

This is a new disk child of APP_FRESH1/install/disk.qcow2, not a RAM
restore. `fresh-source.json` records seed SHA-256
742c7d95993654ede3a0f1fc99b8f662635521c2e44aa61485c8cfef50375841 and
normal binary SHA-256
9018f1b458844e7b34b78cc2d4dba1d8db42ba86087e491457ef14f287cc4544.
Six CPUs, headless, no debugger connection/listener and no TCG plugin;
SCRD context lifecycle and empty type-5 clear enabled. The reverted jump-cache
and PNG experiments are absent. A three-second host sample began at 30 s.
`timing-000-180.json` records 180.100 active wall seconds and paused status.
The unmodelled SCRD 0x33/72-byte request arrived at 50.026 s; the observer
recorded it without stopping. This is request arrival, not measured timeout
length. No full migration or speedup claim follows from this interval.

`find_migration_anchor.py` found cfprefsd pid 115 at virtual
0xffffffdf04488e40 / physical 0x10000458e40 within the first 256 MiB.
It validates the allproc backlink and byte-identical virtual proc before
returning an anchor. This avoids reusing stale prior-boot addresses.
The paused process inventory contains 229 live processes. DataMigrator is
pid 124, proc 0xffffffdf03e5eab0, root 0x100272f9000. A unique 64-byte
shared-cache match resolves runtime 0x246c37228 to static 0x237cd3228,
slide 0xef64000 (`shared-cache-slide.json`). The executable header at
0x100670000 is validated before dependency inspection; its dependency-wait
frame returns at 0x10067f170. The inspector now accepts explicit executable
base and shared-cache slide for independent boots.

`deps180/wait.json` decodes block 0x7d49012990 with pending AppleAccount,
SpringBoard, Accounts, MergeBuddyProvisioningResponse, and MobileSlideShow.
Its captured completed set contains MobileContainerManager.ContainerMigrator.
MergeBuddy depends on -0LaunchServicesMigrator, SpringBoard on WiFiDataMigrator.
This is one dependency block's sets, not an exhaustive completed-plugin
count and not evidence that Wi-Fi is the active bottleneck. The same VM is
continuing for a separate bounded 180-active-second interval; inspection
pauses are excluded from both timers. Host-only suite: 71 tests passed.

Static identification of the remaining initial SCRD command: native
AppleSEPCredentialManager `LibCall_ACMSEPControl` is named at
0xfffffff0095353d0, serializes through 0xfffffff00950a0f8, selects command
0x33 at 0xfffffff0095354c0, and calls the transport at 0xfffffff0095354d0.
The serializer first emits a context token (or 16 zero bytes), then serialized
parameters via 0xfffffff0095036c4, then a u32 input length and that many bytes
at 0xfffffff00950a1b0..0xfffffff00950a1d4. The fresh request body still needs
capture to identify its inner control opcode; no generic success reply added.

APP_COLD_CLEAN1 second interval (`timing-180-360.json`) ran 180.023 active
seconds, for 360.123 total. No new unanswered SEP request occurred. The
251-process inventory and `deps360/wait.json` show the same captured pending
and completed dependency sets as at 180 s. This does not imply deadlock:
lsd pid 120 has active native frames at runtime 0x195e9d8e4/static
0x186f398e4 (`__LSCreateRegistrationData+716`), runtime 0x19cc888a0/static
0x18dd248a0 (`_LNIsLinkEnabled+56`), and on a separate thread runtime
0x1b9e1f08c/static 0x1aaebb08c
(`-[MIExecutableBundle _validateWithError:]+80`). Names resolved through the
same shared-cache slide 0xef64000. Registration/validation remains the next
work to quantify; do not blame the pending Wi-Fi identifier alone.

`helper-profile-phase2.json` puts active-CPU sample observations at 35.66%
other translated execution, 17.63% TB lookup, 13.14% pointer authentication,
10.45% MMU, 9.60% mutex wait, and 5.28% condition wait. These are sampled
stacks including waits, not CPU-time shares. Within pointer authentication,
`pauth_strip` repeatedly calls `arm_stage1_mmu_idx`, and key-enable checks
call `arm_sctlr`/`arm_mmu_idx_el`; both descend into effective HCR computation.
Potential reuse of the already-derived TB MMUIDX requires separate correctness
validation across GXF, exceptions, register writes and restore before adoption.
No such optimization has been implemented or claimed here.

APP_COLD_CLEAN1 third interval ran 180.194 seconds (540.317 total active).
At `deps540/wait.json`, the same dependency block has advanced to seven
completed identifiers: LaunchServices, MergeBuddyProvisioningResponse,
AppleAccount, Accounts, MobileSlideShow, MSUDataMigrator and ContainerMigrator.
Only SpringBoard remains pending in that block (dependency WiFiDataMigrator).
`inspect540` contains 314 live processes. This proves LaunchServices and the
account chain progressed between 360 and 540 seconds without a debugger,
but does not prove that all plugins or app installations have completed.

An unbuilt, opt-in PAuth experiment now accepts `DARWIN_PAUTH_CACHE=hflags`
or `hflags-verify`. It reconstructs the A64 stage-one MMU index from the
already-computed hflags MMUIDX, mirroring translate-a64.c's context setup;
verification asserts equality with `arm_stage1_mmu_idx` on every use and
retains mask-reference comparisons. Default behavior is unchanged. It has
not been built, benchmarked or validated, and is absent from the ongoing
cold VM's binary. Next checks must include the existing randomized TCR/EL
matrix, GXF transitions and checkpoint restore before a performance claim.

At 720.397 active seconds, APP_COLD_CLEAN1 has reached the native SpringBoard
migrator (wrapper pid 631, proc 0xffffffdf04fb31c8). Its outer timed wait
returns at runtime 0x101706034 (SpringBoard.migrator offset 0x6034), and its
capture wait returns at runtime 0x2bafa8834 / SplashBoard static 0x2ac044834.
An active image encoder has `CGImageDestinationFinalizeEx` return frame
runtime 0x19687a35c / static 0x18791635c and SplashBoard dataForImage return
runtime 0x2bafa0d40 / static 0x2ac03cd40. This confirms the same image-encoding
stage in the independently cold-booted lineage. No new unanswered SEP request
was observed in the fourth interval (180.080 s). Full completion is unproven.

A second unbuilt performance candidate guards exception-return BQL sections
with QLIST_EMPTY checks on their respective EL-change hook lists. The only
registrants found are architectural PMU hooks in CPU realization, RME in CPU
realization, and GICv3 in CPU-interface realization. Darwin explicitly clears
ARM_FEATURE_PMU before realization, disables EL3 (which clears RME), and uses
AIC. Empty hook lists have no shared device state to update. Nonempty lists
retain the original lock/call/unlock sequence. Runtime and same-checkpoint
comparison still required; the running cold binary does not contain this edit.

At 900.591 active seconds, the same pid 631 SpringBoard wrapper still has
its capture and outer timed-wait frames. An encoder is active in ImageIO /
ASTC code (`inspect900/migration-processes.json`). `screen900.png` is black;
no rendered-display success claim. The fifth interval recorded unmodelled
SCRD 0x29 (61 bytes) at 2.056 seconds, approximately 722.453 cumulative active
seconds. Earlier 0x28 succeeded (stderr line 341937), then 0x29 is unmodelled
(line 341956). The clean lineage therefore reproduces the encoding-seed
query; it is not solely a restored failed-lineage artifact. Continued encoder
activity shows this unanswered call is not the entirety of the migration
cost. Native seed length/generation behavior is still not established; no
empty or invented seed response is implemented.

APP_COLD_CLEAN1 sixth interval finished at 1080.599 total active seconds.
At `inspect1080`, the migration wrapper has exited; DataMigrator has only
four threads and no captured dependency-wait frame. Worker exit alone is
not proof of successful migration markers. SpringBoard's main thread is
now in its application run loop. `screen1080.png` visibly contains a status
bar, battery icon and home indicator, but most content is black and some
rendering distorted: first UI pixels, not a usable home screen. A second
unmodelled SCRD 0x33 request (208-byte body) arrived at interval 113.032 s,
approximately 1013.623 active seconds. Both inner control payloads remain
unidentified, and 0x29 remains unresolved.

Checkpoint APP_COLD_FIRST_DISPLAY1 preserves this new cold lineage:
source PC 0xfffffff00709a610, vmstate 9,861,496,826 bytes, capture 14.151 s,
migration 4.162 s. Source QEMU pid 57757 terminated as part of capture.
No debugger, plugin or runtime guest mutation was used in the cold run.
Inter-interval inspection and checkpoint time are excluded from active time.
18 minutes to partial UI is not directly comparable to the historical
instrumented 40-minute run and does not establish the requested performance
improvement or full app/migration completion.

The exact pre-optimization binary is preserved at
/tmp/dvm/app-before-hook/qemu-system-aarch64. Only after source termination
and successful checkpoint creation was the owned normal build started with
the empty-hook BQL guard and opt-in hflags candidate. No other agent's VM or
binary was modified. Next gates: successful build, SMP/PAuth regressions,
reference verification under a real restored guest, and same-checkpoint
milestone comparisons. The final cold image still needs successful migration
markers, installed bundled apps, functional display/input and a measured
meaningful performance gain.

Optimization validation: normal QEMU build succeeded. The hflags verifier
passed all 1,024 synthetic PAuth cases across randomized TCR configurations,
address halves and EL2/VHE regimes, checksum 0x5000000000000000. The
cross-cluster CPU0/CPU4 test passed 40,000 shared atomic increments and
bidirectional FIQ IPIs. All 4 SCRD and 26 SKS unit tests pass; 71 host tests
pass. Logs APP_HFLAGS-matrix.log, APP_EMPTY_HOOK-cross-cluster.log and
APP_PERF_CANDIDATES-host-tests.log.

APP_HFLAGS_GUEST_VERIFY1 restored APP_COLD_FIRST_DISPLAY1 at the exact PC
0xfffffff00709a610 using the new binary and hflags-verify, with no plugin or
debugger connection. 30.023 active seconds completed, then paused. Positive
reference comparisons reached CPU0 109m, CPU1 110m, CPU2 113m, CPU3 113m,
CPU4 1 and CPU5 1 (445,000,002 total lower bound). Each comparison verifies
both reconstructed MMU regime and resulting pointer mask against the original
calculation. This is correctness evidence, not performance timing. The
verifier VM was quit before starting APP_EMPTY_HOOK_TIMING1, which isolates
the BQL change using ordinary DARWIN_PAUTH_CACHE=on from APP_SPLASH_ASTC1
with the same sparse snapshot timing plugin v6.

APP_EMPTY_HOOK_TIMING1 was rejected by the launcher before QEMU launch:
--plugin already replaces inherited plugins and cannot be combined with
--no-plugins. No guest executed under that tag. Corrected unique tag
APP_EMPTY_HOOK_TIMING2 restores the same checkpoint with only v6 snapshots
plugin; the 60-second observation is the actual lock-only comparison.

APP_EMPTY_HOOK_TIMING2 finished 60.237 active seconds with no new SEP stop.
For the first 12 complete ASTC encodes, wall span is 34.202280 s, sum of
encodes 32.455705 s, median 2.534849 s (`encoding-timing.json`). Earlier
APP_ASTC_TIMING1's equivalent span is 35.998403 s: a single-run 4.99%
reduction, not yet replicated and not a meaningful whole-VM speedup claim.
Host sample categories remain subject to sampling bias and phase differences;
no strong inference follows from mutex sample percentage alone. This VM was
quit, then APP_HFLAGS_TIMING1 restored the identical APP_SPLASH_ASTC1 parent
with hflags fast mode to measure the additional pointer-regime change. Its
60-second observer is the next active measurement.

The control rejected the apparent improvement. APP_HFLAGS_TIMING1's first 12
encodes span 35.941524 s (median 2.735196 s); APP_HOOK_BASE_REPEAT1 uses the
preserved original binary and spans 33.776841 s (median 2.589047 s), faster
than both candidates. The earlier 35.998403-s baseline was therefore not
stable enough to support the initial 4.99% lock-change claim. All four runs
use the same checkpoint, ASTC format and v6 plugin. No material performance
improvement is established. Both PAuth hflags and empty-hook BQL candidates
were reverted, along with experimental smoke CLI modes; successful correctness
tests remain evidence only for the rejected experiment. The ordinary binary
is being rebuilt with just the retained SEP model fixes.

Before offline image inspection, free host disk space was checked: only
30 GiB available. Avoid producing a roughly 27-GB raw image alongside all
checkpoint artifacts. The saved cold disk and RAM remain intact. Display
resolution / software pixel work is the next configuration-level hypothesis,
not an established fix; any reduced-resolution test must preserve coherent
logical geometry and touch mapping before being considered usable.

### Protected-volume audit of the cold disk

`materialize_checkpoint_clone.py` created APP_COLD_DISK_INSPECT1/disk.dmg
without another full raw copy. APFS clonefile copied the raw base, then each
qcow2 layer was converted and committed bottom-up only into that private raw
clone. qemu-img compare proved logical contents identical to the sealed cold
disk. About 2 GiB additional host space was consumed. safe_attach.sh mounted
it read-only; System/Preboot/Hardware mounted, but Data and User were locked
on the host. The image was detached via its /Volumes/Hardware mountpoint.

APP_DISK_AUDIT1 booted the existing restore ramdisk, using a disposable child
of the same cold disk. Native mount_apfs -o ro mounted Data disk1s2 at /mnt1
and User disk1s5 at /mnt2 successfully (DATA_RO_RC=0, USER_RO_RC=0).
Read-only base64 export captured three migration preference files and 49
installed-app Info.plists. `decode_app_audit.py` validates base64 and plists;
results are APP_DISK_AUDIT1/exported/audit.json. The installed containers have
real executable names (Safari, MobileMail, Calculator, etc.), unlike the
historical placeholder's absent generic `Executable`. A separate root-file
executable listing positively observed 25 executable files before the probe's
180-second lifetime ended; do not claim all 49 binaries independently checked
from that incomplete loop. `DVM_AUDIT_FILES_DONE` proves the plist export
finished; the executable-list completion marker was not reached. The probe
exited with reached shell yes, zero XNU panics, and terminated its owned VM.

Crucially, com.apple.migration.plist contains DMLastMigrationResults:
{success: false, buildVersion: 24A5430a}. LastSystemVersion is 24A5430a.
com.apple.springboard.datamigrator.plist also records lastBuildVersion
24A5430a. These version stamps alone do not prove success. The failed plugin
still needs identification through logs / native return evidence. The wrapper
prefs contain XBRecentScreenSize='{589.5, 1278}', relevant to the proposed
resolution experiment: this is the observed logical size, not an assumption
of scale 3. Native full-app installation and the global migration result are
separate gates.

The original 52-versus-49 count has a known policy distinction in prior
APP_STAGE_DIAG1/diagnosis.md: 49 apps desired state 1; Vision Pro, SiriApp and
Image Playground state 6. Recheck this cold image's own SystemAppInstallState
and MobileInstallation logs before carrying the historical policy forward.
Next native audit should finish executable checks and export
/var/installd/Library/Logs/MobileInstallation plus migration diagnostics.


### Cold disk audit: concrete failed plugin and full installed payloads

APP_DISK_AUDIT2 repeated the read-only native restore audit using a disposable
child of APP_COLD_CLEAN1. Both protected volumes mounted read-only. The complete
MobileInstallation log records 49 live containers and LS operations; at
22:07:30 setSystemAppMigrationComplete marks installation complete for 24A5430a.
The cold image's own BackedUpState/SystemAppInstallState.plist independently
confirms 49 state-1 apps, with Vision Pro, SiriApp and GenerativePlayground state 6.
All 49 named payload files are present: 42 have executable permission, seven
poster payloads are regular mode-0644 files (positive ls output, 73–90 KB).
Do not infer launchability of those seven from file presence or change their
permissions without examining original image semantics. Executable loop and
follow-up listing both reached their end markers. Artifacts are under
APP_DISK_AUDIT2; exported plist includes SHA-256 in state-exported/audit.json.
The owned restore VM was quit after the completed audit.

The exported native stacks+com.apple.datamigrator-2026-09-05-220938.ips names
PassbookDataMigrator.migrator: watchdog after 60 seconds (erase), wrapper pid631.
Its thread8985 waits on com.apple.passd.library, donating through passd pid91
thread822, whose CoreLocation request waits on locationd pid79 thread1863.
That worker and locationd's main thread queue behind thread1285
(CLHarvestControllerSilo), which has lastRunTime 390.403514 guest seconds.
The stopped native frame is locationd+0x34700c, generated MIG request1203,
reply1303. This matches the earlier decoded com.apple.fairplayd.versioned
service dependency in checkpoint-restore.md (R16/R17), rather than establishing
an unrelated location-device fault. passd also has a second thread explicitly
waiting for launchd-throttled com.apple.fairplayd.versioned.

Read-only extraction of the original locationd into APP_FRESH1/passbook-re
confirms the upper stack: +0x5a22b0 calls CLMescalSigner initInSilo:;
+0x5f5098 inside that initializer calls its hardware-info helper, whose return
is +0x5f509c. Its native error path logs MESCAL: Could not derive hardware info
for SAPInit. The current stack is blocked before that return. Earlier R17
captured FairPlay selector21 provider result -42402 while IOReturn was zero;
that historical boundary is not a newly verified result for this cold boot.
Do not claim successful global migration from the SpringBoard worker exiting.
The saved preference success:false and this watchdog are direct failure evidence.
No performance gain is yet established; this dependency and software ASTC cost
remain separate actionable investigations.


### Coherent lower-resolution control started

APP_COLD_786_1 is a fresh child of the identical sealed APP_FRESH1/install seed
(SHA-256 742c7d95993654ede3a0f1fc99b8f662635521c2e44aa61485c8cfef50375841),
using the preserved exact baseline binary (SHA-256
9018f1b458844e7b34b78cc2d4dba1d8db42ba86087e491457ef14f287cc4544), six CPUs,
headless, no debugger and no plugins. Only coherent geometry changes:
-fb 786x1704, D586 and A453 little-endian width/height 12030000a8060000.
Pixel count is 44.44% of 1179x2556. No UIKit scale or image-format override.
The fresh launcher now accepts --framebuffer for this bounded configuration
experiment and rejects missing/ambiguous DCP size payloads. Host regressions
71/71 and shell/Python syntax checks passed before launch. Lower pixel count
is a tradeoff to measure, not evidence of better performance or usability.


At 180.055 active seconds, APP_COLD_786_1 has the same captured migration
frontier as the full-resolution control: ContainerMigrator complete, account
chain and SpringBoard pending; lsd is working. This phase does not yet show a
rendering benefit. Its validated live cfprefsd anchor is pid114,
proc0xffffffe7e2459c78, PA0x100281d5c78 (found in third 256-MiB RAM chunk);
DataMigrator pid123 proc0xffffffe7e2452ab0, executable0x104374000.
The decoder validated its Mach-O header and expected native set classes with
cache slide0x477c000. Timing and sample are preserved as phase1 artifacts;
the second180-second active interval is running, no debugger connection.

PassbookDataMigrator original +0xb3c invokes
PKPassLibrary migrateDataWithDidRestoreFromBackup:, and +0xb44 returns1
unconditionally after that synchronous call returns. The watchdog failure is
therefore specifically a non-returning dependency, not a checked unsuccessful
migration return from this plugin. No return was forced or patch applied.


At 720 seconds the reduced-resolution DataMigrator was not in the particular
bounded dependency-wait frame; this absence is not completion. The wrapper
was still alive, doing SplashBoard/IOSurface work. At 900 seconds it remained
black with zero presents; SCRD29 arrived at 763.207 active seconds (phase5
42.654; exact summed interval values are in timing files), compared with the
full-resolution control's 722.453. At 1080.574 active seconds the wrapper had
exited but SpringBoard main waited in SBAVSystemControllerCache isRingerMuted
(static cache22463c714); other threads waited through CoreMedia Fig XPC.
No usable display or speedup is established. A final bounded60-second interval
is checking for first output before preserving the disk and ending this test.

The full-resolution serial log contains120920 DART set_device_power off and
120917 on lines, not the older TXM selector error flood (zero such TXM errors
in this cold log). The emitted call at statica0d5ca8 targets the import at
a0dc2e0; the paused guest resolves pointerfffffff028298268 tofffffff02b1e1060.
IOLog's byte gate at staticb1e10f4..10fc readsfffffff007e5a32a and skips serial
format/drain if bit0 is set, after its unified-log call atb1e10f0. This matches
Apple XNU serial_protos.h SERIALMODE_NO_IOLOG=0x10 and arm_init.c's native
initialization from serialmode. The current live byte is0. Native serial=19
retains input/output and disables IOKit serial logging. A guarded disposable
checkpoint probe changes just this flag, with no breakpoint or execution and
connection closed before timing; fresh validation must use the boot argument.
This is another measurement hypothesis, not a performance claim.

APP_COLD_786_1 ended at1140.758 active seconds with zero presents and no input
acknowledgments. Its owned VM62908 was quit, exit verified, and its disk sealed
read-only. The reduced-geometry configuration is not validated and must not
be promoted as a working or faster baseline. Full-resolution remains the
control for the next snapshot logging experiment.

APP_IOLOG_OFF1 restored APP_SPLASH_ASTC1 with exact PCfffffff02ac72c54
in1.415s using the exact preserved baseline binary, six CPUs, headless and
the same sparse v6 snapshot observer as prior controls. The guarded diagnostic
verified IOLog code bytes and changed only its initialized serial-disable flag
from0 to1 at VAfffffff027e5a32a, PA10006a0632a. Raw GDB was disconnected
before the60-second active measurement. No breakpoints or guest calls were
installed. Kernel unified logging remains on the native path.

The IOLog paired control rejects a material gain. APP_IOLOG_OFF1 first12
ASTC encodes span36.530570s (sum34.651131, median2.734221); APP_IOLOG_BASE1
with original flag0 spans36.626893s (sum34.573886, median2.774767). The0.26%
span difference is noise. OFF1's serial log is1011bytes with zero DART power
lines, proving the flag actually affected output. Both VMs were quit after
measurement. The probe's initial report always named candidate serial=19 even
for the value0 control; this reporting field was corrected to serial=3 for
value0. The recorded before/after bytes and actual test writes were correct.

Next bounded hypothesis: APP_A385_TIMING1 restores the same exact checkpoint,
binary, display and v6 observer, changing only existing model override A385=01.
Historical boot-idle.md established that1 ends repeated A385 polling, but did
not establish physical hardware semantics or a display fix. This is explicitly
a disposable performance diagnosis, not a proposed default response. A useful
result would require native-semantic follow-up and full display/input validation.

APP_A385_TIMING1 completed12 ASTC encodes in34.189274s (sum32.349517,
median2.581227). This is within the earlier unchanged-binary control range
(original repeat33.776841s); no robust material gain is established. More
importantly the claimed mechanism did not reproduce in this later checkpoint:
A385 override1 was positively applied but repeated polling continued through
the60-second run. Historical early-boot polling cessation cannot be assumed
for this state. The VM was quit and no default device behavior changed.

Next SEP diagnosis reuses APP_EARLY_BOOT_SEED1, with debug only for the bounded
short path to the first outstanding SCRD33 request. This is an instrumented
protocol capture, excluded from performance comparisons.

APP_SCRD33_EARLY1 stopped at the first unanswered72-byte SCRD33 request after
38.142 active seconds from APP_EARLY_BOOT_SEED1 (exact preserved binary,
no plugin, SEP debug enabled for capture). VM64126 was quit. The saved request
scrd33-ratchet-status.bin SHA-256 is
74d3691dfa9796595f81946256ae5fa40d582e5b55fe68f073913b93fb6b17fa.
Its36-byte outer header is followed by a null16-byte context, zero parameter
count, input length12, and control words {1,2,0}. Native
+[LACACMHelper ratchetStatusWithConfig:] at20628a9ac..9b8 constructs precisely
these words and calls _ACMSEPControl at20628aa4c with length12. This identifies
the early request as ratchet status/configuration, not a credential request.

The success response is not empty: LibSer_SEPControlResponse_Deserialize
2063a4f7c consumes u32 length followed by data. LAC's response block20628ab1c
wraps those bytes in NSData. Its native parser _configFromRatchetState:
206285f58 copies56 bytes from offset0; _statusFromRatchetState:206285f9c
copies75 bytes from offset0x100 (last read ends at0x14b). This establishes a
minimum consumed extent331, not the actual full response size or initial
ratchet configuration. An empty successful response would be invalid. No
success/state was fabricated. The error path20628aa50..aaac explicitly handles
nonzero ACM status, but the correct model error or unconfigured-state contract
still needs evidence. The later208-byte SCRD33 request remains unidentified.

Next performance experiment uses an independent host-only QoS init plugin,
tools/re/host_qos_probe.c, with the unchanged v6 milestone observer and exact
original QEMU binary. QEMU plugins/core.c:276 queues initialization on each
CPU thread, so pthread_set_qos_class_self_np changes only that host thread.
The local SDK pthread/qos.h defines and documents the requested QoS API.
The helper records thread IDs, before/after classes and all return values;
it has no translation or instruction callbacks and aborts on a failed request.
This requests scheduling priority, not guaranteed physical-core placement.
sysctl reports this Mac17,7 as six Super cores plus twelve Performance cores;
these must not be described as six performance plus twelve efficiency cores.
APP_QOS_BASE1 observes original class21 on all six CPU threads.

APP_QOS_HIGH1 positively changed all six distinct CPU threads from default21
to user-initiated25, with all setter/getter return codes0. Its first12 format1
encodes span36.709624s (sum34.681682, median2.835834), compared with
APP_QOS_BASE1 span34.044917s (sum32.205434, median2.658463). No gain; no
production scheduling change. Both owned VMs were quit after their60-second
measurements. The diagnostic plugin remains separate and opt-in.

The baseline host sample identifies float32_minmax canonical unpack/pack under
helper_gvec_fmin_s/fmax_s as recurring work. A bounded candidate now directly
orders IEEE binary32 encodings only when both operands have exponent1..254
and the operation is not magnitude comparison. It returns the exact selected
operand; zeros, subnormals, infinities and NaNs retain the original generic
path. There is no host floating-point operation or altered guest FP state.
tests/unit/test-softfloat-minmax.c uses the original canonical implementation
as a differential oracle:5,492,224 comparisons, including both result bits and
exception flags, pass across boundary values, signs, randomized bit patterns,
equal/opposite operands, eight operation flags and128 status configurations.
The main QEMU build and explicit ninja unit target succeeded; invoking that
unit target through make incorrectly selected an implicit Pascal rule, so
the successful test build used ninja. APP_MINMAX_TIMING1 measures the candidate
with the same checkpoint/v6 observer and default host scheduling.

The normal-only candidate APP_MINMAX_TIMING1 spans35.144696s for12 encodes
(sum33.364266, median2.536936), still within baseline variability. Its host
sample continues to hit the generic float32_minmax path extensively. The next
revision also handles signed zeros and infinities: these likewise select an
input without rounding or raising flags, and the ordered-bit transform gives
negative zero below positive zero exactly like parts64_minmax's sign tie rule.
NaNs, subnormals and magnitude operations remain generic. The same5,492,224
differential checks pass again, including signed-zero and infinity boundaries.
APP_MINMAX_TIMING2 measures this revision; no performance gain is claimed yet.

### Repeated min/max improvement, fresh validation pending

The zero/infinity revision now shows a repeated reduction in the captured
ASTC workload. Same APP_SPLASH_ASTC1 state, six CPUs, full geometry, headless,
unchanged v6 observer, default QoS, no debugger,60-second active intervals:

| Run | Binary | First12 span | Encoding sum | First18 span |
|---|---|---:|---:|---:|
| APP_QOS_BASE1 | original, QoS observation only |34.044917|32.205434|50.123184|
| APP_MINMAX_TIMING2 | min/max zero+infinity fast path |27.405995|25.495085|40.480721|
| APP_MINMAX_CONTROL2 | original, only v6 observer |35.444757|33.652476|52.371505|
| APP_MINMAX_REPEAT2 | identical min/max candidate |25.554585|23.675247|38.011112|

All spans/sums are seconds; all counted calls returned nonnil with format1.
The primary first12 criterion was chosen before these measurements. First18
is an additional longer-window check, not a replacement endpoint. Mean first12
span drops23.79%; mean first18 span drops23.42%. The candidate is retained for
fresh validation, not described as a23% whole-boot or UI-interaction gain.
The sampled min/max helper share falls from6.55% in APP_QOS_BASE1 to2.56% in
APP_MINMAX_TIMING2; these are sampling observations, not CPU-time percentages.
All four VMs were quit. Candidate QEMU SHA-256:
2fd1237efba8b857d893b632a04ca5b927cdc5c3976ee94dbf4d7f6903b1f874.
Original QEMU remains preserved byte-for-byte at app-before-hook.

After the final measurement, host71/71, SCRD4/4 and SKS26/26 tests pass;
the5,492,224-case FP differential test and shell syntax checks also pass.
APP_COLD_MINMAX1 starts from the identical sealed bootstrap seed and
APP_COLD_CLEAN1 launch configuration with only the new QEMU binary: full
1179x2556 geometry, six CPUs, no plugins/debugger/RAM restore. No image or
guest-runtime patch was added for this optimization. Successful migration,
usable SpringBoard/input and fresh-boot performance remain required gates.

APP_COLD_MINMAX1 pid65430 reached Early boot complete (serial line677).
At180.229 active seconds it has the same captured dependency frontier as the
full-resolution control: ContainerMigrator complete, account chain and
SpringBoard pending. This early interval is not an established speedup.
Its validated cfprefsd anchor is pid114 procffffffe5762b6390,
PA1002a38a390; DataMigrator124 procffffffe57620a390, executable100b28000,
cache slide53c8000. Native Mach-O and set-class checks pass in wait180.
First phase timing/sample are preserved with phase1 suffixes. A second180s
active interval is running, with inspection/paused time excluded.

tools/re/fairplay_provider_trace.c is a compiled, not yet runtime-validated,
read-only diagnostic observer for the six indirect calls and return of the
native selector21 provider at kernel static9c65e88..9c66368. It logs registers
at14 fixed PCs to identify real call targets/results through the obfuscation;
it neither changes guest state nor treats IOReturn0 as service success.
It has not been added to the fresh performance run.

At360.258 active seconds, APP_COLD_MINMAX1's captured completed set contains
LaunchServices, MSU, ContainerMigrator and merge-buddy-provisioning-response;
accounts, MobileSlideShow and SpringBoard are still pending. This is ahead of
the original full-resolution control's captured360s frontier, but earlier
lower-resolution controls also showed startup variability, so it is not yet a
repeatable cold-boot speedup claim. Timing/sample phase2 and wait360 are saved.
The cold VM is paused while APP_FAIRPLAY_PROVIDER1 independently restores the
early checkpoint with only the sparse provider diagnostic and old exact
baseline binary. They do not execute concurrently. Host RAM is128GiB.

APP_FAIRPLAY_PROVIDER1 is an invalid diagnostic: the helper incorrectly
treated an opaque NULL register handle as absent, whereas plugins/api.c
encodes writable register0 as NULL. Its fatal logging path wedged monitor
stop; owned PID65833 was killed. This is not a guest failure and supplies no
provider values. The helper now tracks explicit availability bits (as the
milestone observer already does) and uses a clear process exit on capture
failure. Recompiled v2 is replayed from the unchanged early checkpoint as
APP_FAIRPLAY_PROVIDER2. The cold VM remains paused at360.258 active seconds.

APP_FAIRPLAY_PROVIDER2 succeeds:56 register events contain four complete
provider transactions, each returning0xffff5a5e (-42402), at29.990286,
37.731126,47.208716 and57.634262 active diagnostic seconds. This reproduces
the older native failure on the current early seed without LLDB; it is not
a performance sample. The provider allocates a16KiB buffer via resolved
kernel2b1e008c, obtains state via2b1e0734, calls29d68090 on the buffer bounds,
then invokes29d3db20 at29c66238. The final computation at29c66348 XORs
buffer status0xb3bdc913 with0x4c42934d to yield0xffff5a5e. Both later cleanup
calls (2b1e0930,2b1e00a8) return before the provider exits. These are actual
resolved targets, not names inferred from IOReturn. The diagnostic VM was
quit before resuming the cold VM's third180-second performance interval.

Static follow-up in fairplay-engine.txt shows9d3db20 has an initial indirect
call at9d3dccc, computed dispatch at9d3df8c and9d3e178, and the final buffer
status store at9d3e184. Those six additional sites are prepared in the trace
source for the next paused diagnostic interval; this extension is not yet
compiled or exercised. No FairPlay return, credentials or input was changed.

At540.360 active seconds APP_COLD_MINMAX1 has seven captured completed plugins
(MobileSlideShow, LaunchServices, merge-buddy-provisioning-response, MSU,
ContainerMigrator, appleaccount and accounts), with only SpringBoard pending.
This matches the original control's540s frontier. Phase3 timing/sample and
wait540 are saved. No full-migration or boot-speed gain is established yet.
The VM is paused while compiled tracev3 replays the early snapshot as
APP_FAIRPLAY_ENGINE1; v3 follows the newly resolved internal dispatch sites.

APP_FAIRPLAY_ENGINE1 captures three complete requests (57 site events).
The internal dispatch at29d3e178 targets29cbca34; its final status store at
29d3e184 writes w20=0xb3bdc913. The last LR at that store is29c76f30,
following another indirect call; static inspection shows it is followed by a
computed branch at9c76f40, so LR alone does not identify the error origin.
This diagnostic VM was quit before resuming cold phase4.

At720.429 active seconds, the cold migration wrapper646 is still alive with
12 threads; SpringBoard35 has5 threads. DataMigrator no longer has the specific
decoded dependency-wait frame; absence is not completion. No SCRD29 has yet
been logged, so a cold-boot speed gain remains unproven. Phase4 timing/sample,
wait720 and inspect720 are saved. The fresh VM is paused for the next diagnosis.

Tracev4 adds optional blocks=on, bounded to20,000 executed blocks in the exact
FairPlayIOKit text range (static9b50950..9d894b0, from the fileset load command).
It captures only the first provider transaction's TPIDR_EL1 thread, including
migration across CPUs, and disables block logging at its return. This locates
the actual status-producing block through the computed branches instead of
guessing from the last LR. APP_FAIRPLAY_BLOCKS1 is the diagnostic replay; it
is excluded from performance timing and cannot alter the cold VM's disk.

APP_FAIRPLAY_BLOCKS1 resolves the native failure origin. The bounded first
provider trace contains 538 executed blocks on TPIDR_EL1 ffffffde4e6c3830.
At static 9b55acc..ad0, helper 9b55a9c requests IOAESAccelerator. Its service
lookup returns NULL at runtime 29b55ae4, then 9b55b94 returns -40. The success
byte at static b878ea8 remains zero; 9cc19b4 reads it and the path through
9d31f60..64 constructs b3bdc913. The provider XORs this with 4c42934d to
produce -42402. Evidence: APP_FAIRPLAY_BLOCKS1/provider.tsv.blocks.tsv and
APP_FRESH1/passbook-re/fairplay-hardware-helper.txt plus
fairplay-error-construction.txt. This proves a missing AES service on this
path, not absent device credentials. No success result or key was fabricated.
AppleS8000AES is present in the kernelcache; dt_fixup has no AES feature and
our Darwin machine has no corresponding model. Native driver requirements
must be established before exposing the device. Reference implementations
exist, but inferno's implementation is AGPLv3-or-later; do not copy it without
resolving compatibility. Hardware-backed key semantics remain unverified.

APP_COLD_MINMAX1 phase5 ends paused at 900.504 cumulative active seconds.
Its first SCRD29 occurs at 733.004 active seconds, versus 722.453 in the
original control: the repeated ASTC micro-workload improvement has not yet
produced an earlier cold-boot frontier. Phase5 timing and sample are preserved.

AES binding correction: firmware/dtree is already rewritten. The original
/tmp/dvm/dtree_raw has aes compatible="aes,s8000" and dart-sio compatible=
"dart,t8110"; both are stripped in the prepared image. The kernelcache
__PRELINK_INFO AppleS8000AES personality has IONameMatch="aes,s8000",
IOProviderClass="AppleARMIODevice", IOClass="AppleS8000AESAccelerator".
The AES node remains, but its match string is missing. Raw AES has version5,
address-width42, IRQ0x453, three register ranges (arm-io-relative)
{0x17500c000,0x4000}, {0xf82dc000,0x8000}, {0xf8078000,0x4000}, and
mapper phandle0x71 is dart-sio/mapper-aes, SID1. Native start93ee0c0 (full
static address fffffff0093ee0c0) maps three register ranges, reads version at
93ee35c and address-width at93ee3ec, and requests its IOMMU at93ee63c.
The diagnostic prepare_aes_probe.py copies only the verified match strings
into a disposable prepared tree and assigns required DART IDs. It does not
provide an AES implementation. A fresh diagnostic can establish the first
actual register transaction before modelling it; do not treat catch-all
writes or registration alone as cryptographic success.

Cold minmax validation finished phase6 at1080.686 active seconds. First
presentation is1026.066s, versus1019.021s in APP_COLD_CLEAN1. The1080s capture
still has a black main area and partial status/home UI, not a usable screen.
Thus the repeated23.8% ASTC workload improvement is retained but does not
satisfy whole-boot performance or usability. Cold VM65430 is paused.

APP_AES_BIND1 is invalid driver evidence: decoding/re-encoding the prepared
DT with dt_fixup's type heuristic changed its printable256-byte random-seed
into a257-byte C string. SPTM stopped at0070f75a8 with x3 pointing to
"random-seed ... size mismatch (257) or NULL". Quit the owned VM. The new
probe decoder preserves binary property bytes, flags, and original padding;
it checks an exact round trip before applying matching changes. An attempted
APP_AES_BIND2 launch without its not-yet-written tree exited immediately.
APP_AES_BIND3 uses the successfully round-tripped tree and is the valid
fresh diagnostic. None of these runs contributes performance timing.

APP_AES_BIND3 positively reaches AppleS8000AES::start, printing its three
physical ranges, version5 and address-width42. No AES MMIO is observed; its
mapper-aes child still lacked compatible="iommu-mapper". The raw tree proves
that string exists there. APP_AES_BIND4 adds only this missing child match;
mapper-sio remains disabled. The preceding VM was quit before this run.
The preparation tool checks exact original byte/padding/flag round trips;
comparison of the final derived tree reports only AES/DART/mapper match
strings and the required DART-ID assignment differences.

APP_AES_BIND4 reaches the real DMA mapper; at120 diagnostic active seconds
FairPlay's cached AES pointer and initialization flags remain zero. APP_AES_START1
adds sparse read-only startup PC witnesses. DMA setup at93ee6e8/93ee724 returns
zero (success), and the driver reaches its superclass registration call at
93ee9f8. The actual target is runtime29f15df0, IOAESAccelerator::start. No
return at93ee9fc is observed in30 diagnostic seconds. APP_AES_START2 follows
that superclass's resource wait and registration sites. These runs use explicit
--diagnostic-plugin; fresh-source.json now truthfully marks them instrumented.
Default fresh performance launches still remove all plugins and debuggers.

APP_AES_START2 captures IOBSD matching and its successful wait return at
runtime29f16130. The next recorded return29f161bc is absent: registration is
inside the SecureRoot platform call (static9f16198 string,9f161b8 call).
Read-only live vtable inspection resolves the provider's char-name wrapper
to kernel b1f3bf0, which calls symbol-name slot0x3a8 through b1f3cc0 and then
forwards to parents. APP_AES_ROOT1 follows only this AES thread through those
forwarding calls. AppleARMPlatform85cda08 is one static SecureRoot handler,
but its actual invocation is not yet proved; no handler return is patched.

APP_AES_ROOT1 proves the actual parent forwarding chain: b1f3bf0 wrapper ->
b1f3cc0 ->9712c84 ->b1f3cc0 ->85cda08, followed by SecureRootCallBack into
AES93eec8c. Native superclass callback9f16560 invokes9f16488, which calls
virtual0x588 resolved by the live AES vtable to9f17064 (key-cache setup).
That routine issues a16-byte native AES request at9f1722c. The observed AES
object still has active=false, empty command queue, and no AES MMIO reads
at30s. APP_AES_KEYCACHE1 follows the request allocation/dispatch sites to
locate the exact remaining wait. No root-security flag or callback is forced.

APP_AES_KEYCACHE1 positively enters native16-byte performAES, prepares both
buffers successfully, obtains a command, and dispatches9f16bd0. It reaches
AppleS8000AES::_setActive93eee74 and the clock-enable call93eef1c, but not its
return93eef20. The provider clock path resolves via85c9374 to AppleT8140
9712dec. Live platform+0xf0 is NULL; its gate action9712bdc waits for that
pointer to be populated (load9712bf8, sleep9712c2c). This is a missing platform
clock-controller dependency, before AES MMIO. The SecureRoot handler did
actually run and call back; the problem is not the earlier IOBSD wait or DMA
allocation. Register semantics alone cannot fix this missing provider.

The clock/power dependency has a native bring-up configuration: AppleT8140
9712930 tests presence of no-clock-gate and9712970 tests no-power-gate on
its provider. Clock entry9712dec then returns unsupported when disabled,
instead of sleeping on missing controller+0xf0; AES ignores that clock return
and continues. prepare_aes_probe.py --ungated adds these properties only to
a disposable diagnostic tree, reflecting always-on virtual devices while
PMGR is absent. APP_AES_UNGATED1 tests the actual subsequent MMIO contract.
This is not a persistent bootstrap change or a working-PMGR claim.

APP_AES_UNGATED1 reaches the actual main AES register at physical38500c00c
(STATUS+0xc), repeatedly reading zero. At7.188 diagnostic active seconds,
the native driver panics: "AppleS8000AESAccelerator::_enableAES: DPA has not
been seeded!" (caller runtime29_93f1764, AppleS8000AES.cpp:434). This proves
the missing clock provider was bypassed by the native bring-up properties,
not that AES is implemented. The normal tree still hides this device.

For bounded command discovery only, APP_AES_COMMANDS1 uses native boot
arguments -ignore_dpa (93ee518..530) and -aes_spew (93ee538..550), without
changing Apple code or returning dummy ciphertext. The former permits the
unseeded diagnostic engine; the latter logs FIFO words at93f11a0..11b0.
These options are not baseline fixes or clean performance measurements.

The native AES work now has its own evidence record in native-aes-bringup.md.
A strict software-key FIFO core and opt-in DARWIN_AES=software MMIO/DART/IRQ
adapter were added to the owned QEMU branch. Three crypto/protocol tests pass,
the binary builds, and all75 host tests pass. The model remains diagnostic,
requires native -ignore_dpa while readiness is unimplemented, and deliberately
refuses snapshots until pending-state restore is implemented and tested.

APP_AES_SOFTWARE1 at85.368 active seconds positively shows both native
FairPlay initialization gates equal1 and a nonnull IOAESAccelerator pointer;
previous probes had both gates0 and no pointer. By129.426 active seconds it
has completed26 AES DMA transactions, then panics on xART ep16 opcode18's
incorrect zero-length success. Native get_ap_slot_info requires56 output bytes
(8 BNCN +48 commit hash), plus response-byte6 slot result. Contents/entanglement
are not yet modelled; no dummy56-byte success was added. VMquit, diagnostic
artifacts retained. The whole-boot/performance goal is still not met.

APP_AES_XART1 validates the opt-in AP nonce slot model: guest matches the
supplied sidp-rom-manifest-hash, reports booted slot0/state3 and L boot,
checks empty slot1 and continues AES requests. No panic through150.109 active
seconds. Details and corrected opcode15 identification are in
xart-ap-nonce-slots.md. VMquit afterward.

The AES model now seeds its virtual entropy state from the host CSPRNG and
reports readiness only after successful initialization; no physical DPA
countermeasure circuitry, UID/GID key or hardware self-test success is claimed.
APP_AES_READY1 removes -ignore_dpa/-aes_spew and UNIMP tracing, uses6CPUs and
the same sealed bootstrap seed, and has no debugger/plugin. Its first60.009
active seconds passed native AES/xART initialization without DPA warnings or
panic. SCRD33/72 remains unanswered at50.507 seconds. At the60s pause the
validated inventory has155 processes. The initial name filter used the wrong
name; the inventory DOES contain com.apple.datami (DataMigrator), pid123. cfprefsd115/procffffffe0cd844720 is a validated
anchor (DRAM base10000000000, not1000000000). Process/stacks and raw evidence
are under APP_AES_READY1/processes60.

The next120s phase is collecting a host sample15s after resume. Its snapshot
of active-CPU observations is32.42% other translated execution,17.56% TB
lookup,11.46% mutex wait,11.41% MMU,8.99% pointer authentication. These remain
sample observations, not elapsed-time fractions. No whole-boot speed gain or
migration completion is established. APP_AES_READY1 pid69929 is the owned
candidate; APP_COLD_MINMAX1 pid65430 remains paused.

APP_AES_READY1 is now paused at180.015 cumulative active seconds (60.009 +
120.006). No panic, native xART slot cleanup passed, and no unsupported AES
command has been logged. DataMigrator123/procffffffe0cd842ab0 has executable
base102d30000, cache slide19b28000 (verified via exact instruction bytes in
the on-disk dyld cache). Its native dependency frame atwait180 reports only
ContainerMigrator complete, matching APP_COLD_MINMAX1 at180 seconds. There
is no early migration speedup at this comparison point. Later milestones and
full e2e still need measuring. The host profile is diagnostic sample evidence,
not a claim that each category occupies that percentage of wall time.

The optional xART VMState now rejects enabling AP slots when restoring an
older checkpoint without their state. Latest source compiles after that
restore guard; it was built only after READY1 was paused. Save/restore itself
remains unvalidated and the experimental AES model still disallows it.


### APP_AES_READY1 six-minute frontier

At360.220 cumulative active seconds (phase3=180.205), wait360/wait.json
captures seven completed dependency identifiers: MobileSlideShow,
LaunchServices, MergeBuddyProvisioningResponse, MSU, ContainerMigrator,
AppleAccount and Accounts. APP_COLD_MINMAX1 at360.258 had four. This single
frontier is encouraging but does not establish repeatable total migration or
interactive speed improvement. Meaningful performance improvement remains
an explicit completion requirement alongside successful cold provisioning.

The pending SpringBoard dependency names WiFiDataMigrator, but the active
wrapper405 is actually SystemAppMigrator. Its mapped image at102918000 has
UUID9a504b35473936f69f62fe1d4928d0c6, matching the extracted original binary.
Its stack return10291e9f8 and exact preceding bytes match static69f8:
-[MISystemAppMigrator synchronouslyCancelAllAppStoreRequests] calls
cancelAllRequestsWithErrorHandler: at69d4, then dispatch_semaphore_wait at69f4
with a45,000,000,000ns deadline built at69d8..69e8. This is a captured wait,
not proof that its full45s expires. appstored410 exists in the inventory but
the current thread decoder returns zero threads; that cannot establish its
health or cause of the wait. Evidence: processes360, wrapper360/header and
cancel-wait.txt, stores360. A further60s uninstrumented interval follows.

The host sample at75 active seconds has CPU4/5 in condition waits for926/984
and930/984 observations. The DT identifies those as the performance cluster;
CPU0..3 are efficiency cores. Background scheduling versus missing wakeup
behavior is unresolved. The previously fixed Apple WFE event-stream bug and
its tests are documented in multicpu.md; these new samples do not demonstrate
recurrence of that bug. No CPU-topology change has been made.

At420.384 cumulative active seconds (phase4=60.164), READY1 remains paused,
without a new panic. appstored410 now decodes nine threads; the prior zero
was not evidence of a crashed service. SystemAppMigrator405 has moved beyond
the cancellation semaphore into another synchronous XPC call (saved frame
10291f0ec, static70ec). The captured dependency set remains seven. This bounds
progress beyond cancellation but does not distinguish its completion callback
from expiry of the45s deadline. Evidence: processes420 and wait420; timing
is preserved as timing-phase4.json. No active watcher remains.


### APP_AES_READY1 installation progress through540s

At420s SystemAppMigrator static70e8 calls
cancelCoordinatorForAppWithIdentity:withReason:client:error:. installd170's
active thread is constructing its global bundle map (ICL static1aaeb6460 /
1aae813cc); three other workers wait in systemAppBundleIDToInfoMap at
1aaeb676c. The getter caches its result at object+48, only calling the builder
when null (1aaeb676c..67a4). This is not evidence of an intentional per-request
full scan. Install Coordination has three MobileInstallationInstallApp calls
in flight and persona-query calls waiting on installd. Symbols are recorded
in install420/symbols.txt.

At540.550 cumulative active seconds (phase5=120.166), the global-map lock
stacks are gone. installd instead has StreamingZip __Prescan workers and
container-manager queries; SystemAppMigrator has advanced to
+[IXPlaceholder placeholderForRemovableSystemAppWithBundleID:client:installType:error:]
(static1c22c1a8c). The captured dependency completed set remains seven; it is
not an exhaustive installed-app count. No new SEP rejection/panic was observed.
Evidence: processes540, wait540, timing-phase5.json.

A three-second host sample taken late in phase5 (outside the watcher's sample
option; sample-install-command.log / host-profile-install.txt) has CPU0..3
observations38.60% translated execution,18.07% TB lookup,11.85% pointer auth,
10.81% MMU,8.70% mutex wait. CPU4/5 condition waits1256/1316 and1259/1316.
These are diagnostic observations, not wall-time fractions; phase5 included
that sample. Current build is O2/non-LTO. Earlier O3 comparison in this same
workstream only showed a3.27% single-run scan difference within noise, so
there is no newly established compiler speedup to promote.

Phase6 resumes READY1 for180 more active seconds, with no debugger or plugin.


At720.727 cumulative active seconds (phase6=180.177), a new wrapper649 has
SpringBoard.migrator's familiar launch-image semaphore frame10515e034
(static6034 at imagebase105158000). The dependency-wait frame is absent;
this does not establish global migration completion. No new SEP no-reply
occurred in phase6. Saved source: processes720/wait720/timing-phase6.

Locationd's main thread is still waiting, but its current site is NOT the old
FairPlay/Harvester call: runtime1046e764c maps to static1003cf64c, verified by
32 unique exact bytes at fileoffset3cf63c of the original locationd binary.
The enclosing method names CLFitnessTrackingNotifier::fitnessTrackingStateChanged
at1003cf4ec..4f0, and calls silo sync: at1003cf648. Its queued block at1003d9120
invokes1003cf44c, which dispatches virtual methods. The destination silo and
reason it is held remain unproven. Other captured threads include two
MobileBluetooth synchronous XPC waits; correlation is not a demonstrated
causal link. Full Passbook success must still be checked.

At this checkpoint CPU4/5 both stop at kernel staticaa654c8 immediately after
WFI (aa654c4), not the previously corrected stackshot WFE loop. Full registers
are in cpu-registers720.txt. This distinguishes these samples from that old
WFE bug but does not prove the performance cluster receives runnable work.
Phase7 now resumes with the default SEP-no-reply stop to capture the next
new unresolved operation promptly (120s maximum).

Phase7 stops after4.090 active seconds on SCRD29/body61 at4.089, total
724.817 active seconds. This is the same unresolved context encoding-seed
length query documented in acm-scrd-response-contract.md, not a new AES panic.
The prior MINMAX first29 was approximately722s. Thus the earlier seven-plugin
frontier advantage has not translated into a meaningful gain by this common
late milestone. READY1 remains paused at the exact next SEP failure, pid69929;
no watcher/session remains. timing-phase7.json preserves the stop.


### Explicit SEP error diagnostic instead of silence

The opt-in DARWIN_SCRD_UNSUPPORTED=1 experiment is built and tested. It
returns kIOReturnUnsupported with a valid12-byte envelope for exactly the
captured initial33/72 and29/61 requests; it supplies no seed, credential or
ratchet state. Details and addresses are in acm-scrd-response-contract.md.
APP_SCRD_ERROR1 (pid71782) restores the earlier APP_SPLASHBOARD_WAIT1 state
with no debugger/plugin and runs60.040 active seconds. The native receiver
reports the explicit29 error; the guest deletes CS[3] afterward. There is no
new unanswered SEP request or panic through this bounded interval. The
native kernel logs AssertMacros failures while propagating the error, which
is expected error handling, not proof of successful credential encoding.

At60s, coreauthd144 has three decoded threads and wrapper700 is still in
SpringBoard's launch-image semaphore. Thus this has not eliminated the major
launch-image delay or proven a total migration gain. The diagnostic VM is
paused, with processes60 and timing.json under
/tmp/dvm/checkpoints/APP_SPLASHBOARD_WAIT1/restores/APP_SCRD_ERROR1.
APP_AES_READY1 remains paused at724.817 active seconds. No watcher remains.
The new QEMU binary was built while both owned control/candidate VMs were
paused; default device behavior is unchanged unless the new flag is set.


### Exact zero-product candidate

APP_SCRD_ERROR1 completed a second60.038 active seconds (120.078 total)
without a new unanswered SEP request. A host sample5s into this interval
still has image-related gvec float multiply/add/FMA helpers. Its display60
SpringBoard stack is in BKSSystemShellService's waitForDataMigration check-in,
while the wrapper is in the known SplashBoard image-generation path. This
alone does not establish a circular dependency: image workers are active.
The error diagnostic VM was quit after its evidence was collected.

The new experimental float32_mul fast path handles only zero times finite
normal/zero operands. It returns the exact XOR-signed zero without adding
exceptions; subnormals, infinities and NaNs retain the generic behavior.
The canonical soft_f32_mul oracle matches5,051,200 result/exception pairs,
including all eight rounding modes, input/output flush controls, default-NaN
mode and initial inexact flag. This is correctness evidence, not a measured
speedup. The immediately preceding executable is preserved at
/tmp/dvm/app-before-zero-mul/qemu-system-aarch64. APP_ZERO_MUL1 uses the
same APP_SPLASH_ASTC1 checkpoint, six CPUs, headless, no debugger, and the
unchanged sparse v6 encoding observer for an equal-work comparison.


### Repeated zero-product result

Four alternating restores of APP_SPLASH_ASTC1 used six CPUs, full geometry,
headless display, no debugger, and the same sparse v6 observer. Each ran for
40 active seconds; the preselected comparison counts the first 12 complete,
nonnull format1 encodes and excludes the in-flight call at restore.

| Run | Binary | First12 span (s) | Encoding sum (s) |
|---|---|---:|---:|
| APP_ZERO_MUL1 | min/max + zero-product |22.200895|20.463487|
| APP_ZERO_MUL_CONTROL1 | immediately preceding min/max |27.599555|25.730877|
| APP_ZERO_MUL2 | min/max + zero-product |22.652398|20.806518|
| APP_ZERO_MUL_CONTROL2 | immediately preceding min/max |26.485736|24.603406|

Mean first12 span is reduced 17.07%. Both candidate runs beat both controls.
Retain the zero-product path for fresh-boot validation. This is an encoding
workload result, not a whole-migration or interactive-latency improvement.
The 5,051,200 differential comparisons pass, and the QEMU diff check is clean.
All four benchmark VMs were quit; the older cold diagnostic VMs remain paused.
Control1 initially failed before guest execution because the preserved binary
had no companion qemu-img; linking the existing build tool allowed a clean
restore. That setup failure is excluded from active timing.

Candidate QEMU SHA256:
0bbd9f4506de6b7ef4f7f52cb34b0e457b13d05db6b655562bdbe1ad2e79a8f9.
Preserved immediate control SHA256:
68b9dcec07404080bee5d0106c220e77bf21968df76cd08af7ef3ae6c014730b.
Each run's encoding12.json, timing.json and milestones.tsv are in its directory
under /tmp/dvm. No commits, merges, or pushes were made.


### Migrated Home responsiveness spot check

APP_UI_PERF1 restored INPUT_V6_NATIVE_HOME1 with the zero-product candidate,
six CPUs, headless, no debugger or plugins. This is an older migrated image
with the v6 input helper, not validation of the latest input implementation.
A 25-second bounded interval included a native relay swipe and a host sample.
The first submission ACK took 7.449s; later submissions fell to tens of ms.
These are submission timings, not input-to-visible-frame latency. The final
screendump shows the distorted Home icon grid; smooth scrolling is not proven.

CPU0..3 sample observations include 15.49% translated-block lookup, 9.07%
pointer authentication, 8.83% MMU translation, and 35.69% other translated
execution. CPU4/5 have 825/849 and 822/849 condition-wait observations. These
are stack-sample proportions, not CPU-time shares or proof of broken SMP.
The guest software-renderer fallback is independently established in
ca-software-path.md. This short sample does not quantify its share of total
latency. TCG execution and missing GPU acceleration remain architectural
limits; phone-equivalent responsiveness is not established. The VM was left
paused. Evidence: APP_UI_PERF1/{host-profile.txt,helper-profile.json,swipe.jsonl,
timing.json,after.png} under /tmp/dvm.
