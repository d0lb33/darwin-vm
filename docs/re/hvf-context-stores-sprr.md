# HVF context replacement, checked stores and SPRR — September 5, 2026

**Latest continuation:** [guarded execution and cache maintenance](hvf-gxf-bootstrap.md).
Actual boot now passes GXF entry, guarded permission setup and cache
maintenance. The stopping point below is historical.

This is the latest continuation of `hvf-integrated-shadow.md`. Adapted SPTM
now builds and switches to its next translation tables, performs 41 checked
table stores, enables SPRR and verifies its privileged permission bank.
The current stop is **SPRR_PMPRR_EL2=0x40010 at
`0xfffffff0070a382c`**. There is still no serial output or `Early boot`.

Use `/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`, branch
`codex/arm-native-experiments` in both repositories. These HVF changes,
headers, tests and notes remain uncommitted/unpushed. Pulling the committed
branch alone will not transfer them. The display checkout was not changed.

## Verified execution progression

Every capture below is `/tmp/dvm/<tag>.virtual.json`, with the run's private
tag locating its full stderr under `/tmp/dvm/probe/`. All five original
bootstrap table pages still match the original TCG checkpoint in every run.
Later table modifications affect newly constructed tables, not those five.

| Capture | Last executed milestone / next stop |
| --- | --- |
| `HVF_CTRR_BOOT2` | Programs 12 inactive CTRR C/D and CTXR A–D bounds; 267 native full TLB invalidations; stops on STR64 at `0xfffffff0070d9a28`. |
| `HVF_CODESTORE_BOOT1` | Checked debug-data STR completes; stops before TCR_EL2 write at `0xfffffff0070d9a4c`. |
| `HVF_CONTEXT_BOOT1` | TCR and both TTBR writes complete; stops on an actual table-entry clear at `0xfffffff0070d5d74`. |
| `HVF_TABLESTORE_BOOT1` | 41 table stores complete; stops before SPRR_CONFIG_EL2=1 at `0xfffffff0070a37dc`. |
| `HVF_SPRR_INTEGRATED_BOOT2` | SPRR enable, PPERM comparison and UPERM programming complete; stops before PMPRR at `0xfffffff0070a382c`. |

The latest run has 1,616 mapping-install events across 46 complete shadow
invalidations. This is not 1,616 simultaneously installed pages or a boot
performance measurement. It emulates 42 STR64 operations: one executable-page
debug-data write and 41 page-table stores. SPTM/ledger hashes remain unchanged
from the earlier adapted firmware; test-only debugger patches are not present
in these actual-boot runs.

Final binary SHA-256:
`92c065b3065c8e6809ce0ceec582a83a8969408134f49a69d10402e55fe0c15f`.
Build log: `/tmp/dvm/HVF_SPRR_INTEGRATED_BUILD2.log`.

## Inactive protection bounds and TLBIs

`target/arm/hvf/virtual-protection.h` accepts initial CTRR C/D and CTXR A–D
lower/upper programming only while each corresponding core control is zero.
It rejects active/locked controls and addresses outside the observed 42-bit
range; low 12 bits are masked on stored readback. The firmware supplies byte
upper bounds, then checks page-aligned register values at original SPTM VAs
`0xfffffff0270b42d0` and `0xfffffff0270b4334`.

VMALLE1, VAALE1 and RVAALE1, including their observed NXS forms, request the
existing native helper's full invalidation. A stronger full invalidation is
used even for VA/range operations. Accepted context changes and table stores
separately discard all checked aliases, so a TLBI never preserves permissions
derived from a modified table. General cache operations remain unsupported.
`HVF_CTRR_RANGES2/results.json` records 48/48 initial range controls, including
readback of rejected backing values; later regression results are below.

This is not full CTRR/CTXR enforcement. Core/ACC initial range semantics and
CTXR permission/lock activation remain unresolved. Existing generated register
storage callbacks are not evidence of hardware enforcement.

## Checked STR64 without native write escalation

At original SPTM VA `0xfffffff0270d9a28`, `STR x0,[x8]` writes a debug field
at runtime VA `0xfffffff0070a2018`, PA `0x8070a2018`. The page includes the
`DEBG` header at offset 0x2000 and executable bootstrap code elsewhere.
Guest tables permit writing the field; our executable-backing guard denied it.

`hvf_vsh_guarded_store()` in `virtual-shadow.h` decodes only aligned,
unsigned-immediate STR64, without writeback/exclusive semantics. It rewalks
the exact guest address for write permission, requires normal writable RAM,
and conservatively excludes core CTRR A–D write-protected ranges. For an
executable backing it validates the resulting complete page before writing
eight bytes and flushing the native instruction cache. Native writable aliases
are never granted. Unsupported instructions or permissions stop at the store.

The same checked operation now handles tracked table backing. Once the write
completes, all native aliases are removed before any further guest execution;
subsequent accesses are rewalked. A guest read-only table alias remains denied.
This is a correctness-first full invalidation, not an optimized dependency graph.
DC ZVA still refuses table/code targets; unsupported atomic/paired/table stores
still stop. DMA writes and multicore coherence are not implemented.

Actual table evidence: `HVF_CONTEXT_TABLE_CAPTURE1.virtual.json` captures
the new high root `0x807068000`, L2 `0x807070000`, and L3 `0x8165a8000`.
The mapping for VA `0xfffffff03ae3e440` has leaf PTE at `0x8165a9c78`, value
`0x200008165a8603`, AP=0, pointing into the table itself. Its target
`0x8165aa440` contains `0x6000081698c603`, mapping VA `0xfffffff03b220000`
to PA `0x81698c000`. SPTM's `STR xzr,[x0]` at `0xfffffff0070d5d74` clears
that entry. The guest's own mapping permits this table write.

## Context replacement

The real switch installs TCR_EL2 `0x10800336519a515`, TTBR0_EL2
`0x80706c000` and TTBR1_EL2 `0x807068000` at runtime PCs
`0xfffffff0070d9a4c`, `...9a6c`, and `...9a7c`.

Accepted writes invoke the actual architectural register callback, remove
every guest IPA alias, clear private shadow tables/dependencies, and construct
fresh roots. The unchanged private helper flushes native TLBs with the MMU
off before resuming. Guest translation faults refill only permissions permitted
by the new context. Unsupported granules/widths are rejected before mutation.
Current TCR/TTBR changes are still restricted to SPRR/GXF disabled; later
changes in active protection contexts need their own validation.

## Initial SPRR enforcement

`virtual-sprr.h` permits CONFIG.EN=0/1 and PPERM/UPERM writes only in the
initial unrestricted virtual EL2 context. Configuration/lock bits other than
EN, an active lower-EL SPRR bank, guarded mode, and nonzero PMPRR/UMPRR/AMRANGE
state are rejected. UPERM storage does not imply EL0 execution is implemented.

The existing explicit-I/O ARM walker applies the AP/UXN/PXN-indexed permission
table from `target/arm/ptw.c:get_S1prot_sprr()`, matching the
[Asahi SPRR permission table](https://asahilinux.org/docs/hw/cpu/sprr-gxf/).
Every accepted SPRR write invalidates native aliases before the new bank can
take effect. Hardware executes ordinary instructions using those checked
permissions; Apple SPRR hardware registers are not borrowed from the host.

`native_virtual_sprr.py` boots real SPTM to the enable site and installs a
small test fixture there. It enables SPRR, primes a native data alias, updates
the selected permission nibble through the dispatcher, then performs a native
read, write or fetch. An independent expected table covers all 16 nibbles and
all three access types, including execute-only. It verifies target bytes,
register state, source-table/code hashes, and exact stop PCs. Additional cases
disable SPRR after denial, reject CONFIG=0xfb, and reject pre-existing PMPRR,
UMPRR and AMRANGE state. The latter are established by real MMU-off guest MSRs
and read back, not by ineffective GDB system-register writes.

`HVF_SPRR_INTEGRATED_TEST2/results.json`: **53/53 pass on the final binary**.
This tests the current EL2 context and one selected PTE index, all 16 nibbles;
it does not claim every EL/GL/configuration combination. The earlier standalone
192-case fixtures are separate evidence, not full integrated GXF validation.

## Current boundary and correction to earlier notes

At original VA `0xfffffff0270a382c`, word `0xd51ef320` is
`MSR S3_6_C15_C3_1,x0`. `scripts/darwin/sysregs.py` identifies this as
PMPRR_EL1, redirected under VHE to **PMPRR_EL2**, not AMRANGE.
The live stop confirms the resolved name and requested `x0=0x40010`.

The [M5 register field dump](https://gist.github.com/justtryingthingsout/2f5213c5fa7d64e4db7e30caac7f5e9c)
labels PMPRR with sixteen two-bit MASK fields. Field names alone do not prove
which writes/accesses each mask restricts or its GL/lock interaction. The
generated project callbacks only store these registers. Do not add the write
to an allowlist without implementing and testing its actual restriction.
The following CONFIG=0xfb at `...a3840` and later GENTER also remain unsupported.

Next: establish PMPRR and configuration-lock semantics from authoritative
code or discriminating firmware/hardware evidence; test denied bank changes
and EL/GL differences before enabling those paths. The current stop is an
unimplemented compatibility boundary, not proof of a fundamental HVF API
blocker. The earlier direct-native-EL2 high-VA/VHE limitation remains separately
documented in `hvf-sprr-vhe-feasibility.md`.

## Regression evidence and remaining scope

On the immediately preceding build (`37b9882865804e19fac438acc01882cefa6d5bc883eaa5dc355ba86a69faf4e9`),
`/tmp/dvm/HVF_SPRR_FINAL_<name>/results.json` records:

| Name | Verified cases |
| --- | ---: |
| CODE | 5/5, exact nonzero write and same-alias unsafe retry included |
| CONTEXT | 9/9, new roots, stale data/rights, RO/NX/unmapped/table-RO |
| TABLE | 6/6, clear/remap/restrict/self-unmap plus forbidden writes |
| ZVA | 5/5 |
| COUNTER | 4/4 |
| FP | 4/4 |
| RANGES | 48/48 |
| HCR | 3/3 |
| PTW | 48/48 |

That build also passes all 50 original integrated SPRR cases and the 60-second
`HVF_SPRR_FINAL_TCG` ramdisk probe: **reached shell yes, 303 serial lines,
zero XNU panics**. The final build adds only the SPRR pre-existing-restriction
guard, validated by the 53-case matrix above and repeated actual boot.
`HVF_SPRR_GUARD_DENY` on the final binary still faults on the first denied
fetch at `0x8070a3744`, ESR `0x8600000f`; source tables match.
`HVF_SPRR_GUARD_TCG` repeats the 60-second TCG check on the final binary:
**reached shell yes, 303 serial lines, zero XNU panics**, final PC
`0xfffffff02aaf76e4`. Host-only suite 23/23, Python compilation, shell syntax,
diff whitespace checks and new HVF header checkpatch checks pass.

The HCR tests retain the native/helper state and RO/NX faults. Separately,
`target/arm/helper.c:hcr_write()` makes RW read-as-one/write-ignored for an
AArch64-only EL1 CPU profile. This explains why raw requested `0x408000000`
can be stored as `0x488000000` with the HVF host profile; it must not be
confused with the earlier independently measured HVF API accessor corruption.
The integrated virtual EL2 bank remains separate from physical EL1 execution.

This is still an opt-in, single-vCPU controlled-firmware experiment. Guest
exception delivery, lower-EL/GXF transitions, full Apple range/lock enforcement,
MMIO, interrupts/timers, DMA coherence, reset and snapshot restoration remain
unfinished. Private helper visibility and runtime code/ledger attestation are
not hardened guest isolation. Migration stays blocked rather than exporting
incomplete state. TCG remains the default and fallback, with Windows untested
here. Guest translation/permission decisions use common QEMU state/walker;
native mapping and register installation stay in HVF-specific code.

Reproduce an actual boot with fresh output names:

```sh
python3 tools/perf/native_virtual_boot.py --tag FRESH_SPRR_BOOT \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --sptm /tmp/dvm/HVF_VSH_SPTM2.macho \
  --ledger /tmp/dvm/HVF_VSH_SPTM2.ledger --shadow \
  --check-tables /tmp/dvm/HVF_SPTM_TABLES1/results.json
```

The four new real-SPTM fixtures are `native_virtual_code_store.py`,
`native_virtual_context.py`, `native_virtual_table_store.py`, and
`native_virtual_sprr.py` in `tools/perf/`. They accept the same dtree, SPTM
and ledger arguments plus a fresh `--out` directory. Each owns and cleans up
only its own process. A probe return code of zero means capture succeeded,
not that iOS booted.
