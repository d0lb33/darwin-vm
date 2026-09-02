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

## Where the DCP bring-up stands

The coprocessor start blocker is understood. `RTBuddy::start()` always returns
true; the real work is a conditional tail call to `_attemptFirmwareLoad()`
(`fcn 0xa7bbefc`, unslid). Which branch it takes is decided by device tree
properties on the IOP nub:

| Property | Effect |
|---|---|
| `no-firmware-service` | do not wait for a firmware service (which this kernelcache has no provider for) |
| `pre-loaded` | take the "firmware already resident" path; **presence only**, value is ignored |
| `region-base` + `region-size` | both required and both non-zero, else the segment list is NULL |
| `quiesced` | **makes RTBuddy skip the CPU_CONTROL.RUN write entirely**, because it assumes the IOP is already running. Ours is cold, so this must be removed. |
| `ignore-gating` (on the ASC node, not the nub) | turns `AppleA7IOP::powerOn` into a no-op, avoiding a poll on the unmodelled PMGR power gate |

Nothing is validated at `region-base` on the pre-loaded path, so staging real
firmware there is not required to make progress.

With those set (see `/tmp/dvm/mkdcp.py`) the DCP now gets past the silent stall
and fails inside SPTM instead:

```
t8110dart_init_instance: dart-dcp:0: DART instance 0: Invalid VM page limits [0x4000000,0x10000000)
```

Ruled out for that check: the advertised VA width (SPTM reads only PARAMS1-4
and nothing else before panicking; raising VA width from 42 to 43 changed
nothing), and the range magnitude (narrowing vm-size gives proportionally
smaller limits and the same panic). Under investigation.

Enabling ANS as well hits a separate SPTM gate,
`sart_sanity_check_throttles: Sart invalid throttle cfg [0] = 0x0`, because
nothing models the SART address filter yet.

The RTBuddy power-state to RTKit `SET_IOP_PWR_STATE` payload table lives at
`0x7b28e30`: `{state0: 0x201, state1: 0x220, state2: 0x202, state3: 0x0}`.
Mirror it in `darwin_asc.c` when the mailbox finally sees traffic.

## Where the rootfs work stands

**The real iOS system volume now boots as a ramdisk.** APFS mounts it,
libignition runs, and the kernel execs the real /sbin/launchd off it:

```
md0 device_handle block size 512 block count 19582976
md0s1 mount-complete volume RaveSeed24A5430a.D47DeveloperOS
libignition: 1:   program : launchd
```

This needed the memdev patch (qemu-sptm/hw/arm/xnu_patch.c) to lift XNU's 4GiB
ramdisk cap, plus `dt_fixup.py -dram` and `probe.sh --mem` to raise guest DRAM,
plus an assembled image with the dyld shared cache copied to
System/Library/Caches/com.apple.dyld (dyld skips cryptex mounting when it finds
one there). See docs/re/rootfs-assembly.md for how the image is built without
sudo, and for the merged v2 trustcache.

The current blocker is dyld failing to map that cache:

```
dyld[1]: dyld cache '(null)' not loaded: syscall to map cache into shared region failed
dyld[1]: Library not loaded: /usr/lib/libSystem.B.dylib
```

Ruled out: memory pressure (identical at 32G and 40G, which leaves 20GB free),
shared region capacity (4.97 GiB of mappable subcaches against a 6.00 GiB
SHARED_REGION_SIZE_ARM64), and code signing (amfi_get_out_of_my_way=1 changes
nothing; cs_enforcement_disable is refused outright). Leading theory is that
`rd=md0` makes libignition classify this as a "ramdisk" (restore) boot, which on
real hardware has no shared cache at all.

### Historical

The real iOS filesystem is decrypted and mounted locally (see
`docs/re/rootfs-boot.md`). `ipsw fw aea --fcs-key` issues decryption keys with
no credentials. The system volume is unsealed, so authenticated-root is not a
barrier. It boots as a ramdisk right up to a size check: XNU's `mdSize` is a
uint32_t page count and eight `mdSize << 12` expressions evaluate in 32-bit
arithmetic, capping memory-backed root disks at 4GiB. The system volume needs
~10GB and its dyld shared cache alone is 5.3GB.

Endpoint map, from the kext IOKit personalities:

| Endpoint | XNU name | Driver |
|---|---|---|
| 0x20–0x2a | `DCPEndpoint1`..`23` | `DCPEndpointV2` (AFK/EPIC framing) |
| 0x37 | `DCPEndpoint24` | `AppleDCPLinkServiceSoC` (IOMFB link) |
| — | `disp0,t8140` | `AppleCLCD2`, the display driver |
