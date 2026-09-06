# Guarded execution and native cache maintenance — September 5, 2026

**Superseded stopping point:** [hvf-sptm-bootstrap.md](hvf-sptm-bootstrap.md)
(September 6) records the native boot passing GXF_CONFIG=0x6f, the CTXR/CTRR
activation, TXM at guarded EL0 and the GEXIT onto the kernel entry.

Fresh [performance checkpoint](hvf-performance-checkpoint.md): ordinary ARM
loops run natively, but the current register and guarded-transition bridge
paths are expensive. Includes checked repeated HVF/TCG measurements and the
rejected same-page-breakpoint timing artifact; no whole-iOS speedup is claimed.

Latest investigation: [GXF internal-ISA VM policy](hvf-gxf-policy.md)
identifies the firmware input that selects `0x6f` and records the following
CTXR activation sequence under TCG. It does not advance the native boot.

Latest verified real boot: `HVF_GXF_BOOT8.virtual.json`, stopped before
GXF_CONFIG_EL2=`0x6f` at `0xfffffff0070a3978`. It passes guarded entry, native
cache maintenance, PAN clearing, disabled-PMU filters, the VMSA lock and
clearing unused lower GXF banks. All five bootstrap table hashes match.
The latest native regression passes 117/117 (`HVF_GXF_TEST8/results.json`).
GENTER now records the reference syndrome and immediate; see the September 5
syndrome continuation below for the evidence and remaining uncertainty.
Full iOS boot is still incomplete; the next exception/access controls must
be characterized and enforced before that write can be accepted.

Continuation of `hvf-pmprr-locks.md`. All changes remain uncommitted in
`/Users/jdolbe1/Downloads/darwin-vm-arm-native-experiments`, branch
`codex/arm-native-experiments` in both repositories. Pulling the branch does
not transfer the working changes or untracked tools.

## What is implemented

`qemu-sptm/target/arm/hvf/virtual-gxf.h` implements the initial GXF_CONFIG=1
setup, entry/abort-entry addresses, guarded TPIDR/VBAR writes, GENTER and
same-EL AArch64 GEXIT. Virtual EL2/GL2 execute at native EL1, with distinct
QEMU saved-state and stack banks. Native Apple protection registers are not
used. The framework runs with nested virtualization and the in-kernel GIC
disabled, and supports one vCPU only.

GENTER saves the caller's state and selects the guarded entry. A local stack
preservation step surrounds the common interrupt helper: that helper selects
CURRENTG before saving SP, which otherwise overwrites the incoming guarded
stack for an SP_EL2 caller. The native register synchronization now selects
SP_GL2 or SP_EL2 according to CURRENTG. GEXIT validates the supported return
state, saves GL SP, restores ordinary PSTATE/SP, and returns to ELR_GL2.
Each transition discards all native aliases before the target fetch is
walked under the new permission bank. A denied target fetch remains denied.

SPRR accepts the observed guarded bootstrap PPERM transitions from
`0x2020a52a302abaf5` to `0x2020a52a302acae6` or `0x2020a52a302abae6`, then the
observed configuration lock `0xff` from the latter bank. This is deliberately
bounded to the observed bootstrap sequence; it does not establish arbitrary
guarded permission-mask/lock behavior. Ordinary-mode attempts to perform
these whole-bank changes are rejected.

The virtual instruction dispatcher rejects guarded-register access from
ordinary EL2 after applying VHE redirects. The generated cpreg table has
PL2 access bits but no guarded access callback. This extra restriction is
therefore necessary for the bridge; a register's presence in the generated
table is not authorization to read it. Higher-EL/lower-GL access is not yet
integrated.

`IC IALLU` synchronizes the host backing of currently executable pages and
schedules a native cache-maintenance helper before guest execution resumes.
That private helper executes TLBI VMALLE1, DSB SY, IC IALLU, DSB SY, ISB,
then returns through a checked SMC. Every newly installed executable mapping
also synchronizes its backing, covering pages discarded by earlier context
changes. Code backings remain non-writable through native aliases; checked
code stores still enforce guest permissions and validate the resulting page.

The host synchronization uses QEMU's `address_space_flush_icache_range`
(`system/physmem.c`), which calls `flush_idcache_range` for direct RAM under
HVF. On Darwin that reaches `sys_icache_invalidate` in `util/cacheflush.c`.
The native helper additionally invalidates the guest's instruction cache;
the guest operation is not treated as an ignored request.

The observed `MSR PAN,#0` clears virtual PSTATE.PAN and discards all aliases
before resuming. This implements the guest's explicit restriction removal;
it does not grant permissions absent from SPRR. Setting PAN and comprehensive
PAN interaction with user mappings are still unsupported.

## Evidence and interpretation

The primary hardware reference is Sven Peter's
[original SPRR/GXF investigation](https://blog.svenpeter.dev/posts/m1_sprr_gxf/),
especially its GL-only register scan and lateral exception discussion.
Asahi m1n1 `src/gxf_asm.S` at commit
`940439b9a407fbfc499bea933269219f3f62d4c7` independently shows entry setup,
separate guarded stacks, saved-state handling and GEXIT. These M1-era sources
do not establish every protection control on the current M5.

Real SPTM instruction sites (original Mach-O addresses):

| Address | Operation |
| --- | --- |
| `0xfffffff0270a38e8` | enable GXF |
| `0xfffffff0270a38f4` / `3900` | configure abort and normal entry |
| `0xfffffff0270a390c` | GENTER |
| `0xfffffff0270a3858` | initialize guarded TPIDR |
| `0xfffffff0270a3880` / `3898` | guarded permission-bank changes |
| `0xfffffff0270a38ac` / `0xfffffff0270b16f8` | configuration lock |
| `0xfffffff0270a38bc` | IC IALLU |
| `0xfffffff0270a38d0` | guarded VBAR initialization |
| `0xfffffff0270a38d8` | clear PAN |

Runtime relocation changes the `0xfffffff027` prefix to `0xfffffff007`.

`HVF_GXF_TEST2/results.json` records 66/66 passing native cases: entry,
separate SP0/SP2 returns, invalid/nested transitions, permission revocation
in both directions, both guarded bootstrap banks and locks, plus all 16
SPRR nibbles for guarded read/write/execute. Each fixture uses a fresh owned
VM and checks actual stopped state, target memory, and page-table/code hashes.
These tests establish the implemented behavior, not unknown hardware semantics.

`HVF_GXF_BOOT2.virtual.json` records the real adapted SPTM entering GL2,
setting TPIDR and PPERM, then stopping at IC IALLU. `HVF_GXF_BOOT3.virtual.json`
advances through cache maintenance and VBAR setup to PAN clearing at
`0xfffffff0070a38d8`. All five captured bootstrap page-table hashes match in
both boots. Neither boot reaches XNU or serial output.

`HVF_GXF_TEST3/results.json` has 73/74 passing cases. Guarded register-access
controls and both cache-helper controls pass. Its self-modifying-code fixture
stops on a deliberately checked STR64 because the fixture destination was
only four-byte aligned; the fixture has been corrected to eight-byte alignment.
Do not report that initial matrix as entirely passing.

The corrected fixture passes in `HVF_GXF_TEST4`: it first executes a function,
replaces its instruction through an aligned checked STR64, executes native
IC IALLU, then observes the new return value `0x456`. Exact code and table
bytes are verified. This uses the actual pre-SPRR writable/executable mapping,
not an invented guarded W+X permission. The remaining new cases verify PAN
clearing, continued SPRR denial after that clearing, and rejection of PAN-set.
All 77 cases pass on SHA-256
`dd2b330d3e26dc74b0ba2f6f5823a9ff483c79a5cb328ccf47140a621e452c83`.

## Next observed boundary: performance-counter filters

SPTM `0xfffffff0270b4154/4158` writes `0x3030000ffff00` to PMCR1_GL2/GL12.
The alternate branch constructs `0x30300`. Linux's
[Apple PMU driver](https://github.com/torvalds/linux/blob/master/drivers/perf/apple_m1_cpu_pmu.c)
distinguishes PMCR1 event filters from PMCR0 global counter enables. The M5
field dump assigns the guarded filter pairs to bits 8..23, 40..41, 48..49.
The new `virtual-pmu.h` accepts only those fields while the virtual PMCR0 is
entirely zero and execution is guarded; it never programs the host PMU.
PMC0/PMC1 reads in this disabled context use the saved raw counts rather
than the TCG model's synthetic random increments. Active counters remain
unsupported. The real boot `HVF_GXF_BOOT5` performs both filter writes,
checks the core and ACC protection-range readbacks, and stops at
VMSA_LOCK_EL2 (`0xfffffff0070b4558`). Five bootstrap table hashes match.

`HVF_GXF_TEST5` initially records 80/84 passing cases. The four failures are
an incorrect GDB-bank assertion: `target/arm/gdbstub.c:248` redirects GL1 to
GL2 under VHE, while the test expected the lower bank. Actual guest readbacks
and native stops were correct. The updated fixture reads both banks using
guest MRS instructions and checks the other bank remains unchanged. Its next
run must pass before the corrected matrix is reported as verified.

The corrected PMU tests pass in `HVF_GXF_TEST6` (92/92 total cases): actual
MRS reads verify distinct GL2/lower banks and VHE aliases, invalid fields and
ordinary-mode writes are rejected, PMCR0 activation remains rejected, and
disabled PMC0/PMC1 counts stay frozen. The GDB alias caveat is retained in
the fixture comment so future tests do not repeat the mistake.

`HVF_GXF_TCG5` reaches the restore-ramdisk shell in the 60-second baseline:
303 serial lines, zero XNU panics, `reached shell: yes`. This is a TCG
regression, not a native system-volume boot or performance measurement.

## Translation-register locks

`virtual-vmsa.h` accepts the observed VMSA_LOCK_EL2 bootstrap values
`0x8000000000000010` and `0x8000000000000011`, requiring guarded execution,
an active shadow context, and no clearing of previous lock bits. It checks
subsequent register writes after VHE redirection and before mutation. The M5
field definitions name bit 63 as SCTLR.M, and bits 4..0 as TTBR1, TTBR0,
TCR, SCTLR, and VBAR locks. Only the observed initial values are accepted;
this does not claim hardware's exact rejected-write exception behavior or
support a reset/unlock sequence.

`HVF_GXF_TEST6` verifies stored lock readback, rejected ordinary-mode setup,
rejected lock clearing, TTBR1 changes and MMU disabling. Its VBAR pair matters:
changing VBAR succeeds with bit 0 clear, but stops unchanged with bit 0 set,
including through the EL1 VHE alias. These latter cases distinguish actual
lock enforcement from a pre-existing unsupported-register stop. All eight
VMSA cases pass as part of the 92-case matrix.

`HVF_GXF_BOOT6` passes the actual VMSA write, changes the guarded entry to
`0xfffffff00709a610`, and installs VBAR_GL2=`0xfffffff0070f8000`. It stops at
`0xfffffff0070a3968`, the first of three zero writes to unused lower GXF banks.
All five table hashes match. `HVF_GXF_TCG6` again records shell=yes, 303 serial
lines, zero panics on its 60-second restore-ramdisk baseline.

The implementation accepts those lower CONFIG/ENTRY/PABENTRY zero writes
only when the lower SPRR/GXF context and entry addresses are already zero and
the caller is guarded EL2. Enabling a lower context or changing its entry is
still rejected. All three new cases pass in `HVF_GXF_TEST7`: actual lower-bank
MRS readbacks remain zero, while nonzero enable and entry writes stop before
mutation. The following SPTM instruction at original
`0xfffffff0270a3978` programs GXF_CONFIG with additional controls; their field
names alone are not enough to establish their exception/access semantics.

## Current binary and continuation

Build: `/tmp/dvm/HVF_GXF_BUILD7.log`; executable SHA-256:
`d407d5d3fee52029750f25b98ab4a9731b3c61c62939a984bb2c417285e86cf4`.
The actual boot uses the same adapted SPTM and instruction ledger as the
earlier shadow runs; debugger test fixtures are not used in this boot.

At the final stop, x0=`0x6f`, CURRENTG=1, GXF_CONFIG_EL2 remains 1 and
SPRR_CONFIG_EL2 remains `0xfb`. The register write has not been accepted.
The M5 field dump names its requested bits ENAB, PEX2, PEX0, LOCK, NACC,
HVAC; ALLW is clear. These names are leads, not a complete behavioral model.
Do not store `0x6f` and assume its restrictions have thereby been implemented.

The existing `native_sprr_sites.py` also finds GXF configuration instructions
in its SPRR register groups. Filtering `HVF_PMPRR_SITES1.json` for encodings
`[3,6,15,1,2]`, `[3,6,15,1,4]`, `[3,6,15,15,1]` produces
`HVF_GXF_CONFIG_SITES1.json`: eight SPTM candidates, zero TXM, four kernel.
SPTM original `0xfffffff0270e694c` installs lower GXF configuration `0x1f`;
`0xfffffff0270f6b54..5c` clears its enable bit; `0xfffffff0270f74b4..c8`
reads the privileged bank and compares with `0x2f`. They prove that the
remaining controls have later users, but not each bit's hardware behavior.
Disassemblies are `HVF_GXF_CONFIG_LOWER1/LOWER2/CHECK1.disasm` under `/tmp/dvm`.

Reproduce the real boot from this worktree:

```sh
python3 tools/perf/native_virtual_boot.py --tag UNIQUE_HVF_BOOT \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --sptm /tmp/dvm/HVF_VSH_SPTM2.macho \
  --ledger /tmp/dvm/HVF_VSH_SPTM2.ledger --shadow \
  --check-tables /tmp/dvm/HVF_SPTM_TABLES1/results.json
```

Native regression: `tools/perf/native_virtual_gxf.py` takes the same dtree,
sptm and ledger plus `--out /tmp/dvm/UNIQUE_TEST`. Every case owns a fresh VM
and cleans it up. `--control-only` omits the 48-case permission matrix.
Build only after all owned probes exit. Use the separate display checkout's
process state as read-only; do not terminate, rebuild or change its instance.

`HVF_GXF_TCG7` records shell=yes, 303 serial lines, zero panics in the
60-second baseline. Host regressions pass 23/23 (`HVF_GXF_HOST4.log`), shell
syntax checks pass, and checkpatch reports zero warnings/errors for the five
changed HVF headers (`HVF_GXF_CHECK7.log`).

Final native matrix: 95/95 pass in `HVF_GXF_TEST7/results.json` on this exact
binary. This includes the 48-case permission matrix and 47 state, cache,
register-access, PMU and VMSA controls. All owned probe/build processes were
confirmed exited after collection. The separate display QEMU, PID 43771 at
that check, remained running from its own checkout and was not touched.

## Remaining scope

This is an experimental execution bridge, not a verified complete isolation
boundary. Full CTRR/CTXR and PAN enforcement, guarded exception delivery,
lower-EL execution, interrupt/timer delivery, MMIO/DMA, multicore, snapshots
and reset remain unfinished. The instruction ledger and private helper also
need hardening beyond controlled firmware inputs. No full iOS boot or native
performance improvement has been measured. TCG remains the portable backend;
these additions do not provide a Windows hardware-acceleration backend.

The earlier direct-EL2/HCR experiments remain documented in
`hvf-native-progress.md` and `hvf-virtual-el-context.md`. This alternative
bridge does not depend on the failing native EL2 high-half VHE mapping path.


## September 5 continuation: GENTER syndrome and immediate

`HVF_GXF_TEST8/results.json` passes 117/117 native cases. The new binary is
`dc2d14c93387cb02e3af8dc493132fdd08c1c2987c87a1bce68e84f91164448a`, built by
`HVF_GXF_BUILD8.log`. The existing 95 cases now check the nonzero GENTER
syndrome where applicable. The additional 22 cases cover all 16 supported
immediates, unchanged saved state on rejected immediates 16 and 31, and
rejection of unadapted native instructions for immediates 0, 15, 16 and 31.
The positive immediate cases execute an actual MRS ESR_GL2 into x2, in
addition to debugger saved-state inspection. Table and code hashes are
checked as in the earlier matrix.

The independent implementation reference is Asahi m1n1's `hv-sprr` branch,
commit `1c98fd09817cede0043d25c95fb540dbd683ef18`:

- [hv_sprr.h](https://github.com/AsahiLinux/m1n1/blob/1c98fd09817cede0043d25c95fb540dbd683ef18/src/hv_sprr.h)
  defines HV_GXF_ESR_GENTER as `0xfe010000` and a four-bit immediate.
- [hv_gxf_genter](https://github.com/AsahiLinux/m1n1/blob/1c98fd09817cede0043d25c95fb540dbd683ef18/src/hv_sprr.c)
  combines those into the saved syndrome when switching worlds.

The HVF bridge now uses `0xfe010000 | immediate` instead of zero. This is
reference-backed emulation, not a direct measurement of native GL2 hardware
on this Mac: public HVF does not provide the protected GENTER access needed
for that experiment. TCG's existing syndrome behavior is unchanged.

There is an explicit width uncertainty. Our inherited TCG decoder accepts
five immediate bits, while Asahi's adapter accepts four. DFF's independent
[SPTM analysis](https://df-f.com/blog/sptm4) describes five bits and shows
firmware masking ESR with `0x1f` at original `0xfffffff027098530`, then
selecting call/resume/panic paths. That demonstrates the firmware consumes
these bits, but does not directly measure every hardware immediate. We
support only 0..15 for now, reject adapted 16..31, and reject the entire wider
class from native executable pages. Rejected means unsupported by this
bridge; it is not a claim those instructions are invalid on Apple hardware.

`native_sptm_patch.py` adapts the supported immediate forms only in instruction
sections. `test_native_sptm_patch.py` verifies matching data words stay intact,
unsupported forms remain visible, SMC ledger indices decode correctly, and
duplicate instructions retain one ledger entry. Host regressions pass 24/24
in `HVF_GXF_HOST8.log`; Python compilation and shell syntax checks pass.
Regenerating the real image as `HVF_GXF_SPTM8.macho` and its ledger produces
exactly the same hashes as `HVF_VSH_SPTM2`: the current SPTM instruction
sections contain one GENTER, immediate zero at original `...270a390c`.

`HVF_GXF_BOOT8.virtual.json` executes that real image without debugger
fixtures. At the same `0xfffffff0070a3978` stop, ESR_GL2 is now `0xfe010000`,
ELR_GL2 is `0xfffffff0070a3910`, CURRENTG is 1, and all five bootstrap table
hashes still match. HCR_EL2 remains `0x488000000`. GXF_CONFIG_EL2 is still 1:
the attempted `0x6f` write is rejected before mutation. This correction has
not advanced the boot PC or reached XNU.

`HVF_GXF_TCG8.log` verifies the unchanged 60-second TCG restore-ramdisk
baseline: shell=yes, 303 serial lines, zero XNU panics. Checkpatch reports
zero errors/warnings for all three changed HVF headers in
`HVF_GXF_CHECK8.log`; both repositories pass `git diff --check`.
All owned probes and builds exited. A final process inspection still found
the separate display QEMU, PID 43771, running from its own checkout;
it was not modified or interrupted. No changes were committed or pushed.

### What the newer reference does not establish

Asahi's [August progress report](https://asahilinux.org/2026/08/progress-report-7-2/)
reports SPTM/XNU execution using emulated SPRR/GXF under m1n1. This is useful
independent feasibility evidence, not proof that public HVF exposes identical
capabilities or that our complete protection model is implemented.

In the pinned source, the GXF_CONFIG handler at `hv_sprr.c:884` retains only
the enable boolean; it does not characterize our remaining bits. The shadow
permission code also explicitly documents overgranting permissions that do
not fit ARM AP coupling, and ignoring hierarchical restrictions it does not
understand. Neither shortcut is suitable for this project's protection
requirement. Its `locked` function argument refers to the hypervisor's
software lock, not the GXF_CONFIG hardware LOCK bit.

DFF's final setup disassembly independently shows the same pattern as our
firmware: start with `0x2f`, conditionally clear lower GXF banks and OR `0x40`,
then write the privileged configuration. Its original sites are
`0xfffffff0270a3920..3944`; ours are `0xfffffff0270a3954..3978`.
This corroborates the requested state but still does not explain PEX2, PEX0,
NACC or HVAC. Do not accept `0x6f` by copying Asahi's enable-only handler.
