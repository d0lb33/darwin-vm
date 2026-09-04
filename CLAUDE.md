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

## iBoot unsigned-boot research policy

On the `codex/iboot-main` research branch, the user explicitly authorizes
opt-in bypasses of Apple boot-policy validation so the VM can run compatible
unsigned or modified iOS components. This includes iBoot Image4 signature,
IM4M/APTicket/personalization, trust-policy, root-hash/seal, SPTM/TXM policy,
and kernel AppleImage4/AMFI gates when each gate is independently identified.
This authorization supersedes the earlier iBoot-task restriction against
weakening validation, but only for cryptographic and boot-policy decisions.

Every bypass must remain explicit, default-off, build/hash-pinned where
practical, and log the image, runtime/static patch address, original bytes or
decision, and resulting control flow. Preserve structural parsing,
decompression, bounds checks, load-address checks, and memory-safety checks.
Keep an unmodified validation path as a control and never describe a bypassed
chain as Apple-verified or secure boot.

This exception does **not** permit invented hardware success values, fake
protocol replies, ignored MMIO failures, or weakened device-model validation.
Continue the first-boundary method for CPU, PMGR, SEP, ANS, storage, display,
and every other device. Preserve direct boot and all existing device behavior.
Use only isolated firmware copies and disposable disk overlays; never modify
the active checkout or its durable/base disk artifacts.

Current iBoot checkpoint (QEMU `8503414`, 2026-09-04): both pinned d47
research and release images pass the bounded LLC/SEP/APIA/root, CPM, unlock,
GPIO, PMGR topology, MCC, signed range-3 tuning, and 78 exact range-2 table
RMWs, then first touch physical `0x300040144`
(`pmgr[2]+0x40144`). The exact next record and runtime/static addresses are
in `docs/re/iboot-runtime.md`. A real-device capture is optional fidelity
evidence, not a prerequisite for continuing the firmware-defined path.

Current SEPROM checkpoint (2026-09-04): direct SPTM boot can opt in to an
authentic encrypted d47 `sepi` with `-sepfw`. The loader preserves the exact
IM4P bytes, and the SEP model now resolves BOOT_IMG4's AP-supplied DVA through
`dart-sep`, enforces the observed status/TZ0/IMG4 state sequence, checks the
outer DER length, and verifies the complete mapped container against the
preloaded SHA-256 before acknowledging it. This is an AP/ROM transport boundary
only: the encrypted payload is not decrypted or executed. Artifact hashes,
controls, logs, and the next Image4-metadata work are in
`docs/re/seprom-behavioral.md`.

Boot-arg `io=0x1f` makes IOKit log every driver match and start, which is how you
see where a driver chain stalls.

### Fast display iteration

Do not give every experiment a blind 480- or 600-second budget. The instrumented
display stall is visible at about 104 seconds. This command stops when selector
79 is still pending at its 30-second deadline, freezes QEMU, and automatically
runs a 2 GiB first-pass post-mortem:

```bash
CALLBACKS=display_iokit_callbacks SECS=180 \
  tools/re/setup_gate_probe.sh UI_MY_TEST1
```

When callbacks fire, they write atomic per-call events under
`/tmp/dvm/UI_MY_TEST1.events`;
`tools/re/probe_watch.py` writes the durable reason to
`/tmp/dvm/UI_MY_TEST1.stop`; and `probe.sh` reports `STOPPED ON CONDITION`.
The post-mortem writes `/tmp/dvm/UI_MY_TEST1.postmortem.txt`. It scans thread
signatures and kext frames from each RAM chunk in one pass. This setup probe asks
for two correlated display-stall stacks; if fewer are found in the first 2 GiB,
it scans only the untouched suffix up to the full 12 GiB. The standalone tool's
`--min-stacks` default is one. A positive fast result is labelled
`partial-first-pass`; use `--ram-size 0x300000000` for an exhaustive 12 GiB scan.

Other bounded conditions are opt-in:

```bash
# Stop on a serial or LLDB log line.
PROBE_STOP_SERIAL_REGEX='set_power_state done powerState=0' SECS=180 \
  tools/re/setup_gate_probe.sh UI_POWER0

# Stop as soon as SpringBoard reaches applicationDidFinishLaunching.
CALLBACKS=sb_setup_path_callbacks PROBE_SUCCESS_LABELS=SB_ADFL_ENTRY \
  AUTO_POSTMORTEM=0 SECS=300 tools/re/setup_gate_probe.sh UI_SB_ADFL1

# Run independent EPIC controls two at a time; each uses a unique tag/port.
MAX_PARALLEL=2 tools/re/setup_gate_sweep.sh UI_EPIC off all
```

Standalone probes leave the guest frozen by default. Sweeps default to
`KEEP_GUEST=0` after collection so 12 GiB guests do not accumulate. Never run a
QEMU rebuild during a sweep. Every parallel run needs a distinct tag and GDB port;
the sweep assigns both, and the post-mortem isolates scratch paths by tag. All
variants inherit the same callback, condition, and post-mortem settings.

Before spending a boot, run the host-only regressions:

```bash
python3 -m unittest discover -s tools/tests -v
bash -n tools/probe.sh tools/re/setup_gate_probe.sh tools/re/setup_gate_sweep.sh
```

For a late SKS opcode, do not enable global request dumping during a long
userspace boot. `DARWIN_SKS_REQUEST_DEBUG_CODE=0x0f` dumps only opcode `0x0f`;
routine successful op19/device-state traffic is sampled by default. See
`docs/re/fast-sks-iteration.md` for the exact-request replay test, bounded
selector/caller probes, and the verified QEMU-under-host-LLDB path.

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
- **Real iOS userspace.** The cryptex-merged System volume roots from ANS/NVMe;
  dyld maps the shared cache, launchd runs, hundreds of services start,
  SpringBoard launches, and backboardd reaches `running`. See
  `docs/re/persistent-data-volume.md` and `docs/re/setup-launch-runtime.md`.
- **Persistent Data.** A formatted, populated, protected APFS Data volume mounts
  at `/private/var` across chained cold boots. The second boot performs no
  41,557-file tmpfs copy. Rebuild the disposable parent with
  `tools/rootfs/rebuild_persistent_parent.sh`; see **Storage**.
- `darwin-sep`: enough SEP for `AppleSEPManager` to reach "control endpoints
  created" and answer `AppleCredentialManager`. That wait used to burn 890 of
  every 900 seconds; it is now zero. `-enable sep`.
- `darwin-ans`: the ANS/NVMe storage controller. **iOS roots off it** —
  `BSD root: disk1s1`, `mount-phase-2` completes, `Early boot complete`,
  0 panics. Reads and writes both proven. See **Storage**.
- `tools/serial.py` drives restore-ramdisk shells over a socket. The historical
  `rootfs_sh.dmg` was lost in a `/tmp` wipe and is not a current artifact.

## The GPU may not be on the critical path

**CoreAnimation has a CPU rasteriser and we are already in the state that selects
it.** `docs/re/ca-software-path.md` has the full derivation. QuartzCore carries four
render backends — `sw_new_context`, `metal_new_context`, `gles_new_context`,
`new_null_context` — and `CA::OGL::SW` is a 972-symbol software rasteriser with
`create_surface_from_iosurface` / `set_destination`.
`CA::WindowServer::Server::render_update()` calls `sw_renderer()` and then
`Display::render_display()`, and the concrete servers fall back to it when
`renderer()` is NULL, which happens exactly when `_CAMetalContextCreate` (a bare
`MTLCreateSystemDefaultDevice` wrapper) returns nil.

**Nothing has to be enabled.** The trigger is the absence of any `IOAcceleratorES`
service, which is our current state: `dt_fixup.py` deletes `/arm-io/sgx`, the only node
`AGXAcceleratorG17P` matches (`IONameMatch gpu,t8140`). No boot-arg or device-tree
property switches it — `CA_NO_ACCEL` / `CA_ACCEL_BACKING` govern only Metal-accelerated
CoreGraphics backing stores.

**Caveat, stated by the study itself:** this is verified in the iOS 27 Simulator and
macOS builds of QuartzCore. The iPhone build is the one binary not yet read, so
`AccelServer : IOMFBServer : Server` inheriting the fallback is *inference* until it is.

**Do not splice the `sgx` node back in.** Measured: with the raw node restored the guest
produces **zero** serial lines and parks in SPTM's self-branch at `0xfffffff0070f75a8`.
SPTM brings up the GPU UAT IOMMU from iBoot-supplied properties (`gpu-iouat`, UAT
handoff, `uat-enforce-gpu-carveout`) and `IOUnifiedAddressTranslator` would panic next on
five missing `gfx-*` carveout properties. A bare node is strictly worse than no node.

If this holds on the device build, the remaining cost for a rendered screen is the DCP
pixel path we already owe — `surface_map_dcp`, `swap_submit`, scanout — plus
TCG-speed compositing, rather than emulating a GPU.

Two false positives recorded so nobody re-chases them: `CA_RENDER_SERVER` /
`CA_CLIENT` in the kernelcache are `IOWorkloadConfig` scheduler names, and `.metallib`
strings in boot logs are file copies during `mount-phase-2`.

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
output except `A401`; class 0 subkind 0 and class 3 are not modelled. The
`D`-series callback direction (DCP → AP) now has a **working transport**: we
send a callback, the AP acknowledges, and a handler runs and writes into our
buffer. `tools/re/dcp_dtable.py` enumerates all **139** callbacks with handler
addresses — note the real dispatch entry is `link_rpc_lookup` at
`0xfffffff00917d328` fanning across nine blocks in two kexts, and
`0xa0d05ac`-`0xa0d0680` is only the D400-D424 leaf. What is *not* solved is
dispatch of an **inbound** request: it is acknowledged but `rpc_callee_gated`
only runs after a further class-2/subkind-1 on the same slot. See
`docs/re/iomfb-dseries.md`; the kick is behind `DARWIN_DCP_IOMFB_CB_KICK`,
default off. There are no pixels yet: the
AP goes quiet after `A353`, has not powered the DCP on, and has not asked for
a framebuffer. On real hardware the firmware drives that next phase. Getters are
now exhausted — a 23-getter sweep returned status 0 with distinct values and
the AP still issues only `A401`/`A465`/`A353` — so the remaining signal is in
the **input-carrying** callbacks.

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
- The verified persistent parent does **not** use `-ephemeral-data`: APFS mounts
  `disk1s2` at `/private/var` with `protect`, plus the Hardware and User roles.
  Two chained clean boots reached `Early boot complete` with no `Copying ` and
  no first `panic(cpu`; exact log lines are in
  `docs/re/persistent-data-volume.md:40-74`.

**Fixed.** `AppleSEPXART::getFullEpochs()` used to panic with `REQUIRE fail:
expected_out_len == out_len`. It was recorded as specific to `-enable sep` plus
the ANS root path; that was wrong — it fired on the plain ramdisk path too,
with no `-enable ans` anywhere. xART replies now carry `{status, u16 length}`
with the data written through the DART into the OOL out-buffer. See
`docs/re/sep-xart-epochs.md`.

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

The rebuild entry point is `tools/rootfs/rebuild_persistent_parent.sh`. It runs
format, restore-helper injection, Data copy, User manifest/layout, completion
marker, and two normal boots, then links the result at
`/tmp/dvm/data-seed/persistent-parent.qcow2`. The base image survives under
`~/dvm-artifacts`; `/tmp/dvm` does not. Do not rerun the full rebuild for display
variants: `setup_gate_probe.sh` creates a fresh qcow2 child from this parent.

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

**Current:** the storage/userspace bootstrap is complete enough for display
work. The cryptex-merged System volume boots from ANS, the protected persistent
Data/User volumes remount across cold boots, `Early boot complete` is reached
without the old tmpfs copy, and SpringBoard/backboardd run. The current blocker
is later: backboardd's `IOConnectCallMethod` selector 79 (`_kern_GetBlock`, kind
`0x41`) does not return while the IOMFB display-power path is parked through
RTBuddy/AppleFirmwareKit. `DARWIN_DCP_EPIC=off` did not release it: `UI_NOEPIC2`
recorded 39 method entries and 38 returns, with the unmatched `x1=0x4f` entry at
104.1 seconds and zero panics. Start from `docs/handoff/display-power-off-plan.md`
and use **Fast display iteration** above.

### Historical fixed blockers

The pre-cryptex System image reached `Early boot complete` and then SpringBoard
crashed:

```
(boot) <Notice>: Early boot complete. Continuing system boot.
<Critical>: rebooting due to critical process crashes: SpringBoard
```

The reason is not on the serial console — it was read out of guest RAM with
`tools/oskcdata.py`, which scans a `pmemsave` dump for the kernel's OS_REASON
records. SpringBoard's own exit reason:

```
EXIT_REASON_USER_DESC  "couldn't dlopen libobjc-trampolines.dylib:
   dlopen(/usr/lib/libobjc-trampolines.dylib, 0x0106): tried:
   '/usr/lib/libobjc-trampolines.dylib' (no such file, not in dyld cache)"
PROC_NAME              'SpringBoard'
```

That is libobjc's `_objc_fatal` on the first IMP trampoline, which no UIKit app
can avoid. Not dyld, not codesigning, not the spawn — the same dump has dyld's
launch record showing the shared cache and executable mapped and dyld reaching
`main`. `temporary-sandbox` dies on the identical string; `lockdownd` dies on
`/usr/lib/libramrod.dylib`.

**Fixed on 2026-09-02: `~/dvm-artifacts/build/rootfs_cx.dmg` is the merged
image** (2,111 files / 127,134,982 bytes rsync'd from the cryptex at
`~/dvm-artifacts/aea/out/094-13150-145.dmg`, excluding its own dyld cache under
`/System/Library/Caches/com.apple.dyld/`, which is byte-identical to the staged
one). A boot of it reaches `Early boot complete` with **no**
`libobjc-trampolines` complaint anywhere. Rebuild it with
`tools/rootfs/build_data_volume.sh`-style safe attaches; the cryptex files are
APFS-compressed, so the copy must go through a mounted volume.

**The original cause was that the image we booted lost its cryptex merge in the
`/tmp` wipe.** `libobjc-trampolines.dylib` deliberately is not in the shared cache; it
ships in the OS cryptex. **2,105 of the cryptex's 2,113 loose files are missing
from `rootfs.dmg`** — the surviving artifact is the pre-merge one, and every
boot since has run without it. `docs/re/userspace-boot-state.md` documents the
merge; `merge_cryptex.sh` itself is still lost. No trust-cache change is needed:
`merged_sysvol_cryptex_tc.bin` already covers all 49 signed cryptex Mach-Os. One
trap on the rebuild: the cryptex files are APFS-compressed (`com.apple.decmpfs`,
type 14), so archive tools list them and extract zero bytes — the copy has to go
through a mounted volume with `cp`/`ditto`.

The former SEP/SKS and Data-volume limitations are also resolved for the
current persistent parent. `sks` is implemented and advertised; the system
boot mounts encrypted/protected Data, Hardware, and User roles and reads the
seeded protected files without `fext_ek`, `apfs_unwrap_key`, timeout-strike, or
first-panic failures across two chained boots. Use
`docs/re/persistent-data-volume.md:40-74` for the runtime proof and
`docs/re/sks-op0f-media-key-migration.md:181-216` for the opcode/key contract.
The older restore-only feasibility notes remain useful history, not current
status.

Still open but not the first display gate: persona completeness. Preserve the
current ANS, SEP/SKS, protected-file, and cryptex behavior in every display
change.

### A trap in fresh agent worktrees

`firmware/` is gitignored, so a new worktree does not have it. `probe.sh` then
silently drops `-sptm`/`-txm` and the guest hangs at the kernel entry with
**zero serial output and zero panics** — which reads exactly like a
catastrophic regression and is not one. Symlink `firmware` into the worktree.

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

Durable artifacts live at `~/dvm-artifacts/`, including the dual-role base image
and `tc/merged_sysvol_cryptex_tc.bin`. Derived qcow2 parents, children, device
trees, sockets, scans, and logs under `/tmp/dvm` are disposable; reconstruct
them with `tools/rootfs/rebuild_persistent_parent.sh`. There is currently no
durable `rootfs_sh.dmg` shell-tools image.

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
