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
- A root shell inside the booted system volume, for poking at it live
  (`/tmp/dvm/build/rootfs_sh.dmg`, 102 tools from the restore ramdisk, uid 0).
  `tools/serial.py` drives it over a socket.

## Where the DCP bring-up stands

The transport is up. A plain `dt_fixup.py -enable dcp` tree now brings all
eleven endpoints through the full AFK handshake, with zero unhandled opcodes and
zero unmapped DVAs:

```
RTBuddy(DCP): start(...)
IOMFB: service matched: AppleDCPExpert
afk(DCP): ep 0x20 started (rings at dva 0x10000000000)
...  through  ...
afk(DCP): ep 0x2a started (rings at dva 0x10000050000)
```

What makes the coprocessor actually *start*, as opposed to merely matching
drivers, is `fixup_iops()`: `ignore-gating` on the IOP node so `AppleA7IOP`
does not poll an unmodelled PMGR gate, and `pre-loaded` + `region-base` +
`region-size` + `no-firmware-service` on its nub. Full derivation with
addresses in `docs/re/dcp-iop-start.md`. Note `quiesced` must NOT be set: it
makes RTBuddy assume the IOP is already running and skip the `CPU_CONTROL.RUN`
write, and ours is cold.

**Next step: EPIC on top of AFK.** Announce one service on endpoint `0x20` and
see whether XNU binds a sub-driver. `darwin_afk_send_qe()` is the entry point
and has no caller yet; `docs/re/dcp-firmware-services.md` has the service map
and `docs/re/afk-epic-references.md` §2.2 the ANNOUNCE payload shape. That
document is wrong on one load-bearing point: the ring granule is `0x80` on iOS
27, not `0x40`, making the header `0x180`. With `0x40` the guest panics in
`afk_messenger_common.c:126`.

Endpoint map, from the kext IOKit personalities:

| Endpoint | XNU name | Driver |
|---|---|---|
| 0x20–0x2a | `DCPEndpoint1`..`23` | `DCPEndpointV2` (AFK/EPIC framing) |
| 0x37 | `DCPEndpoint24` | `AppleDCPLinkServiceSoC` (IOMFB link) |
| — | `disp0,t8140` | `AppleCLCD2`, the display driver |

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
