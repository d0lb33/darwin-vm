# Device-free iBoot feasibility probe

Date: 2026-09-04

## Verdict

The available iBoot is feasible to continue reverse engineering without a
physical d47 device.  A real device would make exact reset-state recovery
faster, but it is not a prerequisite for building an honest behavioral model.

The first blocker, Apple system register `LLC_RAM_CONFIG`
(`S3_3_c15_c7_0`), was reconstructed far enough from three independent Apple
iBoot/device-tree pairs to run a bounded, explicitly gated hypothesis.  The
probe executed iBoot's register write and completion poll, did not enter its
PMGR failure path, and reached a SEP ASC mailbox status read at physical
`0x282608114`. Reusing the existing SEP ASC model for iBoot then exposed a
previously unsupported bootstrap command. Static command-table and caller
analysis identified it as a 32-bit entropy request; returning host entropy with
the evidenced response opcode advances iBoot past that protocol boundary.

Those boundaries are now historical. Device-free static/runtime correlation
has subsequently advanced both pinned d47 images through the EL2 APIA writes,
the page-table-proven `root` scratch mapping, CPM/bootstrap unlock, early PMGR
and GPIO initialization, topology and MCC protocols, the signed PMGR tuning
tables, the late range-2 table, 13 exact boot-on target preconditions, and the
first four range-43 cold-start latches. Static and runtime correlation then
justified a fifth range-43 latch and twelve ordered range-1 table RMWs. Both
images now stop at the same existing PMGR state word: physical `0x308284098`
(`pmgr[1]+0x4098`, `AOP_CPU`) is read as `0x000000ff`, then an active
cross-build-identical descriptor requests `0x000020ff`. This proves bit 13 is
a firmware-authored control field, but not yet its device semantics; see
[`iboot-runtime.md`](iboot-runtime.md#current-bounded-checkpoint).

This does **not** prove every literal T8140 reset value or a kernel handoff. It
does prove the narrower question needed here:
missing early state can be constrained by firmware behavior and Apple device
trees, tested behind a fail-closed gate, and checked against the next observed
boundary without fabricating a chain of successful device replies.

## Cross-image evidence

`ipsw 3.1.713` remotely extracted the latest public 26.6.1 (`23G83`) iBoot and
DeviceTree payloads for three Apple models.  `ipsw img4 im4p extract` unwrapped
each LZFSE IM4P.  The earlier `/bin/echo` IM4P round trip in
[`iboot-image.md`](iboot-image.md) remains the positive control for this tool
chain.

| model | raw iBoot SHA-256 | self base | init routine | E threshold / DT L2 | P threshold / DT L2 |
| --- | --- | ---: | ---: | ---: | ---: |
| iPhone15,4 / d37 | `fb75c7ca35b4e83b819daa24cf63b61951b57b55e3ab67665e4af8e4ceec3aed` | `0x1fc064000` | `0x1fc09b388` | `0x3c0000 / 0x400000` | `0xf00000 / 0x1000000` |
| iPhone16,1 / d83 | `cc813476e33d09b23236997381a4b9942efb55152ef0404a0f5018705f5af67b` | `0x1fc07c000` | `0x1fc0b9f28` | `0x3c0000 / 0x400000` | `0xf00000 / 0x1000000` |
| iPhone17,3 / d47 | `0d021f9f562e09d2ee6f182cbf39122ed73317475afab1560f0cf9e3580eb153` | `0x1fc080000` | `0x1fc0bcf60` | `0x3c0000 / 0x400000` | `0x780000 / 0x800000` |

The d47 tree contains both `l2-cache-size-h17a=0x800000` and
`l2-cache-size-h17p=0x1000000`; the non-Pro d47 routine uses the former.  The
d37 and d83 trees expose `l2-cache-size=0x1000000` for their P cluster.  Every
firmware threshold is exactly 15/16 of the corresponding Apple tree's L2
size.  The E result is repeated unchanged across all three SoC generations;
the P result scales with the model's cache size.

All three images implement the same register algorithm:

1. return if bit 63 is already set;
2. compute a byte unit as `1 << register[13:8]`;
3. compute the requested count as `ceil(threshold / unit)`;
4. clamp it to the maximum count in register bits `[21:16]`;
5. enter the terminal PMGR failure path if `unit * count < threshold`;
6. write the count in bits `[5:0]`; and
7. poll bit 63 until it becomes set.

A second routine in every image clears bits `[5:0]`, writes the register, and
polls until bit 63 clears.  This independently establishes that bit 63 is the
active/completion state associated with a nonzero allocation request; it is
not an arbitrary success flag chosen to pass the first loop.

No exact access to this encoding was found in the paired SPTM, TXM, or BootKC.
The complete local consumer/producer surface is seven instructions in each
iBoot: initialization, a configured-size getter, and shutdown.  The current
m1n1 tree and its complete local Git history through
`940439b9a407fbfc499bea933269219f3f62d4c7` contain neither
`LLC_RAM_CONFIG` nor `L2_CRAMCONFIG`; m1n1's M3 SLC support is a different
memory-controller block and is not evidence for this CPU register.

## What can and cannot be inferred

The Apple tree establishes a 4 MiB E-cluster L2, but the register's unit/count
factorization is not mathematically unique.  Exact-capacity candidates include
`(shift,max-count)` values `(17,32)`, `(18,16)`, `(19,8)`, `(20,4)`, `(21,2)`,
and `(22,1)`.  Equivalent ambiguity exists for the P caches.  Static evidence
therefore cannot honestly claim the literal reset encoding.

The repeated 15/16 policy makes `(18,16)` the useful E-core hypothesis: it
represents sixteen `0x40000` units and makes iBoot request fifteen.  It also
scales coherently to `(20,16)` for a 16 MiB P cache and `(19,16)` for an 8 MiB
P cache.  This is consistent with allocating 15 of 16 cache ways, but the
available artifacts do not directly label the units as ways.  The experiment
therefore remains opt-in and is not the machine's default reset state.

The important distinction is behavioral versus literal equivalence.  An
emulator needs the observable contract consumed by its guest.  For the only
modeled CPU, that contract is a fixed geometry, a bounded low-field request,
and an active bit that follows the request.  It does not require knowledge of
unobserved reserved bits unless later code demonstrates a dependency on them.

## Gated implementation and runtime result

`DARWIN_IBOOT_LLC_WAYS=16` enables the experiment only in iBoot mode.  It:

- reads `cpus/cpu0/l2-cache-size` from the Apple-derived machine tree;
- requires the size divided by 16 to be a power of two;
- exposes only the observed geometry, request, and active fields;
- rejects writes that alter the inferred read-only geometry;
- derives bit 63 from whether the request count is nonzero; and
- rejects every gate value other than `16` before guest execution.

Without the variable, the register remains absent and the original failure is
reproducible.  No direct-boot path registers or initializes this experiment.

For the available research iBoot, the model starts at `0x00101200`
(`shift=18`, `max-count=16`).  Static instruction addresses and the runtime
trace agree:

```text
0x1fc0bc120  mrs  x2, S3_3_c15_c7_0
...
0x1fc0bc1a8  orr  x2, x2, x4
0x1fc0bc1ac  msr  S3_3_c15_c7_0, x2
0x1fc0bc1b0  mrs  x2, S3_3_c15_c7_0
```

```text
iBoot experiment: enabled inferred LLC_RAM_CONFIG: l2-size=0x400000 ways=16 unit=0x40000 reset=0x101200
iBoot experiment: LLC_RAM_CONFIG read pc=0x1fc0bc120 value=0x101200
iBoot experiment: LLC_RAM_CONFIG write pc=0x1fc0bc1a8 request=15 result=0x800000000010120f
iBoot experiment: LLC_RAM_CONFIG read pc=0x1fc0bc1b0 value=0x800000000010120f
```

QEMU reports the preceding translated PC for the write callback
(`0x1fc0bc1a8`); the MSR itself is statically at `0x1fc0bc1ac`.  The effective
capacity is `15 * 0x40000 = 0x3c0000`, exactly iBoot's E-core requirement.
The prior write of `0xa00a0005` to `0x3082b8030` is absent.

Complete positive-probe evidence is
`/tmp/dvm/iboot-main/probe/IBOOT_LLC16_FINAL_20260904.{stderr,serial}.log`.
The gate-off control is
`IBOOT_LLC_GATE_OFF_20260904`, which again stops at the PMGR write and
`PC=0x1fc0bc190`.  `IBOOT_LLC_BAD_GATE_20260904` proves an unsupported
hypothesis is rejected before execution.

## Resolved boundary: SEP ASC and entropy service

The first unsupported access after the LLC increment was:

```text
unimp: read  0x282608114 (sep[0]+0x8114) -> 0x0 size 4 pc=0x1fc09a4e4 el=2 pstate=0x800002c9 sp=0x1fc047e60 x0=0x3e1 x1=0x0 x2=0x0 x3=0x0
```

Attribution is exact:

- the Apple tree's `/arm-io` parent base is `0x210000000`;
- `/arm-io/sep` reg[0] is child `0x72600000`, length `0x88000`;
- `0x210000000 + 0x72600000 + 0x8114 = 0x282608114`;
- the raw tree calls this device `iop-sep,ascwrap-v6`; and
- research iBoot runtime `0x1fc09a4e4` (raw `+0x1a4e4`) is the 32-bit load.

The stop request was asynchronous. Two already-issued writes appeared after
the first line (`0x1000ff` to `+0x8800`, then zero to `+0x8808`), but no
behavior was inferred from the catch-all region's zero result.

The caller invokes that load at `0x1fc09a368` and tests status bit 16 at
`0x1fc09a36c`.  Existing independent BootKC analysis in
[`asc-mailbox-bulk-read.md`](asc-mailbox-bulk-read.md) identifies offset
`0x8114` as `I2A_CTRL` / `OUTBOX0_CTRL`, with FIFO count `[23:20]`, empty bit
17, full bit 16, and enable bit 0.  Thus the next work is not an unknown
four-byte SEP oracle: it is the already-understood ASC mailbox register
window.

The implemented increment:

1. In iBoot mode, instantiate the existing `darwin-sep` MMIO model for
   the raw-tree `iop-sep,ascwrap-v6` hardware even though the host-fixed tree
   deliberately removes `compatible` when SEP is disabled for direct boot.
2. Preserve direct mode's existing enable/disable policy.  Do not make the
   fixed tree globally enable SEP.
3. Let the existing ASC FIFO produce its actual empty/full/count state at
   `+0x8114`; do not force a branch-specific status constant.

This exposed request `0x10` on SEP bootstrap endpoint `0xff`. The request
table at runtime `0x1fc2a2280` (raw `+0x22280`) pairs it with response `0x74`
and timeout `0x1e8480`. The command wrapper at `0x1fc0995b0` is called only at
`0x1fc0b7d20` and `0x1fc0b7d3c`; those calls consume two 32-bit reply data
fields, combine them into a 64-bit nonzero value, and seed the xorshift routine
at `0x1fc23fadc`. The model therefore returns a freshly generated 32-bit word
with response opcode `0x74`. It does not return a fixed success value.

The prior no-response path polled status bit 17 and entered the timeout panic
at `0x1fc09a3e4` after 2,000,000 ticks. Its first apparent data abort was
inside the fatal path at runtime `0x1fc1d8454`, storing through an uninitialized
reset/watchdog object (`x9=0`, `FAR=0x1c`), rather than the original SEP
boundary. Complete successful protocol evidence is
`/tmp/dvm/iboot-main/probe/IBOOT_SEP_RNG_20260904.stderr.log`.

## Resolved boundary: Apple EL2 APIA key aliases

With SEP entropy available, QEMU's unsupported-register trace reports:

```text
w access to unsupported AArch64 system register S3_6_c15_c13_0 (pc = 0x00000001FC0B4120)
w access to unsupported AArch64 system register S3_6_c15_c13_1 (pc = 0x00000001FC0B4124)
```

Static disassembly is unambiguous:

```text
0x1fc0b4120  msr  S3_6_c15_c13_0, x1
0x1fc0b4124  msr  S3_6_c15_c13_1, x0
0x1fc0b4128  dsb  sy
0x1fc0b412c  isb
```

The caller at `0x1fc23fb7c` invokes the now-seeded 64-bit PRNG twice, places
the two results in `x1` and `x0`, and calls this four-instruction routine at
`0x1fc23fb8c`. The register map in
`qemu-sptm/scripts/darwin/sysregs.py` identifies the encodings as
`APIAKEYLO_EL2` and `APIAKEYHI_EL2`. QEMU already stores the architected EL1
APIA key in `CPUARMState.keys.apia.lo` and `.hi`, and its TCG pointer-auth
helpers consume that same state.

The implemented increment:

1. Add only `S3_6_c15_c13_0` and `S3_6_c15_c13_1` to the Apple CPU register
   set as PL2 read/write aliases of `env->keys.apia.lo` and `.hi`.
2. Do not implement them as write-ignore registers and do not synthesize key
   values; preserve the values generated by iBoot.
3. Register these aliases in iBoot mode only; do not expose unrelated c13
   encodings before a guest accesses them.

`IBOOT_APIA1_20260904` writes both nonzero guest-generated values and produces
no unsupported-system-register messages:

```text
iBoot experiment: APIAKEYLO_EL2 write pc=0x1fc0b4120 value=0x719c6dc69caf2c89
iBoot experiment: APIAKEYHI_EL2 write pc=0x1fc0b4124 value=0xfd965025f247d666
```

A debugger read through QEMU's architected `APIAKEY{LO,HI}_EL1` views returned
the same values written through the Apple EL2 aliases in that run. This proves
the aliases update the state consumed by QEMU's pointer-authentication helpers;
they are not write sinks. Complete evidence is in
`IBOOT_APIA1_20260904.{stderr,debug}.log` and
`IBOOT_APIA_GDB_20260904.{stderr,lldb}.log` under the probe directory.

## Resolved boundary: iBoot `root` scratch backing

The first exception after the APIA increment is a synchronous external data
abort at runtime `0xfffffc01fc1f33cc`:

```text
ESR_EL2 = 0x96000050
FAR_EL2 = 0x3f000010000
ELR_EL2 = 0xfffffc01fc1f33cc
```

The instruction is `stnp x1, x1, [x3,#0x30]` in the optimized zero-fill
routine at static `0x1fc1f3370`. At the instruction, `x0=0x3f000000000`,
`x1=0`, `x2=0x57fb0`, `x3=0x3f000000010`, `x5=0x58000`, and
`x30=0xfffffc01fc1f3860`. Its caller at `0x1fc24a220` is initializing iBoot's
statically named `root` region. The research build declares
`0x3f000000000..0x3f000057ce0`; the release build declares
`0x3f000000000..0x3f000057c98`. Both round to `0x58000` on iBoot's 16 KiB
pages.

The faulting address is a VA, so mapping it directly would be incorrect. A
debugger walk of the live iBoot tables establishes the physical ownership:

```text
TCR_EL2                 = 0x36516a516  (16 KiB, 42-bit TTBR0 VA)
TTBR0_EL2               = 0x00010001fc00c000
L1[0x3f] @ 0x1fc00c1f8 = 0x80000001fc01c003
L2[0x00] @ 0x1fc01c000 = 0x80000001fc020003
L3[0x00] @ 0x1fc020000 = 0x00e00001fc470e67
L3[0x04] @ 0x1fc020020 = 0x00e00001fc480e67
```

Thus `root` VA `0x3f000000000` maps contiguously from PA `0x1fc470000`.
The first four pages fall inside the loader's current
`[0x1fc000000,0x1fc480000)` RAM aperture. VA `+0x10000` maps to
PA `0x1fc480000`, exactly its first unbacked byte, which explains why the first
`0x10000` bytes clear successfully before the external abort. The full rounded
region ends at PA `0x1fc4c8000`.

The implemented increment:

1. Extend only iBoot's RAM aperture through physical `0x1fc4c8000`, covering
   the remainder of this page-table-proven `root` mapping.
2. Keep the existing image and BSS upper-bound validation at `0x1fc480000`;
   extending scratch backing must not make larger input images acceptable.
3. Validate that a supported image contains a `root` descriptor with VA
   `0x3f000000000` and a 16 KiB-rounded size of `0x58000` before granting the
   extra backing.
4. Do not map the adjacent `fs`, `storage`, or `usb` virtual regions yet.
   Re-run and stop at the first new boundary.

The table-walk evidence is in
`IBOOT_ROOT_PT{1,2,3}_20260904.lldb.log`; complete fault registers and matching
APIA state are in `IBOOT_APIA_GDB_20260904.lldb.log`.

The loader now grants exactly that physical backing while retaining
`0x1fc480000` as the image/BSS acceptance ceiling. It also requires the input
image to contain the evidenced `root` descriptor before granting the extra
RAM. Both pinned images passed the new validation. The research image reports:

```text
iBoot experiment: mapped [0x1fc000000,0x1fc4c8000) including root scratch [0x1fc470000,0x1fc4c8000), loaded 3952376 bytes at 0x1fc080000, entry +0x0, x0=0
```

The former abort at root VA `0x3f000010000` is absent. iBoot clears its root
scratch, returns from EL2 into EL0 at runtime `0xfffffc01fc1020a8`, issues
`svc #4` at runtime `0xfffffc01fc0b4278`, enters EL2 with
`ESR_EL2=0x56000004`, and returns to EL0 at `0xfffffc01fc0b427c`. This is a
genuine exception round trip, not a skipped instruction or fabricated return.
`IBOOT_ROOT1_20260904.debug.log` contains the exception trace. The
condition-bounded `IBOOT_ROOT_STOP_20260904` run stops on the first new
unsupported access, and `IBOOT_ROOT_RELEASE_20260904` confirms the release
image reaches the same physical boundary.

## Next boundary: PMGR-to-CPM bootstrap pair

The first unsupported access is now an EL0 32-bit read at research runtime
`0xfffffc01fc0c4c48` (static `0x1fc0c4c48`, raw image offset `0x44c48`):

```text
unimp: read  0x3082b8074 (pmgr[1]+0x38074) -> 0x0 size 4 pc=0xfffffc01fc0c4c48 el=0 pstate=0x800000c0 sp=0x300000a3fd0 x0=0x0 x1=0xfffffc01fc443cc8 x2=0xfffffc01fc443d38 x3=0xfffffc01fc2a3ff0
```

The immediately following instruction reads `0x3082b8078`. The zero shown in
the log is the low-priority unimplemented-region diagnostic value, not an
accepted device model and not evidence for a hardware reset value. The release
image reaches the same two physical reads at static/runtime-low-canonical
`0x1fc0c3f98` and `0x1fc0c3f9c` (raw offset `0x43f98`).

The device tree attributes physical `0x3082b8074` to `/arm-io/pmgr` register
range 1: `/arm-io` contributes base `0x210000000`, PMGR range 1 contributes
child base `0xf8280000` and length `0x80000`, and the access is offset
`0x38074` within the resulting `0x308280000` aperture. The boot CPU's
`cpm-impl-reg` is `0x210e40000` with length `0xc010`.

Static code at `0x1fc0c4c2c` establishes what iBoot does with the pair:

```text
0x1fc0c4c38  bl    0x1fc0b423c
0x1fc0c4c3c  mov   x8, #0x8074
0x1fc0c4c40  movk  x8, #0x82b, lsl #16
0x1fc0c4c44  movk  x8, #3, lsl #32
0x1fc0c4c48  ldr   w9, [x8]
0x1fc0c4c4c  ldr   w8, [x8, #4]
0x1fc0c4c50  orr   x8, x8, x9, lsl #32
0x1fc0c4c54  mov   w9, w0
0x1fc0c4c58  mov   x10, #0xc000
0x1fc0c4c5c  movk  x10, #0x10e4, lsl #16
0x1fc0c4c60  movk  x10, #2, lsl #32
0x1fc0c4c64  add   x9, x10, x9, lsl #24
0x1fc0c4c68  str   x8, [x9]
0x1fc0c4c6c  mov   w8, #0x5a01
0x1fc0c4c70  movk  w8, #5, lsl #16
0x1fc0c4c74  str   x8, [x9, #8]
```

Helper `0x1fc0b423c` obtains and caches an 8-bit CPU index, using `svc #4` when
uncached. The observed index is zero. The function combines the reads as
`(word_at_0x38074 << 32) | word_at_0x38078`, copies the 64-bit result to
`0x210e4c000 + (cpu_index << 24)`, then writes `0x55a01` eight bytes later.
This supports the narrow attribution "per-core CPM bootstrap/configuration
pair"; it does not establish a semantic register name or valid reset value.
The caller is `0x1fc083ac8`, with the call at `0x1fc083ad8`, reached from
`0x1fc1020b4`.

### Cross-image and consumer audit

`tools/re/iboot_cpm_scan.py` reproduces an aligned ARM64 constant/signature
scan over unwrapped payloads. The five-image control set gives:

| image | SHA-256 | load base | PMGR/CPM result |
|---|---|---:|---|
| d37 23G83 release | `fb75c7ca35b4e83b819daa24cf63b61951b57b55e3ab67665e4af8e4ceec3aed` | `0x1fc064000` | no exact A18 pair, tail, or command signature |
| d83 23G83 release | `cc813476e33d09b23236997381a4b9942efb55152ef0404a0f5018705f5af67b` | `0x1fc07c000` | no exact A18 pair, tail, or command signature |
| d47 23G83 release | `0d021f9f562e09d2ee6f182cbf39122ed73317475afab1560f0cf9e3580eb153` | `0x1fc080000` | pair at raw `+0x45af0`; CPM tail at `+0x4274c`, `+0x45b0c` |
| d47 24A5430a release | `fff9f51bf2f90487fbf04b2b9a091bc739865a5ec0793c03fbe469eeeb00d8e2` | `0x1fc080000` | pair at raw `+0x43f8c`; CPM tail at `+0x40c24`, `+0x43fa8` |
| d47 24A5430a research | `8e2a7ee4955871de9c577b555606495b636e743e35e58b6843983d98aabbb9cb` | `0x1fc080000` | pair at raw `+0x44c3c`; CPM tail at `+0x418bc`, `+0x44c58` |

This is a positive control as well as a negative comparison: all three d47
images independently match both command signatures and both exact address
materializations, while neither earlier-SoC image does. Their device trees
also distinguish the layout: d37/d83 declare each `cpm-impl-reg` as length
`0xb028`, whereas d47 declares `0xc010`; the new d47 code operates on the final
16 bytes beginning at `+0xc000`.

The d47 23G83 and 24A5430a initialization bodies are instruction-for-
instruction identical except for the relative `bl` to the CPU-index helper.
Each d47 image has exactly one other direct materialization of
`0x210e4c000`. In 24A5430a release it is the teardown/reset routine at static
`0x1fc0c0c10`:

```text
0x1fc0c0c1c  bl    0x1fc0b35f4
0x1fc0c0c20  mov   w8, w0
0x1fc0c0c24  mov   x9, #0xc000
0x1fc0c0c28  movk  x9, #0x10e4, lsl #16
0x1fc0c0c2c  movk  x9, #2, lsl #32
0x1fc0c0c30  add   x8, x9, x8, lsl #24
0x1fc0c0c34  str   xzr, [x8]
0x1fc0c0c38  mov   w9, #0x5a5a
0x1fc0c0c3c  str   x9, [x8, #8]
```

Thus the evidenced protocol is narrower than the original attribution:
initialization copies an opaque PMGR-originated 64-bit payload then writes
command `0x55a01`; teardown zeros the payload then writes command `0x5a5a`.
The two exact CPM-tail materializations in each image are stores, not loads.
This rules out an iBoot software branch on the copied bits along those direct
paths, but it does not make the payload arbitrary: physical CPM hardware is
the consumer.

The static d47 device tree supplies the PMGR range and CPM aperture addresses
and lengths, not contents for either word. Restore firmware contains no Boot
ROM image from which the power-on state could be reconstructed. The current
upstream m1n1 reference at commit
`940439b9a407fbfc499bea933269219f3f62d4c7` names only `PS0`, `PS1`, `PS2`, and
`PG0` in `PMGRRegs1`; it does not describe offset `0x38074` or the d47 CPM tail.
The cross-image scan therefore constrains ownership, width, ordering,
destination, commands, and lifecycle, but not the payload value.

### Historical blocker, resolved behaviorally

The earlier analysis correctly found that firmware cannot reveal the literal
power-on contents of `0x3082b8074..0x3082b807b`, but it overstated that fact as
an execution blocker. Cross-build analysis showed that iBoot only copies this
opaque pair into the per-core CPM tail and never branches on it along the
observed bootstrap path. The VM can therefore expose an explicit representative
payload while validating the independently known transport and command
protocol; this is behavioral equivalence, not a claim about T8140 reset state.

QEMU commit `6ae2067` implements that narrow model:

1. an iBoot-only subregion covers exactly range-1 offsets
   `0x38074..0x3807b` and returns an explicitly logged representative zero;
2. the CPM sink accepts only the exact 64-bit copied payload followed by
   command `0x55a01` at `0x210e4c000/+8`;
3. out-of-order, wrong-width, wrong-value, or extra accesses terminate QEMU;
   and
4. direct boot does not instantiate this model.

The validated log line is
`/tmp/dvm/iboot-main/probe/IBOOT_RANGE3_SECOND_EXACT_20260904.stderr.log:50`.
The same run reaches `0x300040000` at line 375. A real device capture could
replace the representative payload and improve fidelity, but the completed
transport experiment proves it is not required to continue iBoot execution.

## Regression

The rebuilt QEMU binary SHA-256 is
`25af47e1c31d96f172bce0aa22837be47da2167cf8b4da5d63a58223f9785107`.
`IBOOT_ROOT_DIRECT_20260904` ran the unchanged direct path: 294 serial lines,
zero XNU panics, `BSD root: md0`, `Early boot complete`, and the restore shell.
Logs are under `/tmp/dvm/iboot-main/probe/`.
