# Integrated virtual EL2 and native SPTM MMU handoff

**Latest continuation:** [guarded execution and cache maintenance](hvf-gxf-bootstrap.md).
Actual boot now passes GXF entry, guarded permission setup and cache
maintenance. The older stopping points and validation inventory below remain
historical evidence.

September 5, 2026. This supersedes the actual-SPTM stopping points in
`hvf-native-progress.md`, `hvf-virtual-el-context.md`, and
`hvf-sptm-shadow-handoff.md`. Their earlier direct-EL2 and standalone test
results remain historical evidence. Full iOS `Early boot` is not reached.

## September 5 continuation: counters and the SIMD boundary

The counter stop described below is now passed. `virtual-counter.h` supplies
one 24 MHz QEMU virtual clock for ordinary and Apple counter reads in the
initial zero-offset, disabled-timer context. Redirection writes are accepted
only for that common-clock state and the observed values 0/3. This does not
implement independently offset Apple banks or timer interrupt delivery.

The first fixture stopped on `CNTVCTSS_EL0` because the HVF feature profile
does not register ECV counter aliases. The bridge now resolves the two
self-synchronizing read encodings through their ordinary counter access
checks; `helper.c:gen_timer_ecv_cp_reginfo` gives both forms identical access
and read callbacks. It does not advertise full ECV or allow SS writes.

`/tmp/dvm/HVF_VCOUNTER_TEST2/results.json` passes 4/4 cases: measured rate
23,636,184 ticks/second against host elapsed time, only 1,968 ticks across a
100 ms stopped-VM interval and brief resumed execution, nonzero ARM/Apple
offsets rejected before reads, and the SS write rejected at its own PC.
Binary SHA-256: `1649d955fefd640844e7ae706893f9f02ba0bd2c2ca17699a010d3761e532b40`.

`HVF_VCOUNTER_BOOT2` completes both redirection writes, reads the timestamp
at `0xfffffff0070b25a0`, stores it at `0xfffffff0071005c8`, and executes more
initialization. It then stops on `LDNP q2,q3,[x1]` at
`0xfffffff0070a3f84`, native ESR `0x1fe00000` (FP/SIMD access trap). All five
source table hashes still match the TCG capture.

The machine reset already enables the virtual FPU (`darwin.c:95-96`), but
the virtual-EL2 register path had not set physical EL1's CPACR. That path now
sets physical FPEN from `fp_exception_el(env, 2)`, preserving the actual
virtual CPTR/VHE trap decision. Physical EL0/SVE/SME remain disabled.
`HVF_VFP_BOOT1` passes the native SIMD copy and later reaches the first
`DC ZVA,x3` at `0xfffffff0070a3be4`; source tables still match. This is not
by itself proof of all FP permission cases; the dedicated real-SPTM
fixture below supplies that evidence.

That fixture exposed a separate debugger-state leak: a hardware breakpoint
set virtual MDSCR.MDE, changing SPTM's read/modify/write at `0x8070a3784..378c`
from 0x1000 to 0x9000. Virtual mode now keeps host debugger MDSCR settings
separate from the guest register bank. `HVF_VFP_TEST1` is the recorded failed
test before that fix, not a passing FP permission matrix.

`/tmp/dvm/HVF_VFP_TEST5/results.json` then passes all four VHE FPEN values:
0/2 stop on the same LDNP with both vector registers unchanged; 1/3 reach
the following instruction with exactly the 32 source bytes in q2/q3. The
fixture writes CPTR through a real adapted guest instruction before entering
unmodified SPTM, and verifies readback. GDB system-register writes cannot
set up this test: `arm_gdb_set_sysreg()` is a no-op even when the protocol
acknowledges the packet. Failed fixtures TEST2/3 concern GDB feature XML;
TEST4 exposed the ignored register writes. Binary SHA-256 for TEST5 is
`b073b1ecc75d787e08fb51cacb6fd3dde308d98bc24ffe859c8d5859f3c04681`.

The next DC ZVA implementation uses the guest translation/alias permissions
and writes only normal RAM through QEMU's address space. It refuses code,
table dependencies and read-only backing; it does not make an alias writable
to complete zeroing. `HVF_VZVA_TEST2` catches a separate CPU-profile issue:
the HVF feature record leaves DCZID.BS=0, and the first implementation zeros
four bytes. The SPTM loop at `0xfffffff0070a3be4..3bf0` advances 64 bytes.
The virtual platform now sets BS=4 for both the emulated operation and DCZID
reads. `/tmp/dvm/HVF_VZVA_TEST3/results.json` passes all five cases: existing
and fresh data pages change exactly 64 bytes with 16-byte nonzero guards on
both sides intact, including an unaligned input pointer. Code, table and
unmapped targets stop at the same DC ZVA. The code page and all five source
tables retain their hashes in every case. TEST2 remains a recorded failure.

### Latest actual boot and next protection boundary

`HVF_VZVA_BOOT2` passes the zeroing routine and stops before the first
`CTRR_C_LWR_EL2` write at `0xfffffff0070bba90`. It installs 58 checked aliases
and all five source table hashes still match. HCR_EL2 is `0x488000000` and
CPTR_EL2 is `0x300000`. There is still no serial output or `Early boot`.

The original instruction is `MSR S3_0_C11_C0_0,x8` (`0xd518b008`), redirected
from the EL1 encoding under VHE. The routine writes CTRR C/D lower/upper
bounds followed by CTXR A/B/C/D bounds, then `TLBI VMALLE1NXS` at
`0xfffffff0070bbaf0`. That protection/invalidation path is not implemented.
Do not just add these registers to the post-MMU write allowlist.

Initial protections also require an audit: `hw/arm/apple_regs.c:235-275`
prepopulates locked CTRR A/B and ACC CTRR/CTXR control values before boot.
`scripts/darwin/dumpregs.py` generates plain storage accessors for these
registers, and the ARM walker does not contain CTRR/CTXR enforcement. The
shadow implementation's verified page permissions and immutable code/table
aliases are not evidence of full Apple range protection. Future work must
enforce both initial protection state and later changes, with overlap,
granularity, lock and alias-revocation tests.

For reference, the inspected M5 field dump names CTRR_C_CTL_EL2 bit 0
WRPROTECT and bit 63 LOCK, and bound fields [41:12]. Its CTXR controls need
separate interpretation. The older
[Asahi CTRR documentation](https://asahilinux.org/docs/hw/cpu/system-registers/#ctrr-registers)
describes a different combined A/B control layout; do not transplant those
bit positions into this firmware's separate C/D/CTXR registers.

### Latest binary validation

Build `/tmp/dvm/HVF_VZVA_BUILD2.log`, SHA-256
`b3ef4357e55508e9492ebf34e7a26346de82bcfa57a1daae97efdad72eb19991`:

- `HVF_VZVA_TEST3`: exact zeroing/protection matrix 5/5.
- `HVF_VZVA_COUNTER2`: counter positive/negative matrix 4/4.
- `HVF_VZVA_FP2`: actual SPTM FP permission matrix 4/4.
- `HVF_VZVA_HCR2`: native HCR/RO/NX controls 3/3.
- `HVF_VZVA_PTW2`: explicit walker controls 48/48.
- `HVF_VZVA_DENY2`: removing initial execute permission still faults at
  `0x8070a3744`, ESR `0x8600000f`; all five source tables match.
- `HVF_VZVA_TCG2`: 60-second TCG ramdisk boot reaches the shell, 303 serial
  lines, zero panics, final PC `0xfffffff02ab218bc`.
- Host suite 23/23; Python compilation, diff whitespace checks and new-header
  checkpatch pass.

All owned probes/builds have exited. No display process or shared ownership
file was changed. TCG remains the default/fallback; Windows was not tested
on this Mac. Counter and zeroing semantics use QEMU CPU/clock/address-space
primitives, while native permission/register installation remains HVF-specific.

These changes remain uncommitted in the same worktree. The older build and
test inventory below remains historical evidence, not validation of every
new change.

## Actual execution milestone

`/tmp/dvm/HVF_VSH_BOOT4.virtual.json` and the corresponding run log record
adapted SPTM executing under HVF, with virtual EL2 implemented at hardware EL1:

1. Initial Apple register setup, including SPRR configuration/permission
   writes, completes through the existing architectural register model.
2. The first SCTLR.M write at physical `0x8070a3740` installs a private native
   translation context. Guest SCTLR is `0x12001010fc14793d`.
3. Ordinary bootstrap instructions execute with the native MMU enabled.
   VBAR/TPIDR setup and the observed restricted APL_INTENABLE/MDSCR/ACFG
   writes complete.
4. The return into high VA `0xfffffff0070b2568` causes a native stage-1
   translation fault, ESR `0x86000005`. The software walker permits the fetch,
   and an RX alias of PA `0x8070b0000` is installed. Execution resumes.
5. The stack store at `0xfffffff0070b2570` faults at
   `0xfffffff007123fa0`, ESR `0x96000047`. The walker permits a write and an
   RW, NX alias of PA `0x807120000` is installed. The store then completes.
6. Execution stops before `AGTCNTRDIR_EL2=3` at `0xfffffff0070b2594`.
   Counter redirection is not implemented by merely accepting this write.

All five source table pages match the earlier TCG checkpoint byte-for-byte.
This is actual SPTM instruction execution, including native stack stores;
it is no longer just replaying captured tables through a standalone read.
There is still zero serial output and no userspace boot or speedup result.

The guest VBAR value becomes `0xffffffe800000000`: its earlier EL2 bank was
zero before relocation. The TCG pre-MMU capture also reports that bank as
zero. Do not silently replace it with a plausible vector address. Actual
guest exception delivery and this initialization sequence need investigation.

## Code and execution contract

- `qemu-sptm/target/arm/hvf/virtual-el2.h` loads a host instruction ledger and
  dispatches adapted system operations into QEMU's existing CPU state. It
  follows VHE register-bank redirection and validates access callbacks.
  Virtual VH/HCX are software ISA features; HVF feature registers are unchanged.
- `hvf.c` maps virtual EL2 state to physical EL1 while retaining separate
  software system registers and stack banks. Architectural PAC keys are
  mirrored for instructions that execute natively. A distinct key-switch
  control is still needed; getting past PACIBSP alone does not prove every
  Apple authentication mode.
- `virtual-shadow.h` owns private 16 KiB low/high page tables, RX vector/TLB
  helper code, and checked RAM aliases. It uses the ordinary explicit-I/O ARM
  walker with the actual virtual EL2 translation regime. It accepts normal
  RAM with suitable mapping extent and refuses unsupported attributes.
- Native stage-1 translation faults enter a private vector containing only
  SMC. ESR/ELR/FAR/SPSR are captured, and the interrupted virtual PC, flags,
  and stack selection are restored. A successful walk can fill a missing
  alias and retry the original instruction. Guest permission faults stop;
  they are not retried with broader permissions or turned into zero reads.
- Newly discovered table dependencies lose write permission through existing
  aliases. Executable backing is also read-only through every installed
  alias. Unadapted system/state-changing instructions cause executable-page
  validation to fail. This can conservatively reject instruction-like data
  within a page; it is not a general code-discovery algorithm.
- A native TLB helper invalidates the physical EL1 context before installing
  new tables. It validates the exact terminal SMC/PC and preserves registers,
  debug state, and timer masking. Mappings persist between ordinary exits.
  Native TCR retains the guest's low/high VA widths and TBI/TBID controls
  because they also determine pointer-authentication masks. The shadow tree
  supports three-level 16 KiB layouts with 39..47 address bits, and retains
  the walker's BTI guarded-page attribute. Unsupported layouts stop.
- The patcher now covers non-SPSel PSTATE-immediate instructions as well as
  MRS/MSR/SYS/SYSL, ERET encodings and GENTER/GEXIT. DAIFSet is implemented;
  other newly intercepted PSTATE operations remain explicit stops.

The initial implementation is one vCPU, virtual EL2 only, E2H+TGE, SPRR/GXF
disabled at the MMU handoff. Transitions, later protection changes, table or
code writes, DMA invalidation, MMIO, general guest exception delivery,
multicore, reset and snapshot restoration are unfinished. The mode now blocks
migration/snapshot creation rather than exporting incomplete native state.
TCG snapshot behavior is not changed.

The private vector/helper occupies reserved VA/IPA `0xe00000000`, with
checked guest aliases beginning at IPA `0xe40000000`. QEMU physical-address
collisions are rejected. The private helper is visible at a reserved native
VA; arbitrary guest access to that VA, runtime code integrity, and complete
virtual-address isolation are unresolved. This remains a controlled firmware
experiment, not a hardened general guest execution backend. No claim of full
Apple SPRR/GXF/CTRR enforcement follows from these initial mapping tests.

## Evidence and controls

- `HVF_VSH_BOOT1`: initial execution-page validation catches unadapted
  `DAIFSet` (`0xd5034fdf`) at bootstrap-page offset `0x3004`. The MMU remains
  disabled. The patcher was expanded and the observed operation implemented.
- `HVF_VSH_BOOT2`: native MMU execution reaches VBAR write `0x8070a376c`.
- `HVF_VSH_BOOT3`: VBAR write succeeds; TPIDR write stops at `0x8070a377c`.
- `HVF_VSH_DENY3`: removing execute permission from the initial alias produces
  a native permission fault, ESR `0x8600000f`, ELR/FAR `0x8070a3744`, SPSR
  `0x600003c4`. The guest is left at that interrupted instruction, with
  virtual SCTLR.M set; no success marker or PC advance is fabricated.
- `HVF_VSH_BOOT4`: high-VA fetch and stack store succeed as described above;
  five source-table hashes match `/tmp/dvm/HVF_SPTM_TABLES1/results.json`.

Final build: `/tmp/dvm/HVF_VSH_BUILD7.log`, executable SHA-256
`c3968ccfc42a77c2620279365cf247543f25ba80cba5993eee9d7fd1864b2497`.

- `HVF_VSH_FINAL7`: repeats the high-VA execution/stack-store milestone,
  stopping at `0xfffffff0070b2594`; all five table hashes match.
- `HVF_VSH_DENY7`: repeats the native execute-permission fault at
  `0x8070a3744`; all five table hashes match.
- `HVF_VSH_OFF7`: with shadow mode disabled, retains the deliberate pre-MMU
  stop at `0x8070a3740`, SCTLR_EL2=0; all five table hashes match.
- `/tmp/dvm/HVF_VSH_PTW7/results.json`: 48/48 walker cases pass.
- `/tmp/dvm/HVF_VSH_HCR7/results.json`: 3/3 HCR/RO/NX controls pass.
- `HVF_VSH_TCG7`: full 60-second TCG ramdisk probe reaches the shell,
  303 serial lines, zero panics, final PC `0xfffffff02ab136e0`.
- `/tmp/dvm/HVF_VSH_MIGRATION5.json`: the earlier build with the same
  migration-blocking code rejects `-only-migratable` with the explicit
  virtual-EL2 state error before boot.
- Host-only suite 23/23, Python compilation, shell syntax and whitespace
  checks pass. Both new HVF headers pass checkpatch with zero errors/warnings.

All owned guests have exited. No display instance or protected ownership
file was changed. The parent and nested working changes remain uncommitted
and unpushed.

## Register evidence and next investigation

SPTM disassembly at original VA `0xfffffff0270a376c..3798` shows VBAR,
TPIDR=0, APL_INTENABLE=0, MDSCR=0x1000 and ACFG=0x18 setup. The initial
allowlist accepts only those non-translation writes and values. QEMU's
`target/arm/debug_helper.c` identifies MDSCR.TDCC as an EL0 DCC restriction;
lower virtual EL execution remains unavailable.

The primary reverse-engineering register dump
[M5 register fields](https://gist.github.com/justtryingthingsout/2f5213c5fa7d64e4db7e30caac7f5e9c)
names ACFG[4:3] as cache-operation disable bits and APL_INTENABLE[3:0] as
timer enables. Native cache system instructions are already intercepted and
rejected, and timer-enable writes remain rejected. These names support this
restricted interpretation; they are not a complete verified hardware model.

The next real instruction writes AGTCNTRDIR_EL2=3, followed by an EL12 write
of 3 and CNTVCTSS_EL0 read at original VA `0xfffffff0270b25a0`. The register
dump names the low bits counter-redirection controls. The existing generated
QEMU accessors merely store them, so their presence is not evidence that
counter redirection works. Measure the counter domains/frequencies, offsets,
and access conditions before allowing this sequence. Do not substitute a
random or constant timestamp. `sysctl hw.tbfrequency` on this host reports
24,000,000; that alone does not prove the HVF architectural counter frequency.

The new `tools/perf/native_counter_probe.S/.py` uses the inspected isolated
VM runner in `native_el2_probe.c`, without firmware or counter emulation.
The runner now also reports complete exit syndromes and native host CNTFRQ.
`/tmp/dvm/HVF_VSH_COUNTER2/results.json` records 16 cases:

| Operation | Physical HVF EL1 | Physical HVF EL2 |
|---|---|---|
| CNTFRQ_EL0 read | native, 24,000,000 | native, 24,000,000 |
| CNTVCT_EL0 / CNTVCTSS_EL0 | native nonzero reads | native nonzero reads |
| CNTPCT_EL0 | host sysreg trap | host sysreg trap |
| AGTCNTRDIR read/write | host sysreg trap | guest undefined exception |
| AGTCNTVCTSS_EL0 | host sysreg trap | guest undefined exception |

The host's own CNTFRQ also reads 24,000,000. Six cases complete their native
operations; the other ten stop at the first host or guest exception. These
are capability observations, not ten silently accepted register accesses.
The `ordinary` sequence reads CNTVCT successfully and then traps on CNTPCT;
its incomplete outcome must not be attributed to the first read.

This establishes available native counter primitives and different trap
routing at EL1 versus EL2. It does not prove counter-redirection semantics,
clock-rate agreement over time, offset handling, or timer interrupt delivery.
The real SPTM counter-routing write remains guarded pending that work.

## Reproduce

Use the existing uncommitted checkout
`/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`, branch
`codex/arm-native-experiments` in both repositories. Pulling its committed
branch does not transfer the HVF work or these untracked headers/tools.

```sh
python3 tools/perf/native_sptm_patch.py --input firmware/sptm \
  --output /tmp/dvm/FRESH_SPTM.macho --virtual-el2 \
  --ledger /tmp/dvm/FRESH_SPTM.ledger > /tmp/dvm/FRESH_SPTM.json
python3 tools/perf/native_virtual_boot.py --tag FRESH_BOOT \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --sptm /tmp/dvm/FRESH_SPTM.macho --ledger /tmp/dvm/FRESH_SPTM.ledger \
  --shadow --check-tables /tmp/dvm/HVF_SPTM_TABLES1/results.json
```

Add `--deny-shadow-exec` for the negative control. Omit `--shadow` to retain
the deliberate pre-MMU stop. The runner creates a private random monitor tag,
records the owned PID, captures registers and optional table hashes, then
quits only that instance. Probe return code zero means evidence collection
completed; it does not mean iOS booted.
