# SPRR mappings and the native VHE translation boundary — 2026-09-05

This is a continuation of `hvf-native-progress.md`, not a completed iOS boot.
The actual SPTM boot still stops at `MSR SPRR_CONFIG_EL1, X0` at
`0x8070a3520`. No firmware fault was bypassed. These new experiments establish
that a permission-enforcing shadow-mapping primitive works, but the current
native-EL2 design has a separate VHE translation blocker on this host.

Subsequent work: `hvf-virtual-el-context.md` now records a native, bounded
virtual-EL2-at-EL1 transition prototype, including EL0/SVC and SPSel=0 controls.
The remaining-work list below describes the state before that prototype.

Host: Apple M5 Max, macOS 27.0 (26A5421a). All probes are diskless and use
public Hypervisor.framework APIs. No kernel GIC, display instance, or disk
image is involved. No QEMU executable rebuild or production boot-path change
was needed for these experiments. All native changes remain uncommitted.

## SPRR leaf enforcement works in native mappings

`qemu-sptm/target/arm/apple-sprr.h` decodes the AP/UXN/PXN index and the
EL/GL permission nibble. The permission tables agree with the existing
`get_S1prot_sprr()` in `target/arm/ptw.c` and the Asahi reference. This header
has no host API dependency. It does not implement configuration locks,
register banking, GXF transitions, or a page-table walker, and is not yet
used by the production QEMU backend.

`tools/perf/native_sprr_shadow.S/.c/.py` builds 16 KiB shadow leaves with
ordinary stage-1 permissions, selecting private IPA aliases whose HVF
stage-2 mappings enforce the decoded read/write/execute rights. Two different
IPAs map the same host RAM page with different permissions.

The runner checks both the synthetic all-nibble fixture
`0xfedcba9876543210` and SPTM's actual PPERM `0x2020a52a302abaf5`, every
PTE index, both EL/GL policies, and read/write/execute. Python independently
specifies the expected permission table and checks the count of actual
allowed and denied accesses; a C `passed` flag alone is insufficient.

For each of 192 cases, eight native accesses check:

1. The requested access through the selected SPRR alias.
2. Read, write, and denied execution through the second RW/NX alias.
3. Read through the first alias after the guest's write through the second.
4. Re-prime the requested access immediately before revocation.
5. Revoke the first alias with `hv_vm_protect(..., 0)` and retry: it must fault.
6. Restore exactly the original rights and repeat the access.

There is no guest TLBI or MMU toggle between an allowed priming access and
its revocation test. This matters particularly for execute-only pages: an
earlier test sequence put a denied read/fault helper between priming and
revocation and was weaker evidence of cached-right revocation.

Denied accesses exit with a host stage-2 abort. The fixture then delivers an
EL2h or EL1h guest stage-1 permission fault with exact ESR/FAR/ELR/SPSR.
It does not retry with widened rights. The native guest handler records these
registers, and the host checks that forbidden accesses did not modify data.
For EL2, the previously tested native HCR helper restores HCR after API state
writes; the handler checks HCR too. This is a masked synchronous fault fixture,
not a general exception-injection implementation.

Final evidence:

- `/tmp/dvm/HVF_SPRR_SHADOW_EL2_FINAL2/results.json`: 192/192 cases,
  1,536 accesses, native EL2 at low virtual addresses.
- `/tmp/dvm/HVF_SPRR_SHADOW_EL1_HIGH_FINAL2/results.json`: 192/192 cases,
  1,536 accesses, ordinary EL1 at high virtual addresses
  (`0xffffff8080000000` base), nested EL2 disabled.
- `/tmp/dvm/HVF_SPRR_SHADOW_EL1_LOW1/results.json`: an earlier seven-access
  version passed 192/192 ordinary EL1 low-address cases.

The final runner stores source and binary hashes. Reproduce with fresh output
paths:

```sh
python3 tools/perf/native_sprr_shadow.py --out /tmp/dvm/FRESH_SPRR_EL2
python3 tools/perf/native_sprr_shadow.py --el1-control --high-va \
  --out /tmp/dvm/FRESH_SPRR_EL1_HIGH
```

**Limits:** the source PTE remains untouched but the fixture constructs its
shadow leaf directly. No real SPTM page tables are walked or replaced yet.
The GL parameter chooses a permission table; it does not perform GENTER/GEXIT.
The test does not prove tracking/invalidation of guest table writes, context
switches, EL0, multicore coherence, or snapshot restoration. A mapper must
protect every writable alias of a source table and handle guest/DMA writes;
protecting just one IPA does not protect all aliases of that host page.

## Native EL2 lacks the required high-address translation

`tools/perf/native_vhe_mmu.S/.c/.py` uses separate TTBR0/TTBR1 trees. At one
VA offset the low tree maps data `0x1234`, while the high tree maps `0xabcd`.
The guest executes in the selected half, loads its marker, and records HCR
and CurrentEL directly to RAM. Different marker pages prevent a false positive
from accidentally reusing the low mapping. Low identity vectors record actual
faults without depending on the high mapping being functional.

The direct runner does not call an HCR accessor. It uses no kernel GIC or
QEMU. Native and TCG controls use GPA `0x40200000` and identical table indices.

`/tmp/dvm/HVF_VHE_MMU_NATIVE_FINAL2/results.json` records 24 runs:
HCR `0x80000000`, `0x480000000`, `0x488000000`, low/high VA, and four modes.

| Setup mode | Result |
|---|---|
| 0: native MSR TTBR1_EL2 | Undefined instruction at `0x40200014`, ESR_EL2 `0x02000000`, for all three HCR values. |
| 1: set TTBR1_EL2 through HVF API, omit native MSR | API succeeds; low access works, high instruction fetch gets ESR_EL2 `0x86000004`. |
| 2: omit TTBR1 setup | Same low success / high failure control. |
| 3: advertise VH=1 through ID_AA64MMFR1_EL1 API, then native MSR | API accepts the feature bit (`0x100011312000` to `0x100011312100`), but native TTBR1 still faults. Handler HCR is now `0x80000000`; this does not enable VHE. |

The high fault in modes 1/2 is at `0xffffff8040200034` in this payload.
HCR remains exactly the requested value in those modes. This is independent
of the previously solved HCR API getter/setter corruption.

Only six ordinary-low-address cases pass. The native runner intentionally
returns nonzero: it must not relabel unsupported translation as success.
There is no claim that these results apply to all future macOS/Apple CPUs.

`/tmp/dvm/HVF_VHE_MMU_TCG_FINAL2/results.json` passes all four controls:
E2H and E2H+TGE, each with low and high execution. It uses the same assembly
except that terminal SMCs are replaced with stop loops for debugger capture.
The high cases actually execute at `0xffffff804020004c` and load `0xabcd`.
This validates the tables and test expectation independently of HVF.

The tested TCG QEMU hash is unchanged:
`f73584f15ef2a489e740394d7f53dc216d7f815cecc70f9eb558444aa2e3c3c7`.

```sh
# Expected to fail the unsupported native VHE cases on the tested host:
python3 tools/perf/native_vhe_mmu.py --out /tmp/dvm/FRESH_VHE_NATIVE
# Must pass the table/instruction controls:
python3 tools/perf/native_vhe_mmu.py --tcg --out /tmp/dvm/FRESH_VHE_TCG
```

Firmware relevance is concrete, not just a synthetic feature request:
SPTM writes TCR_EL1 at `0xfffffff0270a3568`, TTBR1_EL1 at
`0xfffffff0270a3580`, and SCTLR_EL1 at `0xfffffff0270a3740`, with its EL2
path enabling E2H earlier at `0xfffffff0270a34a4`. The translated return
address is formed at `0xfffffff0270a37a4..0xfffffff0270a37a8` before RET at
`0xfffffff0270a37b8`. Disassemblies are in
`/tmp/dvm/HVF_SPTM_MMU_HANDOFF1.disasm` and
`/tmp/dvm/HVF_SPTM_EL_ROUTING1.disasm`. Merely adapting the register aliases
to TTBR1_EL2 cannot provide the missing high-half translation.

## An alternative execution level remains possible

The `--el1-control` variant runs the same low/high translation fixture with
ordinary EL1 registers and nested EL2 disabled. It neither advertises VHE nor
pretends HCR has hardware EL1 meaning. It records CurrentEL=4.

`/tmp/dvm/HVF_VHE_MMU_EL1_CONTROL1/results.json`: both cases pass, including
actual high-address execution and the distinct `0xabcd` marker.

```sh
python3 tools/perf/native_vhe_mmu.py --el1-control \
  --out /tmp/dvm/FRESH_EL1_HIGH_CONTROL
```

Together with high-address SPRR enforcement, this supports investigating a
compatibility backend that executes expected-EL2 firmware at guest EL1 under
HVF, while software preserves its virtual EL2 state. It does NOT prove that
such a backend can boot SPTM, maintain every protection, or perform well.

This is a larger design than the current narrow register adapters. It needs:

- Separate virtual EL/GL state and register banks from the HVF execution EL.
  Real firmware tests CurrentEL at `0xfffffff0270a348c` and
  `0xfffffff0270a3770`; silently reporting EL1 would select different paths.
- Faithful privileged-register access, ERET/HVC/SMC and exception entry/return.
  Nested guest EL1/EL0 contexts must not gain the virtual EL2/GL permissions.
- A source-table walker and shadow mappings, source-write invalidation across
  every alias, correct barriers/TLBI, and atomic permission/context changes.
- Explicit IRQ/timer/debug handling and then multicore/snapshot validation.
- Measurement of exit/context-switch cost after correctness. These probes are
  not a boot-time estimate or a native-performance claim.

Do not just start the original firmware at EL1 to get past its E2H branch,
ignore SPRR writes, or publish VH=1 without implementing the semantics. Those
would change the guest contract instead of providing the requested backend.

The permission decoder can remain common to HVF and future Windows ARM
backends. A Windows backend that can execute ordinary EL1 might support a
similar design, but WHPX support/behavior has not been established here.
TCG remains the working fallback; none of its execution paths changed.

## Current next step and verification status

The direct-EL2/VHE path cannot be made correct merely by extending the VBAR
adapter or accepting SPRR register writes. First define and prove the virtual
EL2-at-EL1 transition/exception contract, then connect enforced shadow mappings
to SPTM. Full native iOS boot, performance, multicore, and snapshots remain open.

Host-only regressions: 23/23 pass in `/tmp/dvm/HVF_SPRR_HOST1.log`; shell syntax
and Python compilation pass. The new tests do not replace a full TCG boot
regression when production integration changes the executable. Display and
other agents' instances remain untouched. No new work is committed or pushed.
