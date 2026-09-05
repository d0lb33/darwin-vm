# Compatibility operation frequency during first-boot Data migration

September 5, 2026, M5 Max host. Fresh TCG measurement:
`/tmp/dvm/HVF_MIG_CALLS2/results.json`, with exact start/end snapshots in
`start.json` and `end.json`. The measured window is **60.023794 seconds**.
This is early APFS Data/User metadata work, not the later setup migration
plugin, a completed migration, or a game. No HVF boot timing is claimed.

## Workload and evidence

The run cold-boots a disposable qcow2 child of
`/tmp/dvm/data-seed/rebuild/marker.qcow2`, the existing pre-first-boot seed.
The backing chain and exact launch command are recorded in results.json.
This is a populated first-boot seed, not an empty/unformatted drive. No
checkpoint is restored and the backing files are not written. It uses the
stock firmware kernel, the existing NVMe welcome tree, and one TCG vCPU.
The QMP `query-cpus-fast` response confirms one CPU: the historical `gxfstat`
counters are single-writer scaffolding and are disabled for multicore.

The first `set_dir_stats` event appears 19.300 seconds after launch.
The runner then pauses the guest using QMP, reads host counters using LLDB,
detaches, resumes, and lets it execute for 60 seconds before another pause
and read. LLDB uses symbol lookup and ReadMemory/ReadCStringFromMemory only;
it does not evaluate expressions, call target functions, reset counters,
or write guest/host memory or registers. The guest remains QMP-paused during
each attachment. Those pauses are outside the timed window.

The new scripts are `tools/perf/migration_compat_profile.py` and
`tools/perf/host_gxf_snapshot.py`. The native binary is unchanged BUILD8,
SHA-256 `dc2d14c93387cb02e3af8dc493132fdd08c1c2987c87a1bce68e84f91164448a`.

The workload advances from 1 to 19 distinct Data/User dir-stats events,
including three User-volume events. It stops at the requested deadline,
with zero XNU panics and process exit 0. The final six aggregate counters
in QEMU's exit log exactly equal the stopped end snapshot. Each snapshot's
full register histogram exactly sums to its read/write aggregate counts.
The separate display checkout/processes were not controlled or modified.

`Early boot complete` precedes this metadata window in this run; it is not
used as a migration-completion marker. No full migration completion is claimed.

## Sixty-second deltas

| Event | Count | Approximate rate per host second |
| --- | ---: | ---: |
| Apple implementation-defined register reads | 23,155,648 | 385,774 |
| Apple implementation-defined register writes | 3,494,349 | 58,216 |
| GENTER | 744,399 | 12,402 |
| GEXIT | 744,399 | 12,402 |
| MMIO reads | 145,966 | 2,432 |
| MMIO writes | 329,480 | 5,489 |

There are 26,649,997 Apple register operations and 744,399 entry/return pairs.
All guarded transitions occur at virtual EL2. Register operations split into
1,117,261 at EL0 and 25,532,736 at EL2; none are counted at EL1 or EL3.

The busiest registers, ranked by reads plus writes:

| Register | Reads | Writes | Share of counted Apple register operations |
| --- | ---: | ---: | ---: |
| TPIDR_GL2 | 11,603,768 | 0 | 43.54% |
| CURRENTG | 6,196,680 | 0 | 23.25% |
| SPSR_GL2 | 787,274 | 787,248 | 5.91% |
| ELR_GL2 | 768,578 | 768,552 | 5.77% |
| SPRR_UPERM_EL0 | 1,415,310 | 98,796 | 5.68% |
| ASPSR_GL2 | 744,399 | 744,399 | 5.59% |
| APCTL_EL2 | 693,372 | 717,124 | 5.29% |
| SPRR_PPERM_EL2 | 325,034 | 65,356 | 1.46% |
| SPRR_UMPRR_EL1 | 204,373 | 41,643 | 0.92% |
| AGTCNTRDIR_EL2 | 70,190 | 47,528 | 0.44% |

TPIDR_GL2 and CURRENTG together represent **66.79%** of the counted register
traffic, about 296,557 reads per host second. Their high frequency makes
validated cheap reads a concrete optimization target. Their values still
need to respect CPU/guarded context and register-access rules.

## Interpretation and scope

Rare compatibility operations could tolerate higher individual costs, but
this workload invokes them frequently. As a sensitivity calculation only,
744,399 pairs multiplied by the current bridge benchmark's 106.402425 us
per pair is about **79.2 seconds**—already longer than this TCG window.
That is not a measured native migration time: the benchmark uses a synthetic
bootstrap protection context, includes diagnostics, and future implementations
can change the cost. It demonstrates why the present transition cost needs
attention before a whole-system speedup can be expected.

The prior 9.37 us read benchmark measured TPIDR_EL2, not TPIDR_GL2 or CURRENTG.
Do not multiply every register count by that number and present it as a
predicted native boot time. The frequency data instead identifies which
real high-frequency accesses should receive their own benchmarks and fast paths.

The source counter hooks are generated in `scripts/darwin/dumpregs.py` and
implemented in `target/arm/gxfstat.c`. They cover the Apple implementation-
defined register model, guarded transitions, MMIO and exception categories.
They do **not** count all ordinary ARM system-register instructions, TLB/cache
instructions, or every instruction the virtual-EL2 ledger adapts. The listed
shares are shares of Apple register traffic, not CPU-time percentages or
shares of all guest instructions. The sample includes all guest activity
during the migration window, including background services, rather than
attributing every event to a migration worker.

No sampling profiler was attached during the timed window. Periodic existing
aggregate counters remain enabled. This one-CPU measurement does not establish
six-CPU rates, completed-boot performance or game behavior.

## Reproduction and rejected first attempt

```sh
python3 tools/perf/migration_compat_profile.py \
  --out /tmp/dvm/UNIQUE_MIGRATION_CALLS --seconds 60
```

Use a fresh output directory. The runner owns and cleans up its child QEMU.
It stops with an error if no metadata-start marker appears within 180 seconds.

`HVF_MIG_CALLS1` is not a completed measurement. Its initial snapshot rejected
the raw byte of an optimized/LTO bool as if it were an unoptimized C value.
The subsequent runner avoids that assumption and instead requires a single
CPU, nonzero actual counts and exact histogram/aggregate agreement. CALLS1
exited without entering the timed sample; CALLS2 is the successful fresh-child
repeat. No emulator behavior was changed to obtain the result.
