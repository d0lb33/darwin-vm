# CPU cost at the start of first-boot migration

Worktree `codex/arm-native-experiments`, September 4, 2026. Measurements use
six guest CPUs on the ARM Mac. The display agent's VM is independent and was
never stopped or controlled by this investigation. No GPU changes are included.

## Reproducible starting point

`/tmp/dvm/checkpoints/CPU_MIGRATION_START1/manifest.json` pins an immutable
RAM/device stream, six CPU states, boot inputs, QEMU executable and the complete
ANS backing chain. The source stopped at the first APFS `set_dir_stats` event
on Data (`disk1s2`), before the measured User-volume metadata work. The saved
PC is `fffffff02ab0f5c8`; state size is 1,917,167,547 bytes. Creating the stream
took 2.025 seconds, and creation plus validation/hashing took 15.215 seconds.
Every replay gets a new writable qcow2 child and verifies the exact saved PC
before executing. State files contain private guest memory and stay local.

This is the early first-boot filesystem/metadata phase. It is not a measurement
of the later SpringBoard migration plugin, a completed installation, app launch,
or a game's frame rate. Each replay stops after 80 distinct new User-volume
`set_dir_stats` events, a panic, or 150 seconds. Those events are a work proxy,
not equal-size I/O requests. Guest scheduling and inode allocation still vary
after resume; a checkpoint makes the starting state identical, not the entire
multicore execution deterministic. TCG's translated-code cache starts empty in
every new process; disk-page caching in macOS is shared between runs.

## Where the time goes

The original/default build's first two replays took 64.505 and 65.173 seconds.
The first run had two five-second host stack samples; the second had no sampler.
Cumulative ANS service measurements reached 3.635 and 3.695 seconds respectively,
about 5.6–5.7% of elapsed time. ANS service includes allocation, DMA and synchronous
block operations; it is not a measurement of SSD latency alone. The last report
can precede the exact stop by up to about one second.

The two host samples in `CPU_MIG_PROFILE1` show CPU0–3 doing work and CPU4–5
predominantly waiting in QEMU's CPU-event path. Of 3,232 wall-time stack samples
from CPU0–3, exclusive weights partition as follows:

| Sample category | Share of CPU0–3 wall-time samples |
|---|---:|
| JIT / unresolved leaf under translated-code execution | 35.6% |
| Address translation and TLB operations | 17.4% |
| Translation-block lookup/dispatch | 11.4% |
| Other locks or waits | 10.5% |
| Global CPU execution coordination | 9.3% |
| Pointer authentication helpers | 5.7% |
| Other work | 5.3% |
| System-register read locks | 2.3% |
| Generating translated code | 1.6% |
| Storage service on a vCPU | 0.8% |
| Other system-register work | 0.1% |

These are **wall-time observations including blocked threads**, not CPU-time
percentages. Parent and child stack counts are never added together. Unresolved
JIT code includes generated address checks and other translation overhead; it
cannot be labeled pure guest computation. The storage fraction uses a different
denominator from the ANS wall-time measurement above.

The end-of-run TCG report recorded 1,507,684 translation blocks averaging 18
bytes of guest code and 240 bytes of generated host code, 561,504 partial TLB
flushes, and no whole translated-code-buffer flush. This is substantial
address-space/cache churn; merely allocating a bigger code-generation buffer
would not remove it. The global coordination samples include synchronized
queued CPU work and TLB invalidation; they do not establish broken guest atomics.

For gaming CPU performance, the relevant targets are recurring address checks,
indirect-branch dispatch and synchronization. Improving a NEON instruction alone
cannot remove these costs. The six-core machine also does not imply six busy
application workers in this first-boot phase. Six-way application scheduling
remains a separate validation target.

## Experiments

An opt-in identical-TCR-write shortcut was tested and removed. Each active CPU
had at least 16,384 observed TCR writes with **zero identical writes**. The guest
really changes the translation control value. Skipping necessary invalidations
would not be an acceptable performance fix. The local diagnostic patch is
`/tmp/dvm/CPU_TCR/experiment.patch`; it is not part of the final source changes.

The separate diagnostic patch `tools/perf/patches/tcg-jump-profile.patch` adds
`QEMU_TCG_JMP_PROFILE=1` counters for per-CPU jump-cache lookups. It materially
perturbs execution and is **not applied to the performance source/build**.

| Cache layout | Observed lookups | Hit | Empty | Address collision | Context mismatch |
|---|---:|---:|---:|---:|---:|
| Original 4,096 entries, 64 slots/page group | 3,359,637,504 | 85.104% | 6.630% | 7.560% | 0.706% |
| 16,384 entries, 128 slots/page group | 3,271,557,120 | 89.547% | 7.402% | 2.330% | 0.721% |

The larger cache reduces address-collision misses by about 69%, but also doubles
the slots cleared by each page invalidation. Its counter-disabled ABBA replays
were old 59.671 s, new 83.234 s, new 66.307 s, old 64.714 s. The independent
display workload restarted during the first candidate run. A later generated-code
check found an additional confound: adding the counter
branches made Clang stop inlining `tb_lookup`, even with counters disabled.
`nm` finds an out-of-line `_tb_lookup` in those candidates and none in the
original build. These timings therefore do not isolate cache capacity. The
profiling patch was removed before the final performance comparison. The
original binary remains available at
`/tmp/dvm/CPU_TCR_BASE/qemu-system-aarch64`; the larger-page-group candidate is
`/tmp/dvm/CPU_JMP14_WIDE/qemu-system-aarch64`.

## Clean candidate and final decision

The final experiment expands the cache to 16,384 entries while retaining the
original **64 slots per page group**. Full-cache clearing grows; per-page
clearing does not. Both baseline and candidate inline `tb_lookup`, and neither
contains the profiling counters. The candidate is preserved at
`/tmp/dvm/CPU_JMP14_CLEAN/qemu-system-aarch64` (SHA-256
`d8ee64aea77d3bbb8852e9382cbb3ea9bc3a99aa7f0d0ab02a8470d49584d342`). Its two-line
source change is `tools/perf/patches/tcg-larger-jump-cache.patch`.

| Clean comparison, execution order | Seconds to 80 new User events |
|---|---:|
| Candidate D1 | 65.212 |
| Baseline A5 | 65.401 |
| Baseline A6 | 62.204 |
| Candidate D2 | 52.482 |

The two-run medians are 63.803 s baseline and 58.847 s candidate, an observed
7.8% elapsed reduction / 1.084x throughput ratio. But the candidate's 12.73 s
spread exceeds the 4.96 s median difference. This small sample under changing
host load is insufficient to establish a repeatable migration gain.

The separate five-repetition indirect-call test has baseline/candidate medians:
32 functions 26.646/27.091 ms, 512 functions 48.998/48.589 ms, and 8,192 functions
94.286/91.480 ms. That is approximately unchanged for small/medium working sets
and 3.1% higher throughput for the largest. It does not establish faster games.

**The default cache configuration was restored.** Both the larger-cache patch
and counter patch remain separate, unapplied experiments. Previous SIMD work is
preserved: the zero-offset optimization remains in the normal build, while the
nonzero native-vector EXT path remains opt-in. No new default CPU performance
change was accepted from these migration experiments.

Fifteen bounded six-CPU replays verified the exact saved PC, reached at least 80
new User metadata events, and had zero XNU panics. They all stopped without
waiting for migration completion. The default and candidate cache layouts pass
22,282,240 compiled hash/page-invalidation checks per layout; guest tests verify
1,000 executable remap cycles and 1,000 instruction rewrites. The final default
build passes those guest checks, the 23-test host suite, shell syntax and diff
checks. The final ordinary 15-second restore boot (`CPU_MIG_FINAL_RESTORE`)
reaches the shell with 294 serial lines and zero XNU panics. Detailed numbers
and source artifact paths are in
[migration-cpu-results.json](migration-cpu-results.json).

The remaining high-value CPU investigation is translation-context churn and
synchronized TLB work. Reusing address translations across context switches
requires precise ASID, permissions, GXF/SPRR and invalidation handling. The
same-value TCR experiment specifically shows why simply suppressing those
flushes is not a safe shortcut. This investigation establishes the bottlenecks
and a reusable measurement boundary; it does not establish near-native gaming
performance.

## Reproduce

Create a fresh six-CPU checkpoint at the first metadata event:

```sh
python3 tools/re/smp_boot_bench.py --migration-sample --variant pv6 \
  --checkpoint-start --storage-profile \
  --qemu qemu-sptm/build-fast/qemu-system-aarch64 --tag MY_MIG_START
```

Replay with a unique tag, optionally sampling only the owned VM:

```sh
python3 tools/perf/migration_replay.py \
  /tmp/dvm/checkpoints/MY_MIG_START/manifest.json \
  --qemu qemu-sptm/build-fast/qemu-system-aarch64 \
  --tag MY_MIG_REPLAY --sample-at 15 35
```

Omit `--sample-at` and build without the diagnostic patch for timing comparisons.
`sample_cpu_report.py` partitions saved macOS sample trees with exclusive
weights and verifies that the categories sum to the original vCPU sample count.
`jmp_hash_check.py` compiles the actual cache hash functions and checks every
byte offset in 256 pseudorandom 1/4/16/64 KiB pages. `jmp_cache_check.py` executes
1,000 guest executable-remapping cycles plus 1,000 instruction rewrites through
the real guest MMU. `jmp_cache_bench.py` isolates checksummed EL0 indirect calls
across several code working-set sizes; it is not an iOS performance estimate.
