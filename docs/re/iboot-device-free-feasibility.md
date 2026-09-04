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

The first unsupported operation after that increment is now exact: writes to
Apple EL2 aliases for the low and high halves of the APIA pointer-authentication
key. The later observed zero-fill abort for iBoot's `root` region is recorded
separately and has not been papered over with unowned RAM.

This does **not** prove the literal T8140 register reset value, a kernel
handoff, or a bootable SEP path.  It proves the narrower question needed here:
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

## Next boundary: Apple EL2 APIA key aliases

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

Implementation-ready next increment:

1. Add only `S3_6_c15_c13_0` and `S3_6_c15_c13_1` to the Apple CPU register
   set as PL2 read/write aliases of `env->keys.apia.lo` and `.hi`.
2. Do not implement them as write-ignore registers and do not synthesize key
   values; preserve the values generated by iBoot.
3. Re-run with unsupported-register, exception, and serial tracing. Stop at
   the first newly exposed boundary before adding any other c13 aliases.

The next observed exception after QEMU currently ignores those writes is a
data abort at runtime `0xfffffc01fc1f33cc` (`stnp x1, x1, [x3,#0x30]`), with
`FAR=0x3f000010000`. The function entered with `x0=0x3f000000000`, `x1=0`,
and `x2=0x58000`; its caller zero-fills iBoot's statically declared `root`
region (`0x3f000000000..0x3f000057ce0`, rounded to `0x58000`). Nearby
descriptors name `fs`, `storage`, and `usb` regions at `0x3f004000000`,
`0x3f008000000`, and `0x3f00c000000`. This is evidence for a later load-region
or translation requirement, but it is not yet authority to map that range:
the two unsupported APIA writes occur first and may affect subsequent control
flow. Exception evidence is in
`IBOOT_POST_RNG_INT_20260904.int.log`; unsupported-register evidence is in
`IBOOT_POST_RNG_GUESTERR_20260904.debug.log`, both under the probe directory.

## Regression

The rebuilt QEMU binary SHA-256 is
`39401c50ba5e12e3daa14483c0b9341d0cad1447fbe74b2f037f4aed0acfef93`.
`IBOOT_SEP_RNG_DIRECT_20260904` ran the unchanged direct path: 295 serial
lines, zero XNU panics, `BSD root: md0`, `Early boot complete`, and the restore
shell. Logs are under `/tmp/dvm/iboot-main/probe/`.
