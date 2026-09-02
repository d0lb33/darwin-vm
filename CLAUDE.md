# darwin-vm — project guide for agents

Fork of jprx/darwin-vm. Upstream boots iOS/macOS to a serial root shell in QEMU.
Our goal is real display output: get the iOS userspace display stack far enough
to draw a setup / lock / home screen, and keep it working across iOS versions.

`origin` is the fork (d0lb33), `upstream` is jprx. Same for the `qemu-sptm`
submodule. Never push to `upstream`.

## Layout

| Path | What |
|---|---|
| `qemu-sptm/hw/arm/darwin*.c` | our device models (machine, framebuffer, AIC, ASC/RTKit, DART, catch-all) |
| `qemu-sptm/include/xnu/darwin_*.h` | their headers |
| `dt_fixup.py` | device tree rewriter; `-enable <feature>` keeps nodes so XNU drivers bind |
| `tools/probe.sh` | boot headless, freeze, report progress / panic — the inner loop |
| `tools/hmp.py` | QEMU monitor client (registers, memory, screendump, sendkey) |
| `docs/re/` | reverse engineering notes, one file per topic, every claim carries an address |

## Build

```
cd qemu-sptm/build && make -j18
```

First configure on macOS 27 must disable Apple's ParavirtualizedGraphics, whose
API was obsoleted in that SDK:

```
../configure --target-list=aarch64-softmmu --disable-pvg
```

## Run

```
tools/probe.sh --secs 60 --tag mytest                 # baseline, expect: reached shell yes
tools/probe.sh --dtree /tmp/dvm/dt_dcp.bin --secs 100 --tag dcp --grep 'RTBuddy|panic\('
./run.sh --vnc :0                                     # interactive, with a screen
```

`probe.sh` prints serial progress, XNU panics, and decodes an SPTM panic
message out of guest memory. Device model tracing is opt-in per model:
`DARWIN_AIC_DEBUG=1`, `DARWIN_ASC_DEBUG=1`, `DARWIN_DART_DEBUG=1`,
`DARWIN_UNIMP_DEBUG=1`.

Boot-arg `io=0x1f` makes IOKit log every driver match and start, which is how you
see where a driver chain stalls.

## Ownership — do not edit outside your lane

Only the orchestrator session edits these. If your task needs a change here,
describe the change in your report; do not make it:

- `qemu-sptm/hw/arm/darwin.c` (machine wiring)
- `qemu-sptm/hw/arm/darwin_asc.c` and `darwin_aic.c` (shared by every coprocessor)
- `dt_fixup.py`
- `run.sh`, `CLAUDE.md`, `.claude/agents/*`

Everything else is fair game for the agent that owns the task.

Agents that write code work in their own git worktree so parallel work cannot
collide, and build in that worktree's own `qemu-sptm/build`. Report the branch
name; the orchestrator merges.

## House rules

- **Cite the evidence.** Every claim about hardware or firmware behaviour carries
  an address, an offset, a file, or a log line. "The DCP expects X" is worthless
  without "at `AppleDCP+0x1234`" or "Linux `apple-dart.c:118`".
- **No speculative device behaviour.** If you cannot show where a register
  semantic comes from, model it as a logged no-op and say so in a comment.
- **Verify before reporting done.** Run `tools/probe.sh` and paste the verdict.
  A build that compiles is not a result; a boot that gets further is.
- **Comment the why.** These models are read by people who do not have the
  firmware open. Write the register map in a header comment, with its source.
- Do not commit anything under `firmware/`, `ipsw_db/`, or `/tmp/dvm`.
- **Scripts go in the repo; only outputs go in `/tmp/dvm`.** `/tmp` gets wiped on
  restart, and on 2026-09-02 it took every build script with it — the images
  were rebuildable, the scripts were not. Anything you would mind losing belongs
  under `tools/`. Large binaries still stay out of git. See
  `tools/rootfs/README.md` for what survives a wipe and how to recover.
- **Do not rebuild `qemu-sptm/build/qemu-system-aarch64` while someone else is
  booting.** An agent that starts a boot while the binary is being relinked gets
  garbage results — this produced a convincing but entirely false "critical
  regression" report. Build in your own worktree's build directory.
- **Attach disk images only through `tools/rootfs/safe_attach.sh`.** Mounting a
  21 GB image full of iOS exposes ~500k files to Spotlight and fseventsd, and on
  2026-09-02 that plus mass create/delete churn kernel-panicked the host twice
  with `"Data ObjId overflow" @jobj.c:1152` in APFS. The wrapper forces
  `-nobrowse`, excludes the volume from Spotlight, and refuses concurrent
  attachments. Never blanket force-detach — other people's volumes are mounted.
  Details in `tools/rootfs/README.md`.

## What is already working

- Boot framebuffer: XNU's verbose console renders through QEMU (cocoa/SDL/VNC),
  and window keystrokes are bridged into the guest UART. `-fb WxH[@scale]`,
  `-fbmode text|graphics`.
- `darwin-aic`: AIC v2/v3 interrupt controller, geometry read from the device
  tree. XNU probes it, enables it, and registers vectors through it.
- `darwin-asc`: ASC mailbox + RTKit management protocol (hello, endpoint map,
  endpoint start, power states). Shared by DCP/ANS/SMC/AOP/SIO/MTP/GFX.
- `darwin-dart`: t8110 IOMMU with page table translation.
- `darwin-unimp`: catch-all that backs every `/arm-io` range so unmodelled MMIO
  reads as zero instead of faulting, and logs which device tree node owns the
  address.
- `dt_fixup.py -enable dcp` re-exposes the DCP/DART/display nodes and assigns
  `dart-id` (SPTM panics with "error -1 getting dart-id" otherwise).
- `darwin-afk`: the AFK ring transport on top of RTKit. All eleven DCP
  endpoints `0x20`–`0x2a` complete INIT / GETBUF / INIT_TX / INIT_RX / START,
  with their rings translated through the DART.
- **Real iOS userspace.** The actual system volume boots as the ramdisk: dyld
  maps the shared cache, launchd runs, hundreds of services start, backboardd
  reaches `running`. See `docs/re/userspace-boot-state.md`.
- **The normal boot path.** `rootdev=md0` plus a kernel patch gets iOS off its
  restore path onto the `local` boot spec, so `mount-phase-1`/`-2` run and
  `/private/var` is mounted writable as a tmpfs (`-ephemeral-data`).
- `darwin-sep`: enough SEP for `AppleSEPManager` to reach "control endpoints
  created" and answer `AppleCredentialManager`. That wait used to burn 890 of
  every 900 seconds; it is now zero. `-enable sep`.
- `darwin-ans`: the ANS/NVMe storage controller. **iOS roots off it** —
  `BSD root: disk1s1`, `mount-phase-2` completes, `Early boot complete`,
  0 panics. Reads and writes both proven. See **Storage**.
- A root shell inside the booted system volume, for poking at it live
  (`/tmp/dvm/build/rootfs_sh.dmg`, 102 tools from the restore ramdisk, uid 0).
  `tools/serial.py` drives it over a socket.

## Where the DCP bring-up stands

Two channels, and both are now talking. Everything below is behind an env
switch and **off by default**; the default `-enable dcp` boot reaches the shell
with 0 panics and 11/11 AFK endpoints.

### The control plane (endpoints 0x20-0x2a, AFK + EPIC) — working

All eleven endpoints complete the AFK handshake, and `DARWIN_DCP_EPIC=all`
announces the EPIC services XNU's sub-drivers match on, so real drivers bind:

```
afk(DCP): ep 0x20 started (rings at dva 0x10000000000)   ... through 0x2a
Registering: ../AppleH17PPlatformIO/dcp-sac-controller/DCPAVSACController
```

`DARWIN_DCP_REPLY=1` answers the standard-service commands that follow. Two
protocol facts, both derived from the guest rather than the M1-era references:

- The message header's byte 0 is a **sequence counter**, not flags.
- A command body's u32 at +4 is a **payload length inbound and a return code
  outbound**. Echoing it back made `DCPAVRemoteSACControllerProxy` — interface
  8, whose command carried `arg 0x50` — report `bootCompleteGated() error:
  ret = 0x50`. Not echoing it clears the error.

Answering at all is what makes the AP proceed: with no reply it sends one
`OPEN` and one command and stops; with one it opens every announced interface.

### The pixel path (endpoint 0x37, IOMFB) — AppleCLCD2 binds

The display driver at the top of the stack now starts and registers:

```
Registering: ../arm-io@10F00000/AppleH17PPlatformIO/disp0@0/AppleCLCD2
```

Endpoint `0x37` lives in `darwin_iomfb.c`. Its framing is **not** AFK's; it is
derived in `docs/re/iomfb-link.md` from `link_send_message` /
`link_handle_message`, masked by a literal `and x8, x2, 0xffffffffffff0000`:

```
[1:0] class   [7:6] subkind   [8] ack   [15:10] tag   [63:16] payload
```

Four levels, each a superset of the last, all default-off:

| `DARWIN_DCP_IOMFB` | what it does |
|---|---|
| `1` | advertise `0x37`; the stack starts, `IOMFB: AP DRIVER START!` |
| `2` | + the class-1 init-ack carrying the CRC in `[47:16]` |
| `3` | + answer the class-2 RPCs with status 0 and zeroed output |
| `4` | + the measured answers in `iomfb_level4[]` |

The gradient is the evidence, four boots differing only in that value, all
with 0 panics and a shell: no `0x37` gives no `AppleCLCD2` line at all;
handshake only stops at `AP DRIVER START!`; zeroed RPCs get
`AppleCLCD2::start` to run for 596 ms and never register; answering `A401`
with `01` registers it.

Class 2 is a **shared-heap RPC**, from `rpc_caller_gated`
(`fcn.fffffff00a0ce46c`): `bits[31:16]` heap offset, `bits[47:32]` size, and
the request at `heap+off` is `u32 FourCC, u32 in_len, u32 out_len`. The
completion is class 2 subkind 1 with status in `bits[47:16]`. `A401` (stub at
`0xfffffff00a0c8a80`, returns `out[0] & 1`) gates `AppleCLCD2` from its only
call site, `0xfffffff00a0b5fe4` inside `IOMobileFramebufferAP::start()`.

`AppleCLCD2`'s personality matches the IODeviceTree nub for `/arm-io/disp0`
(`IONameMatch = [disp0,t8140, dispext0,t8140]`), not `IOMobileFramebuffer`.
Nothing was ever missing from the device tree; the driver was simply never
allowed to finish `start()`.

**What is still stubbed:** every RPC *method* returns status 0 with zeroed
output except `A401`; class 0 subkind 0, class 3, and the entire `D`-series
callback direction (DCP → AP) are not modelled. There are no pixels yet: the
AP goes quiet after `A353`, has not powered the DCP on, and has not asked for
a framebuffer. On real hardware the firmware drives that next phase, which is
why the `D`-series is the next piece of work. `link_rpc_lookup`'s nested
switch at `0xfffffff00a0d05ac`-`0xa0d0680` is the AP's dispatch table for
those names and gives a handler per callback, so the next gate can be named
before booting.

### Method that keeps working

Static RE names the next gate, a cheap probe tests it, and the *failure
message* names the gate after that — `882` to `624` in one iteration. Several
confident hypotheses have been wrong (the flags byte, a "16 MB length" reading
of the IOMFB header, `genter` as the HVF cost driver); in each case a
measurement corrected them, not more reasoning. Reach for the experiment first.

One more failure mode, learned the expensive way: `a63e509` claimed the
firmware hash check passed. It did not. That commit rewrote the ack value and
deleted the `darwin_asc_send()` beside it, so the model computed the hash,
logged it, and dropped it — and the boot still showed 0 panics and a shell,
because a silent no-op looks exactly like success on those two metrics.
**Grepping for the absence of the failure is not evidence.** Check that the
guest did the work: here, an `IOP -> AP ep 0x37` line, or the guest's own
`check_firmware_hash_crc32()` log.

## Storage

**iOS roots off the emulated controller.** `darwin-ans` is a working ANS/NVMe
model, and XNU takes its root filesystem from it:

```
Got boot device = .../ans@79600000/AppleASCWrapV6/iop-...
BSD root: disk1s1, major 1, minor 2
(mount-phase-2) Doing boot task
Early boot complete. Continuing system boot.
```

42,001 serial lines, 0 panics. **`mount-phase-2` completes for the first
time**: on the ramdisk path it died 2,493 files in with `ramstrategy: buf_map
failed @memdev.c:299`, and with the filesystem on ANS the memory-disk driver
is not in the path at all.

Writes are proven separately — `newfs_apfs` on a scratch image issues 69 NVMe
Writes and produces a real `NXSB` superblock that APFS then mounts and cleanly
unmounts. iOS mounts its system volume **read-only**, so no write ever reaches
`rootfs.dmg`; use the `rootfs-overlay.qcow2` overlay anyway.

The recipe, and three things that each cost a boot (`docs/re/ans-nvme-references.md`
§12):

- `dt_fixup.py -enable ans`, plus `-drive if=none,id=ans,file=<image>`.
- **The volume is `disk1s1`, not `disk0s1`.** `disk0` is the NVMe media; APFS
  synthesises a container device and the system volume lands on `disk1s1`.
  Waiting for a `disk0s1` line is waiting for something that will never print.
- **The boot-arg is `rootdev=`, not `rd=`.** The `bsd_rooted_ramdisk` patch
  only fires on the branch where `PE_parse_boot_argn("rd")` fails; otherwise
  `panic: rootvp not authenticated after mounting @bsd_init.c:976`.
- `-ephemeral-data` is **still needed**, because the image has no Data volume
  (`mount: missing data volume`, `mount[8] exited ... status 66`).

Known-bad, do not chase: `-enable sep` **plus** the ANS root path panics in
`AppleSEPXART::getFullEpochs()` (`REQUIRE fail: expected_out_len == out_len
@AppleSEPXART_embedded.cpp:1021`). `-enable sep` on the ramdisk is fine.

Four model details worth knowing, each traced to a boot that failed without
it: **MDTS is 8, not 5** (at 128 KiB the root mounts and then dyld dies, because
APFS issues 256 KiB reads and `IONVMeFamily` sizes its PRP list from MDTS);
`blk_set_perm()` is required or the first guest write aborts QEMU at
`io.c:2016`; the SART log is rate-limited because it fires 411,000 times and
can never succeed (the CoastGuard mapper programs the filter through
`pmap_iommu_ioctl`, so the region table we can see stays empty); and the
endpoint-nub name no longer underflows to `ANS2Endpoint4294967273`.

Background: `docs/re/storage-path.md` on why **ANS** and not PCIe NVMe (all
three `apcie` ports are taken by WLAN/BT/baseband) or virtio (no virtio kexts
in this kernelcache). The submission path is a **tag-indexed array, not a
ring**. We cannot wrap QEMU's stock `hw/nvme`: it is a PCI device,
`CONFIG_NVME_PCI` is off, and this machine has no PCI bus.

**Next:** `tools/rootfs/build_dual_volume.sh`. A real Data volume is the only
thing left between this and a persistent system — `mount-phase-2` would mount
instead of copying 41,557 files, which is the ~213 seconds of guest time this
boot still spends, and `/private/var` would survive a reboot.

## Performance

TCG is the architecture, not a stopgap. HVF acceleration was costed and
rejected with measurements in `docs/re/hvf-acceleration.md`: the guest runs at
**EL2**, where the Apple IMP-DEF registers, `genter`, `gexit` and `HVC` are all
taken by the guest, so there is nothing for a trap-and-emulate scheme to land
on — and the projected win was only 1.9-3.3x anyway. Windows ARM, the actual
endgame, has no Apple IMP-DEF registers at all and needs TCG regardless.

`tools/time_boot.py` times a boot to a serial marker, `hvf-probe/hvf_exitbench`
measures exit cost, and `target/arm/gxfstat.c` counts per-boot guest events at
0.6% overhead. Note `darwin.c` sets `mc->max_cpus = 1` while the device tree
describes six CPUs, so MTTCG headroom is unused.

## Where the userspace boot stands

**SpringBoard launches.** The system volume boots to `Early boot complete`
with 0 panics, and with `-skip-keybag` launchd gets as far as starting
SpringBoard, which then crashes:

```
(boot) <Notice>: Early boot complete. Continuing system boot.
<Critical>: rebooting due to critical process crashes: SpringBoard
```

`backboardd` (pid 69) and `keybagd` (pid 54) are alive as lingering coalitions
at that point. **Why SpringBoard crashes is the current top question**, and the
reason is not on the serial console — the only nearby clue is AMFI rejecting
`PeerTimeSyncPlugin` for `unsuitable CT policy 0`, which is a plugin, not
SpringBoard. `tools/serial.py` against a shell rootfs, or a `ReportCrash` /
`os_log` route, would name it.

Without `-skip-keybag`, `SEPFINAL` still runs 42,020 lines to 0 panics,
finishes `mount-phase-2`, and reaches launchd's `keybag` boot task and
`MobileAssetEarlyBootTask`.

Three gates were cleared to get here, all in `docs/re/seputil-data-protection.md`
with addresses:

- **the xART marker node.** `gigalocker_init`'s first call (`0x100014820`) is
  just `IORegistryEntryFromPath` on
  `"IODeviceTree:/arm-io/sep/iop-sep-nub/xART"`; with the node absent seputil
  prints "xART is not supported on platform, skipping initialization" and
  returns 0. `dt_fixup.py -enable sep` removes it.
- **`/chosen/sepfw-load-at-boot = 0`.** The personalized `sep-firmware.img4`
  lives on the **Preboot** volume, which the IPSW system-volume payload does
  not contain. Clearing it takes the "Skipping SEP firmware load" branch. The
  SEP still does its full ROM handshake and ACM still sends SCRD commands —
  that was measured, not assumed.
- **the `sks` endpoint is no longer advertised.** `AppleSEPKeyStore` starts its
  IPC the moment `sep-endpoint,sks` appears and panics at strike 20 (`cmp w21,
  0x14` at `0xfffffff00954c0b4`). Advertising an endpoint we cannot answer is
  strictly worse than not advertising it. Default is `cntl,scrd,xars,xarm`;
  `DARWIN_SEP_EPS` restores the old set. This also removed a latent panic from
  the restore-ramdisk path, which was silently reaching strike 18.

A refuted idea worth not re-having: making `/private/xarts` writable cannot
help. seputil's boot-task path passes 0 for "may I create the file" — only
`--gigalocker-init` passes 1 — so it returns `errno` 2 without ever attempting
a create.

Still open: **personas.** `usermanagerd` dies with "Daemon failed to load
persona manifest" and 248 `kpersona_find_by_type(type 6)` failures follow.

### Two traps in the panic logs, both of which have already cost an hour

- **This machine has no reset path.** Any launchd-initiated reboot ends in
  `panic: Halt/Restart Timed Out @IOPlatformExpert.cpp:900`. That panic is a
  *consequence* of the guest asking to reboot; the real failure is above it.
- **The nested-panic block is not your fault.** Every run that panics at all
  prints a byte-identical register dump inside `<Nested panic string>` with
  `esr: 0x96000045` and `far: 0xb1`. It is the panic printer faulting, not a
  NULL pointer in whatever you just changed. It appears in runs whose real
  panic was `seputil[4] exited ... status 2`. Do not decode it and conclude
  anything; find the *first* `panic(cpu` line instead.

Artifacts now live at `~/dvm-artifacts/` (`build/rootfs.dmg`,
`tc/merged_sysvol_cryptex_tc.bin`), not `/tmp/dvm/build`, which gets wiped.
There is currently no `rootfs_sh.dmg` (the shell-tools rootfs) in that set.

## Reading the logs

One line, `TXM [Error]: selector: 38 | 42`, is roughly 99% of all serial output
(1.6M of 1.63M lines in a 300-second boot). Strip it first or nothing is
legible:

```
grep -av 'TXM \[Error\]' probe/X.serial.log > probe/X.clean.log
```

It comes from the kernelcache, not from TXM, and is the generic fallback of five
`TXM [Error]` formats — see `docs/re/txm-selectors.md`. It is noise, not a
failure we have had to fix.

`probe.sh` reports `reached shell: no` for system-volume boots. That is correct
and not a regression: it greps for `can't access tty`, which the restore ramdisk
emits. The system volume runs launchd instead.
