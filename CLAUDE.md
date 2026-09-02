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
- `darwin-ans`: the ANS/NVMe storage controller. `IONVMeController`
  initialises it, an `IOMedia` appears as "APPLE SSD (darwin-ans)", `disk0`
  enumerates, and APFS finds a valid checkpoint on a real 21 GB image. Reads
  are proven; writes and rooting off it are not. See **Storage**.
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

`darwin-ans` works: Apple's own `IONVMeController` initialises it and APFS
mounts a real disk image off it. From probe run `ANSDISK`, with the 21 GB
`rootfs.dmg` as the backing file, 0 panics:

```
IONVMeController::start():844: Successfully initialized NVMe drive 0x1000003EC
AppleEmbeddedNVMeController::StartController():1461: Setting NAND status to Ready
Registering: ../RTBuddy(ANS2)/RTBuddyService/AppleANS3CGv2Controller
Registering: ../APPLE SSD (darwin-ans) Media/IOMediaBSDClient
dev_init:299: disk0 device_handle block size 4096 block count 5593600
nx_mount:1482: disk0 checkpoint search: largest xid 551, best xid 551 @ 109
```

APFS walking the checkpoint tree to a valid superblock is the load-bearing
evidence: it cannot succeed unless the tag-indexed submission path, the NVMMU
TCB handshake and the SART-filtered DMA all return the right bytes.

**Working is not the same as rooting.** That run still booted `rd=md0`, and
APFS only opened the *container* — there is not one `disk0s1` line in the log,
and zero writes ever reached the model. Three things stand between here and
retiring the ramdisk:

- mount a **volume**, not just the container, which exercises far more of the
  read path than a checkpoint search does;
- **writes**, which have never run. Put a qcow2 overlay over the 21 GB image
  first: a bad boot writing through to it costs us the image.
- the **sealed root snapshot**, which is not a storage bug. The same run shows
  `apfs_vfsop_mount:2914: md0s1 failed to find named root snapshot: Need
  authenticator (81)`. We sidestep it on the ramdisk path; rooting off `disk0`
  brings it back, and it is an AMFI/trustcache problem.

Why it is worth it: **228 of the 257 seconds to `Early boot complete` is
`mount-phase-2` rebuilding `/private/var` from 41,557 files**, purely for want
of a Data volume. Rooting off ANS also drops the guest from 40 GB to ~8 GB and
retires `-ephemeral-data`.

Background, still accurate: `docs/re/storage-path.md` on why **ANS** and not
PCIe NVMe (all three `apcie` ports are taken by WLAN/BT/baseband) or virtio (no
virtio kexts in this kernelcache); `docs/re/ans-nvme-references.md` for the
register map, the 11-step bring-up, and the protocol divergences — the
submission path is a **tag-indexed array, not a ring**, and our
`nvme-secure-bar` generation needs extra IO-queue base writes at `0x1200`/
`0x1208` that the Linux driver never does. We cannot wrap QEMU's stock
`hw/nvme`: it is a PCI device, `CONFIG_NVME_PCI` is off, and this machine has
no PCI bus.

Usage: `dt_fixup.py -enable ans`, plus `-drive if=none,id=ans,file=<image>`.
`darwin.c` creates the device and claims the `ans` mailbox; with no drive it
boots anyway and says so. `DARWIN_ANS_SELFWIRE=1` is the model's own bring-up
scaffold, which predates the machine wiring and creates the NVMe half from a
machine-init-done notifier instead — it stays until the wired path is
boot-tested, then both it and its branch in `darwin.c` come out.

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

The real iOS system volume boots, and as of tonight it boots down iOS's
**normal** path rather than the restore path:

```
tools/probe.sh --dtree <tree built with -ephemeral-data> \
  --ramdisk /tmp/dvm/build/rootfs_sh.dmg \
  --tc /tmp/dvm/tc/merged_sysvol_cryptex_ramdisk_tc.bin --mem 40G --secs 420 \
  --bootargs 'rootdev=md0 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort'
```

reaches `libignition ... boot spec name : local` with seven stages and no
"Restore environment", runs `mount-phase-1` and `-2` for the first time, and
mounts `/private/var` as a tmpfs seeded from the on-volume template. It
currently dies 2,493 files into that copy with

```
panic(cpu 0 ...): ramstrategy: buf_map failed @memdev.c:299
```

Booting with plain `rd=md0` still works and still reaches a shell; that path
just cannot reach SpringBoard, for the reasons in
`docs/re/userspace-boot-state.md`.

Two further things are known to stand between here and SpringBoard:

- **SEP.** AMFI asks ACM for developer-mode status on every spawn; ACM waits on
  `AppleSEPManager`; that wait burns 890 seconds of every 900. `-enable sep`
  plus three device tree values gets the driver to `control endpoints created`,
  but ACM then waits for `sep-endpoint,scrd` — a name `AppleSEPManager` builds
  at runtime from what the coprocessor reports over the mailbox, so no device
  tree property can close it. `docs/re/sep-bringup.md`.
- **Personas.** `usermanagerd` dies with "Daemon failed to load persona
  manifest" and 248 `kpersona_find_by_type(type 6)` failures follow. Expected to
  clear once `/private/var` is genuinely writable.

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
