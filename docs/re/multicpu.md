# Experimental multicore iOS boot

2026-09-04. **Real iOS boots with two and six CPUs using an explicit virtual
CPU power-management adapter. Two CPUs are verified simultaneously executing
separate userspace workloads.** Display is not a prerequisite. This remains
experimental: physical ApplePMGR, suspend, hotplug and checkpoint restore are
not supported by this path. The six-core first-boot migration stall was traced
to a missing Apple WFE event stream and fixed; two fresh partial migration runs
now reach 100 User-volume progress events in about 99 seconds each.

Worktree: `/Users/jdolbe1/Downloads/darwin-vm-multicpu`, branch `codex/multicpu`
in both the parent and nested QEMU repository. Based on parent `487f8aa` and
QEMU `d35d9ce7f472365de9d938f5c8f418cef76a9c02`. The original checkout's
uncommitted display/checkpoint work is excluded. Shared firmware was not
modified. Changes have not been merged back.

## Run

From this worktree:

```sh
python3 tools/run_smp.py             # interactive restore shell, 2 CPUs
python3 tools/run_smp.py --cpus 6    # six-CPU experimental boot
```

Exit with Ctrl-A, X. The launcher verifies the supported kernelcache hash,
creates `/tmp/dvm/SMP_PV.bootkc`, then enables `DARWIN_SMP_PV=1` and
`-accel tcg,thread=multi`. `--check` prepares the kernel and prints the command
without booting. The default `run.sh` still uses the original single-CPU path.

Supported input: iPhone17,3 / T8140, iOS 27 build 24A5430a, bootkc SHA256
`dc0f5b6a6fa848053c301949c8376c216c6223c047203b93e408a93d3440f906`.
Other kernel versions are rejected, not patched by guessed offsets.

Build in this worktree's own `qemu-sptm/build`, configured with
`--target-list=aarch64-softmmu --disable-pvg`, then `make -j12`.

## Implementation

`qemu-sptm/hw/arm/darwin_smp.c` creates independent CPU state sharing RAM and
MTE tag memory, with affinities 0–3 and 0x100–0x101 from the device tree.
Secondaries start powered off. Per-core RVBAR/status MMIO and T8140's
PMGR+0x34000 CPU-start bitmap release a selected CPU at its reset vector.
Local/global fast IPIs route by affinity; IPI_SR bit 0 is pending and
write-one-to-clear. An OR gate combines each CPU's IPI and virtual timer FIQ.
Shared IPI state uses QEMU's big lock. CPU-start pending state prevents duplicate
resets, and cluster CPM backing is shared by CPUs in that cluster.

For SMP, machine wiring supplies exactly one `cpus=N` argument, rejects
conflicting `cpus=` / `cpumask=`, and disables the old single-CPU global GXF
measurement counters to avoid concurrent updates. The normal default is one CPU.

`tools/re/smp_pv_patch.py` replaces the missing physical IOPMGR CPU interface
in a separate, hash-pinned kernelcache. It retains real `ml_processor_register`,
`processor_boot`, SPTM setup, secondary execution and XNU scheduling. It does
not manufacture online counts or patch scheduler results. Its virtual ABI
uses `MSR S3_0_C15_C15_7,x1` to release a logical CPU through QEMU's reset path.
This register is explicitly a project ABI, not an alleged Apple register.

The adapter's unslid patch sites are:

| Address | Replacement |
|---|---|
| `0xfffffff00b2f8194` | replace physical IOPMGR service wait with null; retain dictionary release |
| `0xfffffff00b2f8314` | retain zeroed CPU idle parameters instead of physical initCPUIdle |
| `0xfffffff00b2f8950` | idle timer wrapper: zero deadline |
| `0xfffffff00b2f898c` | idle enter/exit wrapper: zero deadline, ordinary WFI |
| `0xfffffff00ac61804` | after SPTM preparation, invoke virtual CPU-start ABI |
| `0xfffffff00ac61844` | return after original authenticated epilogue |

XNU `iokit/Kernel/arm/AppleARMSMP.cpp` and `IOKit/IOPMGR.h` supply the interface
contract; the latter permits a zero idle deadline. Physical PMGR stays absent.
Other paths that dereference gPMGR (suspend/hotplug/cluster power-down) remain
unsupported and may crash if invoked. This is a boot/run adapter, not a full
power-management replacement.

The virtual firmware handoff enables FP before secondary SPTM execution:
24A5430a SPTM `0xfffffff0270db2a0` uses `LDR Q0` before enabling it itself.
`apple_regs_pv_cpu_handoff` copies only the established EL2 CTRR/CTXR protection
map to the powered-off target. SPTM's `ctrr_ctxr_check_region` at
`0xfffffff0270b3f5c` onward validates that map. Upper bounds are normalized to
4 KiB page addresses as checked at `0xfffffff0270b42d0`; no CPU execution state
or authentication keys are copied.

## Verified results

Host: Apple M5 Max, 128 GiB RAM, macOS 27. TCG multithreaded execution.

- Machine tests: both local CPU0/CPU1 and cross-cluster CPU0/CPU4 startup,
  locked RVBAR, shared LDAXR/STLXR atomic count **40000**, and bidirectional
  FIQ/IPI acknowledgement. Final verdict word **0x600d**.
- Real iOS two-core restore boot: `nproc` reports **2**. A simultaneous debugger
  stop captures both CPUs at EL0 with distinct userspace stacks while running
  separate compute loops. Both workloads can be stopped and the shell responds.
  Final probe verdict: **reached shell: yes; panics: 0**.
- Six-core restore boot (`SMP_PV10_SIX`): all five secondaries enter real SPTM
  and XNU, all six show kernel execution, `nproc` reports **6**, shell reached,
  **zero panics**. The shell-loop workload did not demonstrate simultaneous
  EL0 work on both performance CPUs; they remained idle in those samples.
  A foreground-helper attempt stopped at unreliable serial upload and provides
  no additional scheduling evidence. Six-way userspace scaling is unverified.
- Six-core persistent system boot (`SMP_SYSTEM6_FINAL`): a fresh qcow2 child of the
  existing persistent parent reached **BSD root: disk1s1**, then **Early boot
  complete**, remained panic-free for another ten seconds, and stopped on that
  condition. The parent disk was not written. This does not verify display UI.

Reproduce bounded tests after `python3 tools/run_smp.py --check`:

```sh
python3 tools/re/smp_smoke.py
python3 tools/re/smp_smoke.py --cross-cluster
python3 tools/re/smp_userspace_smoke.py --cpus 2 --tag SMP_USER_NEW
python3 tools/re/smp_boot_probe.py --cpus 6 --tag SMP_BOOT_NEW
python3 tools/re/smp_system_smoke.py --cpus 6 --tag SMP_SYSTEM_NEW
```

The system test requires the existing artifacts at
`/tmp/dvm/data-seed/persistent-parent.qcow2`,
`/tmp/dvm/data-seed/dt_nvme_welcome.bin`, and
`~/dvm-artifacts/tc/merged_sysvol_cryptex_tc.bin`. It creates a uniquely tagged
child and reuses the established DCP configuration. Tests own their QEMU
processes and never stop another worktree's guest.

Durable reproduction scripts are checked into the worktree; local evidence:
`/tmp/dvm/SMP_MACHINE_FINAL.log`, `/tmp/dvm/SMP_GLOBAL_FINAL.log`,
`/tmp/dvm/SMP_USER2_FINAL.log`, `/tmp/dvm/SMP_PV10_SIX.log`,
`/tmp/dvm/SMP_SYSTEM6_FINAL.log`, `/tmp/dvm/SMP_HOST_FINAL.log`,
`/tmp/dvm/SMP_BUILD_FINAL.log`. Host regressions: 18 tests pass. The default single-CPU regression also reaches
the restore shell with zero panics (`SMP_SINGLE_FINAL`); the packaged two-CPU
launcher was separately booted to the restore shell (`SMP_LAUNCH_FINAL`).

## Why an unmodified kernel leaves secondary CPUs offline

This firmware uses **AppleARMSMP**, not the legacy AppleARMCPU driver.
The live `SMP_TRACE1` experiment saw all six legacy driver starts return
false at unslid `0xfffffff0085bf0e8`. Public XNU's
`iokit/Kernel/IOCPU.cpp:is_IOCPU_disabled()` explicitly disables that path
when `USE_APPLEARMSMP` is set. The original hypothesis that the legacy
driver's `function-enable_core` resolution was the immediate blocker was
therefore discarded.

The actual `cpu_boot_thread` is at unslid `0xfffffff00b2f8024`. Its relevant
steps, identified statically and checked with hardware breakpoints:

| Runtime PC | Event | Observed in SMP_TRACE2 |
|---|---|---|
| `0xfffffff02b2f8068` | IOPlatformExpert wait returns | yes |
| `0xfffffff02b2f8174` | PassthruInterruptController initialized | yes |
| `0xfffffff02b2f8198` | IOPMGR wait returns | no |
| `0xfffffff02b2f8204` | Subsequent CPU registration begins | no |

`tools/re/smp_trace.py PORT` reproduces these breakpoints when the owned
probe starts with `-S -gdb tcp:127.0.0.1:PORT`. `--pc ADDRESS` selects custom
runtime breakpoints. These addresses apply only to this firmware/slide.

Experiments used separate device-tree output files, preserving raw property
bytes:

1. Restore `/arm-io/pmgr/compatible = "pmgr1,t8140"` from the IPSW tree.
   `SMP_PMGR2` prints `ApplePMGR: Starting AppleT8140PMGR`, but the CPU boot
   thread still waits for IOPMGR. Removing `vmm-present` (`SMP_PMGR3`) does
   not solve it.
2. `SMP_TRACE5` narrows the PMGR wait to its optional
   `function-mcc_ctrl` lookup, unslid `0xfffffff009228310`. Its provider is
   absent in the minimal device tree.
3. Remove that property in an experimental tree. `SMP_PMGR4` advances into
   `ApplePMGR::initDriver`, then the **first** panic is:

   ```
   ApplePMGR: virtual void ApplePMGR::initDriver(IOService *):1450
   voltage-states1 not found
   ```

The raw IPSW tree contains `voltage-states0`, `voltage-states2`, and
`voltage-states9`, but no `voltage-states1`. Additional firmware-provided
power/clock configuration and the PMGR register semantics must be derived
before claiming normal SMP boot. The working path below substitutes an explicit virtual CPU power-management
interface. It does not invent a voltage table or enable physical PMGR.

Do not decode/re-encode an already fixed tree with `dt_fixup` simply to add
a property: its heuristic interprets the 256-byte `A` random seed as a
string and adds a terminator. `SMP_PMGR1` consequently panicked in SPTM
with random-seed size 257. That was a test-input error, not an SMP failure.

## Boot-time measurement (2026-09-04)

Nine sequential fresh system boots, three per configuration, measured host
wall time from QEMU launch to `Early boot complete`. Same QEMU binary, 12 GiB
RAM, firmware/device tree/trustcache, DCP configuration, and persistent disk
parent; every trial uses a new writable qcow2 child. Host filesystem caches
were not cleared. Runs alternate configurations and reverse part of the order.
This is early system boot, not time to a usable display or SpringBoard UI.

| Configuration | Three runs (seconds) | Median |
|---|---|---|
| Original 1 CPU | 12.123, 11.601, 11.642 | **11.642 s** |
| Virtual adapter, 2 CPUs | 10.213, 11.103, 10.448 | **10.448 s** |
| Virtual adapter, 6 CPUs | 10.645, 11.184, 11.106 | **11.106 s** |

Six cores save **0.536 seconds (4.6% less time; 1.05x)** against the original
single-core configuration. Two cores save **1.194 seconds (10.3%; 1.11x)**.
Six did not improve on two in this small sample; this is not evidence for
six-way boot scaling. All nine runs reached the same milestone without a
first XNU panic. No workload throughput benchmark was performed.

This compares complete configurations: the one-core baseline uses the original
kernel input and single-CPU wiring/counters, whereas SMP uses the virtual
adapter and disables the historical global GXF counters. It does not isolate
CPU count from those implementation differences. A preliminary one-core adapter
control (`SMP_BOOT_TIMING1/1_pv1`) stalled and timed out at 60 seconds; that
configuration lacks the SMP wiring and is not a supported launch mode. Its
failure is excluded from the timing table, not counted as a slow boot.

Reproduce with `python3 tools/re/smp_boot_bench.py --tag NEW_UNIQUE_TAG`.
Evidence: `/tmp/dvm/SMP_BOOT_TIMING2/results.json` contains each command,
wall time, host load and the QEMU binary hash; adjacent files contain each
trial's complete serial/stderr logs. The other checkout's existing VM was left
untouched. The results describe this host/session, not an isolated laboratory
or a Windows host.

## Partial first-boot migration sample before the WFE fix (2026-09-04)

The user requested an estimate from fresh first-boot migration work, explicitly
without finishing migration. These tests used fresh qcow2 children of
`/tmp/dvm/data-seed/rebuild/marker.qcow2`, the prepared Data/User image **before
its first normal system boot**, not the later `persistent-parent.qcow2` or a
settled display checkpoint. All configurations used the same parent, QEMU,
12 GiB RAM, device tree and display configuration. Originals were not changed.
No host disk mounts or disk-image rebuilds were needed.

The sampled work is the long first-boot migration's directory-metadata phase,
not the restore helper's initial template copy. The observable proxy is a
unique `(volume, inode)` in `set_dir_stats:3247: disk1s5 setting dir-stats for
ino ...` lines. These are logged progress events, not measured bytes, durable
transaction commits, or a percentage of the complete migration. Each owned
VM stops at 100 User-volume events, 200 combined Data/User events, panic, or
180 host seconds. Earlier evidence has over 1,000 such events and later work;
none of these partial tests completed the migration.

| Configuration | First 100 User events, from QEMU launch | Events 20–100 interval | Rate during that interval |
|---|---|---|---|
| Original 1 CPU | 157.745 s | 46.941 s | **1.704 events/s** |
| Virtual adapter, 2 CPUs | 103.142 s | 29.877 s | **2.678 events/s** |
| Virtual adapter, 6 CPUs | not reached in 180 s; only 2 events | insufficient progress | no meaningful rate |
| 6 CPUs, independent fresh repeat | not reached in 180 s; only 2 events | insufficient progress | no meaningful rate |

Two cores reached the partial work limit in **34.6% less elapsed time**. The
post-warmup metadata rate was **1.57x** the one-core rate, projecting about
**36.4% less time for equivalent work in that phase**. This is an estimate
from one bounded sample each, not a complete-migration benchmark or a full
migration ETA. Later phases may be dominated by different work or waits.
The comparison also retains the kernel-adapter/counter differences described
in the early-boot benchmark above.

Six cores did reach Early boot complete in 11.406 / 10.513 seconds, but neither
run produced sustained progress in this sampled metadata phase before the
180-second cutoff. Each emitted 11 combined Data/User events, including only
2 User events. There was no first XNU panic in either serial log. The first
run was observed consuming about 307% host CPU despite this lack of metadata
progress. This is a repeatable progress failure under this workload, **not a
measured six-core migration speed or proof that every guest task is stalled**.
This was the pre-fix result. The diagnosis and successful correction are
documented next; the early-boot test alone had missed this failure.

Evidence: `/tmp/dvm/SMP_MIG_PARTIAL1/results.json` and
`/tmp/dvm/SMP_MIG_PARTIAL6_REPEAT/results.json`, with adjacent full serial and
stderr logs. Every result records the QEMU binary hash, exact launch command,
parent path, early-boot time, individual metadata event timestamps, and stop
reason. The initial results file's rate over the six-core run's **two events**
is merely a startup burst and must not be treated as sustained throughput;
the script now omits that rate when fewer than 20 events are available.

```sh
python3 tools/re/smp_boot_bench.py --migration-sample --tag NEW_MIG_SAMPLE
python3 tools/re/smp_boot_bench.py --migration-sample --variant pv6 --tag NEW_MIG_REPEAT
```

## Six-core stackshot stall: diagnosis and fix (2026-09-04)

`SMP_DIAG6_1` captured all six vCPUs at 55, 75 and 95 host seconds, after
metadata progress stopped. All three snapshots showed the same kernel PCs:

| CPUs | Runtime PC | Identified loop |
|---|---|---|
| 0–3 (efficiency cluster) | `0xfffffff02aa70384` | `stackshot_cpu_work_on_queue`, immediately after WFE |
| 4–5 (performance cluster) | `0xfffffff02aa75c4c` | `stackshot_aux_cpu_entry`, immediately after WFE while waiting for setup |

Identification uses the matching public XNU
`osfmk/kern/kern_stackshot.c:stackshot_cpu_work_on_queue`,
`stackshot_aux_cpu_entry`, `stackshot_cpu_preflight`, and disassembly of this
kernelcache at unslid `0xfffffff00aa70340` / `0xfffffff00aa75be4`.
Stackshot elects a recommended performance CPU as its main worker; other CPUs
wait for that worker to populate task queues. The four-core control
`SMP_DIAG4_CONTROL`, which has no performance cluster, reached 100 metadata
events in 98.090 seconds. All six CPUs had entered the debugger/stackshot
rendezvous in the failing case, confirming startup and debugger IPI delivery.

**The missing behavior was the Apple timebase event stream that wakes WFE.**
The captured register values on every CPU were `AGTCNTKCTL_EL1=0x0f`, while
architectural `CNTKCTL_EL1` / `CNTHCTL_EL2` were `0x03`. XNU's
`osfmk/arm64/machine_routines.c:_enable_timebase_event_stream()` deliberately
enables the event stream in `KERNEL_CNTKCTL_EL1` (Apple AGT on this SoC), and
separately enables only userspace counter access in architectural CNTKCTL.
`proc_reg.h:2793–2796` gives EVENTI bits [7:4], direction bit 3, enable bit 2.
The generated QEMU `AGTCNTKCTL_EL1` accessor at encoding `S3_4_C15_C9_6`
previously stored the value without connecting it to an event source.

QEMU's WFE helper actually halts the vCPU, and previously consulted only ARM
architectural event-stream controls. The performance CPUs had no pending timer
interrupt (`CNTHV_CTL_EL2=1`); the efficiency CPUs' timers were pending
(`CNTHV_CTL_EL2=5`). That accounts for sleeping performance workers and busy
waiting on the efficiency workers. A masked WFE needed the enabled Apple event
source to return and recheck the shared stackshot state.

The fix adds an optional machine-provided event deadline to ARMCPU's WFE event
calculation. `apple_regs.c:apple_event_stream_deadline_ns` computes the next
selected counter-bit edge from AGTCNTKCTL, AGTCNTVOFF and the emulated counter
frequency, returning an absolute virtual-clock nanosecond deadline. QEMU's
existing WFE timer then delivers the wakeup. Rising/falling edges, disabled
state, nonzero virtual offsets and overflow saturation are handled. The
architectural control registers retain their separate values. WFI behavior
and the guest stackshot/migration algorithms are unchanged. The already saved
Apple register backing remains authoritative; the callback is machine
configuration, not additional guest state. SMP restore is still unverified.

No new kernel patch was needed. The same hash-pinned virtual CPU adapter was
used before and after this emulator correction.

### Runtime verification

- `SMP_EVENT6_FIXED`: 100 User-volume events in **98.778 s**, with 2.531 events/s
  over events 20–100; zero XNU panics.
- `SMP_EVENT6_REPEAT`: independent fresh child, 100 events in **98.616 s**, with
  2.464 events/s over events 20–100; zero XNU panics.
- `SMP_EVENT2_COMPARE`: two CPUs on the same corrected build, independent fresh
  child, 100 events in **102.418 s**, with 2.604 events/s over events 20–100;
  zero XNU panics.
- All three tests stop at the same partial-work limit. None finishes migration.
- `python3 tools/re/smp_smoke.py --wfe` runs 16 masked-IRQ WFE waits for each of
  falling edges, rising edges and a nonzero virtual offset. All 48 wakeups
  complete; after disabling AGT events, the failure sentinel stays zero and
  the CPU remains at the final WFE. Result `(16,16,16,0,0,0,0x600d)`.
- Local and cross-cluster CPU startup/atomic/IPI tests still pass; the normal
  one-core restore boot reaches its shell with zero panics. The two-core
  userspace test still captures simultaneous EL0 execution with distinct
  stacks, stops its workloads and obtains a responsive shell, with zero panics.
- All 18 host tests and script syntax checks pass.

This fixes the observed six-core progress failure. It does not establish linear
speedup with CPU count or completion of every later migration/display phase.
Six cores reach the milestone about 3.6% sooner than the corrected two-core run,
but their events 20–100 throughput is slightly lower (2.46–2.53 versus 2.60/s).
These few samples support roughly comparable performance, not a robust claim
that six cores accelerate the migration work itself. More CPUs do not guarantee
lower elapsed time for serial work or work with synchronization overhead.

Artifacts: `/tmp/dvm/SMP_DIAG6_1/0_pv6.cpus{55,75,95}.json`,
`/tmp/dvm/SMP_EVENT6_FIXED/results.json`,
`/tmp/dvm/SMP_EVENT6_REPEAT/results.json`,
`/tmp/dvm/SMP_EVENT2_COMPARE/results.json`, and
`/tmp/dvm/SMP_EVENT_{WFE,LOCAL,GLOBAL}_TEST.log`, `SMP_EVENT_USER2.log`,
`SMP_EVENT_SINGLE.log`, `SMP_EVENT_HOST_FINAL.log`. The debugger single-step
wake experiment `SMP_WAKE6_DIAG` timed out and is not part of the causal proof.

To capture read-only CPU/register/frame snapshots in an owned partial probe:

```sh
python3 tools/re/smp_boot_bench.py --migration-sample --variant pv6 \
  --capture-at 55 75 95 --tag NEW_SMP_DIAG
```

Capture pauses/resumes the guest and ends the run after the final capture;
use ordinary migration-sample mode for timing. The diagnostic mode also accepts
`pv4` and `pv5` to isolate cluster topology. Every run uses its own fresh disk
child and stops only its owned QEMU process.

## Remaining limits

- Deferred IPIs currently take the immediate path; retract and no-wake are
  unimplemented. External AIC device IRQs still target CPU 0.
- Repeated reset, timer/IPI overlap stress, SMP migration and sustained workload
  stability have not been validated. A VMState descriptor alone is not evidence
  that checkpoint restore works.
- Early system boot timing is measured below; there is no near-native claim.
  Multithreaded TCG still translates guest instructions. Windows ARM/x86 hosts and macOS guests
  have not been built or tested with this change.

Reference contracts: [m1n1 CPU startup](https://github.com/AsahiLinux/m1n1/blob/main/src/smp.c)
provides T8140's CPU-start offset and RVBAR/status layout;
[XNU machine routines](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/machine_routines.c)
describe CPU affinities and IPI encoding;
[XNU FIQ handling](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/sleh.c)
and [Apple register definitions](https://github.com/apple-oss-distributions/xnu/blob/main/pexpert/pexpert/arm64/apple_arm64_regs.h)
establish IPI status/acknowledgement. Firmware-specific addresses and runtime
results above come from the pinned kernelcache and owned probes.
