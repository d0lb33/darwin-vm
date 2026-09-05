# HVF performance checkpoint — September 5, 2026

Follow-up: the [60-second first-boot migration profile](hvf-migration-call-profile.md)
measures actual TCG compatibility-operation frequency. It finds 26.65 million
Apple register accesses and 744,399 guarded entry/return pairs in the window;
TPIDR_GL2 and CURRENTG account for 66.79% of that register traffic.

These are fresh microbenchmarks on the M5 Max host, not iOS boot or game
benchmarks. The real native SPTM boot still stops at GXF_CONFIG=0x6f.
The current bridge preserves native throughput for ordinary computation,
but its implemented compatibility operations are substantially slower than
TCG. It is not yet a fast whole-system replacement.

Both suites use the unchanged BUILD8 binary, SHA-256
`dc2d14c93387cb02e3af8dc493132fdd08c1c2987c87a1bce68e84f91164448a`.
Five repetitions alternate HVF/TCG ordering. Only one owned benchmark VM
runs at a time. The separate display instance is untouched. No persistent
iOS disk is attached. No host setting, production CPU code, commit or push
was changed for these measurements.

## Identical ordinary ARM loops

`/tmp/dvm/HVF_PERF_NOW2/results.json`: existing `arm_island_bench.py`,
100 million iterations per case, 40/40 runs completed with matching
checksums (and complete 64 KiB data hashes for the load/store case).
The guest executes at EL0 with the MMU enabled on QEMU's generic virt board;
this suite does not exercise the iOS compatibility layer. Guest counter
timing excludes setup and GDB; independent host timing agrees within about
0.2 ms at the medians. The completion breakpoint is on a separate page.

| Workload | TCG median | HVF median | TCG / HVF |
| --- | ---: | ---: | ---: |
| Integer add/rotate/XOR loop | 168.089 ms | 164.888 ms | 1.02x |
| Load/store, 64 KiB working set | 199.583 ms | 71.553 ms | 2.79x |
| Mixed SIMD add/EXT/XOR | 1311.065 ms | 377.042 ms | 3.48x |
| Dependent pointer chasing, 64 KiB | 816.653 ms | 563.371 ms | 1.45x |

`HVF_PERF_NOW1` was the preliminary 10-million-iteration run; NOW2 is the
longer measurement used here. These four workloads establish workload-specific
gains, not a universal native multiplier. In particular, basic integer code
is already translated efficiently on this ARM host.

## Current integrated compatibility layer

`/tmp/dvm/HVF_BRIDGE_PERF4/results.json`: new `native_bridge_bench.py` and
`native_bridge_bench.S`, 50/50 cases passed. The runner boots actual SPTM
to runtime `0xfffffff0070a37dc`, then installs an explicitly synthetic fixture.
SPRR/GXF setup executes through guest instructions before timing starts.
HVF runs the ledger-adapted payload in the current virtual-EL2 shadow backend;
TCG executes the original privileged instruction words. Both use one CPU on
the Darwin board. This is not continued real firmware execution.

The fixture checks an independent arithmetic result, loop completion, return
to ordinary execution, unchanged permission-bank/configuration state, and
unchanged five bootstrap table pages and two code pages. HVF uses the same
adapted SPTM/ledger as BOOT8; the synthetic fixture appends its own ledger
entries. Test fixtures are not evidence of unimplemented protection semantics.

Host monotonic timing surrounds one continue/stop pair and includes debugger
transport overhead. A zero-iteration control measures its floor: median
0.164 ms HVF and 0.092 ms TCG. Values below are unadjusted medians.

| Workload | Count | TCG | HVF |
| --- | ---: | ---: | ---: |
| Eight dependent integer adds per iteration | 100 million | 195.015 ms | 189.963 ms |
| Eight dependent SIMD adds per iteration | 100 million | 439.772 ms | 378.480 ms |
| Trapped TPIDR_EL2 read plus checksum accumulation | 100,000 | 0.210 ms | 936.880 ms |
| GENTER / guarded increment / GEXIT pair | 10,000 | 1.486 ms | 1064.024 ms |

The ordinary-compute gains are 1.03x and 1.16x respectively. This SIMD loop
contains only additions, so it is a different workload from the mixed SIMD
case above; their different ratios do not measure bridge overhead directly.

The current HVF costs are about **9.37 us per TPIDR_EL2 read** and
**106.4 us per guarded entry/return pair**, including loop work and diagnostics.
The HVF run ranges are 0.931–0.947 s for reads and 1.034–1.129 s for pairs.
The TCG read loop is close to the debugger timing floor and highly optimizable;
do not turn that ratio into a precise claim about hardware trap latency.
TPIDR_EL2 is the tested register, not a measurement of every Apple register
or the high-frequency TPIDR_GL2 path.

Verbose native diagnostics remain enabled, as in the current bridge:
the read run produces about 8.1 MB stderr and the transition run about 9.4 MB,
including bootstrap and post-run inspection. These are current-development
costs, not minimum costs imposed by Hypervisor.framework. No logging-off
comparison or statistical host-stack profile was performed.

Source inspection explains work present on these paths, without attributing
an exact share of measured time to each component:

- `target/arm/hvf/virtual-el2.h:hvf_virtual_instruction` synchronizes state
  before dispatch and emits diagnostics for each register operation.
- `target/arm/hvf/hvf.c` gets/puts GPR and SIMD banks during that synchronization.
- `target/arm/hvf/virtual-gxf.h:hvf_virtual_gxf_transition` invalidates all
  native aliases at each transition; `virtual-shadow.h` rewalks, validates,
  maps and synchronizes the newly accessed executable page.

This makes narrow validated register fast paths, selective state synchronization,
opt-in diagnostic logging, and permission-correct reuse of mappings concrete
performance targets. Mapping reuse must retain invalidation on permission,
page-table and execution-context changes. These measurements do not justify
dropping permission checks or accepting the currently rejected GXF/CTXR controls.

## Rejected benchmark artifact

`HVF_BRIDGE_PERF1` and the interrupted `HVF_BRIDGE_PERF2` placed the stop
breakpoint on the loop's code page. QEMU `accel/tcg/cpu-exec.c` explicitly
sets `CF_NO_GOTO_TB | CF_BP_PAGE | 1` for such pages, producing one-instruction
TBs and an artificial native speedup. Do not use those timings.

The corrected fixture branches to `0xfffffff0070a4000`, a separate 16 KiB page,
before stopping. No loop-page breakpoint remains during measurement. The
short `HVF_BRIDGE_PERF3` run checks the corrected fixture; PERF4 supplies the
longer repeated results. With that correction, the 100-million integer test
changes from about 5 seconds TCG in PERF2 to about 0.195 seconds in PERF4.
The generic-virt suite already used separate pages and is unaffected.

## Reproduce

Use a new output directory each time. Never overlap a build or another owned
benchmark with these runs.

```sh
python3 tools/perf/arm_island_bench.py \
  --out /tmp/dvm/UNIQUE_RAW_PERF --repeat 5 --iterations 100000000

python3 tools/perf/native_bridge_bench.py \
  --out /tmp/dvm/UNIQUE_BRIDGE_PERF \
  --dtree /tmp/dvm/NATIVE_DRAM_LOW1.dtree \
  --sptm /tmp/dvm/HVF_GXF_SPTM8.macho \
  --ledger /tmp/dvm/HVF_GXF_SPTM8.ledger \
  --repeat 5 --read-count 100000 --transition-count 10000
```

TCG remains the functional boot backend. Neither suite establishes HVF
multicore scaling, migration speed, storage throughput, GPU performance,
Windows ARM acceleration, or a full iOS speedup.
