# ARM performance experiments

These are checksummed synthetic payloads, not a replacement iOS accelerator.
The measurements and integration plan are in
[arm-native-experiments.md](../../docs/re/arm-native-experiments.md).

Run from the repository root on an Apple Silicon Mac with Xcode command-line
tools. The shadow experiment additionally requires that
`hv_vm_config_get_el2_supported` reports support. No sudo or disk attachment is
needed. Each output directory must be new and its parent must already exist.

```sh
tools/build_qemu_fast.sh
python3 tools/perf/arm_island_bench.py \
  --out /tmp/dvm/ARM_ISLAND_NEW --repeat 5 --iterations 10000000
python3 tools/perf/arm_shadow_bench.py \
  --out /tmp/dvm/ARM_SHADOW_NEW --repeat 5 --iterations 100000
```

Run sequentially, after the build completes. No iOS image is opened. The island
runner launches and terminates only its own QEMU processes and uses temporary
local GDB ports. The shadow runner builds and ad-hoc signs a standalone
Hypervisor.framework process using the existing `hvf-probe/hvf.entitlements`;
each invocation has a 15-second process deadline and an 18-second runner timeout.
These scripts do not alter the display VM.

`arm_island.S` runs identical EL0 instructions under TCG and HVF with the MMU
enabled. Its integer, load/store, SIMD and pointer-chase loops report a checksum;
the runner also compares all 64 KiB of modified load/store memory. Timing starts
inside the guest after bootstrap. Final GDB inspection is outside that interval.
`results.json` records each run, binary/payload hashes and medians. Backend order
alternates between repetitions. `--qemu` selects another QEMU binary.

`arm_shadow.S` models one 64-bit TPIDR_GL2 shadow register. It compares an EL2
UNDEF handler, VMM SMC exits, preserving branch thunks, and an EL1 UDF control.
The C runner independently checks the final register value, checksum, access
counts and unused GPR canaries. One VMM variant adds GPR/SIMD save-and-restore
calls to estimate part of a future execution-engine handoff cost. It does not
measure a real TCG/HVF handoff. All timings include the loop's bookkeeping.

The assembly has no external relocations. The Python loader extracts `__text`
from a Mach-O object and rejects relocations. The current build/run scripts are
Mac-specific. Porting the payload to Windows ARM requires an assembler/object
loader adapter and a WHPX runner; Windows execution has not been tested.

Outputs belong in `/tmp/dvm`, never in this directory. Committed compact results
live beside the report so the measurements survive deletion of temporary logs.

## Translation context and TLB broadcast checks

The [translation-context report](../../docs/re/translation-context-performance.md)
describes the clean-table skip, bounded range jump-cache clearing, and cached
PAuth MMU regime. Run the diskless six-CPU correctness matrix with:

```sh
python3 tools/perf/tlb_broadcast_check.py \
  --out /tmp/dvm/TLB_BROADCAST_NEW --negative-control
```

Each of eight cases checks 1,000 synchronized executable remaps on every CPU.
The optional negative control confirms that removing the TLBI prevents
completion. `--qemu` selects a comparison executable. Output includes guest
timing, checksums, all six acknowledgment counts, and executable/payload hashes.

## SIMD EXT implementation checks

The follow-up implementation uses a vector copy for zero-offset 128-bit EXT.
Nonzero 128-bit EXT can use an experimental native ARM vector lowering with
`QEMU_ARM_TCG_VECTOR_EXT=1`; this is off by default because repeated EXT chains
can be slower even though mixed SIMD workloads improve. The 64-bit form keeps
its existing scalar lowering. Unsupported host backends also retain scalar
translation for nonzero offsets. No guest CPU or memory-permission setting is
relaxed by this option.

```sh
python3 tools/perf/simd_ext_check.py --out /tmp/dvm/EXT_CHECK_DEFAULT
python3 tools/perf/simd_ext_check.py --out /tmp/dvm/EXT_CHECK_VECTOR \
  --vector-ext --dump-code
python3 tools/perf/simd_ext_check.py --out /tmp/dvm/EXT_CHECK_SVE \
  --vector-ext --sve-bytes 64
python3 tools/perf/simd_ext_check.py --out /tmp/dvm/EXT_CHECK_TRAP \
  --vector-ext --fp-trap
python3 tools/perf/simd_ext_compare.py \
  --baseline /path/to/preserved/qemu-system-aarch64 \
  --candidate qemu-sptm/build-fast/qemu-system-aarch64 \
  --out /tmp/dvm/EXT_COMPARE --candidate-vector-ext --repeat 5
```

Each matrix checks 960 combinations against an independent Python byte-array
reference: eight input patterns, both widths, every valid byte offset, and five
source/destination alias arrangements. It verifies the destination and both
other registers, including upper-lane canaries. SVE tests support active vector
lengths of 32 or 64 bytes under TCG. `--fp-trap` separately verifies that the EXT
instruction faults at the expected PC when FP/SIMD access is disabled. Use
`--accel hvf` without SVE for a hardware reference run.

The comparison alternates baseline and candidate order for mixed SIMD and six
EXT-specific loops. Omit `--candidate-vector-ext` to measure the default copy
optimization. `--dump-code` captures TCG IR and generated host code; use it for
diagnosis, not performance claims. The runner explicitly sets the option for
each build and shares the repository's ROM directory, allowing the preserved
baseline executable to live outside its original build tree.

## First-boot migration checkpoint and CPU profiles

See [migration-cpu-performance.md](../../docs/re/migration-cpu-performance.md)
for the six-CPU checkpoint, measured cost breakdown and the cache experiments.

- `tools/re/smp_boot_bench.py --migration-sample --variant pv6 --checkpoint-start`
  freezes an owned fresh image at the first metadata update using the existing
  RAM/device/disk checkpoint creator. Supply `--storage-profile` and `--qemu`
  explicitly for the profiling build.
- `migration_replay.py MANIFEST --tag UNIQUE [--qemu PATH] [--sample-at 15 35]`
  verifies and restores into a new writable child, resumes the saved PC, and
  stops after 80 new User metadata events or 150 seconds. It saves serial,
  host statistics, ANS aggregate timings, JIT statistics, and result JSON under
  `/tmp/dvm/UNIQUE`. It quits only its verified QEMU process.
- `sample_cpu_report.py SAMPLE.txt ...` partitions exclusive vCPU stack weights.
  Its denominator is wall-time stack observations including waits.
- `jmp_hash_check.py --out /tmp/dvm/UNIQUE` compiles actual hash functions and
  checks their page-invalidation boundaries.
- `jmp_cache_check.py --out /tmp/dvm/UNIQUE [--qemu PATH]` verifies executable
  remaps and self-modifying instructions through the guest MMU.
- `jmp_cache_bench.py --baseline OLD --candidate NEW --out /tmp/dvm/UNIQUE`
  compares checksummed indirect-call loops, five alternating repetitions across
  32, 512, and 8,192 callable functions. This isolates code-cache effects and
  cannot substitute for the iOS replay.

The optional `patches/tcg-jump-profile.patch` is diagnostic only. Apply it inside
`qemu-sptm` and set `QEMU_TCG_JMP_PROFILE=1` to count jump-cache hit/miss reasons.
Remove the patch and rebuild before timing: its branches can inhibit Clang's
inlining even when the environment flag is off. Always inspect the generated
code when introducing instrumentation into a hot path.

`patches/tcg-larger-jump-cache.patch` is the separate, unapplied cache-capacity
experiment. The default cache was retained because the partial-migration timing
spread did not establish a repeatable gain. Archived candidate executable:
`/tmp/dvm/CPU_JMP14_CLEAN/qemu-system-aarch64`.
