# Native RTC through the SPMI PMU (2026-09-05)

The iOS calendar clock now comes from Apple's own driver chain running
unmodified against two new device models:

```text
PEGetUTCTimeOfDay -> AppleARMPE::getGMTTimeOfDay -> IORTC
  -> AppleDialogSPMIPMURTC (AppleSPMIPMU.kext)
  -> AppleDialogSPMIPMU::_readRegs -> AppleARMSPMIDevice -> AppleSPMIController (Gen3)
  -> qemu-sptm/hw/arm/darwin_spmi.c  (controller, /arm-io/nub-spmi0)
  -> qemu-sptm/hw/arm/darwin_pmu.c   (Dialog "baku" PMU, sid 14, 32,768 Hz upcounter)
```

There is no kernelcache patch and no paravirtual register on this path:
`DARWIN_RTC_PV=0` with the unpatched (or SMP-only) kernelcache.  The PV
adapter in [rtc-pv-wall-clock.md](rtc-pv-wall-clock.md) is superseded and
must not be combined with `-enable spmi` (two IORTC providers).

Branch: `claude/native-rtc-hardware-emulation-8c64b9` (submodule branch
`native-rtc`).  Every claim below names the run that produced it; the logs
are under `/tmp/dvm/probe/RTC_NATIVE_RESTORE*.{serial,stderr}.log` and
`/tmp/dvm/RTC_NATIVE_SYS*/`.

## What was compared before modelling

The user asked for the existing implementations to be checked against the
T8140 driver first.  Result, per item:

| Item | Inferno `apple_spmi.c` / `spmi-pmu.c` | Linux `spmi-apple-controller.c` | OpenBSD `aplpmu.c` | T8140 kernelcache (24A5430a) |
| --- | --- | --- | --- | --- |
| request word | opc, sid<<8, addr<<16, final 1<<15 | same, plus `len-1` in the opcode's low bits | n/a | same as Linux: built at `0xfffffff0096144e4-0xfffffff009614514` |
| status bits | REQ empty 1<<8, RSP empty 1<<24 | RSP empty 1<<24 | n/a | 0x100/0x200 and 0x1000000/0x2000000, from the Gen3 pointer block at `0xfffffff00960c614` (+0x48/+0x50) |
| queue registers | status +0, push +4, pop +8 inside a 0x700 sub-block | +0/+4/+8 | n/a | +0/+4/+8 of `reg[0]` directly; interrupt banks at +0x20/+0x60 |
| response header | `req & 0xfff`, ack mask <<16 (reads), bit 15 on writes | discarded | n/a | bits [11:0] compared under a per-opcode mask, **bit 15 required**, **[31:16] must be `(1<<len)-1`** (`0xfffffff009614a00-0xfffffff009614a18`); the guest names them "parity" bits |
| six-byte tick read | `b0<<1 ... b5<<39` | n/a | Sera: 32.16 fixed point at `0xd002` | `b0>>1 | b1<<7 | ... | b5<<39` at `0xfffffff009622724`; 32,768 Hz |
| clock offset | scratchpad +4 (u32 s) and +21 (u16 ticks) | n/a | separate 33.15 register at `0xd100` | **legacy scratchpad +4 and +0x15**, `0xfffffff0096211dc/0xfffffff0096211e4`; `info-clock_offset` would select a 48-bit register, absent on this tree |
| alarm | ctrl/en-mask, seconds compare, event bit, irq mask | n/a | n/a | same register names from the nub; **not traced at runtime** |

So Inferno's PMU shape is correct for T8140 and was adopted; its controller
is a different generation (`reg-vers`, single block with a 0x700 queue
offset) and was not.  OpenBSD documents the Sera PMU, a different register
map, and was only useful as a second reading of the tick/offset arithmetic.

## Controller facts that had to come from this guest

- `gen = 3` selects the handler at `0xfffffff00960c57c`; its init maps
  `reg[2]` itself and keeps a queue-reset bit at `reg[2]+0x20` that must
  self-clear within 10 ms (`0xfffffff00960c80c`).
- `reg[1]` is the fault block; the driver only requires its status to read
  "both queues empty" and dumps 37 named counters at +0x400..+0x4d8
  (table at `0xfffffff008123998`).
- Interrupts: nine banks of 32, enable at `reg[0]+0x20+4*bank`, status at
  `+0x60+4*bank`, write-one-to-clear.  Index 0x100 is "response ready".
  Only `interrupts[1] = 0x1e7` is an AIC vector, because
  `IODTFindInterruptParent` indexes `interrupt-parent = <0x96 0x20 0x96>`
  per interrupt and wraps to entry 0.  The PMU's `interrupts = 2` is bank 0
  bit 2 of the controller.
- Opcode lengths: `EXT_WRITEL/READL` (0x30/0x38) carry `len-1` in three
  bits.  The first probe decoded four and answered a 1-byte read with 9
  bytes; the driver's message made the error unambiguous:

  ```text
  [nub-spmi0]:extendedReadWriteCommand:927:[error] parity error: parity_rcv=0x00FF parity_exp=0x0001
  ```

  (`RTC_NATIVE_RESTORE1`, serial line 203).

## Device tree

`dt_fixup.py -enable spmi` keeps `compatible` on `/arm-io/nub-spmi0`
(`aapl,spmi`) and `pmu-main` (`pmu,spmi`, `pmu,baku`), strips it from the
other child (`btm,phone`, AppleBTM, no slave behind it), and omits the
root `no-rtc` flag and the placeholder `rtc` node so only the native IORTC
exists.  `firmware/dtree` in the checkout is already stripped of
compatibles; the input must be the IPSW tree:

```bash
ipsw img4 im4p extract --output /tmp/dvm/dtree_raw ipsw_db/24A5430a__iPhone17,3/DeviceTree.d47ap.im4p
python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/RTC_NATIVE1/dt_spmi_restore.bin \
    -nvram nvram.bin -enable ans -enable smc -enable sep -enable dcp -enable spmi -dram 12G
python3 dt_fixup.py /tmp/dvm/dtree_raw /tmp/dvm/RTC_NATIVE1/dt_spmi_system.bin \
    -nvram nvram.bin -enable ans -enable smc -enable sep -enable dcp -enable spmi -dram 12G \
    -development-activation
```

`tools/tests/test_dt_fixup_spmi.py` covers the feature on a synthetic tree.

## Clock and persistence policy

- Upcount = ticks since the model's `clock_base_ns` on QEMU's `rtc_clock`;
  it advances at wall-clock rate and keeps advancing while the guest is
  paused or between a snapshot and its restore.
- Power-on (no `DARWIN_PMU_STATE`, or the file is absent): the scratchpad
  offset is seeded so upcount + offset equals QEMU's RTC time (`-rtc base=`
  honoured).  A guest `settimeofday` rewrites the offset for that run only.
- `DARWIN_PMU_STATE=<file>`: the 64 KiB register file and the host epoch at
  which the upcount was zero are loaded at start and saved after every
  guest write and at exit.  A guest-set clock then survives a QEMU restart
  and the upcount is monotonic across reboots, which is what a
  battery-backed PMU does.
- Snapshots: both devices carry vmstate.  After a restore the upcount is
  "now - clock_base", so the RTC reads current host time plus the guest's
  offset.  XNU only re-reads the RTC at boot and on wake, so a guest
  restored (or unpaused) after a gap keeps its stale calendar until
  something calls into IORTC; the RTC itself is not stale.  RAM snapshots
  taken with the PV register active cannot be restored on this branch.
- Alarm: modelled (ctrl enable bit -> seconds compare -> event bit ->
  PMU IRQ -> controller bank 0 bit 2 -> AIC 0x1e7) but no guest alarm
  transaction has been observed; treat as untested.

## Evidence

All runs: `DARWIN_RTC_PV=0`, this branch's QEMU, tree from the recipe
above, fresh qcow2 child over the immutable
`/tmp/dvm/CLOCK_SOFTWARE_INSTALL1/disk.qcow2`.

### RTC_NATIVE_RESTORE2 (restore ramdisk, 1 CPU, unpatched `firmware/bootkc`)

`tools/probe.sh`: `xnu panics: 0`, `reached shell: yes`.  Serial lines
226-235:

```text
AppleDialogSPMIPMU::start: Primary PMU detected
AppleARMRTC started!#####
AppleDialogSPMIPMURTC started!******
Read RTC offset from leg_scrpad
AppleARMRTC publishing service!^^^^^^
RTC upcountTicks=0000f57e, regs=fc:ea:01:00:00:00 (0)
```

launchd's first line: `2026-09-05 23:18:44` UTC against host 23:18 UTC.
Through the UART, after `cont`:

| step | guest | host |
| --- | --- | --- |
| `date -u +%s` | 1788650473 | 1788650785 (guest 312 s behind: the VM sat frozen 5 min under `--keep` and XNU does not re-read the RTC) |
| 23 s later | 1788650496 | 1788650808 (delta 23 s each, rate 1.00) |
| `date -u 010112002030` | `Tue Jan  1 12:00:00 UTC 2030` | model log: `write 0xf704 len 4: 34 7f dc 70`, `write 0xf715 len 2: e5 2a`, `guest epoch 1893499200` |
| 13 s later | `1893499213` | advances from the set value |

35 SPMI transactions in the whole boot, all `EXT_READL`/`EXT_WRITEL` to
sid 14, zero NAKs.  Registers the PMU driver touched at start, all plain
storage: scratchpad `0xf700-0xf719`, LPM log `0x8f80-0x8fb0` (8-byte
writes then reads), `0x8fdc`, `0x8800` (SOCD magic probe), `0x1000`
(5 bytes) and `0x2413` (2 bytes, off-to-wake source), then `0xf704`,
`0xf715`, `0xf802`.

### Persistence (RTC_NATIVE_RESTORE3 -> RESTORE4, `DARWIN_PMU_STATE`)

Boot 3 (fresh state file): `date -u 010112002030` at host 1788651086; the
model saved `f704 = 9d80dc70`, `f715 = c87d` (offset 1893499200 s).  QEMU
quit.  Boot 4 on the same file: `darwin-pmu: pmu-main restored ...;
upcount 6256123 ticks, guest epoch 1893499228`, launchd's first line is
`2030-01-01 12:00:33`, and `date -u +%s` read 1893499372 at host
1788651280, 22 s under the uninterrupted expectation (1893499394) because
the guest sat frozen at the probe deadline for those 22 s.  The clock the
guest set survived a QEMU restart with the upcount still running.

### RTC_NATIVE_SYS1 (system disk, `-smp 6`, `DISPLAY_SMP6.bootkc`): stall

The native RTC came up (serial 250-259) but the boot never printed
`BSD root`.  After NVMe Identify the driver issued two vendor tunnel
commands (0xd8), and the second was new:

```text
virtual IOReturn AppleEmbeddedNVMeController::SendTimeToDevice()::4532:nvme: CORE_DEBUG_SET_TIME failed with status 0xe00002e9
```

then every CPU sat in the idle loop (static `0xfffffff00aa654c4`, `wfi`).
`SendTimeToDevice` (`0xfffffff00a143d74`) returns immediately unless a
real IORTC exists (`0xfffffff00a143d9c`), so the PV-RTC boots never issued
it; with the native driver it runs during controller start and its failure
stops the start before `AllocateNodes`.  The ANS model now completes tunnel
sub-command 6 (args+0xc) with status 0 at args+0x4c and still refuses the
others; see `darwin_ans.c`.

### RTC_NATIVE_SYS2 / SYS3: the real stall is the SART power gate

With SET_TIME accepted (SYS2 first read the argument block from `prp1`,
which the SQE does not carry; SYS3 reads it from `cdw12:cdw11`, dumped in
`RTC_NATIVE_RESTORE5`) the boot still stopped after
`DetermineNamespaces`, and so did every 1-CPU restore boot with the native
RTC: none of them ever printed `GetNVRAMSize`/`AllocateNodes`, which the
md0 root simply hid.  `tools/re/stall_postmortem.py --kext IONVMeFamily
--kext AppleSART` on the frozen SYS3 found the start thread asleep:

```text
IONVMeFamily+0xb1fc <- +0x1b2c <- +0x270cc <- +0x2360 <- +0x4432c (SendTimeToDevice, after
  the power-assertion release at 0xfffffff00a143e98) <- kernel PM <- AppleSART+0xaa8
```

`AppleSART+0xaa8` is `IOCoastGuardSARTMapper`'s last-release path
(`0xfffffff0094e7ef8-0xfffffff0094e7f1c`): write 1 to `reg[2] +
sart-power-reg-offset (0x13e8)`, IOSleep 100 ms, repeat until it reads 1;
power-up writes 0 and waits for 0 (`0xfffffff0094e7c24-0xfffffff0094e7c44`).
`sart-ans` reg[2] is the ANS model's NVMMU window, and 0x13e8 is below its
NVMe-register alias, so the word never read back.  `SendTimeToDevice`
(`0xfffffff00a143d74`) takes a power assertion at entry (vtable +0x598) and
drops it at exit (+0x5a0, return address `0xfffffff00a143e9c` in the
stack); with a native IORTC that release happens during controller start
and lands on the poll.  `darwin_ans.c` now keeps that word as storage
(`sart-power-reg-offset` from the tree; vmstate subsection so older
snapshots still load).

### RTC_NATIVE_SYS4 (same, with the power word modelled): pass

`tools/warm_boot_probe.py /tmp/dvm/RTC_NATIVE1/warm-manifest4.json --tag
RTC_NATIVE_SYS4 --seconds 600 --keep-paused`; no debugger, fresh child,
`-smp 6`, `DISPLAY_SMP6.bootkc` (SMP patch only), `DARWIN_RTC_PV=0`.

| milestone | value |
| --- | --- |
| `AppleARMRTC publishing service` | serial line 257 |
| SART power gate word | `<- 0x0` then `<- 0x1` (stderr 304, 306), poll satisfied |
| `Early boot complete` | 11.006 s |
| launchd first line | `2026-09-05 23:52:07` UTC, host 23:52 UTC |
| `BSD root`, `GetNVRAMSize`, `AllocateNodes` | present (serial 316-346) |
| presentations / panics at 600 s | 171 / 0 (first at about 500 s) |
| `final.png` | lock screen, `Sat Sep 5`, `5:02` (Pacific; host 00:02 UTC Sep 6) |

The large clock text comes from the persisted software-clock patch already
on this disk (`lockscreen-poster-time.md`); the date and time it shows are
the native RTC's.

### Snapshot round trip

`tools/create_checkpoint.py` on the paused SYS4 produced
`/tmp/dvm/checkpoints/RTC_NATIVE_LOCKSCREEN1/manifest.json` (vmstate
3,123,701,160 bytes, migration 6.1 s) with the darwin-spmi and darwin-pmu
sections included.  `tools/restore_checkpoint.py ... --tag
RTC_NATIVE_RESTORE_CK1 --display none` loaded it into a new QEMU
(`migration completed`, exact PC), and after `cont` the guest ran:
user-space PCs on two CPUs and 210 `iomfb: presented` lines in 20 s, zero
panics.  The restore tool's serial-based witnesses stay false because a
lock screen writes nothing to the console.  This checkpoint is the
diagnostic baseline for RTC work; the older PV-RTC checkpoints cannot be
used with `-enable spmi`.

### RTC_NATIVE_CONTROL1 (unchanged PV configuration on this binary)

Same parent, `CLOCK_SOFTWARE_PATCH1` manifest with only the QEMU binary
replaced (PV register, `DISPLAY_SMP6.rtc-ns.bootkc`, old tree): `Early
boot complete` 10.792 s, launchd `2026-09-05 23:53:42` UTC, zero panics,
zero presentations in its 240 s window (earlier fresh boots of this
lineage presented first at 117-204 s or not within 300 s, so the window is
inconclusive for display).  The ANS changes did not trigger: no SET_TIME
tunnel and no SART power-gate write appear without a native IORTC.

## Limitations and deferred items

- Alarm/wake: modelled from Inferno with this tree's registers, not
  exercised by the guest; no IRQ from the PMU has been observed.
- Every non-RTC PMU register is storage.  The driver accepted that for
  every read it made, but fault log, LPM, boot-stage and shutdown paths
  return zeros; `IOPMURequestSystemReset` and the power-off path are not
  modelled.
- The PMU interrupt bank is wired but the PMU model never raises anything
  except the (untested) alarm.
- `DARWIN_SPMI_DEBUG=1` logs every queue register access and transaction
  and is far too verbose for a UI boot; `DARWIN_PMU_DEBUG=1` logs only
  slave transfers and is fine to leave on.
