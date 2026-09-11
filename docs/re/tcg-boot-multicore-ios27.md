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
supply the generated manifest through `warm_boot_probe.py --manifest`.
Preserve the result, launch manifest, logs, `jit.txt`, and screenshot outside
`/tmp` before cleaning up a run. This commit records evidence and improves
measurement tooling; it does not claim a new TCG performance fix.
