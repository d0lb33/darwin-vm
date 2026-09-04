# External checkpoint and restore

This implementation pairs a QEMU migration stream with one exact, immutable
ANS qcow2 generation.  It deliberately does not use internal qcow2 snapshots:
the source QEMU is paused, the `ans` backend is explicitly flushed, RAM and
device state are written to a migration file, and the verified source process
is terminated.  Every restore gets a new writable qcow2 child above the
read-only checkpoint disk.

The starting revisions for this work are:

- superproject: `8e97927d57a2343102aa7d886666023518cf4f25`
- `qemu-sptm`: `b284c666f7fd582e090dd33e1ae104f0e3eeba33`

Development is isolated on `codex/resumable-checkpoints-super` and
`codex/resumable-checkpoints-qemu`.  The dirty main checkout and its QEMU
processes are not inputs to this work.

The starting binary identifies itself as QEMU 11.1.0.  Its command-line help
explicitly advertises `-incoming file:filename`, `-incoming defer`, and
`-dump-vmstate`; its HMP table supplies asynchronous `migrate -d`,
`info migrate`, and `migrate_set_parameter`.  The implementation therefore
uses the supported external file migration transport rather than `savevm`.
The power-transition build's dump is
`/tmp/dvm/ckpt-power-v2-vmstate-inventory.json` (SHA-256
`5c951af9dc134df4f7dec22c4809a250a3a09fd6cf0fcc1bbbcbcc37b245f01d`).
It contains the QOM sections for AIC, ASC, SEP, ANS, DART, SART, framebuffer,
and AMCC.  AFK, DCP, IOMFB (when configured), and sparse-unimplemented state
are registered non-QOM sections and do not appear in this type-schema dump;
instantiated sections do appear in migration analysis.

## Security model

`vmstate.bin` can contain guest memory, Apple implementation-defined CPU
registers, and SEP/SKS state.  Treat the entire checkpoint directory as secret
and local.  The creator makes the directory mode `0700`, the migration stream
mode `0600`, and the frozen disk mode `0444`.  Do not publish these artifacts.

QEMU virtual time is stopped while no process exists.  Restore starts with
`-S`; execution resumes only after the incoming stream has loaded and the
captured PC matches, so host wall time between create and restore is not
delivered to guest virtual timers as one large jump.

## Launch and create

Start from a disposable qcow2 child, never an established seed parent.  Give
the run a unique tag, monitor, UART, serial log, disk, and GDB port.  The setup
probe can record the exact QEMU argument vector and relevant model environment:

```sh
TAG=CKPT_EARLY_1
CHECKPOINT_LAUNCH_MANIFEST=/tmp/dvm/${TAG}.launch.json \
  tools/re/setup_gate_probe.sh "$TAG"
```

Use a condition-bounded probe from `CLAUDE.md` to stop at the chosen marker.
Then create the checkpoint while that exact QEMU PID still exists:

```sh
python3 tools/create_checkpoint.py \
  --tag "$TAG" \
  --monitor "/tmp/dvm/${TAG}.sock" \
  --pid-file "/tmp/dvm/${TAG}.qemu.pid" \
  --launch-manifest "/tmp/dvm/${TAG}.launch.json" \
  --disk "/tmp/dvm/data-seed/${TAG}.qcow2" \
  --serial-log "/tmp/dvm/probe/${TAG}.serial.log" \
  --marker-regex 'Early boot complete|UIApplicationMain'
```

The creator refuses a stale PID, a non-QEMU process, an argv mismatch, or a
monitor/disk not owned by the recorded process.  It captures registers,
`info qtree`, `info mtree`, `info cpus`, and `info block`; flushes `ans`; waits
for completed migration; quits only the verified PID; checks the qcow2; hashes
the stream, every qcow2/raw backing-chain member, QEMU, and immutable boot
inputs; and writes `manifest.json`.

## Restore twice

Each invocation creates a fresh child disk and unique sockets.  It verifies all
input hashes, starts a new QEMU with `-incoming file:...`, waits for incoming
migration to finish in the paused state, requires an exact PC match, resumes,
observes for 120 seconds by default, samples post-resume state, and leaves that
specific guest running:

```sh
MANIFEST=/tmp/dvm/checkpoints/CKPT_EARLY_1/manifest.json

python3 tools/restore_checkpoint.py "$MANIFEST" \
  --tag CKPT_EARLY_1_R1 --gdb-port 1261 --observe-seconds 120

# After terminating R1 by its exact PID, restore the same immutable checkpoint.
python3 tools/restore_checkpoint.py "$MANIFEST" \
  --tag CKPT_EARLY_1_R2 --gdb-port 1262 --observe-seconds 120
```

The restore report records source/restored/post-observation PCs, time to load,
RSS, serial bytes, repeated boot signatures, first `panic(cpu`, model traffic
counts, the continued leading firmware trace clock (when present), the new PID,
and the unique writable disk.  The source migration stream
and frozen disk hashes provide one checkpoint witness; completed migration plus
an exact pre-resume PC match in a different PID provide an independent restore
witness.

For the traffic-heavy final run, launch with `DARWIN_ANS_DEBUG=1` and
`DARWIN_SKS_REQUEST_DEBUG=1` in addition to the established DCP/IOMFB tracing.
The restore report counts ANS reads and writes, completed SKS replies, DCP/AFK
messages, and IOMFB messages only from stderr bytes written after `cont`; model
initialization before incoming migration cannot satisfy those witnesses.

## Paused debugger replays (2026-09-04)

Use `--leave-paused` to load, verify the exact saved PC and activate the ANS
backend without executing any guest instructions. It sets observation time to
zero and leaves the guest stopped for breakpoint installation. A separate QMP
socket activates migrated block nodes with `blockdev-set-active`; proceeding
straight through GDB without this step otherwise trips `BDRV_O_INACTIVE` on the
first disk write. The report records the paused state and disk activation.

```sh
python3 tools/restore_checkpoint.py \
  /tmp/dvm/checkpoints/DISPLAY_WARM_READY1/manifest.json \
  --tag DISPLAY_NATIVE_R2 --gdb-port 1362 --leave-paused \
  --qemu qemu-sptm/build/qemu-system-aarch64
```

`--qemu` explicitly selects a development binary for a compatible-state replay.
All other hashes remain enforced. The report records both binary hashes and
marks the replay as development; this does not modify the source manifest or
prove that arbitrary QEMU versions are compatible. A checkpoint created with
that new binary pins its hash normally.

QEMU now maps a saved `DEBUG` run state to `PAUSED` on incoming migration. The
source debugger's breakpoint tables do not migrate, and `INMIGRATE -> DEBUG`
was an invalid state transition. This preserves the exact saved PC while
allowing a fresh LLDB session to reinstall its observers. Chained restores also
replace, rather than retain, the source QMP socket.

`DISPLAY_NATIVE_MIGRATION1` captured the native DCP acknowledgement model,
without injected replies or userspace bypasses: 2.56 GB state, 1.54 s migration,
11.83 s total checkpoint creation. It still contains the old, semantically empty
SEP device-state reply; see `sks-userspace-selectors.md` before reusing that
checkpoint for unlock/migration experiments. `DISPLAY_WARM_READY1` is earlier
and can exercise the corrected SEP reply before services enter their waits.

## Device coverage

`checkpoint-device-inventory.json` is the field-level inventory for CPU/SPTM,
AIC, ASC/RTKit, SEP/SKS, ANS/NVMe, DART, SART, DCP/IOMFB/AFK, framebuffer,
timers, interrupts, UART, AMCC, and sparse unimplemented-register state.

The qemu-sptm implementation covers every listed entry.  AIC and ASC support
was applied after explicit owner authorization in QEMU commit `60e1fd0`: it
serializes pending/masked AIC interrupts and ASC mailbox/RTKit endpoint state,
validates bounds, migrates the optional virtual timer, and reasserts
destination-process IRQ lines in `post_load`.  QEMU commit `d35d9ce` versions
the subsequently added ASC firmware-status field and reconstructs it when
loading a version-1 stream.

## Test ladder and acceptance evidence

Run these checkpoints in order, retaining each manifest and restore report:

1. minimal CPU/RAM control;
2. early launchd;
3. encrypted Data/User volume mounted;
4. `Early boot complete`;
5. SpringBoard entering `UIApplicationMain`;
6. recent or active DCP, SEP, and ANS traffic.

For the final checkpoint, keep both independent restores alive for at least
120 seconds.  Confirm no repeated kernel/launchd banner, stable guest process
identity, plausible continued uptime, post-resume serial output, ANS reads and
writes, completed SEP/SKS requests, DCP/IOMFB callbacks and interrupts, and no
first `panic(cpu` or SPTM panic.  Finish with one ordinary cold-boot regression.
The tooling collects evidence but does not convert a migration command's exit
status into a claim that these guest-level checks passed.

## 2026-09-04 runtime results

The bounded control ladder includes both the expected failure before AIC/ASC
support and successful new-process restores after that support was applied:

| Checkpoint | Boundary | Create evidence | Restore evidence | Result |
|---|---|---|---|---|
| `CKPT_CPU_CTRL7` | prelaunch CPU/RAM control using the final allowed-file binary | PC `0x100070a0388`; 414,830,884-byte stream; 1.535 s migration; source PID 61605 exited | PIDs 61787 and 61933 each loaded the exact PC from a fresh disk child, advanced into XNU, emitted 51,129/51,153 new serial bytes, and completed SKS traffic without XNU/SPTM panic in the 15 s control window | control passed; the repeated bootstrap banner is expected because the checkpoint precedes boot |
| `CKPT_EARLY_BOOT2` | after `Early boot complete` and a live SEP/SKS request | PC `0xfffffff02ac72da8`; 1,117,096,894-byte stream; 1.024 s migration; source PID 58979 exited | PID 59233 loaded the exact PC, advanced, emitted new serial output, and did not repeat a boot banner | failed after resume: SKS timeout strikes followed by an IONVMeFamily panic at 27 s |
| `CKPT_AICASC_EARLY2` | after `Early boot complete`, using the migrated Data/User parent and the owner-authorized AIC/ASC build | PC `0xfffffff00709a610`; 663,051,921-byte stream; 1.530 s migration; source PID 65586 exited | PIDs 66009 and 67448 each loaded the exact PC in 0.940/0.941 s from independent fresh disk children and ran 120 s; both showed execution and serial progress, ANS reads/writes, SKS replies, DCP/IOMFB traffic, no repeated boot banner, and no XNU/SPTM panic | passed twice; this directly fixes the prior 27 s failure |
| `CKPT_SB_STAGE1_PROGRESS` | 14+ minutes after restoring the early checkpoint, while Data-volume metadata and SEP traffic were still progressing but before `SB_ADFL_ENTRY` | PC `0xfffffff02aaf76e4`; 3,542,058,441-byte stream; 1.537 s migration; source PID 68274 exited | PID 75975 loaded the exact PC in 1.053 s, advanced for 60 s, emitted 8,318 serial bytes, completed ANS and SKS traffic, and showed no repeated boot banner or XNU/SPTM panic | resumable investigation point, not SpringBoard acceptance evidence |
| `CKPT_POWER_V2_EARLY1` | native ASC VMState version 2 after merging the committed RTKit sleep/restart model | PC `0x18ceab46c`; 2,113,782,729-byte stream; 1.523 s migration; bridge source PID 80960 exited | PIDs 81713 and 82432 each loaded the exact PC in 1.012/1.007 s from independent fresh disk children and ran 120 s; both advanced, emitted 14,132/13,559 serial bytes, completed ANS and SKS traffic, and showed no repeated boot banner or XNU/SPTM panic | passed twice; DCP/IOMFB was quiescent in both native post-resume windows, while the preceding 120 s compatibility run did show live DCP/IOMFB traffic |

The first post-resume request printed by the failed pre-AIC/ASC restore was an
already submitted QID 1 NVMe read.  ANS queue indices, disk generation, and
controller state were present in the stream, but the AIC and generic ASC were
absent from `-dump-vmstate`.  The destination therefore lost the pending AIC
delivery and RTKit mailbox state: the NVMe request timed out and SKS accumulated
timeout strikes.  The two `CKPT_AICASC_EARLY2` restores show that preserving
those devices removes that failure; it was not an ANS completion bug.

The progressed SpringBoard-stage run did not hit `SB_ADFL_ENTRY` within its
bounded condition windows.  It also did not reproduce the other display
experiment's runaway DCP restart loop: there was no repeated coprocessor
handshake/power-cycle storm and no endpoint-11 DCP NMI.  This is deliberately
not a claim that the late DCP power path is fixed.  The committed ASC
power-transition implementation was subsequently merged and is included in
`CKPT_POWER_V2_EARLY1`.

`COLD_REG_CKPT1` was an ordinary 30-second cold boot from a fresh qcow2 child
after the general device-state implementation.  It produced 634 serial lines,
reached the normal early-boot region, and had zero XNU/SPTM panics.  The AIC/ASC
build then independently cold-booted to `Early boot complete` while creating
`CKPT_AICASC_EARLY2`.

The development-only manifest
`/tmp/dvm/CKPT_EARLY_BOOT1.development-bridge.json` was used solely to carry an
older early-boot state across one validator-only rebuild.  It is labeled
`not_acceptance_evidence` and is not one of the results above.

`/tmp/dvm/CKPT_AICASC_EARLY2.power-v2-bridge.json` is likewise explicitly
labeled development-only.  It loaded the version-1 early checkpoint through
the version-2 compatibility path, ran for 120 seconds with ANS, SKS, DCP and
IOMFB traffic and no panic, and was consumed only to create the native
`CKPT_POWER_V2_EARLY1` artifact.

## EL2 virtual timer lost on restore (2026-09-04)

`DISPLAY_RESUME_R13` exposed a missing derived timer state. The native
MobileGestaltHelper worker `0xffffffe18f1e7060` remained inside `sleep(5)`
(static return `0x187f09cf8`, caller `0x1af7378fc`) across many minutes.
A physical read of its sleep frame retained `{tv_sec=5, tv_nsec=0}`.
Host LLDB against owned QEMU PID 74395 measured:

- `qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) = 2966712543000`;
- `gt_get_countervalue = 3113404105`;
- `c14_timer[GTIMER_HYPVIRT] = {cval=2056248248, ctl=1}`;
- `gt_timer[GTIMER_HYPVIRT]->expire_time = -1` (unscheduled).

Thus the enabled timer was overdue, but no host expiry could assert its IRQ.
`target/arm/machine.c:vmstate_arm_cpu` migrates only the EL1 physical and
virtual QEMUTimers. The cpreg array carries the EL2 CVAL/CTL values, but raw
cpreg loading does not reconstruct the corresponding QEMUTimer. The new
`arm_gt_rebuild_extra_timers()` uses the existing architectural recalculation
for each allocated non-EL1 timer after TCG CPU load. It preserves the stream
format and supports existing checkpoints. Real restored-guest validation is
pending; this is not yet proof of a resolved display gate.

`DISPLAY_PRE_TIMER_FIX13` preserves the exact failing state at PC
`0xfffffff02aa60400` (9,416,971,469 state bytes; migration 3.563 seconds;
14.471 seconds total). Installation migration remains completed; this branch
retains the previously documented one-provision watchdog diagnostic.

The fixed binary restores that exact checkpoint in `DISPLAY_TIMER_R14`
(PID 78930) with an exact PC match in 2.028 seconds. Before any guest
instruction executes, host LLDB measures the same CVAL `2056248248`, now
CTL `5` (enabled plus interrupt status), count `3125276079`,
`irq_line_state=16`, and host expiry `INT64_MAX` (next counter rollover).
This is the architectural expected result for an already expired timer;
the missing expiry/IRQ is repaired from existing state without guest writes.
Build: `/tmp/dvm/DISPLAY_TIMER_FIX.build.log`. Guest wakeup proof follows.

R14 supplies native wakeup proof: `MG_IOKIT_BUSY_RETURN` hits at
t=1788559999.831 on the same formerly sleeping thread, with kernel result 0
and busy count 1. Its next five-second retry spans t=1788560035.653 to
1788560041.461 (5.807 seconds including debugger overhead), replacing the
indefinite pre-fix sleep. The native migration wrapper then records eleven
plugin returns with `x0=1` between t=1788560039.672 and 1788560046.820 at
runtime `0x1004fb708`. These are migration progress, not display completion.
Evidence: `/tmp/dvm/DISPLAY_TIMER_R14.guest-lldb.log` and its event directory.
Host regression suite: 21 tests passed; shell syntax and diff checks passed.

R14 later fails natively: serial line 41480 is the first panic,
`userspace panic: datamigrator plugin SpringBoard.migrator hang`. It is a
post-timer-fix timeout, not an SKS or nested-panic fault. Ten observed
SplashBoard image encodes returned between t=1788560211.961 and
1788560278.700, so the plugin was producing work rather than deadlocked.
No post-panic checkpoint was made. R15 instead replays INSTALL_DONE12
with the fixed binary and observes the per-plugin deadline before timer
creation at datamigrator static `0x100013224`. The optional timing diagnostic
changes only the measured SpringBoard plugin's +0x28/+0x30 double deadlines;
plugin execution and return values remain native. See
`tools/re/migration_deadline_callbacks.py`; default is read-only.

R16 verifies the exact scoped timing change at t=1788560835.218: plugin
object `0x79a4c5e1c0`,
name `SpringBoard.migrator`, before `{watchdog=0,reboot=600}`, after
`{watchdog=0,reboot=3600}`, write verified 16 bytes. The zero watchdog
remains disabled. The name object is Swift `__StringStorage`, whose cache
class is `0x1e6f0c220`; R15 live bytes prove UTF-8 data at +0x20, low-48-bit
count at +0x18, and capacity +0x10. The decoder checks class/count/capacity
before interpreting this form. R15 had no applied deadline changes and was
quit before panic after discovering the unsupported representation.

R16 continues through 34 native SplashBoard image encodes. The paired
`DISPLAY_SNAPSHOTS16_TIMING` checkpoint captures that progress with the fixed
timer binary: PC `0xfffffff00709a610`, 6,504,788,843 state bytes,
90,963,968 disk bytes, 2.574 seconds migration / 13.232 seconds total.
`DISPLAY_TIMER_R17` restores it in 1.357 seconds with exact PC match, and
native image encodes resume. This verifies a second-generation checkpoint
under the timer fix. It retains both documented timing diagnostics.

A separate native dependency is now identified precisely: locationd worker
`0xffffffe18f469120` waits in a generated MIG call, runtime
`0x1032c6f84` (return `0x1032c700c`), message ID 1203 / expected reply 1303.
The caller's decoded service string at `0x10848498e` is
`com.apple.fairplayd.versioned`. The bounded saved stack and live code dumps
are `/tmp/dvm/DISPLAY_TIMER_R16.locationd.json`, `.locationd-mig.bin`, and
`.locationd-caller.bin`. Passbook migration waits synchronously on passd,
whose main thread waits on CoreLocation. This is dependency evidence, not
a claim that FairPlay is the only unfinished migration gate.
R17 catches `fairplayd.H2` calling native exit(0) at kernel syscall entry
`0xfffffff02aff6bd8`, t=1788561627.315. Its saved stack goes through dyld's
main-return exit path (`0x184fb75c8`). This corrects the preliminary suspicion
of a startup crash: the observed exit is explicitly status zero.

R17 finally reaches the native backboardd migration return at
`t=1788562216.650`, runtime `0x100f473fc`, verified progname `backboardd`,
thread `0xffffffe18f4020c0`, x0=0. The preceding last observed plugin
completion is AMFIMigrator with success=1 at t=1788562215.295, native
`DMMigrationState pluginDidFinish:withSuccess:...` runtime `0x10214df2c`.
The SpringBoard worker `0xffffffe18f50a890` returned 1 at
`t=1788562017.214` after 56 observed encodes across R16/R17. Its wrapper name
object was unsupported by the string decoder; the identity is from that
worker's earlier native SpringBoard.migrator stack, not guessed from order.
The paired **DISPLAY_MIGRATION_RETURN17_TIMING** checkpoint preserves the
backboardd return before its next instruction: PC `0x100f473fc`,
6,873,142,787 state bytes, 47,644,672 disk bytes, 3.046 seconds migration /
14.579 seconds total. Use it for subsequent UI experiments. It retains the
documented SpringBoard plugin reboot deadline and earlier single FrontBoard
provision timing diagnostics; no migration completion was forced.

FairPlay remains a separately identified service failure, not a necessary
repair for this migration return. Native selector 21 returns 552 bytes with
`-42402` at +0x224 while IOReturn is zero. Driver static `0x9b526ac` marshals
the call through vtable +0x578, then `0x9b52740` writes the provider's error
at output+0x224 and `0x9b52750` returns zero. Prefix these addresses with
`0xfffffff000000000`. Provider static `0xfffffff009b52504` tailcalls
`0xfffffff009c65e88`. The actual trace and input/output structures live in
`DISPLAY_TIMER_R17.events/progress.FAIRPLAY_SELECTOR21_RETURN.json`;
`tools/re/fairplay_boundary_callbacks.py` reproduces this read-only capture.
No FairPlay response or status was patched.


R18 restores the native migration return in 1.908 seconds and produces the
first actual IOSurface handed to fb_swap_set_layer: 1179x2556 BGRA, row stride
4864, 12,432,384 captured bytes; 3,013,524 opaque pixels and zero nonblack
pixels. SHA-256 `92c6bda6476a4d816581935a9cb9ee7c8f5fdd85985b4654eeff1027dda59868`.
Native generic-map entry follows at `0xfffffff02a0c366c`; A407/A408 are not
yet observed. SpringBoard reaches ADFL entry `0x229000ba4` natively. See
`/tmp/dvm/DISPLAY_UI_R18.events/` for exact progress and surface records.

DISPLAY_INDICATOR_WAIT18 captures the next native failure boundary at
`0x189098fe0` (w0=-1), 7,066,106,036 state bytes, 30,867,456 disk bytes.
R19 and R20 restore in 1.341/1.345 seconds. Investigation proves missing
ExclaveOS SIL assets; see `exclave-assets.md`. R20 provisions the unchanged
firmware assets through guest syscalls and resolves Camera natively to 0.
The paired **DISPLAY_SIL_READY20_TIMING** checkpoint preserves this return
with a nonnull SIL manifest, 7,065,297,938 state bytes, 20,578,304 disk bytes,
3.036 seconds migration / 14.474 seconds total. Its directory strings point
to the diagnostic mobile Library asset directory. Production bootstrap now
installs ExclaveOS at its original Preboot path instead.

R21 restores DISPLAY_SIL_READY20_TIMING in 1.759 seconds and enables the
existing scoped FrontBoard 600-second timing probe before continuing. R19's
native Setup scene-create deadline expired after 20 wall seconds with only
0.654 seconds of application CPU. R21 explicitly verifies native provision
durations changed from 10/20/30 to 600; termination bypasses remain off.
All runs since DISPLAY_WARM1 remain RAM+disk checkpoint restores.
