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

## The normal boot path works

`rootdev=md0` plus a one-instruction kernel patch and a device tree change gets
iOS off the restore path. See the commit "boot iOS down its normal path instead
of the restore ramdisk" for the derivation; the short version:

- `rd=md0` is matched by launchd (`strstr(kern.bootargs, "rd=md0")`) and by
  libignition (exact token). `rootdev=` is an XNU alias for the same memory disk
  that neither of them recognises.
- Only `bsd_rooted_ramdisk()` still reads `"rd"` alone, so with `rootdev=` it
  returns false and `bsd_init` panics "rootvp not authenticated after mounting".
  `xnu_patch.c` forces it true.
- `/private/var` comes from Apple's own ephemeral-recovery fstab, promoted by
  `dt_fixup.py -ephemeral-data`, which mounts a tmpfs seeded from the on-volume
  template. No storage controller needed.
- `/arm-io/sep` must be **removed**, not merely stripped of `compatible`:
  `seputil` keys off node presence.

Result:

```
libignition: 1:   name              : local
libignition: 1:   stage count       : 0x7
(mount-phase-1) <Notice>: Doing boot task          [was: Skipping boot-task]
mount: data volume missing, but not required in env: 2
Mounting tmpfs volume at tmp location /.b/8/
Executing command: /sbin/mount_tmpfs -s 4294967296 /.b/8/
UMLCreatePrimaryUserLayout passed without error
(fixup-mobile-tmp) <Notice>: Finished boot task
```

with **zero** "Read-only file system" errors (twelve daemons used to die on
that), **zero** `kpersona_find_by_type` failures, and **zero**
`ACMTRM: waitForSEPEndpoint` timeouts — removing the SEP node eliminated that
890-seconds-per-900 stall outright, and `seputil` now reports
`init_data_protection: No SEP present on this device` and passes.

The boot-task chain that used to be skipped now runs: `fsck`, `mount-phase-1`,
`data-protection`, `finish-obliteration`, `detect-installed-roots`,
`select-boot-mode`, `commit-boot-mode`, `restore-datapartition`,
`mount-phase-2`, `init-with-data-volume`, `fixup-mobile-tmp`, `fips`.

### Two blockers on this path

**1. `ramstrategy: buf_map failed @memdev.c:299`** during the template copy,
with the full template. It is **exactly reproducible**: two identical boots both
copied 2,493 files and died on the same file,
`staged_system_apps/FindMy.app/.../assets/iPhone Wallpaper.png`. That file is
unremarkable (420,765 bytes, and larger files copied fine before it), and its
data lives at ~7.93 GiB in the image — above 4 GiB but not on a boundary, and
many earlier files are above 4 GiB too. A fixed *count* rather than a fixed
offset points at something consumed per-operation and not returned.
`bsd/dev/memdev.c` has one `buf_map` (line 298) and one `buf_unmap` (line 357),
so the source has no leak; whether the compiled, patched function does is open.

Workaround that avoids it entirely: delete `/private/var/staged_system_apps`
from the image. It is 697 MB of the template's 5,992 files, leaving 50, and the
copy then completes. `/tmp/dvm/build/rootfs_slim.dmg` is that image.

Note `memdev.c:301` has another unpatched 32-bit shift,
`fvaddr = (mdev[devid].mdBase << 12) + blkoff`, in the same family as the three
`mdSize << 12` sites we do patch. It cannot cause this panic (it runs after
`buf_map`), but if it wraps it is silent data corruption.

**2. The `keybag` boot task hangs.** With the copy out of the way the boot
reaches:

```
(keybag) <Notice>: Doing boot task
****** IN MKB_INIT ****
MKB_INIT: Could not open /private/var/keybags/systembag.kb: No such file or directory
MKB_INIT: Could not open /private/var/keybags/systembag.kb.writing: No such file or directory
MKB_INIT: Unable to load keybag with No Crypto: -7
```

and stops there — no panic, no "Finished boot task", guest time frozen while the
VM keeps running. `/private/var/keybags` is **absent from the system-volume
template**; creating it (mode 700) in the image changes nothing, so the
directory is not the problem. What creates `systembag.kb` without a SEP is the
open question.

### Enabling SEP is not a way around the keybag

Tested directly (`/tmp/dvm/probe/NSEP.clean.log`), because
`AppleSEPManager::start` now succeeds and it was reasonable to hope `seputil`
would too. It does not:

```
(data-protection) <Notice>: Doing boot task
init_data_protection: Waiting 60 seconds for AppleSEPManager...
init_data_protection: Timeout trying to connect to the SEP
init_data_protection: Failed to connect to sep
panic(cpu 0 ...): seputil[4] exited ... (signal 0, exit status 60)
```

even though, in the same boot, the driver gets further than it ever has:

```
AppleSEPManager::start: control endpoints created
"AppleSEPKeyStore": starting (BUILT: Aug 13 2026 22:21:06) ("normal" variant, 2383.2.1)
```

`seputil` wants a live SEP *connection*, not merely a matched driver — 18
`waitForSEPEndpoint` timeouts in that boot, waiting on the runtime-named
`sep-endpoint,scrd` endpoint that only the coprocessor can announce over the
mailbox. So the choice is currently:

| SEP node | `seputil` | keybag |
|---|---|---|
| removed | passes (`No SEP present on this device`) | cannot create `systembag.kb` |
| kept | panics launchd at 60s | not reached |

Both branches need either real SEP mailbox emulation, or a way to make the
keybag work with no SEP at all.

## Early boot completes

Both blockers above are now cleared, and the boot reaches:

```
(boot) <Notice>: Early boot complete. Continuing system boot.
```

with **zero panics**, all **11,979** template files copied (it used to die at
2,493), and **zero** `kpersona_find_by_type` failures.

### `buf_map` was kernel VA exhaustion, not our patch

The memdev widening was verified innocent, instruction by instruction, in
`mdevstrategy` at `0xfffffff00ac9561c`. Every consumer of the widened `x9` is
either 64-bit (`cmp x19, x9` at `…95694`, `cmp x8, x9` at `…956a0`) or the
32-bit clamp at `…956a8`, which is only reached when
`blkoff < size < blkoff+count` and is therefore exact. `blkoff` never narrows
(`mul x19, x9, x10` at `…95688`), and `mdBase` is a `uint64_t` in this XNU with
a 64-bit shift (`add x19, x19, x8, lsl #12` at `…956c4`) — so the
`memdev.c:301` worry above is unfounded on this build, no silent corruption.
`buf_unmap` is called at `…95894`.

`buf_map` returns ENOMEM only when `vm_upl_map` fails to map the I/O's UPL into
`kernel_map` (`0xfffffff00aca23fc`). The copier reads with `F_NOCACHE`, so
`cluster_read_direct` builds an IO-wire UPL over the *user* buffer and maps it
into the kernel-map DATA range — a path real NVMe never takes, because it DMAs
from the page list instead. That range is squeezed by reservations that scale
with DRAM: the compressor pool is `max_mem`, so **40 GiB of our 40 GB guest**,
against 8 GiB on the real phone.

The fix is a boot-arg, no patch:

```
vm_compression_limit=524288        # 8 GiB at 16 KiB pages
```

which caps the compressor as if this were an 8 GB phone and returns ~32 GiB of
kernel VA. With it, the copy completes.

Also explains an old mystery: `arm_vm_init.c:1846` panics
"Unsupported memory configuration" at `mem_size >= 56 GiB`, before serial is up.
That is why a 56 GB guest produced no output at all.

### The keybag hang, and the lever

`keybagd` finding no `systembag.kb` is normal first-boot behaviour — the `-7` is
a hardcoded literal its own caller swallows into a NULL. It then calls
`aks_get_system()`, which lands in `com.apple.driver.AppleSEPKeyStore`. That is
the **only** keystore kext in this kernelcache: `__PRELINK_INFO` has no software
`AppleKeyStore` to fall back to, and its personality matches `IOResources`
unconditionally, so it registers whether or not a SEP node exists and then
routes the call into a mailbox with nothing behind it. The block is
uninterruptible — the single vCPU sits in the SPTM WFI trap at the same PC
across repeated HMP samples, with nothing runnable.

`keybagd --init` checks `/product` for `boot-ios-diagnostics` first and exits 0
when it is present, which is what `dt_fixup.py -skip-keybag` sets. Presence is
checked, not value. **This is a skip, not a fix**: nothing gets a real keybag,
so anything that truly needs one will fail later.

Ruled out, so nobody repeats them: pre-creating `systembag.kb` (does not avoid
the `aks_get_system` call, and the payload is SEP-wrapped material we cannot
produce), creating `/private/var/keybags` (tested, no change at all), and
keeping the SEP node (`seputil` panics launchd first).

### The recipe

```
python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/dt_full.bin -nvram nvram.bin \
    -dram 40G -ephemeral-data -skip-keybag [-enable dcp]

tools/probe.sh --dtree /tmp/dvm/dt_full.bin \
  --ramdisk /tmp/dvm/build/rootfs_sh.dmg \
  --tc /tmp/dvm/tc/merged_sysvol_cryptex_ramdisk_tc.bin --mem 40G --secs 1800 \
  --bootargs 'rootdev=md0 ignition_level=1 launchd_unsecure_cache=1 vm_compression_limit=524288 serial=3 -v wdt=-1 wlan-olyhal-abort'
```
