# keybag boot task (no-SEP hang)

Source: iOS 27.0 (24A5430a), iPhone17,3 / t8140, kernelcache `firmware/bootkc`,
system volume `/Volumes/RaveSeed24A5430a.D47DeveloperOS` (mounted read-only),
extracted 2026-09-02.

## Summary

The `keybag` launchd boot task runs `/usr/libexec/keybagd --init` synchronously
and does not return until that process exits; it has no `RequireSuccess`, only
`AllowCrash`, so a crash would be tolerated but a **hang is not** — nothing
after it can ever run. `keybagd --init` finds no on-disk `systembag.kb`
(expected — this is a fresh image), logs the `-7` you see and treats that as
"no bag yet, proceed", then unconditionally calls into
`AppleKeyStore.framework`'s `aks_get_system()`. That call opens an IOKit
connection to a service that, on this kernelcache, is provided **only** by
`com.apple.driver.AppleSEPKeyStore` — there is no separate non-SEP
`AppleKeyStore` kext at all. The kernel service registers unconditionally at
early boot (it matches `IOResources`/`"IOKit"`, not any SEP device-tree node),
so the userspace open succeeds, but the actual keybag operation depends on
`AppleSEPManager`'s SEP mailbox, which never completes because there is no
SEP. Live inspection of the stuck VM shows the guest CPU genuinely idle, not
looping — consistent with a kernel thread parked in an uninterruptible,
no-timeout wait. A QEMU model fix for this would need to satisfy or fail-fast
that kernel-side wait; the cheapest thing to try first is a device-tree flag
that makes `keybagd --init` skip all of this and exit(0) — see Options below.

## What prints `MKB_INIT` and what `-7` means

**Binary:** `/usr/libexec/keybagd` (Mach-O arm64e, 317536 bytes,
md5 `360719f088947267daaa1375fe647721`). Not part of the dyld shared cache;
its own `__TEXT.__cstring`. `MobileKeyBag`/`libAKS` do **not** appear anywhere
in this binary or in the dyld cache under that name for this iOS version —
the framework that matters is `AppleKeyStore.framework`
(`/System/Library/PrivateFrameworks/AppleKeyStore.framework/AppleKeyStore`,
version 2383.2.1.0.0 in the dyld cache), which `keybagd` links
(`LC_LOAD_DYLIB … AppleKeyStore.framework/AppleKeyStore`, `otool -L`).

**Boot task definition** (not a `LaunchDaemons/*.plist` — it's an embedded
plist compiled into `launchd` itself, `sbin/launchd` `__TEXT.__config`
section, file offset 486549, size 0x2a71):

```
"keybag" => {
  "AllowCrash" => true
  "PerformAfterUserspaceReboot" => true
  "Program" => "/usr/libexec/keybagd"
  "ProgramArguments" => [ "keybagd", "--init" ]
}
```

No `RequireSuccess`/`RequireRun` — launchd just waits for the process to
**exit**, tolerating a nonzero/crash exit. It never signals "success" until
`keybagd --init` returns from `main()`, and `main()`'s `--init` path
(`main`, `sbin/launchd`… no — this part is in `keybagd`, address
`0x1000126a0`) ends unconditionally in `exit(0)` at `0x100012b78`, *if it gets
that far*. It never does in our boot.

**Call trace inside `keybagd --init`** (`main` dispatches on `argv[1]=="--init"`
at `0x1000126f0`/`0x100012704` → `0x100012a38`):

| Address | What | Evidence |
|---|---|---|
| `0x100012a38` | `--init` entry | `main`, arg dispatch on `strcmp(argv[1],"--init")` |
| `0x100012a48` | checks `IODeviceTree:/product` property `boot-ios-diagnostics`; if present, prints `"****** DIAGNOSTICS MODE ENABLED, SKIP INIT ****\n"` and `exit(0)` at `0x100012b78` | see "Skip-init gates" below |
| `0x100012a78` | else checks `os_variant_uses_ephemeral_storage("com.apple.mobile.keybagd")`; if true, prints `"****** DEVICE HAS EPHEMERAL DATA VOLUME, SKIP INIT ****\n"` and `exit(0)` | see below |
| `0x100012a9c` | else: opens `/var/log/keybagd_init.log`, `fwrite`s `"****** IN MKB_INIT ****\n"` (str `0x10002d492`, len 0x18) — **this is literally what prints on serial**, not an os_log tag | `main`, direct `fwrite(stream, ...)` calls, confirmed by matching byte lengths (`w1=0x18`) against the string |
| `0x100012b44` | `bl sym.func.100012b7c` — load-or-create the system keybag | |
| `0x100012b48` | `bl sym.func.100012e2c` — recreate `/private/var/keybags/backup` if missing (unconditional, always reached if `100012b7c` returns) | |
| `0x100012b5c` | `fwrite("****** DONE MKB_INIT ****\n")`, `exit(0)` | **never observed in our serial log** |

`sym.func.100012b7c` (the actual init logic) starts by calling
`KBLoadSystemKeyBag` (`sym.func.100003d50`) at its very first instruction
(`0x100012ba0`):

1. `KBLoadSystemKeyBag` builds `"/private/var/" + "keybags"` (`%s%s` at
   `0x100003d8c`) and calls `KBLoadLoadKeyBagFile(dir, "systembag", &out)`
   (`sym.func.100003e44`) at `0x100003dac`.
2. `KBLoadLoadKeyBagFile` builds `"%s/%s.kb"` (str `0x10002692f`) →
   `.../keybags/systembag.kb`, and calls a CFPropertyList-from-file loader
   (`sym.func.100002778`) on it at `0x100003e88`. That loader is what prints
   **`"Could not open %s: %s"`** (str `0x100028d10`) on `ENOENT` — this string
   is never directly `adrp`/`add`-referenced anywhere in the binary (checked
   with a Capstone ADRP+ADD scan over the whole `__text`, see
   `/private/tmp/.../scratchpad/kb/findref2.py`), i.e. it's used only inside
   that one helper, consistent with being its single format string.
3. On failure it also checks `IODeviceTree:/defaults` property
   `no_effaceable_storage` (`sym.func.10001c740`, see below) at `0x100003ec8`;
   absent in our tree, so it proceeds to also try
   `.../systembag.kb.writing` (`%s.writing`, str `0x100026a8b`) via the same
   loader at `0x100003f3c`. That also fails (`ENOENT` — the log line at
   `KB.clean.log:801`).
4. Both attempts failed → `mov w20, -7` **(hardcoded literal, not an errno)**
   at `0x100003f88`, logs `"Unable to load keybag with No Crypto: %d"`
   (str `0x10002697e`) with `-7`, and returns `-7` to `KBLoadSystemKeyBag`.
5. `KBLoadSystemKeyBag` treats any nonzero return from step 4 as "no bag on
   disk" and returns `NULL` (`x19=0`) to its own caller — **it does not
   propagate `-7` as a hard error.** (`0x100003db0`: `cbz w0, ...` not taken
   since `w0=-7`≠0, falls through to `mov x19,0` / return 0.)

So **`-7` is not an errno and is not fatal by itself** — it is a fixed
sentinel meaning "neither `systembag.kb` nor `systembag.kb.writing` could be
opened", and the caller's response to it is simply "there is no bag yet,
carry on and ask the kernel." This directly answers the brief's framing: the
message reads like an error, but the code treats it as the expected first-boot
state.

## What happens next — and where we lose the trail

Back in `sym.func.100012b7c` at `0x100012ba8`: since `KBLoadSystemKeyBag`
returned `NULL`, execution takes the "`x19==0`" branch to `0x100012c5c`:

```
0x100012c5c  add x1, sp, 0xc
0x100012c60  bl  aks_get_system        ; AppleKeyStore.framework import
0x100012c64  cmp w0, w22               ; w22 = 0xe00002f0
0x100012c68  b.ne 0x100012d20
```

**This is the last instruction we can prove executes.** No further log line
(`"No system keybag loaded."`, `"Uh Oh…"`, `"Setup allow list:"`,
`"aks_setupallowlist_fs completed…"`, or `"****** DONE MKB_INIT ****"` — all
unconditionally reached on *every* branch out of `sym.func.100012b7c`,
confirmed by re-reading the full function body from `0x100012b7c` through
`0x100012e28`) ever appears in `KB.clean.log`, `SLIM.clean.log`, or in a fresh
reproduction I ran live tonight (see "Live confirmation" below). Since these
prints happen through the *same* log wrapper (`sym.func.1000120bc`) as the
four lines that *did* print, their total absence means control never returns
from `aks_get_system()` (or from the connection setup it does first).

`0xe00002f0` is the value `aks_get_system` is expected to return in the "no
bag currently loaded" case — I could not find a named AKS/`kAKSReturn*`
symbol for it anywhere in the extracted `AppleKeyStore.framework` or
`libsystem_darwin.dylib`; **treat this constant as unverified/unnamed.**

### `aks_get_system` / the kernel service it needs

Extracted `AppleKeyStore.framework` (dyld cache image #1191, version
`2383.2.1.0.0`; extracted to
`/private/tmp/.../scratchpad/kb/extracted/AppleKeyStore`):

- `_aks_get_system` (`0x23e39c404` in the extracted, re-based image) calls
  `_get_aks_client_connection` (`0x23e3415cc`) first.
- `_get_aks_client_connection` is a `dispatch_once`-guarded lookup
  (`sym._get_aks_client_connection.onceToken`) whose block
  (`___get_aks_client_connection_block_invoke`, `0x100012b7c`... i.e.
  `0x23e341614`) calls `__copy_aks_client_connection("IOService:/IOResources/AppleKeyStore", "AppleKeyStore")`
  (`0x23e342620`) — the literal path string is at `0x23e3bcbf0`, the class
  name at `0x23e3baaa7`.
- `__copy_aks_client_connection` itself is a bounded sequence: one lookup
  call, one open call, and error-path logging (`"failed to open connection to
  %s: %d\n"` etc.) — **no retry loop, no `IOServiceAddMatchingNotification`,
  no `dispatch_semaphore_wait` visible in this function.** Whatever actually
  blocks is on the other side of the syscall boundary (the final `bl` at
  `0x23e39c4b8`/similar goes through an auth-stub trampoline into a
  cross-image call I could not symbolicate from the single-dylib extraction —
  most likely `IOConnectCallMethod` or an internal AKS RPC wrapper around it).

**Kernel side** — `firmware/bootkc`, `__PRELINK_INFO` section (file offset
`70615040`, size `0x280000`, parsed as a plist):

```
CFBundleIdentifier: com.apple.driver.AppleSEPKeyStore
IOKitPersonalities:
  AppleKeyStore:
    IOClass: AppleKeyStore
    IOProviderClass: IOResources
    IOResourceMatch: IOKit
    IOUserClientClass: AppleKeyStoreUserClient
  AppleKeyStoreTest: (same shape)
OSBundleLibraries includes: com.apple.driver.AppleSEPManager, com.apple.driver.AppleARMPlatform, …
OSBundleRequired: Root
```

Two load-bearing facts here, both directly read from this plist, not
inferred:

1. **There is no separate `com.apple.driver.AppleKeyStore` kext in this
   kernelcache at all** — I enumerated all 324 `_PrelinkInfoDictionary`
   entries and grepped for `keystore`; `com.apple.driver.AppleSEPKeyStore` is
   the only hit. The old pre-SEP software-only keystore driver simply isn't
   shipped on this build/device.
2. **`IOProviderClass: IOResources` + `IOResourceMatch: IOKit`** is the
   standard IOKit idiom for "match as soon as IOKit's own core resource is
   published" — i.e. this personality does **not** require any SEP hardware
   or SEP device-tree node to match. (This interpretation of the idiom is
   general IOKit knowledge, not something I traced through
   `IOResources::registerResourceService` source in this kernelcache — flagged
   as inferred, not line-cited.) That means the userspace
   `IORegistryEntryFromPath("IOService:/IOResources/AppleKeyStore")` lookup
   *does* succeed even with the SEP device-tree node entirely removed, and
   the `AppleKeyStoreUserClient` connection opens. `OSBundleLibraries`
   listing `com.apple.driver.AppleSEPManager` confirms the actual crypto
   operations inside that driver route through the SEP mailbox layer, which
   is exactly what has no hardware behind it in our boot.

Put together: the open succeeds, but the specific method call
(`aks_get_system`, and everything downstream of it) needs `AppleSEPManager`'s
mailbox to complete a round trip with a coprocessor that does not exist, and
that kernel-side wait appears to have no timeout — unlike `seputil`'s
userspace `waitForSEPEndpoint`, which the coordinator's SEP-present
experiment showed *does* time out at 60s and panics launchd. Nothing analogous
fires here; the process just never returns.

### Live confirmation the hang is a genuine block, not a spin

I reproduced the exact scenario tonight (same `-dtree /tmp/dvm/dt_norm.bin
-tc /tmp/dvm/tc/merged_sysvol_cryptex_ramdisk_tc.bin -ramdisk
/tmp/dvm/build/rootfs_slim.dmg` command line as `KB.clean.log`, `-m 40G`,
monitor on `/tmp/dvm/KBLIVE.sock`), let it reach the identical stall
(byte-identical four log lines, confirmed), then used `tools/hmp.py` to
`stop`/`info registers`/`cont` four times, 2 seconds apart:

```
PC=fffffff02ab218bc  (identical, all four samples)
PSTATE=... ---- EL2t ...
```

The sole vCPU is parked at the **same** instruction every time, in `EL2t`
(SPTM), which is where a `WFI` trap from the guest kernel lands. A busy-spin
in userland or a retry loop would move the PC between samples (or at least
show varying EL0/EL1 activity); a stable EL2 WFI-trap PC across 2-second
gaps means XNU's scheduler has **nothing runnable at all** — consistent with
the `keybagd --init` thread being parked in an uninterruptible kernel wait.
I could not get a symbolized kernel backtrace (no gdbstub/kernel debugger
wired up in this session) to name the exact function or lock; that is the one
genuinely open piece of this trace.

## Skip-init gates (the two branches that avoid all of this)

Both are read directly from the disassembly, not inferred:

**1. `IODeviceTree:/product` property `boot-ios-diagnostics`, presence-only.**
`sym.func.10001c740(path, key)` (`0x10001c740`) does exactly:
`IORegistryEntryFromPath(kIOMainPortDefault, "IODeviceTree" + path)` →
`IORegistryEntryCreateCFProperty(entry, key, …)`; returns `1` if the property
resolves to anything at all (the CF value's contents are never inspected,
just `CFRelease`d), `0` if absent. `main` calls it at `0x100012a48` as
`sym.func.10001c740(":/product", "boot-ios-diagnostics")` and only skips init
if the return is exactly `1`.

Our current device tree already has a `/product` node with dozens of
properties (`ptp-large-files`, `sub-product-type`, etc. — confirmed by
parsing `/tmp/dvm/dt_norm.bin` with the repo's own `dt_fixup.py` decoder), so
adding one more `u32:0x1` property to it is a small, well-understood change,
same shape as the DCP `pre-loaded`/`no-firmware-service` presence-only flags
already in use. **This is the cheapest thing I found that unblocks the boot
task** — but see the risk note below before committing to it.

Risk: `boot-ios-diagnostics` is not a keybagd-private key. Searching the raw
dyld cache split file for the string turns up a string table
(`dyld_shared_cache_arm64e.11`, offset `121286093`) that groups it with
`flash-capability`, `research-enabled`, `osenvironment`, `diagnostics`,
`effective-security-mode-sep`, `sub-product` — i.e. it reads like a shared
MobileGestalt/AppleInternal key namespace, not something private to
`keybagd`. **I have not checked whether SpringBoard or anything else in the
normal boot path also queries this key and changes behavior under it** (e.g.
refuses to launch the ordinary UI, or routes into an actual diagnostics
harness). This needs a `tools/probe.sh` run past the keybag task to confirm
nothing downstream regresses before treating it as the answer.

**2. `os_variant_uses_ephemeral_storage("com.apple.mobile.keybagd")`,
checked second, only if (1) is absent.** Traced into
`/usr/lib/system/libsystem_darwin.dylib` (dyld cache image #1113, version
`1786.0.3.0.0`; the import comes `[from libSystem]` per `dyld_info -imports`,
**not** from `MobileSystemServices.framework` despite that being the only
weak-linked framework in `keybagd` — checked with `xcrun dyld_info -imports`
directly, do not trust the weak-link list alone here).
`_os_variant_uses_ephemeral_storage` (`0x22ffbb7b4` in the extracted dylib)
reads a **process-cached static** (`sym._is_ephemeral`,
`0x26fd74000+0x714`) populated once via a `dispatch_once` block that calls
`__check_internal_release_type` — this is the standard `os_variant_*`
AMFI/CSR-backed "internal release type" classification, not (as far as I
traced) anything sourced from a device-tree property. I did **not** fully
unwind `__check_internal_release_type.cold.1`'s `dispatch_once` target to
find its actual data source (time-boxed out of this pass) — flagged as
**unverified / likely not device-tree-controllable**, do not spend effort
here without confirming first what actually feeds `_is_ephemeral`.

Note: `/product` also already carries `ephemeral-data-mode: u32:0x0` in our
current tree (parsed from `/tmp/dvm/dt_norm.bin`). The name is suggestive,
but I found no code path in `keybagd`, `AppleKeyStore.framework`, or
`libsystem_darwin.dylib` that reads it — the `os_variant_uses_ephemeral_storage`
call above reads a completely different, non-device-tree source. Don't
assume `ephemeral-data-mode` is what gates this without tracing whoever
*does* consume it (unidentified in this pass).

## Answers to the five original questions

1. **Who prints `MKB_INIT` and what is `-7`?** `/usr/libexec/keybagd
   --init`'s `main()` (`0x1000126a0`–`0x100012b78`); the four lines are raw
   `fwrite`s to `/var/log/keybagd_init.log` + stdout, not an os_log
   subsystem. `-7` is a hardcoded literal in `KBLoadLoadKeyBagFile`
   (`sym.func.100003e44` @ `0x100003f88`) meaning "neither `systembag.kb` nor
   `systembag.kb.writing` could be opened" — not an errno, not fatal, and
   explicitly swallowed by the caller (`KBLoadSystemKeyBag` returns `NULL`,
   not the error).

2. **What creates `systembag.kb` normally, and is there a "make one" path?**
   Yes, conceptually — `KBLoadSystemKeyBag` (via `sym.func.100012b7c`) treats
   "no file found" as "not yet created" and falls into a *create* path
   (`aks_get_system` → branch → `aks_load_bag`/`aks_set_system`), which is
   exactly the branch we've traced into a hang. So the "make one" path
   **exists and is the one being taken** — it just requires the AppleKeyStore
   kernel service (`AppleSEPKeyStore.kext`, the only keystore driver in this
   kernelcache) to complete a SEP-mailbox round trip, and that is what never
   returns. There is no separate, still-shipping "software keybag, no SEP"
   creation path in this binary as far as I traced (see below).

3. **Is "No Crypto" a real supported mode, and what selects it?** It is a
   real internal label (`"Unable to load keybag with %s: %d"` with `"No
   Crypto"` vs `"Crypto"` as the two variants — `KBLoadLoadKeyBagFile` picks
   between them based on whether the loaded plist dictionary contains a
   `KeybagxART` key), but it only describes **how the on-disk file was
   parsed** (plaintext plist vs. one carrying SEP-wrapped `xART` material). It
   does **not** mean "the runtime avoids AppleKeyStore/SEP" — both variants
   still funnel into the same `aks_*` kernel calls afterward. I found no
   boot-arg, `/AppleInternal` marker, device-tree property, or entitlement
   that makes the *kernel-call* path itself SEP-free. The two things that
   genuinely skip the kernel calls entirely are the two "skip-init gates"
   above (`boot-ios-diagnostics` presence, or `os_variant_uses_ephemeral_storage`
   returning true) — neither is "No Crypto"; both bypass `MKB_INIT` before it
   even starts.

4. **Can we pre-create a `systembag.kb`?** Not usefully. Even on the
   "bag found on disk" success branch, `sym.func.100012b7c` still calls
   `aks_get_system` (`0x100012c08`/`0x100012c60`) to reconcile kernel state,
   and if the parsed dictionary carries a `KeybagxART` entry it goes on to
   call `aks_load_bag` (`0x100012cb8`) with that data — so a synthesized file
   does not remove the AppleKeyStore/SEP dependency, it just changes which
   `aks_*` call is reached first. Separately, the `KeybagxART` payload is
   exactly the kind of SEP-wrapped material the brief told me to rule out on
   sight — I did not find its format documented anywhere in this binary
   (unsurprising; that's SEP-side). **Drop this option** — both because it
   doesn't route around the block and because the payload it would need is
   SEP-produced.

5. **Why does it hang rather than fail?** Because nothing in this trace has
   a timeout. `seputil`'s hang (per the coordinator's SEP-present experiment)
   *does* fail, loudly, after 60s (`waitForSEPEndpoint`, explicit deadline,
   panics launchd on expiry). Nothing analogous exists on this path: the
   `_get_aks_client_connection`/`__copy_aks_client_connection` userspace code
   is bounded and would return an error quickly if the IOKit lookup itself
   failed — but it doesn't fail, because `AppleKeyStore`'s IOKit personality
   matches unconditionally (`IOResources`/`IOKit`, no SEP node required). The
   block happens *after* the connection opens, inside the kernel method call,
   which is consistent with the live idle-CPU observation but which I could
   not further pin to an exact kext function without a kernel debugger.

## Ranked options ("cheapest thing that unblocks the boot")

1. **[Cheapest, verified code path, unverified side effects]** Set
   `boot-ios-diagnostics` (any value, e.g. `u32:0x1`) on the `/product`
   device-tree node. Verified: this exact property, presence-only, makes
   `keybagd --init` skip straight to `exit(0)` before touching
   `AppleKeyStore`/SEP at all. Unverified: whether this key is consumed
   elsewhere in the boot in a way that stops SpringBoard from being what
   actually launches — needs a `probe.sh` run past this point to check.
2. **[Needs more tracing before trusting it]**
   `os_variant_uses_ephemeral_storage()` returning true would also skip init
   entirely, but I could not confirm what actually feeds its cached
   `_is_ephemeral` value (traced as far as an AMFI/CSR-style
   `__check_internal_release_type` dispatch_once in `libsystem_darwin.dylib`,
   not further). Do not build on this without finishing that trace.
3. **[Real fix, more work]** Model enough of the SEP mailbox
   (`darwin_asc.c`'s territory, per `CLAUDE.md`) that `AppleSEPManager`
   either completes a trivial handshake or `AppleKeyStore`'s kernel method
   fails fast instead of blocking forever — mirrors how removing DCP's
   `quiesced` property unstuck `RTBuddy::start()`. This needs a kernel
   backtrace of the blocked thread first, which this pass didn't obtain.
4. **[Ruled out]** Ship a software-only `AppleKeyStore.kext` fallback — does
   not exist in this kernelcache at all (verified: absent from
   `_PrelinkInfoDictionary`); would mean adding a whole new kext, out of
   scope.
5. **[Ruled out]** Pre-create `systembag.kb` — does not skip the
   `aks_get_system`/`aks_load_bag` kernel calls, and the `KeybagxART` payload
   it would need is SEP-wrapped material we cannot produce.

## Open questions

- **Exact kernel function/lock the hang is parked in.** Not obtained — would
  need a symbolized kernel debugger (gdbstub) attached to the paused VM, or
  debug logging added to whatever `AppleSEPKeyStore`'s `externalMethod`
  dispatcher calls into `AppleSEPManager`, to name it precisely. The live PC
  I *did* get (`0xfffffff02ab218bc`, stable across 4 samples 2s apart, `EL2t`)
  only proves system-wide idle, not which EL1 thread/lock.
- **What `0xe00002f0` (the `w22` comparison constant in
  `sym.func.100012b7c`) actually is.** No named symbol found in either
  extracted framework; if it's `kAKSReturnNoSuchClass`-style "no bag loaded"
  it changes nothing about the trace, but I couldn't confirm it.
- **What else reads `boot-ios-diagnostics`.** The dyld-cache string-table
  grouping (`research-enabled`, `effective-security-mode-sep`, …) suggests
  more consumers than just `keybagd`; unidentified. Settles with a `probe.sh`
  run that gets past `keybag` and watches for anything behaving like a
  diagnostics/factory harness instead of normal `SpringBoard` boot.
- **What actually feeds `os_variant_uses_ephemeral_storage`'s cached
  `_is_ephemeral`.** Traced to a `dispatch_once`-guarded
  `__check_internal_release_type` call in `libsystem_darwin.dylib`
  (`0x22ffbad2c`/cold paths), not resolved further.
- **What the final `bl` inside `AppleKeyStore.framework`'s `_aks_get_system`
  (extracted-local `0x23e39c4b8`) actually calls.** The extracted
  single-dylib re-basing made cross-image auth-stub targets unresolvable with
  the tools used here (`ipsw dyld a2s`/`a2f` returned nonsense or "not in any
  dylib" for these addresses); opening the full multi-file DSC directly in a
  disassembler that understands its stub-resolution chain would settle it.
