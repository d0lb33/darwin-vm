# iOS userspace boot: where it stops, and why

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/d47ap), booted in qemu-sptm
with the real system volume as an XNU ramdisk. Logs referenced are
`/tmp/dvm/probe/GO.serial.log` and `LONG.serial.log` (a `.clean.log` sibling is
the same file with the TXM noise stripped — see below).

## What now works

Booting the real system volume (not the restore ramdisk) reaches, with **no
kernel panic** over a 900-second run:

```
dyld[1]: dyld cache mapped system-wide: customer, auth GOTs: unmapped
apfs_log_op_with_proc:3279: md0s1 mount-complete volume RaveSeed24A5430a.D47DeveloperOS
libignition: 1:        goodbye: ignition sequence complete
com.apple.xpc.launchd|... (user/501/com.apple.backboardd [44]) <Notice>: service state: running
```

launchd runs its full boot-task list and spawns hundreds of services.
`AppleARMBacklight::start` succeeds. `com.apple.iomfb_bics_daemon` reaches
`running`.

`com.apple.SpringBoard` reaches `service state: spawning` and stops there
permanently — 900 seconds, no exit, no error.

## Why SpringBoard cannot start: libignition takes the ramdisk boot spec

The decisive evidence, from `GO.clean.log`:

```
libignition: 1:   rd                  : md0
libignition: 1:   arp0                : n/a
libignition: 1:   rp                  : n/a
libignition: 1:   rootfs mount flags  : 0x1480d001
libignition: 1: boot spec           :
libignition: 1:   name              : ramdisk
libignition: 1:   stage count       : 0x2
```

The `rd=md0` boot-arg selects a two-stage **restore-ramdisk** boot, not the
normal device boot. Everything downstream follows from that:

| Consequence | Log evidence |
|---|---|
| root mounted read-only | `0x1480d001` has `MNT_RDONLY`; `fixup-mobile-tmp <Error>: could not set create /private/var/mobile/tmp: Read-only file system` |
| no data volume mounted | `mount-phase-1` / `mount-phase-2`: `Skipping boot-task`; `restore-datapartition`: `optional boot task not present` |
| personas never created | `usermanagerd`: `SIGABRT \| Daemon failed to load persona manifest.` |
| clients cannot find them | 248× `Unable to find persona with type 6: 3 - No such process`, from launchd and from backboardd |

SpringBoard runs inside a persona and needs a writable `/private/var`. Neither
exists in this boot spec, so its spawn never completes. **This, not the display
stack, is the current blocker for SpringBoard.**

Our image is an APFS container with a single volume and ~6.3 GB of unused space,
so adding a role-`D` data volume is mechanically possible; whether libignition
would then mount it is the open question.

## The TXM log flood

XNU emits this **1,626,184 times in a 300-second boot** — 99.8% of all serial
output, and 5,371,513 lines over a 900-second run:

```
TXM [Error]: selector: 38 | 42
```

The format string lives in the **kernelcache**, not in TXM:

```
$ strings -a firmware/txm   | grep -c "selector: "   ->  0
$ strings -a firmware/sptm  | grep -c "selector: "   ->  0
$ strings -a firmware/bootkc| grep -c "selector: "   -> 28
```

So XNU calls TXM, gets a failure, and logs it. `bootkc` holds five such formats:

```
TXM [Error]: TrustCache: selector: %u | 0x%02X | 0x%02X | %u
TXM [Error]: CodeSignature: selector: %u | 0x%02X | 0x%02X | %u
TXM [Error]: Errno: selector: %u | %d
TXM [Error]: Image4_V2: selector: %u | %u
TXM [Error]: selector: %u | %u
```

Ours is the last — the **generic fallback**, so the failure is in none of the
TrustCache / CodeSignature / Errno / Image4_V2 domains. Selector is 38, error 42.

The rate is bursty, not a spin loop: counting TXM lines between consecutive
timestamped launchd lines gives 39,765 in one guest second at 00:00:17 but zero
at 00:01:27. It tracks process-spawn activity.

Each line is an emulated-UART MMIO trap per byte, so this dominates guest time.
For analysis, strip it first — it makes the logs 500× smaller:

```
grep -av 'TXM \[Error\]' probe/GO.serial.log > probe/GO.clean.log   # 1629197 -> 3014 lines
```

## The OS cryptex was missing entirely

`/System/Cryptexes/OS` is a symlink to `../../private/preboot/Cryptexes/OS`, and
on our built image `/private/preboot/Cryptexes` **did not exist**. The base
system volume also ships 32 graft symlinks pointing into that tree, covering the
WebKit / Safari / AuthenticationServices family plus `GPUExtension.appex`,
`WebContentExtension.appex` and `NetworkingExtension.appex` — all dangling.

Observed effect: processes died with

```
Library not loaded: /usr/lib/libramrod.dylib (no such file, not in dyld cache)
couldn't dlopen libobjc-trampolines.dylib ... (no such file, not in dyld cache)
```

The cryptex (`/tmp/dvm/aea/out/094-13150-145.dmg`) is 5.4 GB, but 5.3 GB of that
is its own dyld shared cache, which is **byte-identical to the one already on our
image** (same 80 files, same sizes, SHA-256 `8e5da188...` on
`dyld_shared_cache_arm64e`) because `build_rootfs.sh` already stages it. The
genuinely missing payload is only the 2,111 loose files, ~127 MB.

`/tmp/dvm/merge_cryptex.sh` copies them at root-relative paths, replacing the 32
dangling symlinks with real directories. Of the 2,111 files, 54 are Mach-O and 49
are signed; all 49 cdhashes are present in `merged_sysvol_cryptex_tc.bin`, which
is exactly the union of the system-volume and cryptex trustcaches (3,802 + 130 =
3,932 entries, no overlap). Result: `/tmp/dvm/build/rootfs_cx.dmg`.

## Device tree

`dt_40g.bin` — used for all the boots above — contains **no display hardware at
all**: zero `dcp-expert-v1`, zero `disp0,t8140`, zero `dart,t8110`. The DCP work
and the 40 GB-RAM work had been carried on separate trees. The combined tree is

```
python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/dt_dcp_40g.bin \
    -nvram nvram.bin -enable dcp -dram 40G
```

## Notes for anyone continuing

- `probe.sh` reports `reached shell: no` for these boots. That is correct and not
  a regression: this is the real system volume, which runs launchd and never
  offers the serial root shell the restore ramdisk does.
- Guest time tracks wall-clock roughly 1:1 despite the log flood.
- Boot-arg `io=0x1f` makes IOKit log every driver match and start.

## The spawn blocker: AMFI waits on a SEP that is not there

`posix_spawn` never returns for SpringBoard — it never gets a pid. The split is
clean: every service that spawns through `xpcproxy` reaches `running`, and the
only two that spawn directly (`com.apple.SpringBoard`, `com.apple.systemstatusd`,
both `.app` bundles rather than daemons) hang in `spawning`.

AMFI consults the SEP-backed credential manager on that path:

```
AMFI: trying to get developer mode status from ACM
AppleCredentialManager: startImpl: will join SEPManager's PM tree in getSEPEndpoint().
AppleCredentialManager: waitForSEPEndpointOutsideACMCommandGate: waiting (cmd=25).
ACMTRM: waitForSEPEndpoint: timed out waiting for AppleSEPManager (timeoutMs=5000).
```

That last line appears **178 times in a 900-second boot — 890 seconds of a 900
second boot spent blocked**, continuously, from kernel driver start (clean-log
line 124) to the last line of the run at guest time 00:14:50. `dt_fixup.py` was
stripping the SEP node, so `AppleSEPManager` never existed and every wait ran its
full timeout.

### Enabling the node gets the driver to start

`-enable sep` keeps `/arm-io/sep` (`iop-sep,ascwrap-v6` — the same ASC wrapper
family as the DCP's `iop,ascwrap-v6`) and `/arm-io/dart-sep` (`dart,t8110`).
Both are already modelled. `AppleSEPManager` then matches and starts:

```
virtual bool AppleSEPManager::start(IOService *): Setting custom allocator for SEP MPM mapper
virtual bool AppleSEPManager::start(IOService *): SEP memory IS coherent
panic(cpu 0 caller 0xfffffff0295bcd2c): "REQUIRE fail: panicBytesData != nullptr
    @ bool AppleSEPBooter::initForSEP(AppleSEPManager *):58"
```

So the remaining gap is one buffer the booter requires. Node shape, from
`/tmp/dvm/dtree_raw`:

| Node | compatible | reg | interrupts |
|---|---|---|---|
| `/arm-io/sep` | `iop-sep,ascwrap-v6` | `0x2_7260_0000`, `0x8_0000` | 344, 343, 346, 345 |
| `/arm-io/sep/iop-sep-nub` | `iop-nub,sep` | — | — |
| `/arm-io/dart-sep` | `dart,t8110` | `0x2_38B7_0000`, `0x2_0000` | — |

The nub is `iop-nub,sep`, not `rtbuddy-v2`, so `AppleSEPManager` drives it rather
than `RTBuddy`, and SEP speaks its own protocol above the mailbox — not RTKit.

### Two cheap bypasses, both tested, both dead

Recorded so nobody spends the time again:

- **`ramrod_disable_sep_load=1`** (a real boot-arg in this kernelcache) does not
  help: `AppleSEPBooter::initForSEP` still panics, 4 panics in a 90s boot.
  Log: `/tmp/dvm/probe/SEPNOLOAD.serial.log`.
- **`trm_enabled=0`** does not help either. `ACMTRM` does read it
  (`initU32FromBootArg: trm_enabled`, overriding `/defaults/trm-enabled = 0x1`),
  but the wait is issued by `AppleCredentialManager`
  (`waitForSEPEndpointOutsideACMCommandGate`), not by TRM, so disabling TRM
  leaves the loop running. Log: `/tmp/dvm/probe/TRM0.serial.log`.

The goal is not working SEP cryptography. It is `AppleSEPManager` publishing the
endpoint so `ACM` stops blocking. `AMFI` has fallback paths that are worth
knowing about — the kernelcache carries `AMFI: developer mode is force enabled`,
`AMFI: Enabling developer mode since protected data is not available` and
`AMFI: Enabling developer mode since we are restoring....` — but reaching them
still requires ACM to answer rather than block.
