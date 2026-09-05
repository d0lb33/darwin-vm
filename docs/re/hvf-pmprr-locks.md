# PMPRR and the observed non-guarded lock sequence — September 5, 2026

**Latest continuation:** [guarded execution and cache maintenance](hvf-gxf-bootstrap.md).
GXF entry and the observed guarded permission changes now execute. This
note preserves the preceding PMPRR checkpoint and its hardware evidence.

Continuation of `hvf-context-stores-sprr.md`. Worktree and branch remain
`/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`,
`codex/arm-native-experiments` in both repositories. Changes are uncommitted.

## Independent evidence

`tools/perf/native_sprr_capability.py` and its assembly payload create a fresh,
isolated HVF VM for each register access. No firmware or register emulation is
involved. `/tmp/dvm/HVF_PMPRR_NATIVE1/results.json` records seven protected
register cases at each physical EL: PMPRR_EL1/EL2 reads and write/read pairs,
CONFIG_EL1/EL2 write/read pairs, and PMPRR_EL12 read. All fourteen stop before
the first register operation completes. EL1 exits to HVF with EC=0x18; EL2
takes a guest undefined-instruction exception (ESR=0x02000000). CurrentEL
controls complete natively at both levels and return 4/8. This prevents using
this public HVF guest to measure these Apple register semantics directly.
It does not show that emulating them is impossible.

The original SPRR article's final notes mention the private entitlement
`com.apple.private.hypervisor.vmapple`. A separate ad-hoc-signed scratch probe
with that entitlement (`HVF_PMPRR_VMAPPL1`) was killed before its CurrentEL
control produced output. The targeted system log records amfid error -424:
the file is ad-hoc signed but contains restricted entitlements. See
`/tmp/dvm/HVF_PMPRR_VMAPPL1.system.log:2`. No host security settings were
changed. Rebuilding with only the public hypervisor entitlement
(`HVF_PMPRR_NATIVE2`) again completes both controls and records fourteen
protected-register faults. Thus the private entitlement is not an ordinary
signing shortcut on this host; this is separate from the software bridge.

The [original SPRR research](https://blog.svenpeter.dev/posts/m1_sprr_gxf/)
documents permission encodings and limited configuration-lock observations,
but does not describe PMPRR. Apple's older
[access-permissions patent](https://patents.justia.com/patent/9852084)
describes APRR-style permission masks, not this SPRR encoding; its formulas
must not be transplanted into PMPRR.

The M5 field dump linked in the preceding note identifies sixteen two-bit
PMPRR fields. SPTM programs `0x40010`: field 2=1 and field 9=1, all others
zero. The following observations come from the actual input firmware, not
the project's generated register-storage callbacks:

| Image, unslid instruction VA | Operation |
| --- | --- |
| SPTM `0xfffffff0270a382c` | PMPRR_EL1=0x40010; VHE redirects to EL2 |
| SPTM `0xfffffff0270a3840` | CONFIG_EL1=0xfb |
| kernel `0xfffffff00ac56fe8` | PPERM entry 2 changes A to B (bit 8) |
| kernel `0xfffffff00ac57024` | PPERM entry 2 restored to A |
| kernel `0xfffffff00ac608ac` | PPERM entry 9 changes 2 to 3 (bit 36) |
| kernel `0xfffffff00ac608f8` | PPERM entry 9 restored to 2 |
| kernel `0xfffffff00ac5099c..509a8` | UMPRR read, shifted by twice the PTE index, tested with 3 |

`tools/perf/native_sprr_sites.py` records candidate register instructions and
input SHA-256 values in `/tmp/dvm/HVF_PMPRR_SITES1.json`. It found 72 candidates
in SPTM, zero in TXM and 84 in the kernelcache's executable segments. These
are candidate instruction counts, not runtime counts. The kernel sites above
were then disassembled with `tools/re/kdis.py` and attributed to
`com.apple.kernel` using `tools/re/kc_text_map.py`.

At SPTM `0xfffffff0270f50ac..50b8`, input flag bit 3 becomes UMPRR bit 18,
and flag bit 0 becomes UMPRR bit 11. Thus the selected fields are index 9
bit 0 and index 5 bit 1. UPERM's baseline nibbles are respectively 2 and 1.
This is consistent with allowing their low permission pairs to switch to 3.

**Inference and implementation boundary:** together these sites support the
interpretation that the observed PMPRR fields select mutable low PPERM bits
in the locked non-guarded context. They do not establish every possible mask,
configuration, guarded-bank interaction, or whether forbidden hardware writes
are ignored versus raising an exception. Tests of our model cannot establish
those missing hardware facts.

## Implementation scope

`target/arm/hvf/virtual-sprr.h` accepts the observed sequence from CONFIG=1,
zero masks/range: PMPRR=0x40010, then CONFIG=0xfb. In the locked context,
PPERM changes are limited to bits 8 and 36; all other bits, including guarded
permission pairs, must remain unchanged. Other modeled SPRR writes must be
identical to their stored value. Disallowed changes stop before mutation;
this intentionally does not claim hardware's exact forbidden-write response.

The zero-mask CONFIG=0/1 paths keep their earlier behavior. Unknown masks,
nonzero UMPRR/AMRANGE, active lower-EL SPRR, guarded execution and GXF remain
unsupported. After mask programming, intermediate unlocked permission-bank
changes also stop until independently characterized. The dispatcher continues
to reject writes to shadow banks and other unimplemented protection controls.
Every accepted write invalidates native aliases before further execution.

No host-native Apple protection registers are used. The ordinary QEMU state
and permission walker still decide guest permissions. TCG behavior is not
changed by these HVF-only checks, and no Windows acceleration is claimed.

## Validation and current boot boundary

Build: `/tmp/dvm/HVF_PMPRR_BUILD1.log`.
Final QEMU SHA-256:
`e110b7673f2e62da2ab1d3354330d1d3190a156dc219e442f8c314c06e94fb53`.

- `HVF_PMPRR_TEST1/results.json`: **66/66 pass**, including all preceding
  53 SPRR tests and thirteen mask/lock cases. The original `allowed` metadata
  on the thirteen new cases was corrected in the harness; assertions checked
  exact PCs, banks and bytes correctly in this run.
- `HVF_PMPRR_TEST2/results.json`: **13/13 pass**, repeated with corrected
  final-access metadata and explicit completed-write counts. Mask setup alone
  leaves indices 2/9 read-only; authorized low-bit changes permit writes;
  revocation removes writable aliases. Guarded-bit, other-index, CONFIG
  unlock, PMPRR unlock and UPERM changes stop with stored state unchanged.
- `HVF_PMPRR_BOOT1.virtual.json`: actual adapted SPTM, with no debugger test
  fixture, completes PMPRR=0x40010 at runtime `0xfffffff0070a382c`,
  CONFIG=0xfb at `...3840` and the following TLBI. It stops before
  **GXF_CONFIG_EL2=1 at `0xfffffff0070a38e8`**. All five bootstrap table
  hashes match the original TCG checkpoint. Still no serial or `Early boot`.
- `HVF_PMPRR_TCG1.log`: 60-second TCG ramdisk regression, **reached shell
  yes, 303 serial lines, zero XNU panics**.
- Host regressions 23/23 (`HVF_PMPRR_HOST1.log`), new-header checkpatch,
  Python compilation and diff-whitespace checks pass.

Next is GXF configuration and guarded entry/return. These must switch the
permission view and invalidate aliases, preserve separate saved exception
state, enforce entry-target permissions and retain denied-access tests.
`GENTER` must not be implemented merely by jumping to an entry address.
Full CTRR/CTXR enforcement, guest exception delivery, timers/interrupts, MMIO,
lower-EL execution, multicore, snapshots and measured native performance are
still unfinished; the preceding note's controlled-firmware limitations apply.
