# Bounded iBoot execution result (d47 / mBoot-20457.2.37)

Date: 2026-09-04

## Outcome

The available release and research iBoot payloads execute under the current
`darwin` machine model, but neither reaches a kernel handoff yet.  The bounded
device-free work has advanced both images through `LLC_RAM_CONFIG`, SEP
entropy, EL2 APIA-key aliases, root scratch, CPM/bootstrap unlock, early PMGR
power and topology setup, GPIO setup, MCC configuration, and two signed PMGR
tuning passes. The latest bounded increment also executes 78 exact range-2
table RMWs, including the intervening descriptor `0x64` and `0x82` updates.
Both images now stop at the same physical access, `0x300040144`
(`pmgr` reg[2] +
`0x40144`).

Follow-up device-free analysis and an opt-in, fail-closed behavioral probe are
recorded in [`iboot-device-free-feasibility.md`](iboot-device-free-feasibility.md).
That probe reconstructs each implemented contract from the firmware, paired
Apple device trees, cross-build comparison, and bounded runtime traces.  Every
non-default identity choice remains opt-in and fail-closed; direct boot is
unchanged.

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
- `6ae2067` — bounded d47 bootstrap, PMGR, GPIO, and MCC protocols
- `42448a1` and `7014188` — strict authentic-SEP-image transport state
- `e43bae6` — first 16 range-2 RMWs and descriptor `0x64` update
- `e706f05` — next eight exact range-2 RMWs through `+0x40064`
- `8503414` — range-2 table through `+0x40140` and descriptor `0x82`

The tested QEMU binary SHA-256 is
`95393538d7ce4316cb28a61f35394bb749a4f513696079841293a9c851e206fa`.
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

## Historical first unsupported boundary

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

## Historical implementation specification (implemented)

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

## Current bounded checkpoint

QEMU commits through `8503414` implement only finite, observed d47 bootstrap
transactions. In addition to the earlier LLC/SEP/APIA/root work, they
validate:

- the PMGR-to-CPM payload copy and command `0x55a01`, without claiming that
  the firmware-insensitive representative payload zero is a hardware reset;
- the boot-unlock key transaction, early GPIO writes, PMGR cold/startup state,
  topology setup, and the seven-block request loop at research raw
  `+0x6d968`;
- both firmware-hardcoded MCC banks, `0x300340000` and `0x300350000`, including
  their eight-record lists, cleanup records, tuning words, and request polls;
- the exact second range-3 selector requests for words
  `0..3,6,7,13,14,23,25,29..33,35,38..40`; and
- the literal range-3 tuning groups through `pmgr[3]+0x62010`, followed by the
  five exact `0x21000000` writes whose bit-30 polls remain clear;
- 16 range-2 masked RMWs through `+0x40040`, followed by the exact later
  `0x05000000` update to the already-observed descriptor `0x64` at
  `+0x40044`; and
- 28 ordered range-2 RMWs at `+0x40048..+0x400b4`;
- the later cold-zero update to topology descriptor `0x82` at `+0x400bc`;
- 31 ordered RMWs at `+0x400c4..+0x4013c`; and
- the single bin-selected zero RMW at `+0x40140`.

No completion value is synthesized for the topology or MCC loops: their prior
firmware writes leave the polled bit clear. Invalid order, width, mask, value,
or an unobserved extra transaction terminates QEMU.

The range-2 values and masks are firmware literals, not hardware reset-value
guesses. The table entry layout is an encoded register word followed by a
literal-data pointer. The implemented continuation is attributed as follows:

| Runtime operations | Research raw | Release raw |
|---|---|---|
| first 16 RMWs and descriptor `0x64` | `+0x2745a8..+0x2746c0` | `+0x272158..+0x272270` |
| 28 RMWs through `+0x400b4` | `+0x2746c8..+0x274880` | `+0x272278..+0x272430` |
| descriptor `0x82` at `+0x400bc` | `+0x274888` | `+0x272438` |
| 31 RMWs through `+0x4013c` | `+0x274898..+0x274aa0` | `+0x272448..+0x272650` |
| selected `+0x40140` RMW | `+0x274aa8` | `+0x272658` |

The three static candidates for `+0x40140` are not executed three times. With
the evidenced tuning-bin and revision inputs, both images select only the
first candidate, whose mask/value is `0x30000000/0x00000000`. The strict
model therefore accepts one cold-zero RMW and rejects an extra transaction.

The research log records the 28-record completion, descriptor `0x82`, the
31-record completion, and the selected `+0x40140` record at
`IBOOT_RANGE2_140_R1.stderr.log:377-380`. Its new first unsupported access is
line 381:

```text
unimp: read  0x300040144 (pmgr[2]+0x40144) -> 0x0 size 4 pc=0xfffffc01fc10b610 el=0 pstate=0x800004c0 sp=0x3000021be20 x0=0x300040144 x1=0x4 x2=0xfffffc01fc10b5dc x3=0xfffffc01fc10b67c
```

The release image independently reaches the same physical boundary at
`IBOOT_RELEASE_RANGE2_140_R1.stderr.log:381`, from runtime
`0xfffffc01fc10a6ec`. The associated write helpers remain research
`0xfffffc01fc10b6b0` and release `0xfffffc01fc10a78c`.

The next record is implementation-ready. Research raw `+0x274ad8` encodes
range-2 `+0x40144` and points to raw `+0x2f03e8`; release raw `+0x272688`
points to raw `+0x2ed678`. Both contain mask/value
`0x30000000/0x00000000`, and both diagnostic continuations write zero at the
boundary (`IBOOT_RANGE2_140_R1.stderr.log:382` and release line 382). The next
increment should admit exactly that one 32-bit RMW only after the selected
`+0x40140` record completes, then stop again. It must not generalize the PMGR
window or treat either zero as a hardware completion response.

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
| `IBOOT_DIRECT_PMGR_CHECKPOINT_20260904` | 303 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_DIRECT_PMGR_CHECKPOINT_20260904.*.log` |
| `IBOOT_DCP_SEP_FB_PMGR_CHECKPOINT_20260904` | DCP + SEP + 828x1792@2 framebuffer, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_DCP_SEP_FB_PMGR_CHECKPOINT_20260904.*.log` |
| `IBOOT_RANGE2_CONT_DIRECT_REG1` | final direct-boot regression, 294 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_CONT_DIRECT_REG1.*.log` |
| `IBOOT_RANGE2_CONT_DCP_SEP_FB_REG1` | final DCP + SEP + 828x1792@2 framebuffer regression, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_CONT_DCP_SEP_FB_REG1.*.log` |
| `IBOOT_RANGE2_144_DIRECT_REG1` | current direct-boot regression, 294 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_144_DIRECT_REG1.*.log` |
| `IBOOT_RANGE2_144_DCP_SEP_FB_REG1` | current DCP + SEP + 828x1792@2 framebuffer regression, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_144_DCP_SEP_FB_REG1.*.log` |

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
