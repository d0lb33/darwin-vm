# Native SPTM bootstrap complete through the kernel GEXIT — September 6, 2026

Continuation of [hvf-gxf-bootstrap.md](hvf-gxf-bootstrap.md) and
[hvf-gxf-policy.md](hvf-gxf-policy.md). The real, ledger-adapted SPTM now
executes natively under the virtual-EL2 HVF bridge from reset through its
entire bootstrap, including TXM initialisation at guarded EL0, and stops on
the first instruction of the kernel. Full iOS boot is not reached: the
kernelcache is not ledger-adapted and its first page fails code validation.
No performance claim follows from this note.

Branch `codex/hvf-research-checkpoint` in both repositories, on top of the
merge of `main` at 9ebaf9a / cc18567.

## Result

`HVF_SPTM_BOOT8` (`/tmp/dvm/HVF_SPTM_BOOT8.virtual.json`, probe log
`/tmp/dvm/probe/HVF_SPTM_BOOT8-f28735e66feab035.stderr.log`):

```text
serial lines : 0
xnu panics   : 0
PC           : fffffff02b354000      (kernelcache __TEXT_BOOT_EXEC entry)
Virtual GXF GEXIT pc=0xfffffff00709b004 target=0xfffffff02b354000 pstate=0x3c9 sp=0x0
Virtual shadow unadapted instruction 0xd51bd05f at page offset 0x34
```

The stop is the phase boundary, not a fault: the kernel page holds an
unadapted `MSR`, which `hvf_vsh_code_safe()` refuses for EL2 execution.

The stopped state matches the TCG state captured at the same PC by
`tools/perf/native_sptm_sites.py` (`/tmp/dvm/HVF_SPTM_SITES3/results.json`,
`terminal`): **172 registers identical**, including every general register,
SP banks, PSTATE `0x3c9`, CURRENTG `0`, SPSR/ELR/ASPSR/ESR_GL2, HCR (apart
from RW), SCTLR/TCR/VBAR_EL2, SPRR/GXF banks, CTRR/CTXR controls and
bounds, APCTL, JCTL, MDSCR, AGTCNTRDIR and TPIDR. The only differences are
CPU-model facts: HCR.RW is forced by the AArch64-only host CPU (helper.c
`hcr_write`), HCRX is absent, and the ID/HID registers report the M5 host
rather than TCG `max`. All five bootstrap table pages still hash-match the
TCG capture (`tables_match: true`).

Counts from the same run:

| Event | Count |
| --- | ---: |
| Native aliases installed | 4,302 |
| SVC exceptions delivered from guarded EL0 to GL2 | 15 |
| ERETs from GL2 (1 bootstrap + 14 SVC returns) | 15 |
| Emulated device accesses (AMCC aperture `0x220000000`) | 14 |
| Emulated `DC CIVAC` (logged, then silent) | ~2,000 |
| Emulated page-table stores | 180 |
| GEXIT into the kernel | 1 |

The 15 SVC sites are TXM `0xfffffff01708425c` (1), `0xfffffff017065bc0`
(11), `0xfffffff017065be8` (2) and `0xfffffff0170842a8` (1), the same
return addresses TCG recorded.

## How the gates were found

Single-stepping under GDB (`native_sptm_trace.py`) is capped at 4096 steps.
Two new tools replace it for this work:

- `tools/perf/native_sptm_exec_trace.py` runs unmodified SPTM under TCG with
  `-d in_asm,int` and classifies every translation block in first-execution
  order. `/tmp/dvm/HVF_SPTM_EXEC1/results.json`: 115,083 blocks in 0.78 s,
  the SPTM→TXM handoff at block 13,693 (`0xfffffff017084000`, TXM
  `__TEXT_BOOT_EXEC`), the kernel entry at block 23,622
  (`0xfffffff02b354000`, bootkc `__TEXT_BOOT_EXEC` slid by `0x20000000`),
  and 37 SVC plus 4,757 GENTER exceptions in the window.
- `tools/perf/native_sptm_sites.py` breakpoints every gate PC in sequence and
  records the operand, the MRS result and the Apple/translation registers
  at each hit, then a full snapshot at a terminal PC. `HVF_SPTM_SITES2`
  (70 hits, `0x6f` → TXM entry) and `HVF_SPTM_SITES3` (253 hits, TXM entry →
  kernel entry) supplied every value below.

## Gates implemented, in firmware order

Original SPTM addresses carry the `0xfffffff027` prefix; runtime is
`0xfffffff007`.

| Site | Operation | Bridge behaviour |
| --- | --- | --- |
| `...a3978` | `GXF_CONFIG_EL2 = 0x6f` | Accepted from guarded EL2 when the register is `1`. ENAB gates transitions, LOCK (bit 3) makes further writes stop. PEX2/PEX0/NACC/HVAC are stored without semantics, as in TCG. |
| `...bbcec..bbd98` | `CTXR_{A,B,C,D}_CTL_EL2` | Sequence `0 → 0x4000000000000000 → 0x40000000_00xxxxxx → bit 63` accepted from guarded EL2; any change after LOCK stops. Stored without enforcement, as in TCG (`apple_regs.c` seeds ACC_CTXR with these same words). |
| `...bbd00`, `...bbd50`, `...b1700`, `...bbf04`, `...d4dcc` | `TLBI VMALLE1NXS`, `VAALE1ISNXS` | Every TLBI encoding (CRn 8 or 9) refreshes the native TLB through the private helper. |
| `...b16f0`, `...b16f8` | `SPRR_PPERM_EL2`, `SPRR_CONFIG_EL2 = 0xff` | Already supported. |
| `...a39dc..3a04` | `ACFG_EL1` `0x18 ↔ 0x10` | Cache-op disable bits toggled around the clean loop; stored. |
| `...a39b8` | `DC CIVAC` ×~2,000 | `ARM_CP_NOP` in QEMU's table; treated as no-op (host RAM is coherent). `IC` forms take the IC IALLU path. |
| `...dd99c` etc. | `LDR W, [AMCC + 0x10..0x320]` | Device memory: stage-1 fault carries no ISV, so the load/store is decoded and replayed through the QEMU address space. Reads returned the AMCC CTRR planes (`0x6fac000`, `0x70ff000`). |
| `...bbefc`, `...bbf14` | `CTRR_C_CTL_EL2` `0 → 1 → 0x8000000000000001` | WRPROTECT then LOCK accepted. The store exclusion in `hvf_vsh_ctrr_write_allowed()` now applies to non-guarded execution only: with WRPROTECT set, SPTM `...d5ce4` keeps storing page-table words into the kernelcache `__DATA_SPTM` segment, which lies inside CTRR C. Hardware and TCG complete that store. |
| `...9ab5c`, `...9ab6c` | `BP_OBJC_CTL_EL1`, `JCTL_EL2` rewritten unchanged | A write that stores the current value is accepted for any register. |
| `...9aaf0`, `...e6be0`, `...9b03c`, `...b4e30` | `SPRR_UPERM_EL0` | The user bank is rewritten after the configuration lock (`0x8004000c8080` for TXM, `0x2010002030100000` for the kernel). Accepted from guarded EL2; aliases are discarded. |
| `...9ab10`, `...e6bf0`, `...9b04c` | `AGTCNTRDIR_EL2` `3 ↔ 1` | Values 0..3 accepted while all counter sources are the common 24 MHz clock. |
| `...9ab20`, `...e6bf8`, `...9b054` | `APCTL_EL2` `0x11 ↔ 0x1` | Stored without semantics, as in TCG. |
| `...9ab3c`, `...e6c00`, `...9b05c` | `SCTLR_EL2` rewritten | Accepted while M stays set; carried to physical SCTLR_EL1 after invalidation. |
| `...9ab4c`, `...e6c08`, `...9b064` | `TCR_EL2` E0PD1 cleared/restored | Any layout the shadow supports; roots rebuilt on invalidation. |
| `...9ab7c`, `...9ac20` | `SP_EL0` | Stored. |
| `...9aabc/aad0`, `...9ac18/ac28`, `...9afd8/aff4/b000` | `ELR/SPSR/ASPSR_GL2` | Saved-bank writes accepted from guarded EL2. |
| `...9ab80`, `...9ac2c` | `ERET` | Mirrors `HELPER(exception_return)`: guarded caller returns through its GL bank, CURRENTG unchanged. Returns to EL2 or EL0. |
| `...9a514` | `MSR DAIF, Xn` | Accepted. `MSR SPSel` executes natively (never ledgered). |
| `...9b004` | `GEXIT` | Already supported; consumes the ASPSR_GL2 = 0 written at `...9b000`. |

## Guarded EL0 (TXM) execution

TXM is entered by SPTM's ERET at original `0xfffffff02709ab80` with
SPSR_GL2 `0x3c0` (EL0t) and CURRENTG still 1. It executes at physical EL0:

- `hvf.c` maps physical EL0 to virtual EL0 (GL0 while CURRENTG) and
  programs CPACR FPEN from the architectural decision for both EL2 and EL0.
- Shadow leaves carry AP[1] for EL0 aliases, with UXN/PXN cleared per
  fetching level (`hvf_vsh_descriptor()`). A page already aliased for the
  other level is widened after a fresh granted walk (one such widening in
  BOOT8). EL0 code is not ledger-validated: privileged instructions trap to
  the private vector and are delivered as the guest's own exceptions.
- Lower-EL aborts (EC `0x20`/`0x24`) fill aliases like same-EL ones.
- `SVC` (EC `0x15`) and undefined instructions (EC `0x00`) from EL0 are
  delivered with `arm_cpu_do_interrupt()`, which selects VBAR_GL2 and the
  guarded ESR/ELR/SPSR banks while CURRENTG is set; SPTM's handler at
  `0xfffffff02709bd34` then ran natively for all 15 calls, and its ERETs
  returned to the recorded TXM addresses.

Native TXM `DC ZVA` executes directly under SCTLR.DZE; no ledger entry is
needed.

## What this does not establish

- Hardware semantics of GXF_CONFIG bits 1, 2, 5, 6, the CTXR fields,
  CTRR WRPROTECT for lower levels, APCTL, or ACFG. Each is stored exactly
  as TCG stores it; the LOCK bits stop later changes instead of guessing.
- Any protection enforcement beyond the existing alias permission model and
  the non-guarded CTRR store exclusion.
- Kernel execution. The next phase needs a ledger for the kernelcache (or
  hardware trapping of its privileged instructions), interrupt and timer
  delivery, and the GENTER/GEXIT fast path measured in
  [hvf-performance-checkpoint.md](hvf-performance-checkpoint.md).

## Reproduce

```sh
python3 tools/perf/native_sptm_exec_trace.py --out /tmp/dvm/UNIQUE_EXEC \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree --start-pc 0xfffffff0070a3978 \
  --seconds 90 --stop-on-serial
python3 tools/perf/native_sptm_sites.py --out /tmp/dvm/UNIQUE_SITES \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree --sites /tmp/dvm/UNIQUE_EXEC/results.json \
  --start-pc 0xfffffff0070a3978 --terminal-pc 0xfffffff017084000
python3 tools/perf/native_virtual_boot.py --tag UNIQUE_BOOT \
  --qemu "$PWD/qemu-sptm/build/qemu-system-aarch64" \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --sptm /tmp/dvm/HVF_GXF_SPTM8.macho --ledger /tmp/dvm/HVF_GXF_SPTM8.ledger \
  --shadow --check-tables /tmp/dvm/HVF_SPTM_TABLES1/results.json
```

The site tool excludes hot loops by list; `HVF_SPTM_SITES1` hit the
wall clock inside the ~2,000-iteration `DC CIVAC` loop and is a recorded
failure, not a result.

## Regressions on the final binary

Executable SHA-256
`681348a173dc5fa52e92f681c7070eb2ed4df1dc6c3e2c2aa391bab33e8cdb20`
(`qemu-sptm/build/qemu-system-aarch64` in this worktree):

- `HVF_SPTM_BOOT9`: the native boot above repeats on this binary: same
  GEXIT onto `0xfffffff02b354000`, 15 SVC deliveries, 15 ERETs, 14 device
  accesses, 4,302 aliases, five table hashes matching, 172 registers equal
  to the TCG terminal snapshot with only HCR.RW/HCRX differing.
- `HVF_SPTM_GXF_TEST2`: native GXF/permission matrix 117/117
  (`/tmp/dvm/HVF_SPTM_GXF_TEST2/results.json`, same executable hash).
- `HVF_SPTM_TCG2`: 60-second TCG restore-ramdisk baseline reaches the
  shell, 311 serial lines, zero XNU panics, final PC `0xfffffff02ab218bc`.
- Host-only suite 75/75 (`python3 -m unittest discover -s tools/tests`),
  both new tools compile, `git diff --check` clean, checkpatch reports zero
  errors and zero warnings for the five changed HVF headers.

`HVF_SPTM_GXF_TEST1` (117/117) and `HVF_SPTM_TCG1` (shell reached, 311
serial lines, zero panics) ran on the previous build, which differs from
the final one only by one wrapped line in `virtual-shadow.h`.
