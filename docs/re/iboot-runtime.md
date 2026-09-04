# Bounded iBoot execution result (d47 / mBoot-20457.2.37)

Date: 2026-09-04

## Outcome

The available release and research iBoot payloads execute under the current
`darwin` machine model, but neither reaches a kernel handoff yet.  The bounded
device-free work has advanced both images through `LLC_RAM_CONFIG`, SEP
entropy, EL2 APIA-key aliases, root scratch, CPM/bootstrap unlock, early PMGR
power and topology setup, GPIO setup, MCC configuration, and two signed PMGR
tuning passes. The current bounded path executes 90 exact range-2 table RMWs,
including the intervening descriptor `0x64`/`0x82` updates and late sparse
groups, plus the exact late selector requests described below. Both images now
stop at the same physical access, `0x30006f000` (`pmgr` reg[2] + `0x6f000`).

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
- `862b31c` — late range-2 sparse groups and selectors through `+0x6f000`

The tested QEMU binary SHA-256 is
`f6d87040a446664cd719647f05c6c0c3d4e30043ea35eefd3cacb3205e128628`.
It was built with `ninja -C qemu-sptm/build qemu-system-aarch64` from this
worktree's own build directory; the build completed successfully.

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

QEMU commits through `53a1b1a` implement only finite, observed d47 bootstrap
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
- the first bin-selected zero RMW at `+0x40140`, the following `+0x40144`
  zero RMW, and exact second selector requests `0x83000001` for words 4..7;
- eight ordered sparse RMWs at `+0x68000`, `+0x68004`, `+0x68924`,
  `+0x69248`, `+0x69b6c`, `+0x6a490`, `+0x6adb4`, and `+0x6b6d8`;
- exact second selector requests `0x84000002` and `0x85000002` for words 0
  and 1, followed by cold-zero RMWs at `+0x58010` and `+0x5c008`;
- one ordered repeat of the `+0x40140` zero RMW;
- the selected word-8 request `0x82000024`, followed by the exact range-2
  RMWs at `+0x6f000` and `+0x6f100`;
- 13 exact PMGR target-nibble reset preconditions proven by the firmware's
  source-line-500 assertion; and
- five range-43 cold-start latch words at `+0x8ac000`, `+0x800000`,
  `+0x8ac028`, `+0x864000`, and `+0x8aa0bc`; and
- twelve ordered range-1 table RMWs at `+0x50400`, `+0x50408`,
  `+0x3c000..+0x3c330`, and `+0x38060`.

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
| first selected `+0x40140` and `+0x40144` RMWs | `+0x274aa8`, `+0x274ad8` | `+0x272658`, `+0x272688` |
| selector words 4..7 second requests | `+0x274b08..+0x274b38` | `+0x2726b8..+0x2726e8` |
| eight sparse RMWs through `+0x6b6d8` | `+0x278b88..+0x278cb8` | `+0x276738..+0x276868` |
| selected `+0x58010` / `+0x5c008` RMWs | `+0x274c10`, `+0x276010` | `+0x2727c0`, `+0x273bc0` |
| repeated `+0x40140` RMW | `+0x274ab0` | `+0x272660` |
| selector word 8 selected tuple | descriptor `+0x274b70`, tuple `+0x2f06b8` | descriptor `+0x272720`, tuple `+0x2ed948` |

The strict model orders every late operation behind the prior phase. In
particular, the sparse group is available only at selector-word-7 phase 5,
which the fail-closed diagnostic records after its exact second write; the two
post-sparse RMWs are ordered behind selector-word-1 phase 5; and the repeated
`+0x40140` RMW is unavailable until that pair completes. An extra, reordered,
wrong-width, or wrong-value transaction terminates QEMU.

The two post-selector RMWs are now attributed to exact table records. The
`+0x6f000` record is research raw `+0x23c318 -> +0x2e5580` and release raw
`+0x239ec8 -> +0x2e2810`, with mask/value `1/1`. The `+0x6f100` record is
research `+0x24b780 -> +0x2eac58` and release `+0x249330 -> +0x2e7ee8`, with
mask/value `0x8000ffff/0x80005110`. Both are ordered behind selector-word-8
phase 5 and neither has a completion poll.

The failure after those RMWs was initially misattributed to
`pmgr[1]+0x38004`. That write is only iBoot's panic marker. The panic buffer at
research VA `0xfffffc01fc44b000` contains:

```text
iBoot Panic: : 9ccf861420ca495:500
```

The real assertion is research `0xfffffc01fc093d20` (release
`0xfffffc01fc093a5c`). Before changing a PMGR device state, the helper requires
the existing target nibble `[3:0]` to be `0xf`. The following exact d47 table
records were observed successively at that assertion and are now validated
against device ID, PMGR range, state offset, flags, and name before being
initialized:

| device ID | range + offset | flags | name |
|---:|---:|---:|---|
| `0x2c` | `0 + 0x218` | `0x09` | `AFISOCNI0` |
| `0x2d` | `0 + 0x220` | `0x09` | `AFISOCNI1` |
| `0x2e` | `0 + 0x228` | `0x09` | `AFISOCNI2` |
| `0x26` | `0 + 0x1e8` | `0x00` | `AFINS0` |
| `0x21` | `0 + 0x1c0` | `0x09` | `AFCMCCNI0` |
| `0x1e` | `0 + 0x1a8` | `0x00` | `PIOGW` |
| `0x1c` | `0 + 0x198` | `0x09` | `AFIMCCNI0` |
| `0x0f` | `0 + 0x130` | `0x09` | `PMS` |
| `0x0e` | `0 + 0x128` | `0x09` | `PMS_BUSIF` |
| `0x09` | `0 + 0x100` | `0x09` | `SBR` |
| `0x35` | `0 + 0x260` | `0x01` | `SIO` |
| `0x8e` | `1 + 0x070` | `0x09` | `NUB_AON` |
| `0x96` | `1 + 0x0b0` | `0x09` | `DEBUG_SWITCH` |

Only target bits `[3:0]` start at `0xf`; actual-state bits `[7:4]` remain zero
until an accepted firmware write drives the existing PMGR state transition.
The research caller at `0xfffffc01fc0e9534..0xfffffc01fc0e962c` contains 13
top-level requests (`a1,a3,a5,95,8b,86,83,76,75,70,ae,11,1b`). Recursive
dependencies eventually reach the 13 asserted target words above. Once all
are present, both images clear the line-500 panic and continue.

The next cross-build-identical block and leaf perform five ordered,
non-polling RMW sequences in PMGR range 43:

| physical address | range offset | exact operation |
|---:|---:|---|
| `0x3028ac000` | `+0x8ac000` | read; write `old | 0x01` |
| `0x302800000` | `+0x800000` | read/write `old | 0x04`; reread/write `old | 0x20` |
| `0x3028ac028` | `+0x8ac028` | read; write `old | 0x01` |
| `0x302864000` | `+0x864000` | read; write `old | 0x02` |
| `0x3028aa0bc` | `+0x8aa0bc` | read; write `old | 0x40` |

Research raw `+0x69250..+0x6929c` and release raw
`+0x68540..+0x6858c` are instruction-for-instruction equivalent for the first
four accesses. The fifth uses a four-instruction leaf at research raw
`+0x6e45c` and release raw `+0x6d74c`. An exact address-materialization scan
finds only two references in each image; the other is a snapshot recorder and
does not branch on the value. The model starts only these five words at the
explicit cold-clear representative and rejects a wrong width, value, order,
or extra transaction; it supplies no status value.

The selected table then performs twelve more ordered, non-polling 32-bit RMWs
in PMGR range 1. Runtime identifies the active adjacent `+0x50400/+0x50408`
descriptors at research raw `+0x265948/+0x265958` and release raw
`+0x2634f8/+0x263508`. Their referenced tuples are respectively research raw
`+0x2e60f0/+0x2ee728` and release raw `+0x2e3380/+0x2eb9b8` and are byte-for-byte
identical across builds. The next nine active descriptors begin at research
raw `+0x265a60` and release raw `+0x263610`. The final accepted descriptor is
research raw `+0x265b30` and release raw `+0x2636e0`.

| physical address | range offset | mask | value/result from cold-zero representative |
|---:|---:|---:|---:|
| `0x3082d0400` | `+0x50400` | `0x00000fff` | `0x00000000` |
| `0x3082d0408` | `+0x50408` | `0x00ffffff` | `0x00000352` |
| `0x3082bc000` | `+0x3c000` | `0x00000001` | `0x00000001` |
| `0x3082bc020` | `+0x3c020` | `0x3fffffff` | `0x3f1c7ff7` |
| `0x3082bc044` | `+0x3c044` | `0x00000001` | `0x00000001` |
| `0x3082bc100` | `+0x3c100` | `0x00000001` | `0x00000001` |
| `0x3082bc110` | `+0x3c110` | `0xffffffff` | `0xf37fe05f` |
| `0x3082bc114` | `+0x3c114` | `0xffffffff` | `0xcbff9400` |
| `0x3082bc118` | `+0x3c118` | `0x01ffffff` | `0x01fe4047` |
| `0x3082bc32c` | `+0x3c32c` | `0x000000ff` | `0x00000000` |
| `0x3082bc330` | `+0x3c330` | `0x800001ff` | `0x00000000` |
| `0x3082b8060` | `+0x38060` | `0xffffffff` | `0x0000001c` |

Every accepted word is isolated to a four-byte region, starts at an explicitly
labeled cold-zero representative, and requires the exact cross-word order,
width, and firmware-computed value. No completion value is returned. The
`+0x38060` list contains two later records that request `0x2c` and `0x0b`; the
current model intentionally accepts only the first `0x1c` transaction so those
later phases cannot be reached accidentally.

### Current first unsupported boundary

After the twelve range-1 RMWs, both images reach the existing `AOP_CPU` PMGR
state word at physical `0x308284098`, range 1 `+0x4098`. Its accepted earlier
transition left target and actual nibbles at 15 (`0x000000ff`). The selected
table reads that word and requests `0x000020ff`; the state model rejects the
new bit 13 rather than silently broadening its global control mask:

```text
research: write pc=0xfffffc01fc10b6b0, changed-outside-model=0x00002000
release:  write pc=0xfffffc01fc10a78c, changed-outside-model=0x00002000
```

Runtime identifies the active descriptor at research raw `+0x265cf8` and
release raw `+0x2638a8`; both encode width 4 and offset `0x4098`. Their tuple
pointers resolve to research raw `+0x2ee7c8` and release raw `+0x2eba58`, where
both images contain `(mask,value)=(0x00002000,0x00002000)`. The immediately
adjacent records apply the same tuple to range-1 offsets `+0x40a0` and
`+0x8000`, but they have not executed because the first write fails closed.

The next implementation increment should add a per-device bit-13 capability,
not change `D47_PMGR_STATE_CONTROL_MASK` globally. First admit only the exact
ordered `AOP_CPU` transaction `read32; write32(old | 0x2000)` after the
`+0x38060 <- 0x1c` phase, store bit 13 without changing target/actual nibbles,
and return no completion. Then stop and verify whether the adjacent `AOP2_CPU`
and `SMC_FABRIC` descriptors execute in the predicted order before extending
that explicit allowlist. Audit later consumers before assigning a semantic
name to bit 13.

Boundary logs are
`IBOOT_AOP_CPU_BOUNDARY_RESEARCH.stderr.log:425-437` and
`IBOOT_AOP_CPU_BOUNDARY_RELEASE.stderr.log:425-437`. Descriptor/register
captures are in `IBOOT_AOP_BIT13_TARGET_RESEARCH.lldb.log` and
`IBOOT_AOP_BIT13_TARGET_RELEASE.lldb.log`.

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
| `IBOOT_RANGE2_6F000_DIRECT_REG1` | current direct-boot regression, 294 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_6F000_DIRECT_REG1.*.log` |
| `IBOOT_RANGE2_6F000_DCP_SEP_FB_REG1` | current DCP + SEP + 828x1792@2 framebuffer regression, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_6F000_DCP_SEP_FB_REG1.*.log` |
| `IBOOT_RANGE2_6F000_SEPI_STRICT_REG1` | authentic encrypted d47 `sepi` transport, full 7,673,931-byte mapped-container SHA-256 verified, SEP protocol accepted, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE2_6F000_SEPI_STRICT_REG1.*.log` |
| `IBOOT_RANGE43_DIRECT_REG1` | current direct-boot regression, 303 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE43_DIRECT_REG1.*.log` |
| `IBOOT_RANGE43_DCP_SEP_FB_REG1` | current DCP + SEP + 828x1792@2 framebuffer regression, 376 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE43_DCP_SEP_FB_REG1.*.log` |
| `IBOOT_RANGE43_SEPI_STRICT_REG1` | authentic encrypted d47 `sepi` transport, full 7,673,931-byte mapped-container SHA-256 verified, SEP protocol accepted, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_RANGE43_SEPI_STRICT_REG1.*.log` |
| `IBOOT_AOP_DIRECT_REG1` | current direct-boot regression, 303 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_AOP_DIRECT_REG1.*.log` |
| `IBOOT_AOP_DCP_SEP_FB_REG1` | current DCP + SEP + 828x1792@2 framebuffer regression, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_AOP_DCP_SEP_FB_REG1.*.log` |
| `IBOOT_AOP_SEPI_STRICT_REG1` | authentic encrypted d47 `sepi` transport, full 7,673,931-byte mapped-container SHA-256 `db0d02a7...28ed817` verified, SEP protocol accepted, 375 serial lines, 0 panics, restore shell reached | `/tmp/dvm/iboot-main/probe/IBOOT_AOP_SEPI_STRICT_REG1.*.log` |

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
