# Fast-path ceiling experiment — September 6, 2026

The question from [hvf-performance-checkpoint.md](hvf-performance-checkpoint.md)
and [hvf-migration-call-profile.md](hvf-migration-call-profile.md): if the
bridge's per-operation costs were driven to their floors, would a native
iOS boot be faster than TCG on this M5 Max? This note measures the floors
and projects the answer. It is a sensitivity model on synthetic loops, not
a boot measurement.

## Method

`tools/perf/native_bridge_bench.py` gained two cases and two switches:

- `native_read`: `MRS Xn, TPIDR_EL0` in a loop. TPIDR_EL0 is never
  ledgered and HVF does not trap it (`hvf_vsh_code_safe()` now lets it
  execute natively). It is the cost of a register read rewritten to
  non-trapping storage.
- `tpidr_gl2_read`: one GENTER, then a loop of `MRS Xn, TPIDR_GL2` from
  guarded mode, then GEXIT. This is the hottest real operation (43.5% of
  Apple register traffic in the migration profile).
- `--bridge-env KEY=VALUE` passes experiment knobs to the HVF runs only;
  `--cases` selects cases.

Three knobs in the bridge, all default-off and read once in
`hvf_virtual_init()`:

| Knob | Effect | Status |
| --- | --- | --- |
| `QEMU_HVF_VIRTUAL_QUIET=1` | Drops the per-operation `error_report` diagnostics on the hot paths. | Safe; diagnostics only. |
| `QEMU_HVF_VIRTUAL_FASTREAD=1` | `hvf_virtual_fast_read()`: MRS of TPIDR_GL2, CURRENTG or TPIDR_EL2 writes the destination GPR straight from the CPU env without synchronizing the vCPU. Those registers only change through emulated writes, so env is authoritative. Guarded-bank reads still require guarded execution. | Correct as implemented; opt-in until exercised by the full matrix. |
| `QEMU_HVF_VIRTUAL_KEEP_ALIASES=1` | Retains native aliases across GENTER/GEXIT instead of `hvf_vsh_invalidate()`. | **Experiment only.** The GL and EL permission banks differ, so retained aliases are not permission-correct. It measures what a per-context alias design could reach, not a shippable mode. |

Four variants, three repetitions each, 100,000 reads and 10,000 pairs per
run, alternating HVF/TCG order, one owned VM at a time, unchanged binary
SHA-256 `67f6091364b21cf4…` (`/tmp/dvm/HVF_CEIL_V{0..3}/results.json`).
All 120 runs passed with matching checksums, table hashes and code hashes.

## Per-operation costs (medians, measurement floor subtracted)

| Variant | Trapped read | TPIDR_GL2 read | Native read | GENTER/GEXIT pair |
| --- | ---: | ---: | ---: | ---: |
| V0 current bridge | 9.47 µs | 9.83 µs | 0.24 ns | 116.0 µs |
| V1 + QUIET | 2.51 µs | 2.54 µs | 0.24 ns | 53.4 µs |
| V2 + FASTREAD | 0.98 µs | 0.99 µs | 0.23 ns | 51.9 µs |
| V3 + KEEP_ALIASES | 0.96 µs | 0.97 µs | 0.23 ns | 4.86 µs |

Reading the table:

- Diagnostics were three quarters of the trapped-read cost and half of the
  transition cost. A stderr write per trap is not free.
- The fast read sits at 0.98 µs, essentially the 720 ns exit floor measured
  in `hvf-probe/hvf_exitbench` plus the ledger dispatch. Nothing below that
  is possible for an operation that traps at all.
- Alias invalidation and refill were 90% of the transition cost. With
  aliases kept, a pair costs 4.9 µs: two exits plus state save/restore and
  the interrupt helper.
- The TCG reference for the same loops: 2.5 ns per trapped read and
  0.19 µs per pair (`V0` `tcg` medians). Even the V3 floor is 400x and 25x
  slower per operation than TCG.

## Projection against the migration profile

`tools/perf/native_ceiling_projection.py` charges the HVF_MIG_CALLS2 rates
(385,774 Apple register reads, 58,216 writes, 12,402 guarded pairs and
7,921 MMIO accesses per guest second on one CPU) at each variant's costs.
"Rewritten" assumes TPIDR_GL2 and CURRENTG (66.79% of register traffic)
are rewritten in the kernel to non-trapping storage at the native-read
cost; "trapping" leaves them on the fast path. The overhead is extra host
seconds per guest second; the speed columns divide the measured
native-vs-TCG compute ratios (integer 1.02x, pointer chasing 1.45x,
load/store 2.79x) by `1 + overhead`. Values above 1 mean faster than TCG.

| Variant | Hot reads | Overhead | Integer | Pointer chase | Load/store |
| --- | --- | ---: | ---: | ---: | ---: |
| V0 | trapping | 5.82x | 0.15 | 0.21 | 0.41 |
| V1 | trapping | 1.81x | 0.36 | 0.52 | 0.99 |
| V2 | trapping | 1.09x | 0.49 | 0.69 | 1.34 |
| V2 | rewritten | 0.80x | 0.57 | 0.81 | 1.55 |
| V3 | trapping | 0.50x | 0.68 | 0.97 | 1.86 |
| V3 | rewritten | 0.21x | 0.84 | 1.20 | 2.31 |

`/tmp/dvm/HVF_CEIL_PROJECTION1.json` holds the numbers.

The projection is optimistic for the bridge: it counts only Apple
implementation-defined registers, guarded pairs and MMIO. Ordinary ARM
system registers (TTBR/TCR/TLBI in context switches, DAIF, timer
registers), interrupts, syscalls and page faults each also cost at least an
exit and are not in the profile.

## Conclusion

The ceiling for a fully optimised bridge on this host is **parity with TCG
for integer and branchy kernel code, about 1.2x on pointer-chasing, and
about 2.3x on load/store-heavy code**, and that ceiling requires three
things that do not exist yet: a permission-correct alias-reuse design
across GL/EL transitions, a kernelcache rewrite of 18 million
TPIDR_GL2/CURRENTG reads per minute into non-trapping storage, and every
uncounted trap class staying cheap. The 1.5x target set before the
experiment is met only for load/store-dominated phases. A kernel boot is
dominated by the other two categories.

Two of the three cheap wins are real and stay in the tree: QUIET and
FASTREAD together take the trapped read from 9.5 µs to 1.0 µs and the pair
from 116 µs to 52 µs with no semantic change. They do not change the
conclusion: the bridge cannot beat TCG on this machine by a margin that
justifies the kernel phase in
[hvf-sptm-bootstrap.md](hvf-sptm-bootstrap.md).

## Reproduce

```sh
python3 tools/perf/native_bridge_bench.py --out /tmp/dvm/UNIQUE_V3 \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree --sptm /tmp/dvm/HVF_GXF_SPTM8.macho \
  --ledger /tmp/dvm/HVF_GXF_SPTM8.ledger --qemu qemu-sptm/build/qemu-system-aarch64 \
  --repeat 3 --read-count 100000 --transition-count 10000 \
  --cases measurement_floor,tpidr_read,genter_gexit,native_read,tpidr_gl2_read \
  --bridge-env QEMU_HVF_VIRTUAL_QUIET=1 --bridge-env QEMU_HVF_VIRTUAL_FASTREAD=1 \
  --bridge-env QEMU_HVF_VIRTUAL_KEEP_ALIASES=1
python3 tools/perf/native_ceiling_projection.py V3=/tmp/dvm/UNIQUE_V3
```

## Steady-state profile: the migration window is not representative

The projection above used the only trap-rate profile that existed, a
first-boot APFS metadata window. `tools/perf/home_exception_profile.py`
measures a rendered home screen instead. The gxfstat counters are compiled
out under SMP and every home-screen checkpoint is six cores, so it enables
QEMU's `CPU_LOG_INT` log at runtime through the monitor of a paused
restore and counts exception entries, `gexit` and exception returns per
window. `CEIL_HOME1` restored `NATIVE_HOME_UART_FIXED1` (pinned app
binary `9d39357b…`) with `--leave-paused`; `CEIL_HOME_PROFILE1` ran a
60 s idle window and a 60 s window with 41 QMP swipes
(`/tmp/dvm/CEIL_HOME_PROFILE1/results.json`, 224 MB log). Only four of the
six vCPUs took more than a handful of exceptions; rates below are per
active vCPU.

| Rate per active vCPU per host second | Migration (1 CPU) | Home idle | Home + swipes |
| --- | ---: | ---: | ---: |
| GENTER/GEXIT pairs | 12,402 | 495 | 731 |
| Apple register ops (measured / estimated at 35.8 per pair) | 444,000 | ~17,700 | ~26,200 |
| SVC from EL0 | n/a | 534 | 986 |
| FIQ + IRQ (all levels) | n/a | 367 | 436 |
| Data/prefetch aborts | n/a | 90 | 134 |
| ERET (EL2→EL0 and EL2→EL2) | n/a | 995 | 1,561 |

The guarded-pair rate is 17 to 25 times lower than in the migration
window. Re-running the projection with these rates, charging every
non-guarded exception entry and ERET at half a pair:

| Variant | Window | Overhead | Integer | Pointer chase | Load/store |
| --- | --- | ---: | ---: | ---: | ---: |
| V2 QUIET+FASTREAD (real) | idle | 0.095x | 0.93 | 1.32 | 2.55 |
| V2 QUIET+FASTREAD (real) | swipes | 0.145x | 0.89 | 1.27 | 2.44 |
| V3 + alias reuse (design needed) | idle | 0.024x | 1.00 | 1.42 | 2.72 |
| V3 + alias reuse (design needed) | swipes | 0.036x | 0.98 | 1.40 | 2.69 |

Rewriting the hot reads in the kernel no longer matters (0.083x vs
0.095x). The register-operation term is an estimate from the migration
ratio, not a measurement, but at these pair rates even a 3x error in it
moves the overhead by less than 0.2x.

### Revised conclusion

Two regimes, not one:

- **Kernel-dominated phases** (boot, first-boot migration, app install):
  the bridge is slower than TCG and cannot be made faster on this host.
  The earlier conclusion stands for them.
- **Steady state** (home screen, interactive use): the bridge's trap
  overhead is 10 to 15% with the two optimisations that already exist, so
  performance is set by the native-versus-TCG compute ratio of the
  workload: parity on integer code, 1.3x on pointer chasing, 2.5x on
  load/store, and up to the measured 3.5x on SIMD. Without a GPU the
  guest composites through CoreAnimation's software rasteriser
  ([ca-software-path.md](ca-software-path.md)), which is exactly the
  load/store and SIMD mix where native execution wins.

So the kernel phase is worth costing again, with a different success
criterion: the boot may take longer than TCG, and the payoff is measured
after SpringBoard. What the steady-state profile does not cover: multicore
scaling of the bridge (single vCPU today), interrupt delivery cost, and
a real game's mix, which needs the app-launch path that is still open on
TCG.

Reproduce:

```sh
python3 tools/restore_checkpoint.py --tag UNIQUE --leave-paused --display none \
  --out /tmp/dvm/UNIQUE.restore /tmp/dvm/checkpoints/NATIVE_HOME_UART_FIXED1/manifest.json
python3 tools/perf/home_exception_profile.py --monitor /tmp/dvm/UNIQUE.restore.sock \
  --qmp /tmp/dvm/UNIQUE.restore.qmp.sock --out /tmp/dvm/UNIQUE_PROFILE --seconds 60 --activity
python3 tools/hmp.py /tmp/dvm/UNIQUE.restore.sock quit
```

## Regressions on this binary

Executable SHA-256 `67f6091364b21cf4…` (same as the sweep):

- `HVF_CEIL_BOOT1`, knobs off: the native SPTM boot of
  [hvf-sptm-bootstrap.md](hvf-sptm-bootstrap.md) repeats: GEXIT onto
  `0xfffffff02b354000`, 15 EL0 SVC deliveries, table hashes matching, 172
  registers equal to the TCG terminal snapshot.
- `HVF_CEIL_BOOT3`, `QUIET=1 FASTREAD=1` through the boot runner's new
  `--bridge-env`: same kernel-entry stop, `tables_match: true`, 172
  registers equal to the TCG snapshot; stderr shrinks from 1.22 MB to 61 KB
  (the transition/exception counters are absent by design under QUIET).
- `HVF_CEIL_TCG1`: 60-second TCG restore-ramdisk baseline reaches the shell,
  312 serial lines, zero panics.
- `HVF_CEIL_BOOT2` was intended as the fast-path boot but the runner then
  stripped `QEMU_HVF_*` from its environment, so it duplicated BOOT1; it is
  recorded as a failed control, and `--bridge-env` was added afterwards.
