# Native kernel boot under the HVF bridge — September 6, 2026

Continuation of [hvf-sptm-bootstrap.md](hvf-sptm-bootstrap.md). The
ledger-adapted kernelcache now executes natively from the GEXIT at
`0xfffffff02b354000` through IOKit start-up to mounting the restore ramdisk
as the root filesystem. This is the first native XNU execution on the
virtual-EL2 bridge. It is single-vCPU, slower than TCG in this kernel-heavy
phase, and not yet at the restore shell; numbers are at the end.

## Kernel ledger

`tools/perf/native_sptm_patch.py --segments --native-zva --exclude-kext
corecrypto --ledger-in <sptm ledger>` patches the kernelcache fileset:

- `--segments` scans whole `__TEXT_EXEC`/`__TEXT_BOOT_EXEC` segments; the
  fileset's top-level segments carry no instruction-section attributes.
  13,180 sites, 1,743 ledger words shared with SPTM (`/tmp/dvm/HVF_KC3.*`).
- `--native-zva` leaves `DC ZVA` native. The kernel zeroes pages one
  64-byte line at a time (XNU `0xfffffff02b34b348`); a trap per line put a
  30 s budget entirely into zeroing (`HVF_KC_BOOT9`). Native zeroing is an
  ordinary store as far as alias rights go.
- `--exclude-kext corecrypto` leaves the corecrypto kext unpatched. Its
  FIPS power-on self-test hashes the kext text; patched, the kernel panics
  `"FIPS Kernel POST Failed (-2074)!" @corecrypto_kext.c:362`
  (`HVF_KC_BOOT17`, the first native kernel panic text on serial). Its 23
  privileged sites are DIT toggles and two ID-register reads, which execute
  or trap natively; `hvf_vsh_code_safe()` admits those classes.
- `--ledger-in` preserves the SPTM ledger's indices so one ledger serves
  both images (`native_virtual_boot.py --bootkc`).

## Bridge additions, in the order the kernel needed them

| Stop | Fix |
| --- | --- |
| `FPCR` write not on the allowlist (`HVF_KC_BOOT1`) | Storage policy: any register not owned by a module is stored through its callback; only a live-regime HCR change and HCRX stop. MAIR/AMAIR changes discard aliases. |
| Kernel PSTATE ops | Full MSR-immediate set (PAN, UAO, DIT, SSBS, TCO, DAIFSet/Clr, SPSel), all mirrored to the physical PSTATE. |
| `RDSVL` after `SMCR` write (`HVF_KC_BOOT5`) | CPACR ZEN/SMEN and SMCR_EL1 mirror the guest's decisions; SME state goes through the common put. |
| `AT S1E1R` (`HVF_KC_BOOT6/7`) | AT registers defined under HVF; TCG's `ats_write64` walks through the soft TLB and segfaults, so `hvf_virtual_at()` walks with the explicit-I/O walker and formats PAR_EL1 itself. |
| PMC0/PMC1 reads with PMCR0 enabled (`HVF_KC_BOOT8`) | Active counters report virtual-clock-derived counts. |
| Stale registers after a masked interrupt (`HVF_KC_BOOT12`) | `hvf_inject_interrupts` peeks at the hardware PSTATE; it never leaves a synchronized env unflushed before `hv_vcpu_run`. |
| Instruction permission fault on a data alias (`HVF_KC_BOOT14`) | `hvf_vsh_upgrade_exec()`: grants execute to an existing alias after a fresh walk. |
| Kernel trap opcode `0xe7ffdeff` (`HVF_KC_BOOT16`) | Same-level UNDEF is emulated if it is an unledgered system instruction, else delivered to the kernel's vector. |
| Alias thrash on every guarded call (`HVF_KC_BOOT19`) | Four alias contexts (EL2, EL0, GL2, GL0) selected on resume; see below. |
| Stage-2 write fault after revocation (`HVF_KC_BOOT21`) | Write revocation rewrites the stage-1 leaf in every context. |
| `CASL` on a page-table word (`HVF_KC_BOOT22`) | Emulated stores: STR/STLR/CAS in all four sizes on table and code pages. |
| Store to a freed code page (`HVF_KC_BOOT25`) | `hvf_vsh_grant_write()`: executable aliases of the backing are demoted, the alias regains write. |
| Store to a freed table page (`HVF_KC_BOOT26`) | The dependency list never retired entries; a granted write to one restarts aliases and dependencies, with a repeat guard for live tables. |
| `SPRR_UPERM_EL0` after `UMPRR` programmed (`HVF_KC_BOOT28`) | UMPRR stored like PMPRR. |

Also new: guest fault delivery (translation, permission and access-flag
faults from EL2 and EL0 become the kernel's own aborts with long-format
FSC), virtual IRQ/FIQ delivery through `arm_cpu_do_interrupt`, WFI halting
on QEMU's timers, TPIDR_EL0/TPIDRRO_EL0/TPIDR_EL1 mirrored to the physical
registers (user code reads them natively), the hardware virtual counter
pinned to QEMU's clock on every resume, and fast reads for TPIDR_EL1.

## Alias contexts

Guarded and ordinary execution share the guest's translation tables but not
its SPRR permission bank, and ARM's AP[2:1] cannot express the asymmetric
combinations SPRR allows (a page writable at GL0 but read-only at GL2
stopped TXM in `HVF_KC_BOOT20` when both shared a leaf). The shadow now
keeps one private table tree and alias set per (world, level): EL2, EL0,
GL2, GL0, each a quarter of the table pool and alias window.
`hvf_vsh_select()` picks the context from CURRENTG and the current level on
every resume, so GENTER/GEXIT, ERET and exception entry switch trees
instead of discarding aliases. Table-page dependencies are shared; a
guest-visible translation change (`hvf_vsh_invalidate_all`) restarts all
four. Effect: `HVF_KC_BOOT23` reached in 3 s the point `HVF_KC_BOOT18`
needed 60 s for.

Latent hole closed by the same change: the old design cleared the
dependency list on every transition, so SPTM's PTE updates could hit a
writable alias natively. Dependencies now persist, which is what exposed
the CAS and freed-page cases above.

## Result

Executable SHA-256 `457437cce910f5f6…` (`/tmp/dvm/HVF_KC_FINAL.sha256`):

- `HVF_KC_BOOT32`, 600 s budget with `QUIET=1 FASTREAD=1`: 959 serial
  lines, zero panics, the restore ramdisk mounted as root
  (`apfs_log_op_with_proc: md0s1 mount-complete volume ramdisk`), then the
  first user process running at EL0 (`HVF_KC_BOOT30` froze at
  `0x1049cdb2c`). The counters kept moving for the whole budget: 160,022
  guarded calls, 156,361 FIQs, 584 SVCs, 236 data aborts. No shell prompt:
  TCG reaches `can't access tty` at serial line 309 in about 45 s; the
  native run printed nothing after the mount and delivered no AIC IRQ at
  all, against 40 in TCG's 60 s (gxfstat `e5`). That is the next stop.
- Rate: about 750 guarded calls per second natively against 12,000 under
  TCG in the same phase, so roughly 15x slower during kernel boot, as the
  ceiling projection predicted for this regime.
- `HVF_KC_BOOT31`, SPTM only: the [hvf-sptm-bootstrap.md](hvf-sptm-bootstrap.md)
  result repeats on this binary (kernel entry, five table hashes, 172
  registers equal to the TCG snapshot).
- `HVF_KC_GXF_TEST3`: native GXF/permission matrix 117/117. The runner now
  sets `QEMU_HVF_VIRTUAL_STOP_ON_FAULT=1` so denied accesses stop at the
  faulting instruction as the fixtures expect instead of being delivered
  as guest aborts, and it expects PAN-set and PMCR0 activation to complete.
- `HVF_KC_TCG2`: 60-second TCG restore-ramdisk baseline reaches the shell,
  311 serial lines, zero panics.
- Host suite 75/75; checkpatch clean on the five changed HVF headers.

`HVF_KC_BOOT1..30` are the iteration record; each intermediate binary is
superseded.

## What is still open

- ROOT CAUSE of the post-mount stall was a DEVICE-TREE CONFIG BUG, not the
  bridge. `NATIVE_DRAM_LOW1.dtree` was built with no `-enable` flags, so
  `dt_fixup.py`'s `del_compat` stripped `compatible` from every node except
  `aic`/`uart`. With `aapl,spmi` gone from `nub-spmi0`, `darwin_spmi_create`
  returned NULL, the SPMI/PMU model was never created, and
  `AppleDialogSPMIPMU` polled the unmodelled controller (PA 0x385200000,
  read as 0 by `darwin-unimp`) forever. Fixed by booting a tree that keeps
  the SPMI compatible: `native_dram_tree.py --input firmware/dtree
  --base 0x800000000` (firmware/dtree is already dt_fixup-patched and keeps
  it). With it (`HVF_KC_SPMI1/2`): the SPMI model is created, the FIRST
  native AIC interrupt is delivered, and the boot runs `Primary PMU
  detected`, `AppleARMRTC started`, the RTC tick, `CoreAnalyticsHub`, and
  reaches `RTBuddy(SMC): start`. The proper fix is to rebuild the native
  tree from the raw IPSW DeviceTree with `-enable spmi` (plus smc/sep) and
  rebase; `firmware/dtree` is already patched so `dt_fixup` refuses it.
- Next stall: the SMC coprocessor RTKit handshake over the ASC mailbox.
  The guest writes the power-on doorbell (`asc(SMC) 0x08114 <- 0x20001`)
  then waits; the model must send HELLO and raise its AIC interrupt as it
  does under TCG. Same coprocessor-bringup class as SPMI.
- Reaching the restore shell natively and then `Early boot complete` on
  the system volume. The blocker is a stall in IOKit driver start-up, not
  interrupt delivery: `HVF_KC_BOOT33` with `DARWIN_AIC_DEBUG=1` shows the
  kernel programming the AIC but no device ever raising a line, because
  the drivers that raise them never start. TCG's 60 s baseline delivers
  40 IRQs, all from SPMI (`nub-spmi0`, irq `0x1e7`) and the SMC mailbox
  (`0x245..0x248`); natively `AppleDialogSPMIPMU`, `AppleARMRTC`,
  `PMU-LPM` and `RTBuddy(SMC)` never print, `asc(SMC)` traffic is zero,
  and `AppleSEPManager` wait messages repeat about three times per second
  (911 in 300 s against 48 in TCG's 60 s). The user process the kernel
  started spins at one library offset (`...db2c`) for the rest of the
  run (`HVF_KC_BOOT35`, 36 monitor samples). The first divergence on
  serial is AMFI asking the credential manager for developer-mode status
  before `AppleSEPKeyStore` has started, where TCG takes the
  "protected data is not available" branch; disabling AMFI with
  `amfi_get_out_of_my_way=1` (`HVF_KC_BOOT36`, `--bootargs`) does not
  change the outcome, so the ordering is a symptom. Next step: freeze a
  native run with `probe.sh --keep` and use the RAM post-mortem thread
  scan to see what every kernel thread is blocked on.
- Timer delivery works: `HVF_KC_BOOT29` delivered 105,698 FIQs, 248 SVCs
  and 235 data aborts into the kernel's vectors in 150 s (gxfstat exception
  counters). IRQ from the AIC has not been exercised yet.
- Multicore, snapshots, DMA invalidation from the ANS model.
- Performance: about 15x slower than TCG in this phase. Measured lever
  test (2026-09-06): deferring the per-alias hv_vm_unmap syscalls and
  scoping the invalidation memset (HVF_KC_OPT1) left the guarded-call
  rate unchanged (9,631 vs 9,555 genters in the first 10 wall-seconds).
  The bridge is exit-bound, not host-overhead-bound: this run took 15.6
  million system-register reads (~40,000/s), each a guest exit. Boot-time
  speedup would require eliminating traps (rewriting XNU register reads to
  non-trapping storage), whose ceiling for this trap-dense phase is low
  (docs/re/hvf-fastpath-ceiling.md). The optimisation is kept because it
  cuts host syscalls with no guest-visible change (matrix 117/117), not as
  a speedup. Diagnostic knobs added this session, all default-off:
  QEMU_HVF_VIRTUAL_NO_VIRQ (skip interrupt delivery),
  QEMU_HVF_VIRTUAL_TIME_DIV (slow the emulated counter),
  QEMU_HVF_VIRTUAL_STOP_ON_FAULT (matrix fault semantics).

## Reproduce

```sh
python3 tools/perf/native_sptm_patch.py --input firmware/bootkc \
  --output /tmp/dvm/UNIQUE_KC.macho --virtual-el2 --segments --native-zva \
  --exclude-kext corecrypto --ledger-in /tmp/dvm/HVF_GXF_SPTM8.ledger \
  --ledger /tmp/dvm/UNIQUE_KC.ledger > /tmp/dvm/UNIQUE_KC.json
python3 tools/perf/native_virtual_boot.py --tag UNIQUE_BOOT \
  --qemu qemu-sptm/build/qemu-system-aarch64 --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --sptm /tmp/dvm/HVF_GXF_SPTM8.macho --ledger /tmp/dvm/UNIQUE_KC.ledger \
  --bootkc /tmp/dvm/UNIQUE_KC.macho --shadow --seconds 300 \
  --bridge-env QEMU_HVF_VIRTUAL_QUIET=1 --bridge-env QEMU_HVF_VIRTUAL_FASTREAD=1
```

Drop the QUIET knob for the full per-operation log; a 25 s run produces
about 700,000 lines.
