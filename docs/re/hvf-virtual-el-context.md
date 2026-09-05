# Virtual EL2 on HVF EL1: verified transition prototype

**Subsequent integration:** `hvf-integrated-shadow.md` records real SPTM
executing beyond MMU enable at high VA. This document describes the earlier
standalone transition fixture, which is not yet fully integrated.

Date: 2026-09-05. Continue from `hvf-sprr-vhe-feasibility.md`.

The alternative execution-level design now has a working, bounded native
prototype. It executes virtual EL2 and EL1 code at HVF EL1, and virtual EL0
code at HVF EL0. Selected privileged instructions and native exceptions are
handled in software without granting the lower virtual level the higher
level's access. The checked architectural results match a TCG control.

This is **not an integrated QEMU accelerator or an iOS boot**. SPTM's last
actual native boot remains at the SPRR configuration fault, `0x8070a3520`.
No SPRR/GXF operation has been added to that boot path. The previous finding
that native HVF EL2 lacks the tested high-half VHE translation still stands.

## New implementation

`tools/perf/native_virtual_el.S/.c/.py` provides:

- Separate software HCR and per-virtual-EL VBAR/ESR/FAR/ELR/SPSR/SP banks.
  CurrentEL reads report the virtual level. Relevant EL1 register names alias
  the EL2 bank only when accessed from virtual EL2 with E2H set.
- Conversion between the virtual PSTATE mode and the actual HVF mode.
  SP_EL0 is shared architectural state; the virtual SP_EL1/SP_EL2 banks are
  swapped into hardware SP_EL1. A native `MSR SPSel, #0` is tracked correctly.
- Rewriting selected MRS/MSR and ERET instructions to a private SMC, keeping
  original words in host memory. The handler validates the trap PC and word.
  It handles only the fixture's known instructions, not arbitrary firmware.
- A host-owned vector bridge consisting of one SMC per vector slot. No GPRs
  or guest stack are used by the bridge. It captures the real ESR/ELR/SPSR/FAR
  and dispatches the instruction or delivers the appropriate virtual fault.
- Native HVC routing to virtual EL2 and native EL0 SVC routing to virtual EL1.
  HVF reports HVC's return PC, whereas SMC reports its instruction PC; the
  fixture asserts this distinction.
- Context permission changes using the common SPRR decoder and HVF mappings.
  The protected page is RW for virtual EL2 and inaccessible to virtual EL1.
  Monitor code/stack mappings are revoked at lower levels, and the kernel
  code mapping is revoked while EL0 runs. Mapping updates occur on context
  changes, not on every emulated register read.
- Guest fault delivery that preserves the tested ESR/FAR/ELR/SPSR and PAN
  semantics, followed by ERET restoration of flags and stack selection.
  Unsupported modes and HCR translation-regime changes terminate the fixture.

The native probe disables nested EL2 and uses no kernel GIC, disks, QEMU, or
project firmware. Executable pages and page-table backing are protected from
guest writes. All of this is still experimental code under `tools/perf`;
the small `Bank` structure is a test fixture, not a second production CPU model.
Production integration should use QEMU's existing architectural CPU state and
exception routines, not copy this restricted model wholesale.

## Verified execution sequence

The original native positive tests check 42 recorded values and the terminal PC/context:

1. Virtual EL2 executes at high VA `0xffffff8080000000` and reads CurrentEL=8.
   Its VBAR_EL1 alias resolves to the virtual EL2 bank.
2. An intentionally unpatched HCR_EL2 read raises a native EL1 undefined
   exception. The private bridge emulates that valid virtual-EL2 instruction,
   restoring the interrupted state, rather than exposing the hardware fault.
3. ERET enters virtual EL1. It observes CurrentEL=4, its own stack, and the
   specified NZCV flags.
4. An EL2-register read from virtual EL1 remains undefined. A separate native
   load from the protected page really faults. The virtual EL1 handler records
   both faults and returns; it does not retry with wider permissions.
5. HVC #0x71 enters virtual EL2 through its lower-EL vector. The handler sees
   ESR `0x5a000071`, the exact return PC and saved EL1 flags, and its own SP_EL2.
   Its protected read succeeds. ERET restores the EL1 stack and flags.
6. ERET enters actual EL0 at low VA `0x8000c000`. An attempted EL2-register read
   is undefined, and a protected-data read raises a lower-EL permission abort.
   The handlers record ESR/ELR/SPSR/FAR and return to EL0.
7. Native EL0 arithmetic produces `0xabe`. SVC #0x72 enters virtual EL1 and
   returns to EL0 with SP_EL0 and NZCV intact. SVC #0x73 records the post-return
   state and completes the test at virtual EL1.

There are four deliberately denied guest operations: EL2-register and data
reads at each of virtual EL1 and EL0. A successful positive run uses 27 patched
sites, 54 host exits, six private-vector entries, eight ERETs, one HVC and two
SVCs. These counts describe this small fixture, not iOS boot overhead.

Both normal stack selection and native `SPSel=0` at virtual EL2 pass. The
latter specifically exercises the current-SP0 exception vector and restoration
used by the real SPTM setup (`0xfffffff0270a34f8` sets SPSel to zero).

Final native evidence: `/tmp/dvm/HVF_VIRTUAL_EL_FINAL2/results.json`.
It contains source/binary hashes and four runs:

| Variant | Expected and observed outcome |
|---|---|
| normal | All checks pass. |
| sp0 | All checks pass after native SPSel=0 at virtual EL2. |
| no-revoke | Intentional negative control fails: missing protected-read fault. |
| no-bank-switch | Intentional negative control fails: wrong EL1/EL0 stack values. |

The negative controls alter only the diskless test fixture. Their `passed`
field is false; `verified` means the runner verified the expected rejection.
They are not selectable production policies and do not boot iOS.

## Independent architectural comparison

The TCG control runs the original privileged instructions at actual guest
EL2/EL1/EL0, rather than the HVF replacements. The instruction stream differs
in two relevant ways: the final SMC becomes a stop loop, and the EL1 SPRR
protected load becomes a NOP because the generic `virt` fixture does not
configure Apple's SPRR registers. The EL0 access fault remains real in both
runs. The omitted EL1 SPRR fault is covered by the native positive/negative
control and the earlier 192-case native SPRR matrices.

All other recorded fields match, including both virtual-register privilege
faults, HVC/SVC syndrome and return-PC values, banked stacks, restored NZCV,
and PAN=1 at EL1 exception entry versus PAN=0 at the tested EL2 entry.

- `/tmp/dvm/HVF_VIRTUAL_EL_TCG_FINAL2/results.json`: normal case passes.
- `/tmp/dvm/HVF_VIRTUAL_EL_SP0_TCG1/results.json`: SPSel=0 case passes.

The unchanged QEMU binary hash is
`f73584f15ef2a489e740394d7f53dc216d7f815cecc70f9eb558444aa2e3c3c7`.

A setup false lead was resolved during this comparison: ARM's
`arm_gdb_set_sysreg()` at `target/arm/gdbstub.c:265` returns zero without
writing the register, despite the remote P packet returning OK. The final
control executes a real MSR setup payload and records the register state.
Do not use a successful GDB system-register P reply as writeback evidence.
This does not invalidate the earlier native HCR helper tests, which used real
HVF accessors and ordinary GPR/debugger synchronization.

## Reproduction

Use fresh output directories in the existing worktree:

```sh
python3 tools/perf/native_virtual_el.py --out /tmp/dvm/FRESH_VIRTUAL_EL
python3 tools/perf/native_virtual_el.py --tcg --out /tmp/dvm/FRESH_VIRTUAL_EL_TCG
python3 tools/perf/native_virtual_el.py --tcg --sp0 \
  --out /tmp/dvm/FRESH_VIRTUAL_EL_SP0_TCG
```

The initial direct EL1 trap-routing evidence is also recorded in
`/tmp/dvm/HVF_EL1_TRAP_ROUTING1/results.json`: Apple sysreg accesses and HVC
exit to the host, while GENTER/HCR/HCRX become guest undefined exceptions.
Those exploratory runs used `native_el2_probe` and reported routing only;
its return code was not an assertion that an operation completed successfully.

## E2H+TGE and illegal returns — subsequent verified extension

`native_virtual_el.py --tge` now tests fixed HCR `0x488000000`. EL0
permission/privilege faults and SVCs route to virtual EL2. With SPAN=0,
PAN is set on E2H+TGE EL2 exception entry. Both SP_EL2 and SPSel=0 cases pass.
The fixture now records 48 values, including six illegal-return fields.

The illegal-return variant attempts ERET to EL1 while TGE=1. It keeps the
current virtual EL and stack selection, restores the specified flags, and
sets PSTATE.IL. HVF actually executes with IL set and raises an illegal-state
exception: ESR `0x3a000000` at `0xffffff808000005c`. The instruction at the
illegal target does not execute (its marker remains `0x777`). The guest's
handler records the fault and supplies a valid EL2 return state.

- `/tmp/dvm/HVF_VIRTUAL_TGE1/results.json`: normal, sp0, illegal, and
  illegal-sp0 positives pass; no-tge-route and no-bank-switch controls reject.
- `/tmp/dvm/HVF_VIRTUAL_TGE_TCG1/results.json`: all 48 fields match TCG.
- `/tmp/dvm/HVF_VIRTUAL_TGE_ILLEGAL_TCG1/results.json`: TCG illegal+SP0 matches,
  including SPSR `0xa01003c8`, CurrentEL=8, and the unexecuted marker.
- `/tmp/dvm/HVF_VIRTUAL_TGE_OFF1/results.json`: the original TGE-off positive
  and negative controls still give their expected outcomes.

Normal TGE uses 36 exits; illegal-return TGE uses 47. These are fixture
counts, not boot performance. This extension still runs outside QEMU and
does not advance the actual SPTM boot. Reproduce using fresh output paths:

```sh
python3 tools/perf/native_virtual_el.py --tge --out /tmp/dvm/FRESH_TGE
python3 tools/perf/native_virtual_el.py --tcg --tge --out /tmp/dvm/FRESH_TGE_TCG
python3 tools/perf/native_virtual_el.py --tcg --tge --illegal --sp0 \
  --out /tmp/dvm/FRESH_TGE_ILLEGAL_TCG
```

## Scope and next integration work

The fixture fixes 16 KiB tables and HCR=E2H|RW, with TGE selected at build
time. It does not yet support dynamic guest MMU/HCR configuration changes, TLB maintenance,
SPRR configuration/locks, GXF banks/transitions, arbitrary code discovery or
self-modifying code, interrupts/timers, all architectural PSTATE features,
multicore, or snapshots. Do not infer those from the successful round trip.
The tested TGE routing does not prove the selection of different real SPTM
TTBR/TCR regimes or all architectural exception/PSTATE combinations.

The earlier first draft cleared E2H while executing at high VA. That was an
invalid architectural control: it would change translation on real EL2. It
was replaced with pre-seeded EL1 state and a fixed VHE regime, then validated
under TCG. No real SPTM behavior was altered to accommodate that mistake.

Next, connect this execution-level separation to QEMU's CPUARMState and full
exception handling, plus an enforced shadow-table walker. The initial SPTM
case needs its real TTBR/TCR/SCTLR configuration, SPRR bank selection, and
E2H+TGE semantics. Source-table writes and every alias must invalidate the
shadow mappings before resuming. GXF must preserve separate EL/GL protection
and exception state. Continue from the observed firmware faults rather than
accepting unknown registers as no-ops.

The design relies on ordinary EL1/EL0 execution and explicit software state,
so its compatibility semantics need not be Apple-host-only. HVF mapping and
exit plumbing should remain separate from those semantics. Actual Windows
ARM/WHPX support is still unverified; TCG remains the working fallback.

Verification: host-only suite 23/23 in `/tmp/dvm/HVF_VIRTUAL_EL_HOST1.log`,
Python compilation and shell syntax pass. No QEMU execution source changed
in this continuation, no rebuild occurred, and no full TCG boot was repeated.
All owned probes have exited. Display instances and protected project files
were untouched. All native work remains uncommitted and unpushed.
