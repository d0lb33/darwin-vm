# Experimental multicore iOS boot

2026-09-04. **Real iOS boots with two and six CPUs using an explicit virtual
CPU power-management adapter. Two CPUs are verified simultaneously executing
separate userspace workloads.** Display is not a prerequisite. This remains
experimental: physical ApplePMGR, suspend, hotplug and checkpoint restore are
not supported by this path.

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

## Remaining limits

- Deferred IPIs currently take the immediate path; retract and no-wake are
  unimplemented. External AIC device IRQs still target CPU 0.
- Repeated reset, timer/IPI overlap stress, SMP migration and sustained workload
  stability have not been validated. A VMState descriptor alone is not evidence
  that checkpoint restore works.
- No measured performance improvement or near-native claim. Multithreaded TCG
  still translates guest instructions. Windows ARM/x86 hosts and macOS guests
  have not been built or tested with this change.

Reference contracts: [m1n1 CPU startup](https://github.com/AsahiLinux/m1n1/blob/main/src/smp.c)
provides T8140's CPU-start offset and RVBAR/status layout;
[XNU machine routines](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/machine_routines.c)
describe CPU affinities and IPI encoding;
[XNU FIQ handling](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/arm64/sleh.c)
and [Apple register definitions](https://github.com/apple-oss-distributions/xnu/blob/main/pexpert/pexpert/arm64/apple_arm64_regs.h)
establish IPI status/acknowledgement. Firmware-specific addresses and runtime
results above come from the pinned kernelcache and owned probes.
