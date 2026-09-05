# SIMD EXT translation experiment

The 128-bit, zero-offset EXT translation now uses the existing generic vector
copy operation. A second implementation emits native ARM vector EXT for nonzero
offsets, but stays **off by default**: it substantially improves a mixed SIMD
loop while making repeated EXT chains slower. The 64-bit instruction retains
its scalar translation. This is an instruction-lowering change, not HVF or a
new accelerator.

Worktree and both branches: `codex/arm-native-experiments`, based on parent
`3a6f4b4` and QEMU `a122a9d`. Host: M5 Max, 18 CPUs, 128 GiB, macOS 27.0
`26A5421a`. The independent display VM remained under its agent's control;
host load was not isolated. No build overlapped our runtime comparisons.

## What changed

`target/arm/tcg/translate-a64.c:trans_EXT_q` still performs the existing FP/SIMD
access check. It then selects:

- Offset zero: `tcg_gen_gvec_mov`, preserving upper-lane clearing. A self-copy
  can disappear completely when no upper lanes need clearing.
- Nonzero offset, `QEMU_ARM_TCG_VECTOR_EXT=1`, supporting host: load both vector
  inputs before writing the destination and emit `extract2_vec`.
- Otherwise: the original scalar element/extract/store path.

`extract2_vec` is an optional TCG operation with a constant byte offset. The
AArch64 backend emits the standard AdvSIMD EXT encoding, with Q at bit 30,
Rm at bits 20:16, offset at 14:11, Rn at 9:5 and Rd at 4:0. The generated code
is independently disassembled in `final_vector16/host-code.log`, and the
assembly-compiled input cases run against actual hardware through HVF.

Capability defaults to false on other host backends. Windows ARM uses this same
AArch64 TCG backend; there is no Hypervisor.framework dependency in the change.
Windows x86 keeps scalar lowering for nonzero offsets. The zero-offset copy uses
QEMU's existing portable gvec implementation. Windows builds and execution were
not available for validation here.

The environment option is read during A64 translator initialization and changes
host code generation only. It is not guest architectural state or a change to
PAuth, memory permissions, interrupts, devices or CPU count. Existing checkpoint
formats are unchanged; no new checkpoint save/restore test was performed here.

## Measured performance

`tools/perf/simd_ext_compare.py` runs identical EL0 machine code through preserved
baseline and candidate executables. Five repetitions per build/workload, ten
million loop iterations, alternating which build runs first. Guest counter
timing excludes bootstrap and debugger requests; all paired checksums match.
The build remains O3/LTO with assertions and debug symbols retained.

| Workload | Baseline | Default candidate | Speedup |
|---|---:|---:|---:|
| Mixed SIMD: ADD, EXT, XOR | 135.664 ms | 135.182 ms | 1.00x |
| Eight zero-offset 128-bit self-copies | 33.191 ms | 8.997 ms | **3.69x** |
| Eight 128-bit EXT, offset 3 | 39.958 ms | 39.956 ms | 1.00x |

The other unchanged paths varied from about -3% to +2% in this small default
comparison. The large zero-offset result is specifically a self-copy loop,
where removing redundant work is effective; it is not a claim that every
zero-offset copy or all SIMD operations become 3.69x faster.

With `QEMU_ARM_TCG_VECTOR_EXT=1` on the candidate:

| Workload | Baseline | Vector candidate | Result |
|---|---:|---:|---|
| Mixed SIMD: ADD, EXT, XOR | 135.186 ms | 58.357 ms | **2.32x faster** |
| Zero-offset 128-bit self-copy | 33.701 ms | 8.462 ms | 3.98x faster |
| Repeated 128-bit EXT, offset 3 | 39.865 ms | 57.278 ms | **44% longer** |
| Repeated 128-bit EXT, offset 8 | 36.667 ms | 59.018 ms | **61% longer** |
| Repeated 128-bit EXT, offset 11 | 42.479 ms | 59.218 ms | **39% longer** |

The first prototype also used vector lowering for 64-bit EXT and reused the
first input temporary as its result. It passed semantic checks, but the 64-bit
chain regressed badly. The final change keeps scalar 64-bit EXT and uses a
separate result temporary. That adjustment did not resolve the 128-bit chain
regression, so the nonzero vector path remains experimental.

Generated-code inspection explains the mixed-loop opportunity: the old EXT
translation brings vector halves into scalar registers between surrounding
vector operations. The new operation avoids that conversion. A repeated EXT
chain already lets the scalar implementation reuse scalar values. Fewer host
instructions alone therefore do not establish a speedup; dependencies,
register copies and execution-unit throughput matter. The precise contribution
of each factor to the remaining chain regression is not isolated by these tests.

## Correctness and iOS evidence

`tools/perf/simd_ext_check.py` computes its oracle with Python byte-array slices,
independent of the QEMU lowering. Each matrix covers eight deterministic input
patterns, every valid byte offset in the 64-bit and 128-bit forms, and five
alias patterns: distinct registers, Rd=Rn, Rd=Rm, Rn=Rm and all equal.

The final candidate passed **4,800 cases** across default vector lengths 16/64
bytes and experimental vector lengths 16/32/64 bytes. Each test verifies the
destination, unchanged other registers and cleared upper lanes. The 64-byte
matrix compares 184,320 output bytes, with nonzero upper-lane input canaries.
Baseline and native HVF reference matrices also pass. The separate access test
requires EC=7 at guest PC `0x8020010c` when FP/SIMD access is disabled; it passes
with the vector option enabled.

All 23 repository host tests and shell syntax checks pass. QEMU checkpatch reports
zero errors/warnings with sign-off checking excluded for this uncommitted patch.
The real restore-ramdisk probe with the experimental option enabled reports:

```text
=== probe: SIMD_EXT_RESTORE ===
serial lines : 294
xnu panics   : 0
reached shell: yes
```

The six-core fresh-Data A/B/B/A comparison completed with zero XNU panics in
all four runs. Each stopped at 100 unique User-volume directory metadata
updates; Data migration and setup migration were not completed.

| Order | Build | Time to 100 events | Events/s over events 20–100 |
|---|---|---:|---:|
| A1 | Baseline | 118.251 s | 2.368 |
| B1 | Vector option enabled | 128.024 s | 2.063 |
| B2 | Vector option enabled | 99.104 s | 2.285 |
| A2 | Baseline | 115.751 s | 2.340 |

Median elapsed time was **117.001 s baseline versus 113.564 s candidate**, an
apparent 2.94% reduction. The candidate pair differs by 28.92 seconds, much more
than the 3.44-second median difference, so this does **not** establish a reliable
iOS migration speedup. Both candidate runs also had lower event rates in the
later 20–100 window than either baseline run. Events are progress proxies,
not exact byte-work counters; the sample is small and host load was uncontrolled.
These results support leaving the nonzero vector path opt-in.

Compact per-run measurements, validation hashes and raw evidence hashes are
preserved in [simd-ext-results.json](simd-ext-results.json). Baseline executable
SHA256 is `ab20bc23e2a0210b381c9d1fd8284dd8ddb0e05ec8626807a53422a4fa1283d6`;
final candidate is `d2478029d8088c2c979c966b12b2cab0d0bbf2645219320f33d47e459e56806d`.

## Reproduce and use

The default copy optimization applies when using the rebuilt binary. To enable
the experimental nonzero-offset path for an existing launcher:

```sh
QEMU_ARM_TCG_VECTOR_EXT=1 python3 tools/run_smp.py --fast --cpus 6
```

Unset the variable, or set it to `0`, for the default scalar nonzero-offset path.
This example uses the restore-shell launcher; apply the same environment option
to the existing system-volume launch command for a normal userspace boot.

See [tools/perf/README.md](../../tools/perf/README.md) for reference matrices and
synthetic timing commands. The real workload comparison command is:

```sh
QEMU_ARM_TCG_VECTOR_EXT=1 python3 tools/re/smp_perf_compare.py \
  --baseline /tmp/dvm/SIMD_EXT/qemu-baseline \
  --candidate "$PWD/qemu-sptm/build-fast/qemu-system-aarch64" \
  --tag SIMD_EXT_IOS_ABBA --cpus 6
```

The old baseline ignores the new environment option. All candidate runs enable
it. Each run creates its own writable child of the same backing parent and
terminates only its owned VM. Raw artifacts are under `/tmp/dvm/SIMD_EXT` and
`/tmp/dvm/SIMD_EXT_IOS_ABBA*`; compact results are kept beside this report.

The next step for a general nonzero-offset optimization would be a cost model
that accounts for the producer/consumer representation, followed by real
workload measurements. The current data does not justify enabling the vector
path unconditionally or claiming a several-fold iOS boot improvement.
