# Storage versus CPU cost during partial first-boot migration

Measured 2026-09-04 on the multicpu worktree, after the Apple WFE event-stream
fix. **The ANS storage request handler is not the dominant delay in this
sample.** Its synchronous request service accounts for about 2.3% of elapsed
time. The host is mainly executing the guest through TCG and its support code.

This is a fresh child of the pre-first-boot `marker.qcow2`, not a full migration
benchmark or an isolated SSD benchmark. The stop condition is 100 unique
User-volume `set_dir_stats` progress events, 200 total events, panic, or 180
seconds. No test waits for migration to finish. The host's other checkout and
VM were left untouched; host filesystem caches were not purged.

## Direct request timing

`hw/arm/darwin_ans.c:ans_io_command` runs `blk_pread`, `blk_pwrite`,
`blk_pwrite_zeroes` or `blk_flush` synchronously from the submission path. It
uses one I/O queue pair. Optional `DARWIN_ANS_PROFILE=1` now wraps the complete
I/O command handler and emits cumulative counters at most once per host second.
The interval includes allocation, guest-memory DMA copies and the synchronous
block operation, including time the host deschedules the thread. It excludes
submission-queue fetch, completion posting, guest filesystem CPU work, and guest
waits before submitting a request. It is not merely physical SSD latency.
Diagnostics are host-only and omitted from VMState; default operation is
unchanged. Read/write bytes count successful logical transfers through ANS,
not physical SSD bytes or unique file content.

`SMP_STORAGE6_PROFILE1` reached 100 events at **104.818 seconds**, with zero XNU
panics. The last aggregate record, at 104.334 seconds since device realization,
shows:

| Operation | Calls | Logical data | Total service time | Mean service time |
|---|---:|---:|---:|---:|
| Read | 37,069 | 1,582.2 MiB | 1.114 s | 30.1 microseconds |
| Write / write-zero | 3,948 | 223.2 MiB | 1.240 s | 314.1 microseconds |
| Flush | 129 | — | 0.0606 s | 470.0 microseconds |

All I/O commands combined: **2.415 seconds / 104.334 seconds = 2.31%**.
The largest individual read, write and flush were 5.89, 7.61 and 3.18 ms.
Between the first record after 60 seconds and the last record, service time
was 0.987 seconds out of 43.441 seconds (**2.27%**). Storage does not become
dominant once the metadata updates are progressing steadily.

An independent fresh-child repeat, `SMP_STORAGE6_COUNTERS`, kept the aggregate
counters but omitted host stack sampling. It reached 100 events at **104.068
seconds**, with zero XNU panics. Its last counter record at 103.066 seconds
shows **2.342 seconds of request service (2.27%)**, including 1,580.2 MiB of
reads, 220.1 MiB of writes and 128 flushes. After 60 seconds, the share is 2.12%.
Thus the low storage-service share reproduces without stack sampling. This
does not separately measure the aggregate counters' own overhead.

The record is emitted after a completed command, so the final fraction of a
second is not included. These are cumulative synchronous call times, not an
exact end-to-end speedup prediction. They do show that eliminating the measured
request service alone cannot account for most of this roughly 100-second wait.

## Where the host threads spend their time

The same run used macOS `sample` for five seconds at elapsed 15, 45 and 75
seconds, requesting a 10 ms sampling interval. `tools/re/smp_storage_report.py`
parses the call trees and partitions each observation once, using the full
ancestry. Tree count consistency is checked. These are **sampled thread wall
states, including waits**, not percentages of CPU cycles or percentages of the
entire boot. Generated guest code cannot be resolved to iOS process names by
this host sampler.

For CPU0–3 in the 75-second sample (1,512 combined observations):

| Call-path category | Share of observations |
|---|---:|
| Other translated guest execution and helpers | 37.63% |
| Pointer-authentication helpers | 16.60% |
| Translated-block lookup | 14.88% |
| MMU translation / TLB fill | 9.79% |
| Host mutex waits | 9.39% |
| Host condition waits | 4.89% |
| Generating translated code | 3.37% |
| Other CPU management | 2.78% |
| ANS command handling | 0.66% |

The earlier two samples show the same broad pattern. Main concrete paths are
`pauth_autib` / `pauth_autdb` / `aa64_va_parameters`, `helper_lookup_tb_ptr` /
`tb_htable_lookup`, and `arm_cpu_tlb_fill_align` / `get_phys_addr`. Some mutex
waits come from `helper_get_cp_reg64` and other shared QEMU work. Do not treat
all host mutex waits as storage waits.

The pointer-authentication category is the complete helper path, including
address-regime and trap checks, not just cryptographic hashing. This fork
already defines `QEMU_SPTM_DISABLE_PAC` in `pauth_helper.c`; helper overhead
remains. The profile is not a recommendation to disable further guest checks.

CPU4–5 spent 93–95% of their observations in host condition waits in all three
samples, mainly `qemu_process_cpu_events`. The six-vCPU configuration is not
running six continuously busy guest workers in these windows. This does not
establish why the guest leaves the performance cluster largely idle, nor prove
that every migration operation is serial. Host `ps` CPU-time deltas outside
stack-sampling windows indicate roughly 3.7–3.8 host CPU-seconds per second
after 30 seconds, consistent with four active efficiency-cluster vCPUs.

## Implications and reproduction

CPU emulation support paths and guest work placement are the better next
performance investigations for this workload. A guest-aware profile is needed
to assign the remaining execution to individual migration workers, background
services, or spin loops. Asynchronous ANS requests and more queues may help
other I/O-heavy workloads, but the measured storage service here provides
little evidence for a large migration speedup from that rewrite alone.

```sh
python3 tools/re/smp_boot_bench.py --migration-sample --variant pv6 \
  --storage-profile --host-sample-at 15 45 75 --tag NEW_STORAGE_PROFILE
python3 tools/re/smp_storage_report.py /tmp/dvm/NEW_STORAGE_PROFILE
```

Evidence: `/tmp/dvm/SMP_STORAGE6_PROFILE1/results.json`, `profile-summary.json`,
`0_pv6.stderr.log`, `0_pv6.serial.log`, and `0_pv6.host{15,45,75}.txt` in that
directory. The stack-sampled run took about six seconds longer than the earlier
98.6–98.8-second plain runs, so its milestone time is not substituted into the
earlier core-count benchmark. The counters-only repeat has corresponding
artifacts under `/tmp/dvm/SMP_STORAGE6_COUNTERS`; it also took about 104 seconds,
so the timing difference cannot simply be attributed to stack sampling.

The instrumentation build passed the bounded normal restore probe
`tools/probe.sh --secs 15 --tag SMP_STORAGE_DEFAULT`: **reached shell: yes,
xnu panics: 0** with profiling disabled. All 18 host regressions passed before
the measurement boots; script syntax checks and both saved profile analyses
also pass. All owned measurement guests were stopped after collection.
