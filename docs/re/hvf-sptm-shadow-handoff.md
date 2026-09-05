# SPTM's real first tables and native shadow reads — 2026-09-05

**Subsequent integration:** see `hvf-integrated-shadow.md` for actual SPTM
execution with persistent native mappings. This document retains the earlier
capture/replay evidence and its then-current boot boundary.

Continue from `hvf-page-walker.md`. This work consumes real SPTM table bytes
and connects QEMU's permission-checked translation result to an actual native
read. It is still a bounded probe, not an integrated SPTM execution backend.

## Actual firmware capture

`tools/perf/native_sptm_tables.py` starts an isolated diskless Darwin/TCG guest
with the low-DRAM device tree. A breakpoint stops at physical `0x8070a3740`,
immediately before `MSR SCTLR_EL1, X0`, original firmware VA
`0xfffffff0270a3740`. The instruction bytes are checked (`00 10 18 d5`).
The vCPU stays stopped while the tool reads registers and follows table
descriptors. It writes no guest registers, patches no instructions, bounds
collection to guest RAM and 64 MiB, and terminates its owned process afterward.

`/tmp/dvm/HVF_SPTM_TABLES1/results.json` records hashes and these values:

| State | Value |
|---|---|
| PSTATE | `0x600003c8` (EL2, SP0) |
| TCR_EL2 | `0x010800336519a511` |
| TTBR0_EL2 | `0x807024000` |
| TTBR1_EL2 | `0x807110000` |
| Intended SCTLR (X0) | `0x12001010fc14793d` |
| MAIR_EL2 | `0x0c0d00ff01a040ff` |
| HCR_EL2 | `0x408000000` |
| SPRR_CONFIG_EL1 / EL2 | both zero |
| SPRR_PPERM_EL1 / EL2 | both `0x2020a52a302abaf5` |
| CURRENTG | zero |
| High VA base / RAM base | `0xfffffff000000000` / `0x800000000` |

The capture contains only five 16 KiB table pages:

- Low L1/L2/L3 at `0x807024000`, `0x807028000`, `0x80702c000`.
  The two leaves map physical/virtual `0x8070a0000..0x8070a7fff`, the
  bootstrap code. Adjacent low addresses are unmapped.
- High L1/L2 at `0x807110000`, `0x807114000`. The L2 table contains 256
  contiguous 32 MiB blocks covering the 8 GiB RAM region at the high VA base.

The ordinary privileged permissions at this checkpoint are RWX; the high
blocks have UXN set. SPRR is disabled here according to the captured registers
and existing model. This does not establish later SPRR/GXF configuration or
lock semantics. It explains why the first MMU handoff can be addressed before
the later protected contexts. The native boot still must implement the earlier
configuration writes faithfully; a checkpoint capture does not bypass them.

The TCG `max` capture and HVF `host` boot have different CPU feature profiles;
the raw HCR RW bit differs (`0x408000000` here versus `0x488000000` under HVF).
Both have E2H+TGE. Do not mistake this capture for another reproduction of the
HVF accessor clearing E2H/TGE, or assume the complete feature profiles match.

## Replay through the QEMU walker

`tools/perf/native_sptm_walk.py` starts a fresh Darwin/HVF instance, with
nested EL2 off, and places the five captured table pages at their original
physical addresses. It verifies the capture/firmware/device-tree hashes.
Only its probe code executes; SPTM is not resumed from the captured checkpoint.

The test uses the ordinary EL1 two-range walker with the captured TCR/TTBR,
MAIR and intended SCTLR state, while the probe's physical CPU initially has
its MMU off. This deliberately tests the common initial translation contract,
not complete virtual-EL2 state or exception semantics.

`/tmp/dvm/HVF_SPTM_WALK1/results.json`: 783/783 reads/writes/fetch translations
match the captured layout. Every high block is sampled, both low mapping ends
are checked, and the neighboring low/high holes fault at the expected table
level. All five source table hashes remain unchanged.

## Connecting a translation to native execution

`target/arm/hvf/ptw-native-probe.h` implements a temporary shadow-read operation
behind the existing opt-in walker probe. It accepts only an aligned normal-RAM
read covered by at least a 16 KiB source mapping. Smaller extents, unsupported
addresses/attributes, private-address collisions, and failed translations are
rejected. No larger mapping is inferred from a TCG invalidation extent.

It constructs separate low/high private 16 KiB tables, maps the translated RAM
page at a private IPA with the walker's R/W/X permissions (retaining physical
ROM write protection), and maps its helper RX and table backing read-only.
Private IPA ranges are checked against the QEMU address space. The helper
invalidates its stage-1 TLB context, enables its private MMU, executes one
actual LDR at the requested guest VA, and returns through SMC. Original CPU
registers, MMU/vector/debug state and timer mask are restored before unmapping
the private pages. Unexpected exits fail the probe rather than relaxing rights.

The first positive run, `/tmp/dvm/HVF_SPTM_SHADOW1/results.json`, passes all
783 translation checks plus 258 native permitted reads. Native values match
the original backing RAM, including the bootstrap code pages; all source-table
hashes remain unchanged. Final verification and the denied-alias control are
recorded below after the final build.

Final verification uses executable SHA-256
`712ca483f85775f156473d71b4ae731d254611ddc02b6d779a8856e4c7bf9dad`,
built in `/tmp/dvm/HVF_PTW_NATIVE_BUILD3.log`:

- `/tmp/dvm/HVF_SPTM_SHADOW3/results.json`: 783/783 translations and 258
  native reads pass; source tables remain unchanged.
- `/tmp/dvm/HVF_SPTM_SHADOW_DENY3/results.json`: removing read permission
  from the private native alias prevents completion. Native LDR stops at
  `0xe00000014`, VA `0xfffffff000001238`, IPA `0xe40001238`, syndrome
  `0x93d08006`. The probe restores the original context and does not advance
  its calling SMC. The case has `passed=false, verified_rejection=true`.
  All source-table hashes still match afterward.
- `/tmp/dvm/HVF_SPTM_SHADOW_PTW3/results.json`: original 48 walker cases pass.
- `/tmp/dvm/HVF_SPTM_SHADOW_HCR3/results.json`: three HCR/RO/NX regressions pass.
- `HVF_SPTM_SHADOW_TCG_REGRESSION3`: full TCG ramdisk reaches the shell,
  303 serial lines, zero panics (60-second probe, launch manifest recorded).
- `HVF_SPTM_SHADOW_NATIVE_BOUND3`: actual adapted SPTM remains at the earlier
  SPRR fault, ELR_EL2 `0x8070a3520`, HCR_EL2 `0x488000000`, ESR_EL2
  `0x02000000`, PC zero, no serial output. The checkpoint probe did not
  silently advance that boot or bypass its missing compatibility operations.

The first denied-alias assertion incorrectly expected a permission FSC. HVF
reports this no-read mapping as a stage-2 translation abort (FSC=6) on the
tested host. The final control asserts the observed native instruction,
syndrome and exact IPA/VA. This host fault is not delivered to iOS as an
invented guest permission fault; the experimental operation stops and fails.

The native helper also explicitly rejects combined stage-1/stage-2 regimes,
whose TCG invalidation extent is unsuitable as a shadow-mapping extent, and
retains flattened read-only alias restrictions as well as ROM restrictions.
The test does not validate all those unsupported regimes by refusing them.

Host-only suite: 23/23 (`/tmp/dvm/HVF_SPTM_SHADOW_HOST1.log`). Python compilation,
shell syntax, and whitespace checks pass. Checkpatch: zero errors and one
generic MAINTAINERS warning for the new experimental headers. All owned guests
have exited. No display instances or protected project files were changed.
Everything remains uncommitted and unpushed in `codex/arm-native-experiments`.

This is a synchronous single-vCPU read primitive. It does not yet support
persistent mappings, guest table-write interception, DMA invalidation, context
switches, native instruction patching, GXF, interrupts, multicore, or snapshots.
The next production step is a persistent mapping/context owner connected to
SPTM's virtual EL2 register writes and actual instruction execution. Do not
report this checkpoint probe as an iOS native boot or a speedup measurement.

```sh
python3 tools/perf/native_sptm_tables.py --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --out /tmp/dvm/FRESH_TABLES
python3 tools/perf/native_sptm_walk.py --capture /tmp/dvm/FRESH_TABLES \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree --native-read --out /tmp/dvm/FRESH_SHADOW
python3 tools/perf/native_sptm_walk.py --capture /tmp/dvm/FRESH_TABLES \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree --native-read --deny-native-read \
  --out /tmp/dvm/FRESH_SHADOW_DENIED
```
