# Faster ARM-host TCG without changing guest results

This work follows the [storage profile](storage-performance.md), which put ANS
request service at about 2.3% of the partial migration window and exposed
substantial pointer-authentication and address-translation helper costs.

The first alternating comparison reduced median time to 100 User-volume
metadata events from **97.914 seconds to 84.207 seconds (14.0% less elapsed
time)**. This is the combined cache/unused-work/compiler change, not a claim
of native-speed execution or a measurement of full migration completion.

## Execution path and portability

On this Mac, QEMU is an ARM64 executable and TCG generates native AArch64 code
using `tcg/aarch64/tcg-target.c.inc`; `CONFIG_TCG_INTERPRETER` is undefined.
It is not interpreting every ordinary ARM arithmetic instruction in C.
Translated execution still performs guest memory translation, privileged-state
operations and device emulation, with frequent transitions into C helpers.

QEMU already detects host LSE/LSE2, AES and PMULL in
`util/cpuinfo-aarch64.c:cpuinfo_init`. All four corresponding macOS `sysctl`
features returned 1 on this M5 Max. The backend selects native atomic paths
through `tcg/aarch64/tcg-target-has.h`, and host crypto helpers have guarded
AArch64 implementations under `host/include/aarch64/host/crypto/`.

The changes below use portable C and normal QEMU translation, with no new
Hypervisor.framework, Apple-only host-register operations, or host-pointer
substitution for guest addresses. Windows ARM and x86 remain design targets;
these changes were not built or run on Windows in this session. The optional
macOS stack sampler is a diagnostic, not a runtime dependency.

This does not change the accelerator to HVF. The existing
[real-host EL2/GXF probe](hvf-acceleration.md) documents why the present guest
cannot simply execute under HVF with the desired Apple-register traps. A
different hardware-assisted architecture would require additional guest/VMM
work and separate correctness validation.

## Changes

### Pointer-mask reuse

`target/arm/tcg/pauth_helper.c:pauth_strip` caches the four pointer masks for
instruction/data accesses and lower/upper addresses on each CPU. The cache is
keyed by the **live TCR value and stage-one MMU regime**, checked on every use.
The mask is still derived by the original `aa64_va_parameters` and
`pauth_ptr_mask` functions on a miss. The hot path selects the cached mask and
uses integer AND/OR operations that compile to native host instructions.

This follows the exact dependencies in `target/arm/helper.c:aa64_va_parameters`:
only `tsz`, `tbi` and `mtx` affect the mask, and they depend on TCR, translation
regime, access kind, address bit 55 and fixed CPU features. Other returned
fields, such as SPRR and PIE, are not cached. Checking raw live register values
avoids relying on a new invalidation hook for guest writes or GXF banking.
The cache is host-derived state in ARMCPU, not guest or migration state.
This design does not constitute a new claim that SMP checkpoint restore works.

The cache defaults on only for Apple machines through `apple_regs_init`.
`DARWIN_PAUTH_CACHE=off` selects the original mask calculation;
`DARWIN_PAUTH_CACHE=verify` compares every cached result with that calculation
and asserts equality. Verification emits positive per-CPU progress counts.

The fork already had `QEMU_SPTM_DISABLE_PAC`: signing returned its input and
authentication returned the stripped pointer. We move those existing early
returns ahead of calculations whose results are unused. We do not change
key-enable/trap checks, PACGA, stripping semantics, or the existing bypass
policy. The cache-off option does not restore the discarded computations;
the performance comparison therefore uses a preserved pre-change executable
as its baseline.

### Compiler optimization with checks retained

`tools/build_qemu_fast.sh` creates `qemu-sptm/build-fast` and selects:

- `optimization=3` and link-time optimization;
- `b_ndebug=false` and `debug=true`, retaining assertions and debug symbols;
- the normal portable host target, without `-march=native` or unsafe math flags.

It refuses to build while this worktree's QEMU executables are running. It does
not overwrite the ordinary incremental build. Its approach builds on the
earlier `codex/arm-performance-boot` compiler measurements, now applied to the
fixed multicore model and measured on the fresh-image metadata workload.

## Correctness evidence

`SMP_PAUTH_VERIFY6` ran the six-core guest with reference comparison on every
pointer strip. It reached 100 User-volume events at 94.495 seconds, with zero
XNU panics and at least **2,182,000,000** successful comparisons across CPU0–3
(last printed counts: 622m, 548m, 511m, 501m). This was a verification run, not
an unbiased performance comparison.

`tools/re/smp_smoke.py --pauth-cache verify` executes 1,024 synthetic cases:
512 deterministic randomized TCR configurations across ordinary EL2 and VHE,
both address halves, XPACI/XPACD, AUTIA/AUTIB/AUTDA/AUTDB and signing no-op
checks. The optimized LTO executable passes with verdict `0x600d`, failure
sentinel zero, checksum `0x5000000000000000`, and a positive verification log.
The original O2 cache implementation produced the same checksum. This matrix
covers the feature set of the T8140 model, not every possible ARM CPU model.

Artifacts: `/tmp/dvm/SMP_PAUTH_VERIFY6/results.json` and adjacent stderr/serial;
`/tmp/dvm/SMP_PAUTH_MATRIX_VERIFY.log`, `SMP_PAUTH_FAST_MATRIX.log`, and
`/tmp/dvm/probe/SMP_MACHINE_PAUTH_VERIFY.stderr.log` (the latest LTO matrix).

Final regressions on the LTO executable also passed:

- `SMP_PERF_FAST_RESTORE`: **reached shell: yes, xnu panics: 0**.
- WFE event-stream test: all 48 masked-IRQ wakeups and disabled-stream wait.
- CPU0/CPU4 test: 40,000 shared atomic increments and bidirectional FIQ IPIs.
- `SMP_PERF_FAST_USER2`: two online CPUs captured simultaneously at EL0 with
  distinct stacks; workloads stopped and shell responsive, zero panics.
- All 18 host tests and changed-script syntax checks pass.

These regression logs are `/tmp/dvm/SMP_PERF_FINAL_{restore,wfe,cross,user2}.log`
and `/tmp/dvm/SMP_PERF_HOST_FINAL.log`. The four regression probes ran after
the timing comparison; all owned guests were stopped after collection.

## Running and reproducing

### Partial migration comparison

`SMP_PERF_ABBA` used six CPUs, with profiling and verification disabled for
timing. Both binaries include the earlier multicore/WFE fix. The preserved
baseline is the O2, non-LTO build at QEMU `f1999ed`; the candidate has the
pointer-helper changes and O3/LTO with assertions retained.

| Run order | Build | Time to 100 events | Events/s over events 20–100 |
|---|---|---:|---:|
| 1 | Baseline | 99.089 s | 2.489 |
| 2 | Optimized | 80.964 s | 2.885 |
| 3 | Optimized | 87.449 s | 2.528 |
| 4 | Baseline | 96.740 s | 2.417 |

Every run reached the same partial-work limit with zero XNU panics and was
stopped there. Both optimized runs beat both baseline runs. The sample is
small and the optimized pair varies by 6.5 seconds; use the measured 14% median
reduction as a result for this host/session, not a universal speedup guarantee.
It does not isolate how much each of the three changes contributed.

Evidence: `/tmp/dvm/SMP_PERF_ABBA/{results,summary}.json`, per-run logs in that
directory, and complete guest logs/results under
`/tmp/dvm/SMP_PERF_ABBA_{0_base,1_fast,2_fast,3_base}/`. Results pin binary SHA256
and backing-parent paths. No build or other owned boot overlapped the four
timed runs.

### Launch commands

```sh
tools/build_qemu_fast.sh
python3 tools/run_smp.py --fast --cpus 6
```

That launcher boots the experimental restore shell. To compare partial
first-boot migration on fresh children:

```sh
python3 tools/re/smp_perf_compare.py \
  --baseline /path/to/pre-change/qemu-system-aarch64 \
  --candidate qemu-sptm/build-fast/qemu-system-aarch64 --tag NEW_PERF_ABBA
```

The comparison is sequential A/B/B/A, uses the same firmware and backing
parent, records binary hashes, and stops each guest at 100 User-volume events.
No storage-cache or guest boot-argument changes are made between the builds.
For a normal probe with the fast executable:

```sh
DVM_QEMU="$PWD/qemu-sptm/build-fast/qemu-system-aarch64" \
  tools/probe.sh --secs 15 --tag NEW_FAST_RESTORE
```
