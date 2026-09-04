# Bounded iBoot execution result (d47 / mBoot-20457.2.37)

Date: 2026-09-04

## Outcome

The available release and research iBoot payloads execute under the current
`darwin` machine model, but neither reaches a kernel handoff.  Both take the
same early failure path because QEMU does not implement the Apple
`LLC_RAM_CONFIG` system register (`S3_3_c15_c7_0`).  The failure path writes
`0xa00a0005` to physical `0x3082b8030` (`pmgr` reg[1] + `0x38030`) and then
branches to itself.  The experiment stops on that first unsupported MMIO
access.  No success value was added for either the system register or PMGR.

Follow-up device-free analysis and an opt-in, fail-closed behavioral probe are
recorded in [`iboot-device-free-feasibility.md`](iboot-device-free-feasibility.md).
That probe reconstructs enough of the register contract from three Apple
iBoot/device-tree pairs to pass this historical boundary and stop at the next
unsupported access, SEP ASC `OUTBOX0_CTRL` at `0x282608114`.  The default
machine behavior documented here remains unchanged.

The static image contract and direct-boot responsibilities are recorded in
[`iboot-image.md`](iboot-image.md) and
[`iboot-loader-references.md`](iboot-loader-references.md).  This document
records the loader, runtime evidence, first blocker, and regressions.

## Isolation and revisions

All work was performed outside the active checkout.  The integration worktree
is `/Users/jdolbe1/.codex/worktrees/iboot-main-darwin-vm` on
`codex/iboot-main`.  Independent firmware-static, direct-boot-reference,
runtime-audit, and loader/device worktrees were also created under
`/Users/jdolbe1/.codex/worktrees/`.

The QEMU submodule was based on pinned commit `607e977`.  That object is no
longer advertised by the configured remote, so it was checked out from the
existing local submodule object store.  The integrated QEMU commits are:

- `8f4a7ad` — minimal raw iBoot loader and first-MMIO stop instrumentation
- `5fcf6dd` — mutually exclusive machine wiring and `-iboot` command line

The tested QEMU binary SHA-256 is
`0b8199d131c0c56b403328b2e3472e8565471ba7db685b337127a6c6ac3bbcc6`.
It was built with `make -j18` from this worktree's own
`qemu-sptm/build`; the build completed successfully.

## Image and machine contract

The research payload was the primary runtime image:

- raw file: `/tmp/dvm/iboot-fw-static-20260904/iboot-research.raw`
- size: `0x3c4ef8` (3,952,376 bytes)
- SHA-256: `8e2a7ee4955871de9c577b555606495b636e743e35e58b6843983d98aabbb9cb`
- format: flat little-endian AArch64 code, not Mach-O
- load address and entry point: `0x1fc080000`, raw offset `0`
- required execution level: EL2
- evidenced writable/executable SRAM: `[0x1fc000000, 0x1fc480000)`
- statically observed BSS end: `0x1fc46ddd0`

The release payload also passed validation:

- raw file: `/tmp/dvm/iboot-fw-static-20260904/iboot-release.raw`
- size: 3,885,976 bytes
- SHA-256: `fff9f51bf2f90487fbf04b2b9a091bc739865a5ec0793c03fbe469eeeb00d8e2`
- statically observed BSS end: `0x1fc459dd0`

The loader checks the d47 startup instructions, identity banner, self/SRAM/
canonical address literals, 64-byte-rounded copy end, image size, and BSS
bounds before mapping anything.  `/bin/echo` was used as a negative control;
QEMU exited 1 and reported the mismatching first instruction rather than
executing it.

For the first experiment, `x0` through `x3` are zero at reset and no
iBootData, guest device tree, trust cache, IMG4 ticket/IM4M, SPTM, TXM,
BootKC, ramdisk, or disk is loaded.  The existing fixed-up `firmware/dtree`
(SHA-256
`2bb789930f3a2bd47a6b229a23cfec8caae3a04fb74b465546e0ef3c93676274`)
is used only to construct the current QEMU machine devices.  It is not handed
to iBoot.  Supplying the raw Apple tree as the machine description remains a
separate task because the present machine requires host-only `dram-base`,
`dram-size`, clock, and fixed-up device enablement properties.

In iBoot mode the direct loader's synthesized BootArgs, kernel patches, MTE
tag RAM, memory-map entries, and preloaded SPTM/TXM/BootKC/trust-cache/ramdisk
are skipped.  Direct-loader CTRR/CTXR bounds and `TAG_OFFSET_EL2` are also not
derived from sentinel memory-map entries.  Direct mode retains its existing
behavior.

## Entry positive control

The research run was started paused with no `-drive` and with
`DARWIN_UNIMP_STOP_FIRST=1`.  Before continuing the CPU:

- HMP reported `PC=0x1fc080000`, `PSTATE ... EL2h`, `SP=0`, and every GPR
  `x0` through `x30` zero.
- `info mtree -f` contained exactly the loader SRAM mapping
  `0x1fc000000-0x1fc47ffff` named `darwin.iboot-memory`.
- A 16 KiB `pmemsave` at `0x1fc080000` hashed to
  `c11aaedb5bfd0eef818966f66f817f505e3df06e82cc3eccd1f53970807cb5b6`,
  exactly matching the first 16 KiB of the source payload.
- HMP disassembly began `msr vttbr_el2, xzr; isb; mrs x2, hcr_el2`, matching
  raw offsets `0x0`, `0x4`, and `0x8`.

Complete evidence is under
`/tmp/dvm/iboot-main/IBOOT_EXEC1C_20260904/`: `initial.regs`,
`initial.disasm`, `entry-page.bin`, `mtree.txt`, `final.regs`,
`final.disasm`, `static-boundary.disasm`, `serial.log`, `stderr.log`, and
`tcg.log`.  Serial and TCG exception logs are empty because this boundary is
before console initialization and is not an architectural exception.

## First unsupported boundary

The first catch-all line in the research run is
`stderr.log:23`:

```text
unimp: write 0x3082b8030 (pmgr[1]+0x38030) <- 0xa00a0005 size 4 pc=0x1fc0bc18c el=2 pstate=0x800003c9 sp=0x0 x0=0x1fc080000 x1=0x1fc080000 x2=0x408000000 x3=0x0
```

The stop request left the CPU paused at the following instruction,
`PC=0x1fc0bc190`.  Research runtime `0x1fc0bc18c` is raw image offset
`0x3c18c`.  Static disassembly identifies the complete terminal sequence:

```text
0x1fc0bc17c  cmp  x7, x8
0x1fc0bc180  b.ge 0x1fc0bc1a8
0x1fc0bc184  ldr  x10, 0x1fc0bc198  ; 0x3082b8030
0x1fc0bc188  ldr  w11, 0x1fc0bc1a0 ; 0xa00a0005
0x1fc0bc18c  str  w11, [x10]
0x1fc0bc190  b    0x1fc0bc190
```

The release image independently reaches the identical physical write and
value from runtime `0x1fc0bb4f4` (raw offset `0x3b4f4`), then parks at
`0x1fc0bb4f8`.  Its complete logs are
`/tmp/dvm/iboot-main/probe/IBOOT_RELEASE_BOUNDARY_20260904.{serial,stderr}.log`;
the first unsupported line is `stderr.log:10`.

### Attribution

The routine begins at research runtime `0x1fc0bc120`:

```text
mrs  x2, S3_3_C15_C7_0
and  x3, x2, 0x8000000000000000
cbnz x3, <return>
ubfx x3, x2, 8, 6
...
ubfx x5, x2, 16, 6
...
cmp  x7, 0x3c0000       ; MPIDR affinity-2 == 0
```

The repository's Apple system-register table names this encoding
`LLC_RAM_CONFIG` (`qemu-sptm/scripts/darwin/sysregs.py:1235`), but the
generated Apple register model does not include it.  Consequently the MRS
does not supply a modeled register value: `x2` remains the preceding HCR_EL2
value `0x408000000`.  Its fields `[13:8]` and `[21:16]` are both zero, the
calculated LLC capacity is zero, and iBoot deliberately enters the PMGR-write
plus spin failure path.  The PMGR write is therefore evidence of the missing
CPU system-register contract, not evidence that returning success from PMGR
would advance boot; the next instruction is unconditionally a branch to
itself.

## Implementation-ready next specification

The next independently justified increment is an Apple CPU
`LLC_RAM_CONFIG` model, not a fabricated PMGR response:

1. Add `LLC_RAM_CONFIG` to the generated Apple register set with encoding
   `S3_3_c15_c7_0` (`opc0=3`, `opc1=3`, `crn=15`, `crm=7`, `opc2=0`) and
   make it accessible from EL2.
2. Model the fields that this exact iBoot routine consumes: completion/valid
   bit 63, unit shift `[13:8]`, maximum unit count `[21:16]`, and the low
   requested-count field written by the routine.
3. Use the cross-image/device-tree constraints in
   [`iboot-device-free-feasibility.md`](iboot-device-free-feasibility.md).
   They establish the capacity policy and active-bit behavior without a real
   device, while explicitly leaving the literal reset encoding underdetermined.
   Keep any geometry factorization opt-in until independently corroborated.
4. Add a focused register test covering reset read, the low-field write, and
   bit-63 completion behavior derived from that source.  Rerun this same
   stop-first probe.  If the PMGR failure write disappears, stop at the next
   unsupported access and capture the same address/register evidence.

No behavior should be added at `pmgr[1]+0x38030` for this path.  Even a
plausible reset/watchdog implementation cannot make the parked caller resume.

## Regressions and controls

Before the iBoot changes, direct boot used binary SHA-256
`a6e56578b64723fe8a1f9a1339b57f28820b3c4d9bb37be992c797c9320a84ba`.
`IBOOT_DIRECT_POSCTRL_20260904` produced 303 serial lines, zero XNU panics,
and reached the restore shell.

After the changes:

| Probe | Result | Evidence |
|---|---|---|
| `IBOOT_DIRECT_POST_20260904` | 303 serial lines, 0 panics, `BSD root: md0`, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_DIRECT_POST_20260904.*.log` |
| `IBOOT_DCP_SEP_FB_POST_20260904` | DCP + SEP + 828x1792@2 framebuffer, 375 serial lines, 0 panics, `BSD root: md0`, shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_DCP_SEP_FB_POST_20260904.*.log` |
| `IBOOT_FEATURE_POST_20260904` | negative ANS-without-backing smoke: reached `BSD root: md0`, then the existing SPTM queue-entry validation rejected `0x41` | `/tmp/dvm/iboot-main/probe/IBOOT_FEATURE_POST_20260904.*.log` |

The ANS smoke is not claimed as a working storage regression: the documented
ANS path requires `tools/ans/dt_ans_fixup.py`, the ANS firmware region, and a
per-run qcow2 disk child.  None was attached because this bounded iBoot task
does not authorize altering the active disk artifacts.  `darwin_ans.c`,
storage, ASC, SEP, DCP, AIC, display, and direct-loader sources were otherwise
left unchanged; the successful direct and DCP/SEP/framebuffer boots exercise
the shared path.

Host-only regressions also passed:

- `python3 -m unittest discover -s tools/tests -v`: 12/12 tests
- `bash -n tools/probe.sh tools/re/setup_gate_probe.sh tools/re/setup_gate_sweep.sh`
- `git diff --check` in both the superproject and QEMU submodule

No disk was attached to either iBoot execution, no active-checkout firmware or
disk artifact was modified, and nothing was pushed.
