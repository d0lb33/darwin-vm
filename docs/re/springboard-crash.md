# Why SpringBoard crashes

**Answer: `/usr/lib/libobjc-trampolines.dylib` is not on the root filesystem we
boot.** libobjc `dlopen`s it lazily, the `dlopen` fails, libobjc calls
`_objc_fatal`, and the process aborts. launchd counts four or five of those in
a row and reboots the machine.

It is **not** the display. The same crash happens with the whole DCP stack up.

Source for everything below: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/d47ap),
probes `SEPDCP`, `SBRDCP`, `SBBASE`, `SEPSNAP` (2026-09-02).

## The evidence

SpringBoard's own exit reason, recovered from guest RAM:

```
=== OS_REASON kcdata @ 0x22f5d070 ===
  BUFFER_BEGIN_OS_REASON
  EXIT_REASON_USER_DESC   "couldn't dlopen libobjc-trampolines.dylib:
                           dlopen(/usr/lib/libobjc-trampolines.dylib, 0x0106):
                           tried: '/usr/lib/libobjc-trampolines.dylib'
                           (no such file, not in dyld cache)"
  0x847 (proc name)       'SpringBoard'
```

`couldn't dlopen libobjc-trampolines.dylib: %s` is libobjc's `_objc_fatal` in
`objc-block-trampolines.mm`; `_objc_fatal` ends in `abort_with_reason`, which is
why the string survives as an `OS_REASON` payload rather than as console output.
libobjc reaches it the first time anything needs an IMP trampoline —
`imp_implementationWithBlock`, a block installed as a method, Swift/ObjC
interop — which for a UIKit application is within the first moments of
`main()`.

A full scan of the 40 GiB snapshot finds five `OS_REASON` blobs, and **three of
them are separate SpringBoard instances with the byte-identical description** -
one per launch-and-die cycle, which is the crash loop itself. Two other
processes die of the same class of fault, which is what makes this a
whole-image problem rather than a SpringBoard one:

| process | `EXIT_REASON_USER_DESC` |
|---|---|
| `SpringBoard` | `couldn't dlopen libobjc-trampolines.dylib … no such file, not in dyld cache` |
| `temporary-sandbox` | same string, verbatim |
| `lockdownd` | `Library not loaded: /usr/lib/libramrod.dylib … no such file, not in dyld cache` |

### It is not dyld, not codesigning, and not the spawn

The same RAM dump holds dyld's own launch record for SpringBoard, a binary
plist (`tools/bplist_carve.py`):

```
{'imgs': [{'file': '/System/Library/CoreServices/SpringBoard.app/SpringBoard', ...},
          {'file': '/usr/lib/dyld', ...}],
 'proc': 119, 'stat': 16 -> 80, 'plat': 2,
 'dsc1': {'file': '/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e', ...},
 'metr': {'init': 309563582, 'main': 309712081, 'lcln': 44483, 'istd': 6, ...}}
```

The shared cache is mapped, the main executable is mapped, dyld advances its
state word from 16 to 80 and records a `main` metric. So the process is
launched, signed, sandboxed and running its own code; the failure is a
**runtime `dlopen` of a file that is absent**, not a link-time failure. That
distinction is what rules out the whole family of "AMFI rejected it" and
"posix_spawn failed" hypotheses — and it is why nothing appears on the serial
console, since neither AMFI nor dyld ever complains.

## Where the file went

`libobjc-trampolines.dylib` is deliberately **not** in the dyld shared cache —
libobjc has to map its trampoline pages from a real file — so it ships as a
loose file, and on this device it ships in the **OS cryptex**, not on the base
system volume:

```
$ 7zz l 094-13182-141.dmg -ba | grep libobjc-trampolines     # system volume
                                                             (nothing)
$ 7zz l 094-13150-145.dmg -ba | grep -E 'libobjc-trampolines|libramrod'
2026-08-27 23:47:20 .....  68176        usr/lib/libobjc-trampolines.dylib
2026-08-27 23:47:20 .....  2190848      usr/lib/libramrod.dylib
```

and the image we boot does not have them:

```
$ 7zz l ~/dvm-artifacts/build/rootfs.dmg -ba | grep -E 'libobjc-trampolines|libramrod'
                                                             (nothing)
```

That is not two stray files. Comparing the two listings, **2,105 of the
cryptex's 2,113 loose files are missing from `rootfs.dmg`** (the 8 that match
are paths the base system volume happens to carry too):

```
cryptex files (excluding its own dyld cache): 2113
  present in rootfs.dmg :    8
  missing from rootfs.dmg: 2105
```

`docs/re/userspace-boot-state.md` §"The OS cryptex was missing entirely"
describes merging exactly these 2,111-odd files and names both symptoms —
`couldn't dlopen libobjc-trampolines.dylib` and `Library not loaded:
/usr/lib/libramrod.dylib`. That merge produced `/tmp/dvm/build/rootfs_cx.dmg`,
and both it and `merge_cryptex.sh` were destroyed in the 2026-09-02 `/tmp`
wipe. **The image that survived into `~/dvm-artifacts/build/rootfs.dmg` is the
pre-merge one**, so every boot since has been running without the cryptex.

The trust cache is already correct: `merged_sysvol_cryptex_tc.bin` is the union
of the system-volume and cryptex caches (3,802 + 130 entries) and covers all 49
signed Mach-Os in the cryptex payload. Only the file copy is missing.

## Why it becomes a reboot

`/System/Library/LaunchDaemons/com.apple.SpringBoard.plist`:

```
"_PanicOnCrash" => { "InternalOnly" => true
                     "PanicOnConsecutiveCrash" => true
                     "PanicOnCrashDeadline" => 180 }
"ThrottleInterval" => 5
"KeepAlive" => true
"UserName" => "mobile"
"POSIXSpawnType" => "App"
"_Conclave" => "com.apple.springboard.conclave"
```

`KeepAlive` respawns it, `ThrottleInterval` spaces the respawns 5 s apart, and
after enough consecutive crashes launchd gives up. `/sbin/launchd` carries the
matching strings — `ConsecutiveCrashCount`, `PanicOnConsecutiveCrash`,
`PanicOnNonZeroExit`, `Enabling panic-on-crash due to consecutive crashes`,
`panic on consecutive crashes (%zd)`, `Panicking in 3 seconds.` and
`critical process crashes: %s`. We get the *reboot* branch rather than the
*panic* branch because `_PanicOnCrash key: InternalOnly not enabled in the
current environment` — this build is not detected as internal.

Measured window, probe `SEPDCP`:

```
16:05:22.658  (boot) <Notice>: Early boot complete. Continuing system boot.
   ... 19.6 s, nothing at all on the console ...
16:05:42.269  <Critical>: rebooting due to critical process crashes: SpringBoard
```

19.6 s / 5 s throttle = four or five launch-and-die cycles. The
`panic: Halt/Restart Timed Out @IOPlatformExpert.cpp:900` that follows is the
usual consequence of a guest reboot on this machine, not a cause.

## What was ruled out, with the boot that ruled it out

| Hypothesis | Probe | Result |
|---|---|---|
| SpringBoard dies for want of a display | `SEPDCP`: system volume + `-enable dcp` + `DARWIN_DCP_IOMFB=4`, 11/11 AFK endpoints started, IOMFB level 4 active | **crashes identically.** The display stack makes no difference |
| ...so is the display stack even up? | same probe's stderr | `dcp: advertising endpoint 0x37 (IOMFB link), level 4`, `iomfb: PROBE override: A401 returns 1 byte(s) 0x01`, 11 × `afk(DCP): ep 0x.. started (rings at dva …)` |
| SpringBoard crashes on the ANS root path too | `SBBASE`: `-enable ans`, root `disk1s1`, 0 panics, `Early boot complete` | **no SpringBoard message at all** in the following ~600 s |
| ...because of the display? | `SBRDCP`: ramdisk root + `-enable dcp` + IOMFB 4, no SEP | also no SpringBoard message |
| dyld / codesigning / spawn failure | dyld launch record in RAM (above) | ruled out: the process reaches `main()` |

## `-enable sep` is required to see this at all

**SpringBoard only reaches the crash when `-enable sep` is on.** Without the SEP
node, `Early boot complete` is reached and then nothing happens — no crash, no
reboot, for the rest of the run. That matches
`docs/re/userspace-boot-state.md` §"The spawn blocker": `posix_spawn` of an
`.app` bundle blocks in AMFI's call into `AppleCredentialManager`, which waits
on a SEP endpoint that does not exist. With `-enable sep` the wait is answered,
the spawn completes, and SpringBoard runs far enough to hit the missing dylib.

So the two-line summary of the current state is: **SEP unblocks the spawn, and
the missing cryptex kills the process.**

## A side finding: `-enable ans` plus `-enable dcp` panics SPTM

Not pursued, but reproducible and worth recording, because it blocks testing the
display stack on the storage path:

```
nx_mount:1157: disk0 initializing cache w/hash_size 16384 and cache size 55296
AppleA7IOPNub: withRegistryEntry, 47: allocated nub <ptr>
RTBuddy(DCP): start(<ptr>) - (Aug 13 2026@22:18:01)
panic(cpu 0 caller 0xfffffff02b358010): [SPTM] VIOLATION_FRAME_TYPE:
  refcounts_update_page_op(sptm_types.c:3347) - pt_fte(0xfffffff03a66e160),
  fte(0xfffffff03a644000), fte->type(XNU_KERNEL_RESTRICTED), old_ro_refcnt(1)
```

`-enable dcp` alone at the same 12 GB DRAM reaches the shell with 0 panics and
all 11 AFK endpoints (probe `DCP12`), so it is the *combination*, not the DCP
model and not the DRAM size. The DCP work is currently done on the ramdisk root
path, where the two never meet.

## How the exit reason was obtained

The reason is not on the serial console and never will be: launchd logs service
exits at a level the console writer filters out (`/sbin/launchd` compares the
message level against a per-job field at `+0x488`, `0x1000209b0`, which the job
plist's `Debug` key sets), and `/private/var` is an ephemeral tmpfs, so
ReportCrash's output never touches a disk the host can read.

But a tmpfs lives in guest RAM. So:

1. `tools/snap_at_marker.sh` waits for `Early boot complete` on the serial log,
   then freezes the guest 8 s and 16 s later — inside the 19.6 s crash window,
   before launchd's reboot frees everything. Guest time does not advance while
   the VM is paused, so the snapshots are free.
2. `tools/guest_memgrep.py` streams the 40 GiB of guest RAM out through the
   monitor with `pmemsave` (1 GiB/s measured). Two traps: `pmemsave`'s size
   argument is 32-bit, so chunks must be ≤ 4 GiB − 1, and the filename must be
   quoted or the monitor parses `/tmp/...` as a division and fails with
   `invalid char 't' in expression`.
3. XNU's `os_reason` payloads are kcdata blobs beginning with
   `KCDATA_BUFFER_BEGIN_OS_REASON` = `0x53A20900`. Scanning the dump for that
   little-endian magic found four; walking the items
   (`u32 type, u32 size, u64 flags, data[]`) yields
   `EXIT_REASON_USER_DESC` (`0x1002`) and the process name.

Searching for the on-disk `.ips` JSON finds nothing — ReportCrash's templates
are resident, but under TCG it never finishes rendering a report inside the
window. The kernel's `os_reason` is the durable copy, and it is enough.

## What to do about it

Rebuild the root image with the cryptex merged, per
`docs/re/userspace-boot-state.md` and `docs/re/rootfs-assembly.md`: copy the
2,105 missing loose files from `094-13150-145.dmg` at root-relative paths,
replacing the 32 dangling graft symlinks with real directories. `~127 MB`; the
5.3 GB of dyld cache inside the cryptex is byte-identical to the one already
staged and does not need copying. No trust-cache change is needed.

The single file `usr/lib/libobjc-trampolines.dylib` (68,176 bytes) is what
SpringBoard is blocked on, so copying just that one file is a legitimate cheap
first probe before redoing the full merge - it should move the crash to the
next missing thing rather than fix the boot outright, and whatever that next
thing is will name itself through the same `OS_REASON` channel.

One trap for whoever does the copy: **the cryptex's files are APFS-compressed**,
so 7-Zip reads the directory but extracts zero bytes. `libobjc-trampolines.dylib`
carries

```
com.apple.decmpfs = 66 70 6d 63  0e 00 00 00  50 0a 01 00 00 00 00 00
                    'f  p  m  c'  type 14      0x00010a50 = 68176 bytes
```

i.e. decmpfs type 14, LZFSE held in the resource fork. A mounted-APFS `cp` or
`ditto` decompresses transparently and is the right way to do it; an offline
archive extractor is not.
