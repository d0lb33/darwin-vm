# seputil and launchd's `data-protection` boot task

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140. `/usr/libexec/seputil`
(204,336 bytes, `MH_EXECUTE` arm64e, `__TEXT` at `0x100000000`) and `/sbin/launchd`,
both copied out of the built system volume
(`/Users/jdolbe1/dvm-artifacts/build/rootfs.dmg`, attached read-only through
`tools/rootfs/safe_attach.sh`). Analysed 2026-09-02. Every address below is a
static file address in `seputil`; it is a PIE, so runtime addresses are these
plus the dyld slide.

## Why this matters

`launchd` runs a table of boot tasks embedded in its own `__TEXT` as a plist.
Two entries there matter to us:

```
<key>data-protection</key>
<dict>
  <key>RequireSuccess</key>   <true/>
  <key>Program</key>          <string>/usr/libexec/init_data_protection</string>
  <key>CSIdentityOverride</key><string>com.apple.seputil</string>
</dict>
```

`/usr/libexec/init_data_protection` is a symlink to `seputil`, and
`RequireSuccess` means a nonzero exit takes the kernel down:

```
panic(cpu 0 caller 0xfffffff02aff7ae8): seputil[4] exited
  -- no exit reason available -- (signal 0, exit status N )
```

So on the system-volume boot path, every one of `seputil`'s failure exits is a
kernel panic. This note is the map of which device tree properties decide which
exit it takes. **The whole task only runs at all when `-enable sep` keeps
`/arm-io/sep`**; with SEP disabled `dt_fixup.py` deletes the node and seputil
prints "No SEP present on this device" and exits 0.

## The gate chain, in the order seputil walks it

`get_chosen_property(name)` below is `sym.func.100008f80`: it does
`IORegistryEntryFromPath(kIOMainPortDefault, "IODeviceTree:/chosen")`
(cstring `0x10001b0db`), then `IORegistryEntrySearchCFProperty(..., CFSTR(name),
...)`, requires a `CFData` of exactly 4 bytes (`CFDataGetLength` compared with
4 at `0x100009020`) and returns "the u32 is nonzero".

| # | Address | Test | If it fails |
|---|---|---|---|
| 1 | `0x100002fec` | `get_chosen_property("sepfw-load-at-boot")` | `0x100003104`: `puts("Skipping SEP firmware load")` (cstring `0x10001bc82`), **exit 0** |
| 2 | `0x10000307c` | `sym.func.100004570`: `IOMasterPort` + `IOServiceMatching` on the SEP service, open a user client | prints and exits nonzero; this is the "Timeout trying to connect to the SEP … exit status 60" already documented in `dt_fixup.py` |
| 3 | `0x1000032cc` | `get_chosen_property("protected-data-access")` | jumps straight to step 5, skipping the gigalocker |
| 4 | `0x1000032d8` | `gigalocker_init(create = 0)` — see below | **exit 2** (ENOENT) when the gigalocker file is missing |
| 5 | `0x100003314` | `sym.func.10000439c(buf, 5)` → `lookupPathForPersonalizedData(5, buf, 0x400)` at `0x1000043bc`, i.e. open `/private/preboot/<boot-manifest-hash>/usr/standalone/firmware/sep-firmware.img4` | **exit 5** (`0x1000043ec: mov w0, 5`) |
| 6 | `0x10000336c` | `Img4DecodeInit`, then a type check (`0x100013278`, wants type 1), then a second personalized file of up to `0x40000` bytes (`0x1000033b4`, kind 8) | exits nonzero |

Steps 4 and 5 are the two that killed the system-volume boot, in that order.

## `gigalocker_init` (`sym.func.10000517c`)

Called from exactly two places in `main`: `0x100002ecc` with argument 1 and
`0x1000032d8` with argument 0. The argument means "you may create the file".
The `1` call site is gated on the flag byte at `0x100024c94`, which is the
`flag` field of the `--gigalocker-init` entry in the `getopt_long` table at
`0x100024180`. **The boot task passes no arguments, so the create path is
unreachable on boot.**

Body, in order:

1. `0x1000051d8` `sym.func.100014820(&flag)`. Its entire body is
   ```
   *flag = IORegistryEntryFromPath(kIOMainPortDefault,
             "IODeviceTree:/arm-io/sep/iop-sep-nub/xART") != 0
   ```
   (path cstring at `0x10001bd63`; `0x100014844` is the call, `0x1000099a4`
   is the outlined `cset`/`strb` tail that writes the byte).
2. `0x1000051e4` `tbz flag, 0` → `0x100005344`: print
   `"%s: xART is not supported on platform, skipping initialization"`
   (cstring `0x10001b326`, via `sym.func.100014598`) and **return 0**.
3. Otherwise `stat("/private/xarts")` (the string is `0x10001bdc0`; the
   alternative `/mnt7` at `0x10001bdba` is selected only when the create flag
   set the byte at `0x100025568`). Not a directory → os_log
   `"Gigalocker partition does not exist"` and return `0x14`.
4. Build `"%s/%s.gl"` into a `0x400` buffer (`0x100005224`) and `stat` it.
   * exists → print `"Gigalocker file (%s) exists"`; then `st_size` must be
     `>= 0x600000` (6 MiB, `0x1000052f0`) or return `0x11`.
   * missing → print
     `"Gigalocker file (%s) doesn't exist: %s"` and then branch on the create
     argument at `0x10000527c`. With 0 it returns `errno`, i.e. **2**. With 1
     it creates a 6 MiB file (`0x100005284: mov w1, 0x600000`).
5. `IOConnectCallMethod(conn, 44, NULL, 0, path, 0x400, ...)` at `0x100005324`
   hands the path to the kernel (`AppleSEPXART::gl_initialize(const char *)`
   is the matching kext symbol string), then prints
   `"Gigalocker initialization completed"` and returns 0.

**Consequence:** a writable `/private/xarts` — the obvious first guess, and the
one an ephemeral-tmpfs device tree entry would provide — cannot fix this.
The boot invocation never creates the file; it only looks for one that a
restore would have written. The fix has to be step 2.

## What else reads the xART marker node

`/arm-io/sep/iop-sep-nub/xART` is a bare marker: in the raw device tree
(`/tmp/dvm/dtree_raw`) it has only `name` and `AAPL,phandle = 0x65`, no `reg`,
no `compatible`. Two kernel consumers, both with an explicit no-xART path:

* AMFI, `LocalSigning.cpp`. `0xfffffff0091a0e74` is exactly the same
  `IORegistryEntry::fromPath(".../xART") != nullptr` predicate; its caller
  `0xfffffff0091a0ee4` branches at `0xfffffff0091a0efc` to an outlined logger
  (`0xfffffff0091c73d0`) that prints
  `"AMFI: calling %s without xART storage support" @LocalSigning.cpp:90`.
  It is a log-and-return, not a `panic`.
* `AppleLockdownMode`, `LDMShouldEnforceParity` — the path literal sits next to
  `-restore`, `AppleSEPManager` and `sep-endpoint,scrd` in that kext's cstring
  block.

`AppleSEPManager` does **not** look the node up. Its ART decision is the nub's
`self-power-gate` property (`docs/re/sep-protocol.md`), and its xART service
test is `hasService(kxART_service_name)`, i.e. driven by the endpoints the
coprocessor advertises over the mailbox — which `darwin_sep.c` still does
(`xars`, `xarm`).

## `sepfw-load-at-boot`

Step 5 wants a file that our image does not have and cannot have: the
`sep-firmware.img4` lives on the **Preboot** volume, and the IPSW system-volume
payload `094-13182-141.dmg` does not include it. Checked on the built image:
`/usr/standalone/firmware/` contains only `Rose`, `SLAM`,
`SmartIOFirmwareT7000.bin` and `nfrestore`, and `/private/preboot` is an empty
mount point. The `<boot-manifest-hash>` component comes out as 96 zeros because
`/chosen/boot-manifest-hash` is zero in our tree, but that is incidental — no
value of it names a file that exists.

Clearing `/chosen/sepfw-load-at-boot` takes branch 1 instead. It is a correct
description of this machine rather than a dodge: `darwin_sep.c`'s ROM accepts
the loader's zero-filled `SEPFW` region as its IMG4 and reports sepOS alive
before launchd even starts, so a second firmware load has nothing to do.

Two other readers of the property, both checked before changing it:

* `AppleSEPManager` `0xfffffff0095a0500`, immediately after
  `protected-data-access` at `0xfffffff0095a04ec`. Both feed the xART fetch
  path in the same function that logs `"%s: Fetched %s-xART with CRC: 0x%x"`
  (`0xfffffff00771232a`) and computes a CRC-16 over the blob at
  `0xfffffff0095a0490`. It is not the firmware fetch, which is
  `copyProperty("sepfw-loaded")` at `0x5a33a0` (`docs/re/sep-protocol.md`).
* `AppleSEPCredentialManager::isSEPAvailable` (`0xfffffff009524be4`), a cached
  `get_chosen_property("sepfw-load-at-boot")`. Its three callers
  (`0xfffffff0094eac8c`, `0xfffffff009510a70`, `0xfffffff009512afc`) record it
  as an analytics field and use it to short-circuit two SEP queries.

The measurement, not the reasoning, is what settled it: with the property
cleared the SEP still completes its whole boot conversation and ACM still
sends SCRD commands. From `GLNOFW2.stderr.log` (that run still advertised
`sks`, hence five endpoints; the default is four now):

```
sep(SEP): ROM: status queried -> 1
sep(SEP): ROM: TZ0 accepted (param 0x00), status -> 2
sep(SEP): ROM: IMG4 accepted (firmware page 0x1000000c, param 0x20); sepOS "running"
sep(SEP): TXM secure channel page at dva 0x10000004000 published
sep(SEP): reporting sepOS alive and advertising 5 endpoints
sep(SEP): scrd request tag 4, 36 byte body: no handler
```

## Result

Both device tree changes live in `dt_fixup.py`'s `fixup_sep()`, so they only
apply with `-enable sep`. On the system-volume path
(`rootdev=md0 ignition_level=1 launchd_unsecure_cache=1 …`, 40 GB, the
`rootfs.dmg` system volume as the ramdisk):

| probe | tree / model | serial lines | first panic |
|---|---|---|---|
| `GLBASE` | before | 534 | `seputil[4] exited … exit status 2` (gigalocker), line 374 |
| `GLNOXART` | xART node removed | 511 | `seputil[4] exited … exit status 5` (sep-firmware.img4), line 351 |
| `GLNOFW2` | + `sepfw-load-at-boot = 0` | 16,921 | `AppleSEPManager panic for "AppleSEPKeyStore": sks request timeout`, line 16,720 |
| `SEPFINAL` | + `darwin_sep.c` stops advertising `sks` | 42,020 | **none** |

The third boot gets through `data-protection`, `finish-obliteration`,
`detect-installed-roots`, `select-boot-mode`, `commit-boot-mode`,
`restore-datapartition` and into `mount-phase-2`, where it is well into the
`/private/var` rebuild (`Creating /.b/8//staged_system_apps/…`) when the SEP
keystore timeout fires. The fourth finishes `mount-phase-2` and runs on to
launchd's `keybag` boot task at guest time 00:04:25, then to
`MobileAssetEarlyBootTask`, with zero panics in 700 s.

The restore-ramdisk regression (`-enable sep -dram 8G`, `--secs 120`, probe
`SEPREG2`) is unchanged where it matters and better where it does not: 0
panics, `reached shell: yes`, `AppleSEPManager::start: control endpoints
created` — and 0 `sks timeout strike` lines, where the same probe before this
change reached strike 18, two short of the panic.

### With `-skip-keybag` on top: `Early boot complete`, then SpringBoard

Probe `SEPKB` is the same tree plus `-skip-keybag`, which puts `keybagd --init`
and `usermanagerd` into `DIAGNOSTICS MODE ENABLED, SKIP INIT` instead of
blocking on a keystore we do not answer. 43,445 serial lines:

```
com.apple.xpc.launchd|... (boot) <Notice>: Early boot complete. Continuing system boot.
com.apple.xpc.launchd|... <Critical>: rebooting due to critical process crashes: SpringBoard
com.apple.xpc.launchd|... (shutdown) <Notice>: shutdown UNINITIALIZED -> COMMITTED
panic(cpu 0 caller 0xfffffff02b347a14): Halt/Restart Timed Out @IOPlatformExpert.cpp:900
```

So `Early boot complete` is reached and SpringBoard is actually launched. It
crashes, launchd asks for a reboot, and the reboot cannot complete because this
machine has no reset path — **every launchd-initiated reboot will end in that
`IOPlatformExpert.cpp:900` panic** until one exists. Treat `Halt/Restart Timed
Out` as "the guest asked to reboot", not as a fault of its own; the reason is
always the `<Critical>: rebooting due to ...` line above it.

The one AMFI complaint immediately before the crash is

```
AMFI: '.../CoreTime.framework/TimeSources/PeerTimeSyncPlugin.bundle/PeerTimeSyncPlugin' is adhoc signed.
AMFI: ...: unsuitable CT policy 0 for this platform/device, rejecting signature.
AMFI: code signature validation failed.
```

which is a plugin load, not SpringBoard itself; the SpringBoard crash reason is
not on the serial console and needs a different tool to see.

### Reading these panics

Every panic in this kernelcache is followed by a *nested* panic whose register
dump is always the same:

```
Nested panic string:
  pc: 0xfffffff02ac6e104  cpsr: 0x204033c8  esr: 0x0000000096000045/46  far: 0x00000000000000b1
<end nested panic string>
```

It is the panic *printer* faulting, and it appears identically in runs whose
real first panic is `seputil[4] exited … exit status 2`, `… status 5`,
`AppleSEPManager panic for "AppleSEPKeyStore"` and `Halt/Restart Timed Out`.
Decoding `far = 0xb1` as a fresh NULL-pointer bug is a trap: read the
`panic(cpu ...)` line above `Nested panic detected`, not the register dump
inside `Nested panic string:`.

## The next gate: `sks`

`AppleSEPKeyStore` sends one IPC request on endpoint 18 and re-sends it every
~5 s, logging `"AppleSEPKeyStore":pid:0,:3466: sks timeout strike N`
(`0xfffffff00954c118`). At `0xfffffff00954c0b4` the counter is compared with
`0x14`; on the twentieth strike it branches to `0xfffffff00954c75c`, and
`AppleSEPManager` panics:

```
panic(cpu 0 caller 0xfffffff0295a6e4c): AppleSEPManager panic for
  "AppleSEPKeyStore": sks request timeout
Firmware type: UNKNOWN SEPOS   SEP state: 8   PM state: 2
```

The request itself is captured in `docs/re/sep-protocol.md` ("sks: the keystore
IPC, as far as it was captured"): frame `{18, tag 77, byte2 1, 0, data
0x005c0000}` with a 92-byte `AppleKeyStore` `ipc.c` header in the endpoint's
OOL in-buffer. The reply has to carry a header of its own — the kext logs
`negotiated to ipc header theirs:v%llu, ours:v%u -> negotiated:v%llu` at
`0xfffffff00954cd10`, and the version arithmetic just above it
(`0xfffffff00954ccdc: cmp x10, 2; csel x11, x10, x20, lo`) is
`negotiated = theirs < 2 ? theirs : ours`.

Until that IPC is modelled, `darwin_sep.c` does not advertise the `sks`
endpoint at all: an endpoint that is announced and then never answers is a
hard panic, while one that is never announced is a service that never binds.
`DARWIN_SEP_EPS=cntl,scrd,xars,sks,xarm` puts it back for whoever implements
the reply.
