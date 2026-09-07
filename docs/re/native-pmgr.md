# Native PMGR: ApplePMGR starts, and it boots the secondary CPUs itself

2026-09-06. `dt_fixup.py -enable pmgr` plus the `darwin-pmgr` power-state
register model let the **stock, unpatched kernelcache** start
`AppleT8140PMGR`, and ApplePMGR then releases the five secondary CPUs
through the real PMGR CPU-start bitmap. The restore ramdisk reaches its
shell with six vCPUs and zero panics without `DARWIN_SMP_PV` or the
hash-pinned kernel adapter from `docs/re/multicpu.md`; the native-SMC system
disk boots to `Early boot complete` in 8.8 s and renders frames with zero
panics (`SYS_PMGR1`). Every gate below was found by booting, reading the
first panic, and reading the kext at the address it named.

Motivation was the DCP sleep/reboot loop (`docs/re/tcg-idle-profile.md`):
IOMobileFramebuffer logs `IOMFB AP: use_psd_dcp_power2: 0`, and the string
table ties PSD to PMGR (`ApplePMGRFunctionEnablePSDService`,
`ApplePMGRv2::_enablePSDService`). **That hypothesis was wrong for this
product**: the value comes from `UnifiedPipeline2` vtable slot `0x8e0`
(`AppleMobileDispH17P-DCP+0x10788`), which asks a per-platform traits object
(`this+0x5e88`, vtable `0xfffffff008089f98`) for slot `0x78`, and on
iPhone17,3 that method is a bare `mov w0, #0; ret`
(`AppleMobileDispH17P-DCP+0x3288`, captured live with
`tools/re/gdb_bp_dump.py` at runtime `0xfffffff029189a9c/aa0`, `SYS_BP2`).
So the RTKit sleep path is this device's real DCP power path, PMGR or not.
The PMGR work stands on its own merits: native SMP and one fewer guest patch.

## What the driver needed, in the order it asked

| Probe | First panic or stall | Cause and fix |
|---|---|---|
| `SMP_PMGR2/4` (earlier) | waits on `function-mcc_ctrl`; then `voltage-states1 not found` | provider absent; `fixup_pmgr` drops the property |
| `PMGR1` | `voltage-states5 not found` (`ApplePMGR.cpp:1450`) | `initDriver` wants one table per `perf-domains` entry (28-byte entries, byte 0 = domain id: SOC 0, ECPU 1, DCS 2, PCPU 5, ANE 8, AVE 10, DISP 11) |
| `PMGR2` | `initDriver:2002 "ECPU_0 1 request ECPU.0 > 0"` | the IPSW placeholder tables (`{0,1,2}` as u64) parse as zero states; the parser at unslid `0xfffffff009231d2c-0x92324e4` counts 8-byte entries until the first zero word |
| `PMGR4` | stall after `AppleSMCEmbedded::setPowerState`, PC in `hw_wait_while_equals` | ApplePMGR wrote 1 to PS-window offsets `0x60400/0x60800/0x60c00/0x61000` and `0x80000000` to `0x24060/0x24068` and read them back; the model now stores unlisted registers like memory |
| `PMGR5` | same stall, `ApplePMGR: Started` printed | with one vCPU the kernel now waits for CPU 1 forever: with IOPMGR present `processor_boot()` runs the real path |
| `PMGR6SMP` (`-smp 6`) | `cpu 1 failed to boot for the first time` | the PMGR PS window (priority 0) shadowed `darwin-smp`'s CPU-start bitmap at PMGR+0x34000; the window is now priority −500 |
| `PMGR7SMP` | same, but `darwin-smp: start CPU 1..5 result=0` | the native bitmap path skipped `apple_regs_pv_cpu_handoff()` (CTRR map + FP for SPTM's secondary entry); `start_write()` now does it from `current_cpu` |
| `PMGR8SMP` | data abort in `AppleT8140CLPC+0x32e70`, index −1 into an empty table | the CLPC counts per-state `fANE0` entries; ANE has none |
| `PMGR9SMP` | same | replacing every placeholder table did not help; the kext reads an `ane-disabled` u32 property (`AppleT8140CLPC+0x301d8..0x30224`, via `getBytesNoCopy`, or the boot-arg) |
| `PMGR10SMP` | data abort at `+0x3020c` | a NULL-length property faults; it must be `u32:1` |
| `PMGR11SMP` | `getThermalUPOPerfLimiting:14663 REQUIRE failed: frequencyMHzRequested` | live dump (`SYS`-style breakpoint at `0xfffffff0292622ac`): the PCPU domain had 4 states but `perfStateFrequenciesMHz` was all zero |
| `PMGR14SMP` | same | the fill loop at `0xfffffff009232414`: for entries whose byte 2 is 1 (CPU clusters) MHz = `0x3e80000 / word0` (`udiv`), for others MHz = `word0 * 0x431bde83 >> 50` (Hz). CPU tables must hold `65,536,000 / MHz` |
| `PMGR15SMP` | **reached shell: yes**, zero panics | |

The stand-in tables are four states, 600/1000/1500/2000 MHz at 700–1000 mV.
No frequency here is measured; they exist so the parsers see well-formed
data. Real iBoot fills these from fused SoC data.

## The register model

`qemu-sptm/hw/arm/darwin_pmgr.c`, from the `devices` table (m1n1
`struct pmgr_device`, 48 bytes: flags at +0, `group:8|offset:24` at +16,
name at +32) and `ps-groups` (u32 triples, first word = `reg` window index):
138 power-state registers in two windows, `0xf0700000+0xcc000` and
`0xf8280000+0x80000`. Bit layout from Linux
`drivers/pmdomain/apple/pmgr-pwrstate.c`: TARGET [3:0], ACTUAL [7:4]
(follows TARGET at the write), sticky WAS_PWRGATED/CLKGATED [9:8] as
write-1-to-clear, RESET bit 31. All listed devices start ACTIVE. Unlisted
offsets in the windows behave as memory. Migration carries both.
`DARWIN_PMGR_DEBUG=1` logs accesses; `tools/re/pmgr_devices.py` prints the
decode. Other PMGR windows stay on darwin-unimp; during boot the driver also
touches `pmgr[43]+0x8ac000..` and `pmgr[5]/[6]/[14]/[15]` (seen with
`DARWIN_UNIMP_DEBUG=1` in `PMGR4U`) without needing anything from them.

## Tools added for this loop

- `tools/re/gdb_bp_dump.py PORT --pc ADDR --mem 'x1+0x50:*+0x98:*:0x40'`:
  stop at runtime PCs over the gdbstub, print all registers, follow pointer
  chains into guest memory.
- `tools/re/manifest_probe.sh MANIFEST TAG SECS [qemu args]`: run
  `probe.sh` with a warm-disk manifest's inputs, env and a fresh child,
  e.g. `-S -gdb tcp:127.0.0.1:4713` for the system boot.
- `tools/re/kstrrefs.py LO HI`: strings a code range materialises with
  ADRP+ADD (the inverse of `kaddr.py`); finds the property names a kext reads.
- `tools/re/pmgr_devices.py`: decode the PMGR device table.

## Status and what is not done

- The restore boot with `-enable pmgr` needs `-smp 6 -accel tcg,thread=multi`;
  with one vCPU the kernel waits for CPU 1 (`PMGR5`). `run.sh --restore`
  does not pass `-enable pmgr` yet.
- The system-disk boot with the stock kernel and no `DARWIN_SMP_PV`
  (`SYS_PMGR1`, manifest variant) reaches frames with zero panics; it has
  not replaced the package's pinned kernel/manifest, and its first frame
  came later (≤210 s) in that single run than the package's typical
  100–160 s; repeat before drawing conclusions.
- Guest perf-state control now runs against stand-in tables; CLPC/thermal
  telemetry values are fiction. Nothing reads them back into the model.
- The DCP sleep loop is unchanged; see the lead in `tcg-idle-profile.md`
  on the IOMFB idle detectors' runtime properties.
