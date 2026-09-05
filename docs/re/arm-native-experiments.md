# ARM-native performance experiments, 2026-09-04

Follow-up: [SIMD EXT implementation and measurements](simd-ext-performance.md)
adds a default zero-offset copy optimization and an opt-in nonzero vector path.
The latter improves mixed SIMD but regresses repeated EXT chains, so it remains
experimental.

Hardware execution has measurable headroom for selected memory and SIMD loops,
but there is no new hardware-accelerated iOS boot yet. The strongest near-term
TCG targets are address translation, translated-block dispatch and SIMD lowering.
A larger accelerator project could run eligible userspace intervals in hardware
while retaining the existing kernel/device emulation. That design needs a real
state-transfer and memory-permission prototype before predicting boot speed.

## Source and measurement scope

Both repositories use local branch `codex/arm-native-experiments` in the isolated
`darwin-vm-arm-native-experiments` worktree. They start from the latest committed
local display integration available when this investigation began:

| Repository | Local base |
|---|---|
| darwin-vm | `codex/display-multicpu-integration`, `3a6f4b46823921ee6f779bbd1bfe5c3781837086` |
| qemu-sptm | `codex/display-multicpu-integration`, `a122a9d533f5fa0a0596af3d6e3c0d937345b113` |

The base already contains the multicore, PAuth-cache, display integration and
short class-13 SEP unwrap work. These experiments add standalone source under
`tools/perf`; they make no production QEMU changes. The build uses
`tools/build_qemu_fast.sh`: O3/LTO, assertions and debug symbols retained.

Host: Apple M5 Max, 18 CPUs, 128 GiB, macOS 27.0 build `26A5421a`, confirmed by
`sysctl` and `sw_vers`. The project's iOS guest models t8140/iPhone17,3; the host
is T6050/Mac17,7, as recorded in [the earlier host probe](hvf-acceleration.md).
Sharing AArch64 instructions does not give a virtual machine access to Apple's
protected execution modes or its physical device register map. Even identical
silicon would still require an appropriate hypervisor interface for those modes.

The display agent retained ownership of its VM. No commands stopped or modified
it. It was active during portions of this investigation and later restored a
paused instance. These are same-host, alternating measurements under ordinary
host load, not isolated-core laboratory results. No QEMU rebuild overlapped the
measurements. Windows was researched but not available for execution.

Compact per-run measurements, hashes and profile evidence are preserved in
[arm-native-experiments-results.json](arm-native-experiments-results.json).
Full artifacts remain under `/tmp/dvm/ARM_NATIVE_EXPERIMENTS` and
`/tmp/dvm/ARM_NATIVE_PROFILE6`.

## Experiment 1: identical EL0 ARM code, TCG versus HVF

[`arm_island.S`](../../tools/perf/arm_island.S) contains four loops. The runner
uses the same binary in the same QEMU build with either `-accel tcg -cpu max` or
`-accel hvf -cpu host`, on `virt,secure=off,virtualization=off`. It verifies entry
at EL0, completed iteration count, equal checksums, and equal final memory for
the load/store case. Five repetitions, 10 million iterations per case, alternating
backend order. All comparisons passed.

| Loop | TCG median | HVF median | Hardware speedup over TCG |
|---|---:|---:|---:|
| Eight integer add/rotate/XOR operations | 16.040 ms | 15.751 ms | **1.02x** |
| Paired loads/stores over 64 KiB | 19.583 ms | 6.914 ms | **2.83x** |
| Eight dependent NEON add/EXT/XOR operations | 124.747 ms | 35.294 ms | **3.53x** |
| Eight dependent pointer loads over 64 KiB | 76.017 ms | 52.964 ms | **1.44x** |

These timings use CNTVCT/CNTFRQ inside the guest; bootstrapping and GDB requests
are excluded. Host resume-to-break times provide a consistency check. The payload
is at PA `0x40200000`, bootstrap VA `0x40200000`, EL0 VA `0x80200000`, and data
VA `0x80220000`. A normal-cacheable 1 GiB block mapping makes the MMU active while
keeping the setup small. This does not model iOS's 16 KiB page mappings, page
fault frequency, changing address spaces, syscalls, PAuth policy or devices.

The integer result is a useful counterexample to an assumed universal 10x
emulation tax: optimized same-ISA TCG already executes this particular hot loop
within about 2% of HVF. The memory/SIMD results show specific headroom; they do
not establish a 2.8–3.5x full-boot gain. The 64 KiB data and tiny instruction
footprints are deliberately cache-friendly.

A concrete SIMD lead exists in
`qemu-sptm/target/arm/tcg/translate-a64.c:trans_EXT_q`: the current translator
reads scalar 64-bit vector elements, emits `tcg_gen_extract2_i64`, and writes
the halves back. The benchmark's two EXT instructions can execute as native
vector EXT under HVF. A vector extract operation in TCG, with an AArch64 EXT
lowering and portable fallback, is a bounded implementation candidate. This
source inspection does not isolate how much of the 3.53x gap comes from EXT;
vector-state traffic and other translations also contribute.

## Experiment 2: avoid emulating every Apple-register access in the VMM

[`arm_shadow.S`](../../tools/perf/arm_shadow.S) repeatedly reads and writes a
single software TPIDR_GL2 value. The real instruction encoding is
`S3_6_C15_C11_1`, sourced from `qemu-sptm/scripts/darwin/sysregs.py`.
`arm_shadow_bench.c` runs it through Hypervisor.framework and independently
checks the final value, rolling checksum, fault/exit counts and unused GPRs.
Five repetitions of 100,000 iterations, two modeled accesses per iteration:

| Approach | Median ns per modeled access |
|---|---:|
| Actual Apple MRS/MSR, decoded by a full-GPR guest EL2 UNDEF handler | **1,624.986** |
| SMC per access, software register serviced by the host VMM | **753.774** |
| Same SMC with GPR and SIMD register get/set round trip | **1,183.442** |
| Two sites rewritten to preserving branch thunks | **1.425** |
| EL1 UDF control with equivalent full-GPR handler structure | **35.443** |

These are total loop times divided by the access count, not isolated instruction
latencies. The 1.425 ns result includes branch, scratch-register preservation,
shadow load/store and loop bookkeeping, all in a tiny hot loop with MMU off.
It is not the expected cost of arbitrary iOS register or GXF emulation.

The EL2 handler decodes only the two known TPIDR_GL2 instruction forms, preserves
all GPRs except the decoded read destination, advances ELR and returns with ERET.
All 200,000 accesses per run were handled and verified. “Zero VMM exits” means
zero modeled-access returns to our userspace VMM; it says nothing about hidden
hypervisor work. The slower EL2 result may involve nested-virtualization handling
of EL2 state, but this test does not isolate the framework's internal cause.
The EL1 control also uses UDF instead of Apple MRS/MSR, so it is not a pure
single-variable comparison of exception levels.

This revises an important inference in the older [HVF analysis](hvf-acceleration.md):
its 22 ns EL1 exception result cannot be assigned to a real EL2 register shim.
The new EL2 test is slower than explicit VMM exits. Conversely, fault-free
rewritten register sites are worth investigating; the old absolute rejection of
all native execution options was broader than the experiments established.

The thunk prototype does **not** implement GENTER/GEXIT, SPRR permissions,
guarded register banks, SPTM entry/return, per-thread shadow banking, asynchronous
exceptions, protected shadow storage, arbitrary instruction destinations, or
relocation of real kernel code. These are required project work, not omissions
that can be ignored when replacing the kernel execution engine.

The register-roundtrip case covers 31 GPRs, PC, PSTATE and 32 SIMD registers. It
omits FPCR/FPSR, system-register reconciliation, memory synchronization and actual
TCG execution. As an illustrative lower-bound model, budget two such crossings
(2.37 microseconds) per native interval. That requires roughly 1,869 iterations
of the load/store loop, or 265 of the SIMD loop, just to recover the measured
transition budget. A real handoff can cost more. Crossing on every instruction
or tiny basic block is therefore the wrong initial design.

## Real iOS validation and profile

The unchanged production executable passed all 23 host tests, shell syntax checks,
and `tools/probe.sh --secs 15 --tag ARM_NATIVE_BASE_RESTORE`:

```text
serial lines : 294
xnu panics   : 0
reached shell: yes
```

The six-CPU fresh-child profile used `DARWIN_SMP_PV=1`, `-accel tcg,thread=multi`,
the existing PV kernel and `/tmp/dvm/data-seed/rebuild/marker.qcow2` as a read-only
backing parent. Only a newly created qcow2 child was writable. It reached early
boot at **8.702 s**, and stopped at **100 unique User-volume directory metadata
updates at 88.450 s**, with zero XNU panics. Events 20–100 ran at 2.670 events/s.
This is incomplete Data migration, not completed setup or a rendered UI. No
before/after speed claim follows from this single instrumented baseline.

Two five-second host samples starting at 40 and 65 seconds repeatedly contain
`helper_lookup_tb_ptr`, `qht_lookup_custom`, `probe_access_internal`,
`get_phys_addr_nogpc`, `tlb_set_page_full` and PAuth helpers. CPU4 and CPU5 spend
most of the first sample in waits under `qemu_process_cpu_events` or
`cpu_exec_start`; the other four have substantial translated execution. This
does not by itself establish a multicore bug. Do not turn inclusive sample
counts, idle thread counts, or unsymbolized JIT addresses into CPU percentages.

The final emitted ANS profile at 88.059 s records 6.120 s read service, 1.648 s
write service, and 0.065 s flush service: **7.833 s, or 8.9%** of that wall
interval. This includes allocation, guest DMA and synchronous block work
(`darwin_ans.c:ans_io`), not just SSD latency. It differs from the earlier 2.3%
session and should not be substituted into that experiment. Storage is a real
secondary cost here; eliminating its measured service time alone would not
produce a several-fold speedup. Asynchronous I/O could also change scheduling,
so this is not a universal upper bound on every storage redesign.

## What the project would need

| Work item | Implementation scope | Evidence gate |
|---|---|---|
| Better TCG SIMD lowering | Vector EXT IR/lowering with scalar fallback; alias-safe input capture; preserve FP access checks and high-lane clearing | All offsets and overlapping source/destination registers match reference; island matrix improves; iOS regression passes |
| Reduce TCG dispatch/translation cost | Profile cache misses and invalidations; specialize only proven stable state; preserve ASID, stage-2, GXF/SPRR permissions, TLBI and self-modifying-code coherence | Per-operation reference comparisons, SMP/TLBI tests and alternating fresh-child iOS measurements |
| Native userspace intervals | New hybrid execution owner; shared guest RAM; synthetic EL1 monitor and shadow page tables; transfer eligible EL0 state; route SVC, faults, interrupts and unsupported operations back to TCG | One real guest thread returns from hardware to TCG with matching registers/memory/exceptions, then SMP and workload validation |
| Native kernel with rewritten sites | Decode and relocate kernel/SPTM sites; safe per-CPU shadow state; implement GXF transitions and permission behavior; preserve guest exception routing | Full differential state/permission tests and firmware boot; much larger risk and scope |

The smallest useful native-execution milestone is **one EL0 interval**, then one
SVC/fault return, then repeated execution of a real userspace workload. Keep TCG
authoritative for unsupported state. Native execution needs consistent PAuth
behavior with this fork's existing bypass, matching CPU features, memory access
permissions, atomics/exclusives, timers, signal delivery, and dirty-code tracking.
Directly mapping all guest RAM writable/executable would not preserve these
semantics. It is also necessary to drain native CPUs back to canonical state
before taking an existing checkpoint; host hypervisor handles and shadow caches
must be recreated rather than serialized as guest architectural state.

Current QEMU assigns one accelerator to a machine in
`accel/accel-system.c:accel_init_machine`. Supplying `-machine accel=hvf:tcg` is fallback
selection, not per-thread or per-privilege-level switching. A hybrid owner or
companion VMM would need explicit scheduling, shared memory and state transfer;
the two standalone runners here do not supply that integration.

My engineering assessment: SIMD lowering is a focused compiler change; memory
and dispatch changes need a careful cross-layer audit; hybrid userspace is a
substantial accelerator project; full native SPTM/kernel support is open-ended
firmware and hypervisor work. These are scope assessments, not calendar promises.
Near-native performance for long supported compute intervals is plausible from
the measurements. Near-native performance for the entire VM is unproven.

For scale, if half of elapsed time were acceleratable at 3x with no transition
cost, total speedup would be 1.5x; if 80% were, it would be 2.14x. Those are
illustrative Amdahl calculations, not measured fractions of this iOS boot.

## Windows ARM remains a hardware-acceleration target

Current [QEMU WHPX documentation](https://www.qemu.org/docs/master/system/whpx.html)
explicitly supports ARM64 hosts and `qemu-system-aarch64 -accel whpx -M virt
-cpu host`. It requires Windows 11 24H2 with the April 2025 optional or May 2025
security updates; Microsoft documents ARM64 VP execution starting at build
26100.3915 in
[WHvRunVirtualProcessor](https://learn.microsoft.com/en-us/virtualization/api/hypervisor-platform/funcs/whvrunvirtualprocessor).
The source is already in this checkout at `target/arm/whpx/whpx-all.c`.

An interface for mapping RAM, transferring architectural state, running/stopping
a CPU and reporting exceptions could have HVF and WHPX implementations. The
hardware userspace approach need not depend on Apple-specific host registers;
the Apple kernel state would remain in the existing emulator. Windows nested
EL2 availability and all required register/trap capabilities still need real
probes. The current ARM WHPX code assumes a `virt` machine with in-hypervisor
GICv3 (`whpx-all.c:whpx_accel_init`), whereas our machine uses Apple's AIC;
full-machine acceleration also needs an interrupt-controller design.

WHPX support does not make today's `darwin` machine boot natively on Windows.
Windows ARM needs its own validation host and test adapter. Windows x86 cannot
hardware-execute ARM64 instructions and retains the TCG path. Portable TCG
optimizations and a complete fallback therefore remain necessary.

## Reproduce

See [tools/perf/README.md](../../tools/perf/README.md) for both synthetic matrices.
After host tests and a completed isolated build:

```sh
DVM_QEMU="$PWD/qemu-sptm/build-fast/qemu-system-aarch64" \
  tools/probe.sh --secs 15 --tag ARM_NATIVE_RESTORE_NEW
python3 tools/re/smp_boot_bench.py --migration-sample --variant pv6 \
  --qemu "$PWD/qemu-sptm/build-fast/qemu-system-aarch64" \
  --tag ARM_NATIVE_PROFILE_NEW --storage-profile --host-sample-at 40 65
```

The profile stops at 100 unique User-volume metadata updates, 200 total updates,
panic or 180 seconds. It stops only its own VM. Use fresh tags and existing
firmware/PV/backing artifacts described by the project's migration tooling.
