# Native HVF progress — resumed goal, 2026-09-04

**Latest integrated result (September 5):** read `hvf-integrated-shadow.md`
first. Real adapted SPTM now enables the native MMU, executes at high VA,
and performs native stack stores using demand-filled checked aliases. It
stops at an unimplemented counter-redirection write, not the earlier SPRR
fault. Full iOS boot is still incomplete. Older checkpoints below are history.

The active objective remains a native iOS boot to `Early boot` on eligible
Apple Silicon, preserving permissions and leaving the architecture open to
Windows ARM. That objective is not complete. All HVF changes remain uncommitted.
See `../handoff/native-hvf-2026-09-04.md` for checkout/build/source inventory.
This report supersedes its pending boundary-test and adapted-SPTM status.

**Latest (September 5, subsequent work):** the alternative virtual-EL2-at-EL1
execution bridge now passes native EL2/EL1/EL0 transition, exception, privilege,
stack, and permission tests, including SPSel=0. The checked architectural state
matches TCG controls; broken revocation and stack switching are detected.
See `hvf-virtual-el-context.md`. This is still a bounded standalone prototype,
not QEMU integration. Its fixed E2H/TGE=0 regime does not yet cover SPTM's
E2H+TGE setup. Actual SPTM remains at SPRR.

The native SPRR mapping and direct-EL2 VHE limitation evidence is in
`hvf-sprr-vhe-feasibility.md`. The earlier HCR/VBAR sections below remain valid
implementation history; they do not demonstrate complete native VHE semantics.

## Boundary test resolved

`/tmp/dvm/HVF_BOUND_MAP1.mtree` shows:

```text
0x210050000..0x21005900f: RAM cpu_reg_impl
0x210059010..0x210e3ffff: MMIO darwin-unimp-0
```

The test incorrectly expected a zero read after writing adjacent MMIO.
`hw/arm/darwin_unimp.c:195-212` initializes reads to zero but remembers writes.
The implementation was behaving correctly. The payload now checks initial zero,
then write/readback. Its runner also requires the adjacent device read/write
callbacks to appear in the trace, guarding against accidentally mapping RAM
over the device.

- `/tmp/dvm/HVF_BOUND_FIXED1/results.json`: adapter-enabled loop passes with
  checksum 50,005,000; RAM boundary passes with checksum 13,980; disabled-adapter
  control does not complete, as expected.
- `/tmp/dvm/TCG_BOUND_FIXED1/results.json`: same boundary program passes on TCG.
- All owned processes exited after their runs.

## Actual adapted SPTM boot advanced

`NATIVE_DARWIN_AGT1` booted the disposable AGTCNTVOFF-adapted SPTM using
`QEMU_HVF_APPLE_BOOT_COMPAT=1`, `-accel hvf,nested-virt=on,ipa-bits=40`,
`-cpu host`, and `/tmp/dvm/NATIVE_DRAM_LOW1.dtree`.

Evidence: `/tmp/dvm/NATIVE_DARWIN_AGT1.launch.json`,
`/tmp/dvm/NATIVE_DARWIN_AGT1.state.json`, and corresponding probe logs.

```text
serial lines : 0
xnu panics   : 0
reached shell: no
PC           : 0000000000000000
ESR_EL2      : 0x02000000
ELR_EL2      : 0x8070a3520
```

The guest progressed through boot page-table construction and reached
`MSR SPRR_CONFIG_EL1, X0`, word `0xd51ef100`, at original SPTM virtual address
`0xfffffff0270a3520`. The zero-PC instruction abort follows the missing exception
vector; it is not an XNU panic. Guest x0 was 1. Disassembly is recorded in
`/tmp/dvm/HVF_SPTM_SPRR.disasm`.

SPTM's following sequence writes PPERM `0x2020a52a302abaf5`, then writes SPRR
configuration zero, then sets TCR/TTBR/MAIR. Further Apple APSTS/APCTL/JCTL
operations occur before later MMU setup. Do not indiscriminately shadow these
registers and call that permission enforcement. No SPRR bypass was implemented.

## HCR API defect isolated; native helper workaround demonstrated

New tools:

- `tools/perf/native_hcr_state.S`: snapshots architectural registers directly
  into guest RAM before/after a host exit; includes native MRS/MSR helpers.
- `tools/perf/native_hcr_state.c`: direct Hypervisor.framework runner, no QEMU.
- `tools/perf/native_hcr_state.py`: 51 bounded cases, validates expected exit
  reasons, register preservation controls, and helper behavior.

Run:

```sh
python3 tools/perf/native_hcr_state.py --out /tmp/dvm/FRESH_UNIQUE_HCR_TAG
```

Latest verified results: `/tmp/dvm/HVF_HCR_STATE4/results.json`.
51 runs; 27 preservation/helper controls passed; 16 accessor cases changed
the guest's HCR. The remaining accessor cases used the baseline HCR and did
not have VHE bits to lose. All other tested architectural registers remained
preserved: TPIDR_EL2, VBAR_EL2, TTBR0_EL2, and SCTLR_EL2.

The matrix covers HCR values `0x80000000`, `0x480000000` (E2H), and
`0x488000000` (E2H+TGE), with both SMC and MMIO host exits:

| Mode | Host operation between snapshots | Result for VHE values |
|---|---|---|
| 0 | Only set PC to the next test block | HCR preserved |
| 1 | Get/set all five registers through API | HCR becomes 0x80000000 |
| 2 | API getters, then setters with original guest values | Same loss |
| 3 | Resolve MMIO by mapping a page; no vCPU register API | HCR preserved |
| 4 | Only HCR getter, then set PC | Same loss |
| 5 | Only HCR setter with original value, then set PC | Same loss |
| 6 | Only getters for the other four registers, then set PC | HCR preserved |
| 7 | Execute a native MRS helper, preserve its scratch GPR | Correct read; HCR preserved |
| 8 | Trigger API loss, then execute native MSR/MRS helper | Original HCR restored |

Mode 3 applies only to MMIO, giving 17 cases per HCR value.

Thus the issue is more than a stale displayed register: even the HCR getter
changes subsequent guest execution state on this host. Native execution and
ordinary exits preserve it when those accessors are avoided. The framework's
internal cause is unknown; no Apple bug report has been sent.

At this earlier milestone the native MRS/MSR helper was a standalone building
block. Those tests run at EL2 with the MMU disabled. QEMU integration must
preserve all interrupted state, support valid execution with the guest MMU on,
avoid guest-address collisions, handle unexpected exits, and keep helpers
inaccessible for guest modification. Do not skip HCR synchronization and leave
QEMU's MMU/debugger state stale as a substitute.

Earlier `/tmp/dvm/HVF_HCR_ROUND1` tested resume only AFTER calling the HCR getter;
its mode named "no write" was not a no-access control. The new RAM-snapshot
matrix is the stronger evidence and distinguishes read-side mutation.

## Regressions

- Host-only suite: 23/23 passed on resumption.
- `/tmp/dvm/HVF_VIRT_REGRESSION1/results.json`: all five generic virt/HVF EL2
  payload cases passed using the current executable.
- `HVF_TCG_REGRESSION1`: ordinary TCG ramdisk boot reached the shell, 303 serial
  lines, zero panics, PC `fffffff02ab197b8`.
- Python compilation and whitespace checks passed.
- Tested QEMU SHA-256 remains
  `0d340278de921242724b407df1966bbf64a0d2671d3882e4d14627241969405d`.
  A subsequent C line-wrap-only formatting edit has not been rebuilt.

## HCR integration and VHE limits — subsequent verified work

Worktree and both branch names remain `codex/arm-native-experiments` in
`/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`. All new native
work is uncommitted and unpushed, including the parent tools/docs and nested
QEMU edits. Do not assume pulling the branch transfers these changes.

### Implemented in QEMU

`target/arm/hvf/hvf.c` now accepts **`QEMU_HVF_NATIVE_HCR=1`** (exact value).
It requires guest EL2, at least 36 IPA bits, and **`kernel-irqchip=off`**.

- A lazily reserved ROM page at `0xf00000000` holds native MRS/MSR helpers.
  The code checks for address-space overlap and refuses a collision.
- The helper saves PC, PSTATE, X9, SCTLR_EL2, MDSCR_EL1, and the virtual-timer
  mask. It executes only its own instructions with the MMU disabled and
  interrupts/debug masked, then restores the interrupted state. It never
  changes guest page tables. Unexpected exits or write-readback mismatches
  terminate the experiment instead of resuming with corrupt state.
- HCR is read **before** the bulk register getters and restored **after** all
  bulk register setters and timer-offset updates. Middle-of-list restoration
  failed: later API operations replaced the native HCR value.
- The original VM-exit structure is copied before processing because running
  a helper overwrites the framework-owned exit structure.
- `accel/hvf/hvf-all.c` retains inward page alignment and now also preserves
  section/ROM read-only attributes when dirty logging ends.

Verified QEMU binary SHA-256:
`f73584f15ef2a489e740394d7f53dc216d7f815cecc70f9eb558444aa2e3c3c7`.
Build log `/tmp/dvm/HVF_VBAR_BUILD1.log`. Subsequent QEMU edits are comments
only; executable behavior corresponds to the final implemented code.

### Stronger tests and a second API limitation

- `/tmp/dvm/HVF_HCR_FINAL1/results.json`: three QEMU MMU-on tests pass.
  Debugger get/put preserves HCR, SCTLR, TTBR0, TCR, MAIR, and VBAR.
  Writes to RO code and execution from NX data cause actual guest permission
  faults, including correct FAR/ESR and HCR inside the fault handler.
- `/tmp/dvm/HVF_HCR_FINAL_OFF1/results.json`: disabling the helper reproduces
  the expected discrepancy: guest HCR `0x488000000`, QEMU API view `0x80000000`.
- `/tmp/dvm/HVF_HCR_MMU_FINAL1/results.json`: nine standalone MMU-on cases pass.
- `/tmp/dvm/HVF_HCR_CANCEL1/results.json`: nine earlier standalone variants
  also passed when another host thread stopped the vCPU with `hv_vcpus_exit`.
  These earlier cancellation tests did not yet assert HCR inside the fault
  handler; the final ordinary-exit matrix does.
- **Apple's in-kernel GIC is incompatible with this tested VHE path.**
  QEMU `virt` enables it by default. With it enabled, native HCR initially
  reads `0x488000000`, but a guest TTBR0_EL2 write leaves it `0x80000000`.
  The same effect is independently reproduced by adding `hv_gic_create` to
  the direct runner: `/tmp/dvm/HVF_HCR_GIC2/results.json`, all nine tests fail
  their VHE-preservation assertions. No QEMU or HCR API getter is needed to
  produce that failure. The new helper rejects this configuration.

Run the positive QEMU matrix:

```sh
python3 tools/perf/native_hcr_qemu.py --out /tmp/dvm/FRESH_HCR_TAG
python3 tools/perf/native_hcr_qemu.py --disabled-control --out /tmp/dvm/FRESH_OFF_TAG
python3 tools/perf/native_hcr_mmu.py --out /tmp/dvm/FRESH_DIRECT_TAG
```

The standalone runner accepts `--cancel`, `--shift 0x200000`, and diagnostic
`--pre-access` values. Its C runner also accepts a fourth argument naming a
text file of hex `sysreg value` pairs to replay. Environment variables
`HVF_PROBE_GIC`, `HVF_PROBE_IPA_BITS`, `HVF_PROBE_LOW_ROM`,
`HVF_PROBE_RAM_SIZE`, and `HVF_PROBE_SPLIT_THREAD` isolate setup differences.
The GIC control is expected to fail on this host; do not treat it as a passing
test or silently relax its assertions.

### VHE aliasing is a separate compatibility requirement

The guest reads `ID_AA64MMFR1_EL1 = 0x100011312000`, whose VH field is zero
(`target/arm/cpu-features.h:312`). Preserving HCR's E2H bit does NOT prove
working VHE semantics.

New `native_vhe_alias.S/.c/.py` seeds VBAR_EL1 and VBAR_EL2 with **different**
addresses, enables E2H, writes VBAR_EL1, and reads both banks into guest RAM.
`/tmp/dvm/HVF_VHE_ALIAS2/results.json` proves that HVF leaves VBAR_EL2
unchanged for both E2H and E2H+TGE. The non-VHE control behaves correctly.
The older native_el2_probe case 5 used equal vector values and could NOT prove
aliasing; its successful completion must not be cited as alias correctness.

An opt-in `0xd200..0xd23f` SMC adapter now routes patched VBAR_EL1 MRS/MSR
operations to the correct bank according to the saved HCR.E2H value. It
requires the native-HCR helper and execution at EL2. The host API operation
is followed by native HCR restoration. No SPRR/GXF operation is accepted.
`/tmp/dvm/HVF_VHE_ALIAS_QEMU2/results.json`: all three distinct-bank tests pass
through the adapter. Direct hardware alias tests still fail as expected.

```sh
python3 tools/perf/native_vhe_alias.py --out /tmp/dvm/FRESH_ALIAS_ADAPTER
python3 tools/perf/native_vhe_alias.py --native --out /tmp/dvm/FRESH_ALIAS_NATIVE
python3 tools/perf/native_sptm_patch.py --input firmware/sptm \
  --output /tmp/dvm/FRESH_SPTM.macho --vbar-compat > /tmp/dvm/FRESH_SPTM.json
```

The last command patches three AGTCNTVOFF and nine VBAR instruction sites in
the current original SPTM. `/tmp/dvm/NATIVE_SPTM_VBAR1.json` records every
site and both hashes; original firmware is unchanged.

### Actual boot remains at SPRR, with accurate HCR

`NATIVE_DARWIN_HCR2`, `NATIVE_DARWIN_VBAR1`, and `NATIVE_DARWIN_VBAR2` all
stop at the same unimplemented SPRR operation, without bypassing it:

```text
serial lines : 0
xnu panics   : 0
reached shell: no
ELR_EL2      : 0x8070a3520  (MSR SPRR_CONFIG_EL1, X0)
HCR_EL2      : 0x488000000  (now captured accurately)
VBAR_EL1    : 0x8070a1000
VBAR_EL2    : 0
PC           : 0
```

The VBAR adapter does **not** advance this particular boot boundary. At
original SPTM `0xfffffff0270a347c`, VBAR_EL1 is written BEFORE enabling E2H at
`0xfffffff0270a34a4`. Correct alias selection therefore writes the EL1 bank
at that point. Do not invent a vector mirroring rule just to avoid PC zero.
The patched instructions are verified in actual guest memory in
`/tmp/dvm/NATIVE_DARWIN_VBAR2.state.json`; this is not inferred from argv alone.
Disassembly: `/tmp/dvm/HVF_SPTM_VBAR_SETUP.disasm`.

Current launch additions:

```sh
QEMU_HVF_NATIVE_HCR=1 QEMU_HVF_APPLE_BOOT_COMPAT=1 \
DVM_QEMU="$PWD/qemu-sptm/build-fast/qemu-system-aarch64" \
tools/probe.sh --secs 3 --tag FRESH_BOOT --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --keep -- -accel hvf,nested-virt=on,ipa-bits=40,kernel-irqchip=off \
  -cpu host -sptm /tmp/dvm/NATIVE_SPTM_VBAR1.macho -gdb tcp:127.0.0.1:UNUSED_PORT
```

Use a fresh numeric port, capture frozen state with `native_state.py`, then
quit only that owned VM. All probes from this work have exited.

Latest TCG regression `HVF_TCG_REGRESSION2` reaches the shell: 303 serial
lines, zero panics. `/tmp/dvm/HVF_NATIVE_BOUND_FINAL1/results.json` passes the
Darwin AGT/boundary/disabled-adapter matrix with the native-HCR option enabled.
These are CPU bring-up results, not an iOS acceleration measurement.

## Next implementation work

1. Extend the now-tested EL2 HCR synchronization path to explicit EL0/EL1,
   debugging, and multicore controls before relying on it for those contexts.
   VHE aliases beyond VBAR and EL0 exception/translation semantics remain
   unverified. HVF's VH=0 cannot be fixed merely by exposing a feature bit.
2. Implement SPRR configuration/permission semantics with explicit enforcement
   before enabling guest translation. A software register value alone cannot
   change hardware PTE permission interpretation. Shadow page tables are one
   candidate; the design is not yet selected or implemented.
3. Keep GXF banked state and EL/GL permission differences intact when adding
   its compatibility operations. No native GXF implementation exists yet.
4. Continue actual boot from the observed SPRR fault, retaining TCG regression
   controls and isolated guest disks when the system-volume path becomes useful.

The compatibility semantics should be shared across host backends; Apple API
workarounds belong in HVF. Windows ARM WHPX guest EL2 remains unverified, and
TCG must remain the functioning fallback. Do not claim native snapshots,
multicore iOS, `Early boot`, or a performance multiplier from these results.

SPRR research caveat: Asahi's current SPRR/GXF page describes the per-PTE
index and permission table, but has conflicting prose versus macros for its
enable bit and a repeated GENTER/GEXIT opcode. Cross-check actual m1n1 source
and firmware behavior. The existing QEMU `arm_apple_is_sprr_enabled()` uses
either EL's entire config word being nonzero and explicitly contains an EL0
TODO; that implementation is not proof of precise hardware semantics.

Reference: https://asahilinux.org/docs/hw/cpu/sprr-gxf/

## Subsequent virtual-EL and page-walker integration work

The direct native EL2 route has an independently verified high-half VHE
translation limitation. See `hvf-sprr-vhe-feasibility.md`; fixing HCR state
alone does not enable that translation. A separate native prototype executes
virtual EL2/EL1 at actual EL1 and virtual EL0 at actual EL0. Its latest
E2H+TGE routing and illegal-return tests match TCG, including native illegal
state exceptions. See `hvf-virtual-el-context.md` for exact scope and evidence.

QEMU now has an explicit descriptor-I/O interface for its architectural ARM
page walker, retaining permission/AF/dirty checks without requiring the TCG
soft TLB. This is the first integration component for enforced shadow
mappings, not a working native shadow cache. The HVF matrix passes 48 cases,
the host-feature control passes 48, and real TCG accesses pass 40 comparable
cases. `hvf-page-walker.md` records the API, tests, corrected fixture
assumptions, source paths, and remaining integration requirements.

Current tested QEMU SHA-256:
`206891b39886bee45fa85c04ec231b88509667165cbc0e5a36c82e3e43f65699`.
The full TCG ramdisk still reaches a shell (303 lines, zero panics), and the
three native HCR/RO/NX regression cases pass. Actual adapted SPTM was rerun as
`HVF_PTW_NATIVE_BOUND1`: it remains at `0x8070a3520` with accurate HCR
`0x488000000`, before serial output. No SPRR fault was bypassed.

The native implementation and all new tools/docs remain uncommitted in the
existing experiments worktree. All owned probes have exited; display work
and protected project files were untouched. Windows native acceleration,
full iOS HVF boot, multicore and snapshot semantics remain unverified.

## Real SPTM tables connected to native shadow reads

`hvf-sptm-shadow-handoff.md` records the next integration component. The actual
TCG SPTM is stopped before its first MMU enable at physical `0x8070a3740`;
only five table pages are needed at that checkpoint. They describe two low
bootstrap pages and 256 high-half 32 MiB blocks. Both SPRR configuration
registers are zero at this initial checkpoint; later protection transitions
still require implementation.

The captured bytes now pass 783 translation checks through the explicit-I/O
walker under HVF. Temporary private native tables and IPA aliases then let
the CPU perform 258 matching reads, including the bootstrap pages. A denied
native alias prevents the actual load from completing, and all source-table
hashes stay unchanged. This is a real native mapping/read operation inside
QEMU, but still a single-vCPU test operation, not a persistent shadow cache
or resumed SPTM execution.

Latest tested executable:
`712ca483f85775f156473d71b4ae731d254611ddc02b6d779a8856e4c7bf9dad`.
The 48-case walker matrix and three HCR/RO/NX tests pass; full TCG ramdisk
still reaches a shell with 303 lines and zero panics. Actual adapted native
boot remains at the same SPRR write (`HVF_SPTM_SHADOW_NATIVE_BOUND3`).
The next integration requirement is a persistent mapping/context owner tied
to virtual EL2 system-register operations, with source-write invalidation.
No commits or pushes were made; all owned guests have exited.
