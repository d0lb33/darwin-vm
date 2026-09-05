# Translation-context and synchronized TLB work

Follow-up to [the migration CPU profile](migration-cpu-performance.md), on
`codex/arm-native-experiments`. All changes are local and uncommitted. The
independent display VM is never stopped or controlled by this work. No storage, device, GPU, or guest
memory-permission behavior is changed.

## Implementation

`accel/tcg/cputlb.c` filters page and range invalidations through the existing
per-CPU MMU-mode dirty bitmap while holding the TLB lock. A clean mode has no
main or victim translations. The full-flush path already relies on this same
invariant; every TLB insertion sets its mode dirty. Partial invalidation does
not clear the dirty bit. The implementation deliberately does not use
`n_used_entries`, which is an occupancy estimate for resizing, as proof that a
table is empty. Jump-cache invalidation and all cross-CPU work queues,
exclusive execution boundaries, locks and barriers remain in place.

Range invalidation previously switched to a whole jump-cache clear only at
4,096 guest pages. Each individual page actually clears 64 cache slots, and
the preceding page must also be considered for a TB spanning two pages.
For G page-hash groups, any 2G−1 consecutive pages contain an aligned G-page
block. The hash maps that block onto every cache group. The final threshold is
therefore 126 requested pages plus the preceding page for the current 64 groups.
At that length a single whole-cache clear invalidates **exactly the same
entries** as the original loop. Shorter ranges retain the original page loop.
The hash test checks this coverage for every starting alignment over 256 base
addresses (including address-space wraparound) and four target page sizes.

`target/arm/tcg/pauth_helper.c` reuses the MMU regime in the current AArch64
translation flags when the existing pointer mask cache is enabled. Previously
each operation recomputed it from EL/HCR/PAN state. The live TCR remains part
of the cache key, covering register changes and GXF banking. Existing
`DARWIN_PAUTH_CACHE=verify` now also compares the reused regime with the
original calculation. Cache-off behavior retains the original calculation.
No authentication, trap, or permission checks are removed by this change;
the fork's preexisting strip-only PAC behavior is unchanged.

These are portable TCG/ARM-target C changes, with no Hypervisor.framework or
host-specific dependency. Windows ARM and x86 hosts retain the TCG path.
Windows compilation and execution have not been tested here.

## Diagnostic evidence

`CPU_CONTEXT_RANGE_PROFILE` resumed the immutable six-CPU migration-start
checkpoint and stopped at 80 new User-volume metadata events, with no panic.
A temporary diagnostic patch counted the invalidation work on each CPU.
The last complete reporting interval contained 294,912 range callbacks per
CPU, requesting five MMU modes per callback:

| CPU group | Requested mode flushes | Already clean | Share |
|---|---:|---:|---:|
| Busy CPUs 0–3 | 5,898,240 | 2,499,076 | 42.370% |
| Mostly waiting CPUs 4–5 | 2,949,120 | 2,939,602 | 99.677% |

The profile patch is archived locally at
`/tmp/dvm/CPU_CONTEXT_PROFILE/range-profile.patch`. It is absent from the final
source and performance executable. Its elapsed time is not a performance
comparison.

## Correctness checks

`tools/perf/tlb_broadcast_check.py` boots a diskless six-CPU ARM guest. CPU0
repeatedly switches an executable VA between two physical functions returning
different values using break-before-make, TLBI RVAE1IS plus DSB/ISB, then publishes a phase
with release ordering. Every CPU checks the new function result and
acknowledges the phase. Readers retain their old executable translation while
waiting. The writer waits for all five readers before starting the next phase.

The matrix covers 4 KiB page mappings and 2 MiB block mappings with invalidation
ranges of 2, 64, 2,048 and 65,536 pages, 1,000 remaps per case. A negative
control omits TLBI and must fail to complete. An independent existing guest
test checks 1,000 executable remap cycles and 1,000 instruction rewrites.

A diagnostic candidate verified every cached PAC operation against the
original MMU-regime and pointer-mask calculations during an iOS checkpoint
replay. At least **1,489,000,002 operations** passed across all six CPUs before
the 80-event stop, with no mismatch or panic. This verifies the exercised
workload, not every possible guest translation regime. The diagnostic build
and counts are retained under `/tmp/dvm/CPU_CONTEXT_VERIFY`; they are excluded
from performance comparisons.

```sh
python3 tools/perf/tlb_broadcast_check.py \
  --out /tmp/dvm/CONTEXT_SMP_NEW --negative-control
python3 tools/perf/jmp_cache_check.py --out /tmp/dvm/CONTEXT_SMC_NEW
python3 tools/perf/jmp_hash_check.py --out /tmp/dvm/CONTEXT_HASH_NEW
python3 tools/perf/tlb_broadcast_compare.py \
  --baseline /tmp/dvm/CPU_TCR_BASE/qemu-system-aarch64 \
  --candidate qemu-sptm/build-fast/qemu-system-aarch64 \
  --out /tmp/dvm/CONTEXT_COMPARE_NEW --repeat 5
python3 tools/perf/migration_replay.py \
  /tmp/dvm/checkpoints/CPU_MIGRATION_START1/manifest.json \
  --qemu qemu-sptm/build-fast/qemu-system-aarch64 --tag CONTEXT_REPLAY_NEW
```

Run sequentially after building. The checkpoint and its backing images remain
immutable; every replay uses a fresh child image. These measurements cover
early filesystem metadata work, not completed data migration, full boot time,
app launch or game frame rate.

The first candidate switched to a full jump-cache clear at 63 requested pages,
which could discard extra entries. Its first replay took 68.219 s versus a
63.177 s baseline while the display agent started its own concurrent performance
test. This pair does not establish a regression, but the final implementation
uses exact cache coverage to avoid the additional eviction tradeoff altogether.
That earlier candidate is excluded from the final timing comparison.

## Stopped experiment and committed state

The user stopped this investigation before the planned six-run comparison.
Both baseline replays completed at the work limit: `CPU_CONTEXT_A1` took
63.177 s and `CPU_CONTEXT_A2` took 83.693 s. The final candidate replay
`CPU_CONTEXT_C1` took 68.897 s. All restored the exact checkpoint PC, stopped
after 80 User metadata updates, exited their owned QEMU, and recorded no panic.
These three runs do **not** establish a migration speedup or regression. The
baseline itself varied by 20.516 s on the shared host.

Final executable SHA-256:
`2e52c52759221dc65c3b4f57b305bd030ee1401f353c613f2c242e426c694e89`.
The final break-before-make six-CPU matrix and its negative control passed
under `/tmp/dvm/CPU_CONTEXT_EXACT_SMP`. The independent executable remap/SMC
test passed under `/tmp/dvm/CPU_CONTEXT_EXACT_SMC`. Hash coverage passed
22,282,240 page checks and 65,536 range checks under
`/tmp/dvm/CPU_CONTEXT_HASH_BOUNDARY`.

The final cold ramdisk probe, with six CPUs configured and runtime PAuth
reference verification enabled, reported **295 serial lines, reached shell:
yes, XNU panics: 0** (`CPU_CONTEXT_EXACT_BOOT6`). Host regressions passed
23 tests; QEMU patch style and whitespace checks passed. The repeat comparison
script is supplied for future measurements but its five-repeat matrix was
not run before the user stopped the work. No overall boot or gaming speedup
is claimed for these committed changes.
