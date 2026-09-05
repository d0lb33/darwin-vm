# Native HVF handoff — 2026-09-04

## September 5 preservation checkpoint

CPU/HVF development is paused at the user's request and preserved on
`codex/hvf-research-checkpoint` in both repositories. This checkpoint commits
the experimental bridge, probes and findings; it is not a working iOS HVF
release. The parent commit pins the matching QEMU commit. Historical statements
below about uncommitted files and branch names describe earlier checkpoints.

Current evidence: [benchmarks](../re/hvf-performance-checkpoint.md),
[migration frequency](../re/hvf-migration-call-profile.md), and
[private ISA / vphone research](../re/hvf-vphone-assessment.md).
Latest real HVF boot stops before accepting GXF_CONFIG_EL2=0x6f; TCG remains
the functional iOS backend. Existing native matrix: 117/117; latest host-only
regression rerun: 24/24, shell syntax and diff whitespace checks pass.
No new boot is claimed for this preservation commit. No host security policy
was changed. New GPU work starts independently from current local main.

**Start here now:** [latest native HVF continuation](../re/hvf-gxf-bootstrap.md).
Real adapted SPTM now enters guarded execution and completes native cache
maintenance. The latest continuation also corrects GENTER syndrome/immediate
handling and passes 117/117 native cases. The real boot still stops before
GXF_CONFIG_EL2=0x6f; the linked note records the evidence and remaining scope.
The [policy investigation](../re/hvf-gxf-policy.md) now traces bit 6 to
internal-ISA VM permission inputs and identifies CTXR activation as the next
protection stage. No host settings were changed.
Full iOS boot is incomplete. The dated sections below are older checkpoints.
Everything remains uncommitted in the existing worktree; the older checkpoints
below do not describe the latest execution bridge.

Update: the user resumed the full native-boot goal. See
`../re/hvf-native-progress.md` for the now-resolved boundary test, actual
adapted-SPTM boot, and stronger HCR API/helper evidence. The remainder is the
original handoff checkpoint, with its pending items preserved for context.

September 5: read `../re/hvf-virtual-el-context.md` FIRST, then
`../re/hvf-sprr-vhe-feasibility.md`. The limited alternative execution bridge
now passes native privilege/exception/EL0 tests and TCG controls; it is not yet
integrated into QEMU or booting SPTM. New experiments
prove enforced SPRR leaf mappings, but native EL2 high-half VHE translation
fails on the tested host. Ordinary EL1 high-address controls pass, leaving a
larger virtual-EL2 compatibility design to investigate. All changes remain
uncommitted in the existing worktree.

Status: real SPTM entry code has executed under HVF at guest EL2. iOS has NOT
booted under HVF. A narrow Apple-register adapter passes a synthetic test on
the Darwin board; the actual SPTM boot with that adapter has not yet been run.
The last RAM-boundary test failed and needs investigation. Work is paused at
the user's request for handoff. No HVF changes have been committed or pushed.

## Checkout and ownership

- Worktree: `/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`
- Parent and nested `qemu-sptm` branch: `codex/arm-native-experiments`.
- Parent HEAD: `95efeb1` (previous TCG experiments, committed before HVF work).
- Nested QEMU HEAD: `1cfc4a5` (previous TCG implementation).
- HVF source changes are unstaged in the nested repository. All new native
  tools and this report are untracked in the parent repository. Pulling the
  branch alone WILL NOT transfer these changes. Continue in this worktree or
  explicitly copy both repositories' working changes and untracked files.
- Read CLAUDE.md and AGENTS.md. The user explicitly superseded the old claim
  that HVF had been ruled out and requested fresh experiments. Existing code
  was read as implementation evidence, not accepted as hardware proof.
- Do not touch the display agent's instances, disks, or checkout. No display
  files, `darwin.c`, AIC/ASC wiring, `dt_fixup.py`, or `run.sh` were edited.
- At handoff, process inspection found no QEMU, build, or probe running from
  our worktree. Other agents' VMs were left untouched.

## Host and build

Apple M5 Max, 128 GB RAM, macOS 27.0 (26A5421a); QEMU fork VERSION 11.1.0.
Build: `qemu-sptm/build-fast/qemu-system-aarch64` (O3/LTO, assertions retained).
Latest successful build log: `/tmp/dvm/NATIVE_HVF_MAP_BUILD2/build.log`.
Latest executable SHA-256:
`0d340278de921242724b407df1966bbf64a0d2671d3882e4d14627241969405d`.

Build only with our probes stopped:

```sh
make -C qemu-sptm/build-fast -j12 qemu-system-aarch64
```

## Implemented QEMU changes

`qemu-sptm/accel/hvf/hvf-all.c`:

1. ARM-host accelerator property `nested-virt=on`. This enables guest EL2
   before VM creation/host-feature discovery on a machine other than `virt`.
   Upstream's `virt,virtualization=on` already provides the normal virt path.
2. ARM-host accelerator property `ipa-bits=N` (0 retains the machine default).
   Validates the override against the machine requirement and host maximum.
   Our native Darwin launch uses `ipa-bits=40` for its MMIO address space.
3. Return `-ENOTSUP` for unsupported VM creation instead of asserting. This
   permits QEMU's accelerator selection machinery to handle the failure; a
   complete automatic HVF/TCG launcher and matching CPU selection are NOT done.
   In particular, `-cpu host` is not a portable TCG fallback configuration.
4. Map/unmap only whole host pages contained within a MemoryRegionSection.
   `hvf_section_bounds()` aligns inward, leaving partial pages unmapped.
   Outward rounding could incorrectly cover adjacent devices or permissions.
   Unaligned backing aliases are left unmapped. The same bounds are used by
   dirty-log protection callbacks via `hvf_log_protect()`.

`qemu-sptm/target/arm/hvf/hvf.c`:

5. Explicitly reject unavailable guest EL2, including macOS older than 15,
   instead of returning misleading success from VM creation.
6. Dirty-log write-fault handling now checks that a complete, aligned RAM page
   exists before trying to unprotect/retry it. Partial-page writes reach the
   trapped memory-access path instead of retrying forever.
7. Unhandled host-visible exceptions return `EXCP_DEBUG` after logging, keeping
   the first fault instead of spinning and producing megabytes of identical
   errors. The last smoke log has one unhandled-fault line.
8. Default-off Apple boot adapter, enabled by the presence of environment
   variable `QEMU_HVF_APPLE_BOOT_COMPAT` (currently even `=0` enables it).
   SMC immediates `0xd100..0xd11f` write AGTCNTVOFF_EL2 from Rt;
   `0xd120..0xd13f` read it into Rt. Rt=31 has architectural zero/discard behavior.
   Requires guest EL2 and an existing Darwin AGTCNTVOFF cpreg model. Uses that
   model's per-CPU read/write backing. This is OFFSET READBACK ONLY, not a
   functioning Apple auxiliary timer. It accepts no SPRR or GXF operations.
   It avoids full CPU-state synchronization in this narrow path because of
   the HCR_EL2 API discrepancy described below.

The mapping changes are broader HVF changes and still need the failing
boundary test resolved and final regressions. Do not call the patch ready.

## New tools

All are under `tools/perf/` in this worktree:

| File | Purpose |
|---|---|
| `native_el2_probe.S` | Fresh ARM payload: arithmetic, TPIDR_GL2, GENTER, HVC, tiny guest register shim, VHE/HCRX/AGT probes; also Darwin adapter and RAM-boundary cases |
| `native_el2_probe.c` | Public Hypervisor.framework runner, small guest RAM, alarm, actual EL2 configuration and register/exit capture |
| `native_el2_probe.py` | Compile/sign/run standalone probes; Apple ARM/macOS capability gating; optional high-IPA controls |
| `native_el2_qemu.py` | Run the first five payload cases through QEMU's `virt` HVF backend and verify UART/checksums/EL2 |
| `native_dram_tree.py` | Change only the 8-byte chosen/dram-base property in a disposable tree, recording input/output hashes |
| `native_sptm_patch.py` | Scan Mach-O instruction sections and rewrite only AGTCNTVOFF_EL2 MRS/MSR to the adapter's SMC encodings; record all sites/hashes |
| `native_state.py` | Capture frozen GDB GPRs and relevant EL1/EL2 registers, plus bytes at PC and ELR_EL2 |
| `native_darwin_smoke.py` | Diskless Darwin-board native register-adapter and RAM-boundary tests, with bounded Popen cleanup |

The standalone C runner currently supports cases 0–7. Cases 8/9 are run by
the Darwin smoke runner. The QEMU generic runner currently covers cases 0–4.
The tiny guest exception shim in case 4 reserves guest registers; it is not a
general ABI-preserving iOS exception handler.

## Evidence and findings

### Guest EL2 is real and works for ordinary instructions

- `/tmp/dvm/NATIVE_EL2_FRESH2/results.json`: direct framework probes.
- `/tmp/dvm/NATIVE_EL2_QEMU3/results.json`: all five original QEMU virt cases
  passed. This predates the newest mapping/adapter build and newer payload.
- Arithmetic: one million iterations, expected checksum 3,000,000, CurrentEL=8.
- TPIDR_GL2 (`S3_6_C15_C11_1`) and GENTER (`0x00201420`) generate guest undefined
  exceptions at EL2. They do not automatically exit to the host VMM.
- HVC at EL2 reaches the guest's exception vector.
- A tiny guest-resident TPIDR_GL2 readback shim handled 20,000 exceptions and
  produced checksum 50,005,000. GENTER semantics were NOT emulated.
- These are small diagnostic programs, initially MMU-off. There is no measured
  native iOS speedup and no justified boot-time/game-performance multiplier.

### Two startup mapping failures were identified

1. `/tmp/dvm/probe/NATIVE_DARWIN_TRACE1.stderr.log`: attempted mapping
   `gpa=0x10000000000, size=0x200000000`, rejected by HVF. The normal Darwin
   tree starts RAM at 1 TiB, beyond the default 36-bit IPA range.
2. `/tmp/dvm/NATIVE_EL2_HIGH_IPA3/results.json`: this Mac reports MAXIMUM 40 IPA
   bits. Requesting 42 returns HV_UNSUPPORTED. Mapping at 1 TiB under 36 bits
   fails; native execution at RAM base `0x800000000` succeeds with 36 and 40 bits.
   Simply increasing the IPA property cannot preserve the original RAM base.
3. Disposable tree: `/tmp/dvm/NATIVE_DRAM_LOW1.dtree`, manifest
   `/tmp/dvm/NATIVE_DRAM_LOW1.json`. Only offset `0x388c` (dram-base) changed from
   `0x10000000000` to `0x800000000`; RAM size remains 8 GiB. The loader derives
   boot_args and loaded memory-map addresses from this value. Full firmware
   compatibility with this relocated layout is NOT established.
4. `/tmp/dvm/probe/NATIVE_DARWIN_LOW1.stderr.log`: after fixing the RAM base,
   startup rejected unmap `gpa=0x210050000, size=0x9010`. This motivated the
   whole-page mapping fix. Next boots got into SPTM.

### Real SPTM boot reached the first unsupported register

`/tmp/dvm/NATIVE_DARWIN_LOW3.state.json` and corresponding probe logs:

```text
serial lines : 0
xnu panics   : 0
reached shell: no
PC           : 0000000000000200
ESR_EL2      : 0x02000000 (undefined instruction)
ELR_EL2      : 0x8070a30cc
VBAR_EL2     : 0
```

The guest ran SPTM entry, read its boot arguments, wrote standard registers,
and faulted on `MSR AGTCNTVOFF_EL2, XZR` (word `0xd519f99f`). Original Mach-O
address `0xfffffff0270a30cc`, file offset `0x9f0cc`. The missing EL2 vector
then led to an instruction fetch fault at `0x200`. This is not an XNU panic.

Disassemble independently:

```sh
ipsw macho disass firmware/sptm --vaddr 0xfffffff0270a3000 --count 95 --quiet
```

### Adapter implementation passed, actual adapted boot pending

`/tmp/dvm/NATIVE_DARWIN_SMOKE1/results.json`, case 8:

- Native Darwin board at EL2, adapter enabled.
- 10,000 write/read pairs through the real QEMU SMC handler.
- Checksum 50,005,000; completion marker 0x600d; UART `OK`.
- This proves the adapter's offset readback path, not timer behavior or iOS boot.

Disposable patched SPTM: `/tmp/dvm/NATIVE_SPTM_AGT1.macho`.
Patch manifest: `/tmp/dvm/NATIVE_SPTM_AGT1.json`.
Three zero-write sites patched: offsets `0x9f0cc`, `0x9f4b0`, `0xe27b4`
(VAs `0xfffffff0270a30cc`, `0xfffffff0270a34b0`, `0xfffffff0270e67b4`).
Original firmware untouched. This copy HAS NOT YET BEEN BOOTED.

### Last test failed: RAM-boundary smoke case 9

Same results file, case 9:

- Three RAM readbacks passed: checksum 13,980 = 3 * 0x1234.
- Test then failed, marker 0xbad, final PC 0x200; no UART completion.
- It tests addresses `0x210057ff8`, `0x210058000`, `0x210059008`, then writes
  `0x210059010` and assumes its readback must be zero (adjacent MMIO).
- That last assumption has NOT been verified against the actual MemoryRegion
  map. Inspect `info mtree` and compare the same program under TCG before
  concluding the mapping implementation is wrong. No root cause confirmed.
- The third scheduled test (adapter disabled negative control) did NOT run
  because the runner stopped on case 9's assertion.
- The runner cleaned up both of its QEMU children.

### HCR_EL2 API discrepancy — unresolved

`/tmp/dvm/NATIVE_EL2_VHE2/results.json`, standalone case 5 (no QEMU involved):

- Guest writes E2H/TGE and its MRS HCR_EL2 reads `0x488000000`.
- The earlier VHE alias probe completes, but used equal values in both banks
  and did not establish correct aliasing. New distinct-bank tests FAIL natively;
  see the subsequent integration findings in `../re/hvf-native-progress.md`.
- After host exit, `hv_vcpu_get_sys_reg(HV_SYS_REG_HCR_EL2)` reports `0x80000000`.
- HCRX_EL2 MRS works in standalone case 7. AGTCNTVOFF faults in case 6.

The later progress report supersedes this section: an integrated HCR helper
passes EL2 MMU-on debugger round trips, the in-kernel GIC causes an additional
VHE state loss, and native VBAR aliasing is independently shown unsupported.
Full VHE, snapshots, and multicore iOS remain unproven.

## Suggested next steps / commands

1. Resolve case 9's adjacent-region expectation using `info mtree` and a TCG
   control; rerun the full smoke suite including adapter-disabled control.
2. Run the first actual adapted SPTM boot, bounded and with fresh tag/port:

```sh
QEMU_HVF_APPLE_BOOT_COMPAT=1 \
DVM_QEMU="$PWD/qemu-sptm/build-fast/qemu-system-aarch64" \
tools/probe.sh --secs 3 --tag NATIVE_DARWIN_AGT1 \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --launch-manifest /tmp/dvm/NATIVE_DARWIN_AGT1.launch.json \
  --keep -- -accel hvf,nested-virt=on,ipa-bits=40 -cpu host \
  -sptm /tmp/dvm/NATIVE_SPTM_AGT1.macho -gdb tcp:127.0.0.1:1460
python3 tools/perf/native_state.py --port 1460 \
  --output /tmp/dvm/NATIVE_DARWIN_AGT1.state.json
python3 tools/hmp.py /tmp/dvm/NATIVE_DARWIN_AGT1.sock quit
```

Run this block in one shell tool invocation so inspection happens before that
invocation's process group is cleaned up. Choose an unused port. Verify that
the repeated `-sptm` option selects the disposable copy (launch manifest and
fault instruction evidence); this launch has not yet been tested.

3. Recheck generic virt HVF payloads and normal TCG ramdisk boot after the final
   changes. Host tests passed 23/23 before the latest changes; Python compile
   and both repos' `git diff --check` passed afterward. Final full runtime
   regressions/checkpatch have not been completed.
4. Test the HCR API round-trip discrepancy before implementing native snapshots.
5. Continue compatibility work from observed faults. GXF enter/exit requires
   real banked-state semantics, and SPRR requires correct memory permissions.
   Do not turn those into no-ops just to make the boot counter advance.

Production defaults still use TCG. Windows remains supported by that path.
Windows ARM WHPX is a future host backend; its guest EL2/iOS compatibility has
not been probed here. Native snapshots, multicore iOS boot, native MMU/SPRR
compatibility, migration, and GPU acceleration are not implemented/validated.

GXF statistics emitted by these native tests include cpreg synchronization and
debugger reads. They are NOT counts of executed native Apple instructions.

## Primary references used

- Local Apple SDK Hypervisor headers: `hv_vm_config.h`, `hv_vcpu.h`,
  `hv_vcpu_types.h` under the CommandLineTools macOS SDK.
- QEMU 11.1 announcement: https://www.qemu.org/2026/08/11/qemu-11-1-0/
- Apple EL2 API: https://developer.apple.com/documentation/hypervisor/hv_vm_config_set_el2_enabled(_:_:)
- Asahi m1n1 GXF instruction source:
  https://raw.githubusercontent.com/AsahiLinux/m1n1/main/src/gxf_asm.S
- Windows ARM backend documentation: https://www.qemu.org/docs/master/system/whpx.html

The real-host results and firmware addresses above are the decisive evidence;
older project HVF feasibility conclusions were not treated as authoritative.
