# TCG boot and multicore validation — 24A5430a (2026-09-11)

Goal: a repeatable disk boot to visible lock screen in less than 100 seconds,
plus evidence that all six guest CPUs execute work correctly. The timing starts
at QEMU launch, excludes manifest hashing/preparation, and does not mean every
background daemon has finished. Use fresh children of the same immutable,
already-migrated native-SMC/cellular-plan disk; no saved RAM, device-tree,
SPTM/TXM, guest version, CPU topology or service changes.

Evidence root: `/Users/jdolbe1/dvm-artifacts/research/tcg-boot-multicore-20260911`.

## Initial observation

The installed default's `TCG_BASE_0911A` reached early userspace at 8.376 s and
first 1179×2556 BGRA presentation at **99.198 s**, zero panics. `final.png` shows
a real lock screen. The first-frame timing completed before sampling; the
20-second CPU profile therefore did not contaminate it. A single sub-100 result
has too little margin to establish repeatability.

The subsequent `TCG_BASE_PROFILE` observation contradicts the earlier claim
that performance CPUs never work: all six CPUs executed EL0 instructions.
Across 40 simultaneous register samples, CPUs 0–5 had respectively
18, 15, 13, 12, 22, and 27 userspace samples. Three samples captured five CPUs
in EL0 simultaneously, including CPUs 4 and 5. All six busy host threads used
16.52–18.13 CPU seconds over 20.3 seconds (whole process 516.4% of one core).
This establishes real activity on both clusters in this run; it does not
explain the historical idle-core observations or prove linear scaling.

Do not force scheduler core recommendations based solely on those old samples.
The exact CLPC `"clpc"` string reference at `0xfffffff0097571ec` is used in
lock-group setup, not evidence that a `clpc=0` boot argument disables it.
No such option or kernel patch was applied.

## Controlled candidate

`tools/build_qemu_fast.sh` builds current QEMU with O3/LTO, assertions and debug
symbols retained. `tools/re/tcg_boot_candidate.py MANIFEST QEMU NEW_DIRECTORY`
pins that executable and qemu-img while preserving all other manifest inputs.
This includes the branch's epoch-based 2^14-entry TCG jump cache; the original
package remains available for alternating controls.

Check local and cross-cluster secondary startup, shared-memory atomic count,
bidirectional FIQ/IPIs and masked-interrupt WFE event wakeups with
`tools/re/smp_smoke.py` (plain, `--cross-cluster`, `--wfe`). These machine tests
are distinct from observing real XNU/userspace scheduling. Then compare fresh
boots with `warm_boot_probe.py --seconds 240 --stop-on 'iomfb: presented '`.
Promote only after repeat timing and a post-boot display/completion check.

The host profiler's HMP reader now exits on socket EOF instead of spinning
forever after an owned VM closes its monitor. This affects tooling, not TCG
execution or guest scheduling.

Machine checks on the rebuilt candidate passed: local CPU0/1 and global
CPU0/4 tests both returned `(1,1,1,40000,1,1,0x600d)`. WFE returned
`(16,16,16,0,0,0,0x600d)`: both event edges and a virtual timer offset woke
correctly with IRQs masked; the disabled stream remained asleep. These are
bare-machine probes, so their `reached shell: no` verdict is expected.

## First candidate results and rejected cache change

`TCG_FAST_0911A`: first frame 99.089 s, zero panics, effectively tied with the
99.198 s control (observer resolution ~0.2 s). `info jit` at first display shows
3 full TB flushes and 949,851,864 / 1,072,906,240 bytes of generated-code space.

`TCG_CACHE4_0911A`: same rebuilt executable and disk, only `tb-size=4096` added.
First frame 100.307 s, zero panics. Full TB flushes fell to zero; generated-code
space used 2,115,401,064 / 4,294,131,712 bytes; RSS at the paused milestone was
7,299,120 KiB. This change removed cache pressure without improving boot and
was not promoted.

`TCG_TRACE_0911A` adds RPC trace logging and a deliberate paused RAM capture;
its elapsed time is **not** a benchmark. First IOMFB setup completes around
2 s, display configuration at 16.4–16.8 s, and display-on A484 at 47.9 s.
During the startup gap, backboardd's main thread is in CFRunLoop's Mach receive;
SpringBoard's main thread is executing ObjC lookup inside
`STStatusDomainXPCServerHandle registerClient:forDomain:` called by
`SpringBoard applicationDidFinishLaunching:`. A keyboard worker waits for the
main dispatch queue. These stacks do not prove a fixed timeout. Exact stacks,
slide 0x6260000, and symbolication are in `boot-wait-snapshot/`.

A subsequent bounded experiment tested opt-in `DARWIN_TCG_QOS=user-initiated` in the macOS
MTTCG vCPU thread entry. It requests host pthread QoS and records set/get API
results and effective class per CPU. It does not pin physical cores, change
XNU scheduling, or change default policy when absent. Acceptance requires an
actual timing benefit with repeated fresh disk boots and preserved CPU tests.

The QoS experiment (`TCG_QOS_0911A`) returned set/get success and effective class
25 on all six CPUs, but first frame was **102.057 s**. It was rejected and its
implementation removed; experimental source/binary remain in the evidence
package only. `TCG_CACHE512_0911A` then tested a smaller 512 MiB translation
cache on the original rebuilt executable: **103.240 s**, also rejected. Keep
the standard 1 GiB cache and inherited host scheduling.

The retained-state comparison was explicitly a **guest-state** experiment. The stopped
`TCG_BASE_0911A` disk child was cloned read-only into `retained-data/`, preserving
its original backing chain. A fresh QEMU process boots a new child of this disk,
with the same fast executable and 1 GiB cache. No RAM, CPU or device checkpoint
is restored. Any timing benefit here is retained guest work/caches, not faster
TCG instruction execution; it must not be conflated with the same-disk controls.


## Final measurements and scope

| Fresh-process run | First presentation (seconds) |
|---|---:|
| Installed default A | 99.198 |
| Optimized build, standard cache | 99.089 |
| Optimized build, 4 GiB cache | 100.307 |
| User-initiated host QoS | 102.057 |
| Optimized build, 512 MiB cache | 103.240 |
| Retained guest disk state | 99.596 |
| Installed default B | 101.283 |

All listed runs recorded zero panics. The final control is recorded in
`baseline-b.log`; the retained-state result is in `retained-a.log`.
The two unchanged-default measurements straddle 100 seconds: **repeatable
sub-100-second boot is not established**. None of these candidates demonstrated
a meaningful improvement, and no candidate was promoted to the installed
default. Multicore execution and the bounded machine contracts above passed;
that does not establish arbitrary SMP correctness or linear scaling.

These timings stop at first presentation. Completion counters with RPC tracing
disabled are not completion verdicts. The final repeat does not establish
post-boot input recovery or sustained frame pacing. The diagnostic paused run
is excluded from the timing table. The fast-build run also overlapped a short
host test suite, so its 0.109-second difference is not an optimization claim.

Reproduce an installed-default control from this worktree:

```sh
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/warm_boot_probe.py \
  --tag TCG_CONTROL_NEW --seconds 240 --stop-on 'iomfb: presented '
```

For a candidate, first pin its executable with `tcg_boot_candidate.py`, then
supply the generated manifest as the positional argument to `warm_boot_probe.py`.
Preserve the result, launch manifest, logs, `jit.txt`, and screenshot outside
`/tmp` before cleaning up a run. This commit records evidence and improves
measurement tooling; it does not claim a new TCG performance fix.


## Startup host profile and next bounded candidate

`TCG_HOSTPROF_0911A` sampled the installed default for 25 seconds during
startup, without HMP register sampling. `boot-host-profile/host-sample.txt`
and `categories.txt` preserve the raw call tree and single-attribution report.
Across 55,284 vCPU stack observations: condition waits 20.14%, mutex waits
13.17%, translated-block lookup 11.94%, MMU translation 11.84%. These include
waits, not just on-CPU cycles, and are not additive speedup predictions.
Of the condition-wait observations, 5,153 are under `qemu_process_cpu_events`
and 5,059 under `cpu_exec_start`; 922 are under `start_exclusive` from queued
CPU work. Idle/event waiting must not be confused with avoidable contention.
The instrumented boot reached first presentation at 113.040 seconds, zero
panics; sampling perturbs execution, so exclude it from candidate rankings.

The next candidate changes only the jump-cache capacity from 2^14 to 2^16
entries in the current epoch-invalidation implementation. Historical 2^16
results used full-array clears and do not test this implementation. Preserve
all invalidation and synchronization behavior; require machine SMP checks,
then fresh disk boot comparisons. Reject absent a repeated timing benefit.


`TCG_EPOCH16_0911A` rejected the larger epoch cache: early userspace took
52.629 seconds and no presentation arrived within the 180-second bound
(zero reported panics). Cross-cluster reset/atomics/IPIs passed beforehand
(`epoch16-smp.log`), demonstrating why machine smoke tests alone are not
boot acceptance. The source change was reverted; the experimental executable
and manifest remain isolated under `epoch16/`. `build-fast` still contains
that experimental binary until rebuilt, so use the accepted pinned `fast/`
executable for controls. The installed default was never changed. This result
does not establish the cause of the slowdown or justify removing TLB ordering.
Both diagnostic VMs were stopped by their owned observer after collection.


## Identical TCR writes: bounded diagnostic

The current `vmsa_tcr_el12_write` flushes on every write, including identical
values; `TCR_EL1` and `TCR_EL2` use this handler. A diagnostic candidate adds
`DARWIN_TCR_SAME_WRITE=observe|elide`, disabled by default. Observe mode keeps
all flushes and counts changed/identical values per CPU. Elide mode skips only
this handler's flush when the full old and new register values are equal;
raw register writes, changed values, TTBR/ASID paths and explicit TLBI remain
unchanged. This is an experimental hypothesis, not an accepted optimization.
Source and executable are pinned under `tcr-same/` for reproduction. The
observation run must establish frequency before investing in translation
correctness tests and fresh-boot comparisons for elision.


`TCG_TCROBS_0911A` reached first presentation at 100.522 seconds, zero panics.
Exit counters found 187,618 changed writes and just one identical write across
six CPUs. Identical-write elision cannot materially help this workload; it was
not enabled, and the diagnostic implementation was removed after preserving
its source/binary. No correctness claim is made for unexecuted elide mode.

Next candidate: scope TCR_EL2 invalidation to `alle2_tlbmask()` and TCR_EL1
invalidation to `alle1_tlbmask(env)`, selected by actual backing field for VHE
aliases. Both masks already serve TTBR/VMID invalidation paths in `helper.c`.
Unknown fields retain full invalidation, and explicit guest TLBI paths remain
unchanged. This needs exact-guest timing and translation/multicore validation;
the presence of reusable masks is static evidence, not runtime proof.


`TCG_TCRSCOPE_0911A`: early userspace 8.503 seconds, first presentation
100.940 seconds, zero reported panics. No useful gain versus the 100.522-second
observation control or the original 99–101-second controls. First-frame partial
TLB flush counts were 1,502,549 versus 1,520,257 for the observation control;
these are different executions, not an instruction-matched differential test.
The candidate passed CPU0/4 atomics/IPIs and the 1,024-case PAuth matrix before
this boot. After resuming from the first-frame pause, a helper ping received no
ACK within 10 seconds. Serial showed helper PID87 initializing, then PID245
initializing; readiness/input recovery was not established. This does not prove
candidate causality. Do not retry the ambiguous packet. Evidence is in
`tcr-scope-a/`, `tcr-scope-input.jsonl`, and the smoke logs.

The scoped-flush source change was reverted for lack of demonstrated benefit.
Its source/binary remain under `tcr-scope/`; `build-fast` currently contains that
rejected experimental binary until rebuilt. The accepted `fast/` executable
and installed default remain intact. The owned VM was explicitly stopped.


## Late startup snapshot: actual first-commit work and helper abort

Correction to the scoped-flush input check: its retained serial log later
contains `DVM_INPUT_READY` and `DVM_INPUT_ACK 1789130356273 1`. The 10-second
host deadline expired, but the ping was eventually acknowledged. This was
late readiness, not evidence of permanently failed input or TCR causality.

`TCG_LATE_0911A` uses the accepted original `fast/candidate.json`, unchanged
TLB behavior, and pauses at 85 seconds before first presentation. Input PID87
started at 11.960 s and initialized at 12.164 s; replacement PID246 started at
59.707 s and initialized at 59.914 s. Full paused RAM, hashed capture manifest,
process stacks and symbolication are retained in `late-snapshot/` (shared-cache
slide 0x11bd8000). The VM was explicitly stopped after offline analysis.

At 85 s, SpringBoard PID35's main stack is `_platform_memmove` through
`_malloc_type_realloc`, CoreFoundation notification registration and
`SBHIconViewContextMenuStateController registerIconView:`; below it are
`SBIconListView`/`SBFolderView` icon construction, UIKit layout and
`UIApplication _firstCommitBlock` -> `CA::Transaction::flush`.
Static PCs include 0x18c373fec, 0x1c4b0f764 and 0x1849f4f84. Backboardd PID74's
main thread is in CFRunLoop Mach receive. This is a sample of real first-commit
work, not proof of a fixed timeout or of the percentage of boot spent there.

The retained old helper PID87 has an abort stack: `query_displays` at
0x1844e4eb4 -> `CADisplay displays` -> `RCPActiveScreens` ->
`RCPEventEnvironment` -> `touchScreenDigitizerSenderForDisplayUUID:`. Its
replacement PID246 is loading Objective-C categories through
`AXUtilsBackBoardServer`, `_AXSAssistiveTouchEnabled`, and
`RCPVirtualHIDService initWithIdentifier:properties:` (0x29b1fe774). Old process
objects can survive in RAM; the serial PID transition corroborates which is
the replacement. These observations motivate a controlled comparison with
the existing direct-HID helper, avoiding Recap's display/accessibility loading.
That comparison must preserve input functionality and the exact disk lineage;
no boot-time benefit is established yet.


The existing direct-HID source has been built and signed into `direct-hid/`
and staged in a small isolated restore ramdisk with `prepare_ramdisk.sh` and
`install_hid_in_guest.sh`. Its system trust cache merges the installed cellular
baseline trust cache, retaining all existing hashes. The staging attachment
was safely detached. No helper was installed into a system disk in this step;
installation and comparative fresh boots remain pending.


## Direct-HID comparison

`TCG_HID_INSTALL1` confirmed `DVM_HID_INSTALL_DONE`, validated packets in the
restore guest, and sealed its child. Durable disk/evidence are under
`direct-hid-installed/`; no migrated baseline file was modified.
The first attempted `TCG_HID_0911A` had an orchestration error: the manifest
pinned the new backing chain but retained the old `disk.path`. It was stopped
and excluded. `warm_boot_probe.py` now rejects such a mismatch before creating
an overlay or launching QEMU; the new regression test and all 85 host tests pass.
The other saved experiment manifests were checked for the same mismatch; none
was found. Both selected path and backing-chain root were fixed before run B.

`TCG_HID_0911B` with direct-HID v16 (`touch_builtin=0`): early userspace 8.130 s,
first helper ready 25.606 s, first presentation **98.564 s**, zero panics at
that milestone. The helper restarted once, from PID87 to PID180; the successor
was ready and answered commands. Two Home actions dispatched both edges with
zero timeouts; the second screenshot visibly shows the home app grid. A tap
on Settings and horizontal page swipe were dispatched successfully, but their
screenshots do not show the expected app/page change. Clock updates can change
frame hashes, so `frame_changed=true` is not touch acceptance. This configuration
is not promoted; it has only one timed boot and incomplete touch verification.

`direct-hid/` contains action JSON and before/after screenshots; small final run
artifacts are in `direct-hid-b/`. The VM was explicitly stopped. A bounded
follow-up uses the existing `DVM_HID_TOUCH_BUILTIN=1` build option: event built-in
metadata is already set while the previous virtual-service property was false.
`TCG_HID_INSTALL2` confirmed installation on another child of the same original
parent; `direct-hid-builtin-installed/` preserves it. Its boot and visible touch
checks remain pending. Neither this metadata hypothesis nor a sub-100 boot
establishes useful input until visible actions pass.


`TCG_HIDBI_0911A`, built-in touch service: early userspace 8.393 s, helper
ready 26.689 s, first presentation **98.551 s**, zero panics at first frame.
Two Home actions visibly unlocked to the app grid with two successful HID
edges each and zero timeouts. The Settings tap's five-second screenshot still
showed the icon grid; a later capture (`settings-late-a.png`) confirms Settings
opened. The launch latency was not continuously measured, so do not call that
a five-second launch or infer touch failure from the early frame. A subsequent
400 ms upward swipe visibly scrolled to lower Settings rows, with 13 successful
dispatches, zero timeouts/failures, and ready state R. Action evidence and images
are under `direct-hid-builtin/`. Small final run files are in
`direct-hid-builtin-a/`; the VM was explicitly stopped. The identical-config
fresh repeat `TCG_HIDBI_0911B` is the next timing check. This variant has useful
input evidence, but a single sub-100 measurement is not repeatability proof.


## Final direct-HID repeatability checkpoint

The same built-in-touch configuration completed three fresh disk boots:
`TCG_HIDBI_0911A` **98.551 s**, `TCG_HIDBI_0911B` **98.278 s**, and
`TCG_HIDBI_0911C` **100.180 s** to first visible lock-screen presentation.
Median **98.551 s** does not establish repeatable sub-100-second boot: the
maximum still exceeds the target. Small run evidence is preserved in
`direct-hid-builtin-a/`, `direct-hid-builtin-b/`, and `direct-hid-builtin-c/`
under the artifact root above. The installed default remains unchanged.

After B's timing, `direct-hid-final-cpu/result.json` and `vcpu-samples.txt`
record all six CPUs executing EL0 in 40 samples (counts 19, 23, 14, 15, 26,
25). Host process usage was 503.3%, with the six busy threads accumulating
17.91, 17.67, 17.43, 17.23, 15.73 and 15.59 CPU seconds over about 20.3 s.
This corroborates multicore execution; it does not establish linear scaling.
B's later Home command acknowledged both edges but left a black capture, so
that action is not display-recovery proof. C separately exercised power-off
and explicit wake: `already_on:false`, `display_power_on:true`, both edges
successful, followed by the visible lock screen in
`direct-hid-builtin/wake-c.png`.

The isolated candidate can be launched with a fresh disposable child:

```sh
DVM_SMC_MANIFEST=/Users/jdolbe1/dvm-artifacts/research/tcg-boot-multicore-20260911/direct-hid-builtin/candidate.json ./run.sh --cocoa
```

Both interactive and bounded boot entry points now reject a selected disk
that differs from the verified backing-chain root before running subprocesses.
All **86 host tests passed** (`host-tests-final.log`). Rejected QEMU source
experiments were reverted; the restored-source rebuild completed successfully
(`build-restored-source.log`). Timed results above use the separately pinned
accepted fast binary. No experimental TCR optimization is included in this
checkpoint.


## Bounded range jump-cache invalidation experiment

Question: does the epoch jump cache make full hint invalidation cheaper than
page-by-page bucket clearing during large TLB range invalidations?
Static evidence: `accel/tcg/cputlb.c:tlb_flush_range_by_mmuidx_async_0` retains
the old `TARGET_PAGE_SIZE * TB_JMP_CACHE_SIZE` crossover; each
`tb_jmp_cache_clear_page` clears `TB_JMP_PAGE_SIZE` atomic pointers.
`translate-all.c:tcg_flush_jmp_cache` now advances an epoch except at wrap.
The candidate uses the epoch path at 16 pages while retaining every TLB flush,
cross-CPU work item and synchronization operation. Invalidating additional
jump-cache hints is conservative, but the resulting refill cost may lose.

Acceptance: same direct-HID built-in disk/firmware/topology, real visible first
frame, no panic, then repeat and input checks if the initial timing improves
meaningfully. Stop at 180 seconds without presentation, any panic, or no useful
initial speedup; do not retain a performance change merely because it boots.
The source patch and build log are preserved with `range-epoch16/` artifacts.


Result: **rejected for no demonstrated performance benefit**.
`TCG_RANGE16_0911A` reached early userspace at 8.184 s, input-ready at
28.146 s and first presentation at **100.713 s**, with zero reported panics.
`range-epoch16-a/final.png` was visually checked and contains the lock screen.
The cross-cluster machine smoke passed `(1,1,1,40000,1,1,24589)` before boot
(`range-epoch16-smp.log`). This is no useful improvement over the selected
control's 98.278–100.180 s range. One run does not prove a statistically
significant regression or identify refill cost as the cause. Per the stop
condition, no additional boot was spent on this threshold and the source was
restored. The owned test VM exited and all small evidence was copied outside
`/tmp`. The next investigation should measure synchronization frequency and
queue depth before attempting to batch exclusive work; this experiment did
not alter or establish the safety of that contract.


## Exclusive-work diagnostic

Question: is there enough adjacent exclusive CPU work to amortize repeated
stop-the-world handshakes without changing queue ordering? The temporary
`DVM_CPU_WORK_PROBE` instrumentation in `cpu-common.c` preserves the existing
BQL unlock, `start_exclusive`, callback, `end_exclusive`, BQL lock sequence.
It measures those phases separately and, under the existing queue mutex,
counts an immediately exclusive successor and maximum remaining depth (capped
at 64). Per-thread counters log at item 1 and each 1024 items. No shared
counter lock is introduced. `work-probe/source.patch` and its pinned candidate
preserve the exact instrumented source and executable.

Expected evidence: per-CPU cumulative counts and phase times from one isolated
fresh boot, analyzed by `tools/re/cpu_work_report.py`. Stop on first presentation,
panic or 180-second deadline. Wait times overlap across CPUs, callback timing
includes `end_exclusive`, and unreported tails are excluded. This diagnostic
run is not eligible for boot-performance acceptance. If adjacent exclusive
items are rare, do not implement general queue batching on that hypothesis.


`TCG_WORK_0911A` was excluded and stopped after detecting that the boot tool
filtered out `DVM_CPU_WORK_PROBE`; its saved launch environment confirms this.
A temporary explicit allowance in `warm_boot_probe.py` enabled run B. Both
instrumentation patches are preserved in `work-probe/`; production source and
launcher filtering were restored afterward.

`TCG_WORK_0911B` completed first presentation at 101.368 s, zero reported
panics. This is instrumented diagnostic timing, not a candidate speed result.
Its 694 periodic samples cover **704,512 exclusive items**, with **zero
immediately exclusive successors** across all six CPUs. Maximum remaining
queue depths were 2, 2, 2, 3, 2, 3; these items were not adjacent exclusive
work. Summed phase times: **8.950666 s** acquiring exclusivity, **3.839376 s**
callback plus ending exclusivity, **0.802921 s** reacquiring BQL. Largest
single observed exclusivity acquisition was 48.833 ms on CPU2. Summed waits
can overlap and omit time other CPUs spend stalled, so they are not an
estimate of achievable boot speedup.

Conclusion: no demonstrated opportunity for batching adjacent exclusive queue
items. Do not implement it on the prior profile hypothesis. The many
synchronization points remain a possible cost, but reducing their number
requires identifying the actual issuing operation and its architectural
completion boundary, not relaxing the existing handshake. Source, executable,
manifest and summary are under `work-probe/`; raw evidence is in
`work-probe-b/`. The owned VM exited normally after collection.


## TLBI issuing-operation diagnostic

Question: which guest TLB-maintenance operations generate the large exclusive
work count? The temporary `DARWIN_TLBI_PROBE` patch instruments 42 write entry
points in `target/arm/tcg/tlb-insns.c`, preserving their behavior. Per-thread,
per-register counters log item 1 and each 4096 items, with a sampled PC and
operand. There are 128 slots per thread and an explicit overflow error.
`tools/re/tlbi_report.py` rejects counter resets and overflow; totals exclude
unreported tails. Sampled PCs are not a full precise instruction trace.

Stop on first presentation, panic or 180 seconds. Preserve the candidate and
patch in `tlbi-probe/`; this instrumented timing is ineligible as a speed
comparison. Use operation counts and source call paths to select the next
contract investigation; counts alone do not measure operation cost.


`TCG_TLBI_0911A` completed first presentation at 102.719 s with zero reported
panics (diagnostic timing only). Reported lower-bound counts: VALE1ISNXS
**425,984**, VAALE1ISNXS **212,992**, RVALE1ISNXS **16,386**; remaining names
have only small sampled counts. Periodic sampling cannot exclude substantial
sub-4096 tails for a rare name on each CPU. There was no counter-slot overflow.
The sampled runtime PCs 0xfffffff0070d4e84 and 0xfffffff0070d4dcc correspond
to `tlbi vale1isnxs, x9` and `tlbi vaale1isnxs, x9` at linked SPTM addresses
0xfffffff0270d4e84 and 0xfffffff0270d4dcc. Disassembly is preserved in
`tlbi-probe/sptm-issuing-loops.txt` and `sptm-issuing-tail.txt`. These are
operation-dispatch sites; this alone does not establish a batching loop or
where every caller completes its maintenance sequence.

Both names use `tlbi_aa64_vae1is_write` in QEMU, which computes the address-bit
mask and invokes the synchronized page-bits API. `tlbbits_for_regime` returns
56 with TBI, 64 otherwise. For masked single pages,
`tlb_flush_range_by_mmuidx_all_cpus_synced` currently copies one parameter
allocation per CPU in addition to each queued work item. This suggests a
narrow allocation optimization: pack eligible page parameters into the
existing 64-bit work payload, preserving all callbacks and synchronization.
Runtime frequency of eligible masked/canonical addresses is still unmeasured;
this trace establishes the instruction family, not that all calls allocate.
Any implementation must round-trip the complete MMU mask and address, fall
back for ineligible parameters, and demonstrate a boot benefit before being
retained. No guest, SPTM or TXM change is proposed.

The diagnostic VM exited and small evidence is in `tlbi-probe-a/`. The
instrumentation was removed from source after preserving its exact patch.
