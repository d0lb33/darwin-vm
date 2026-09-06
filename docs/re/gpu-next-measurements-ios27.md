# Measurement contract for the next auxiliary GPU experiment

Baseline: research commit `61fa449`, QEMU commit `89b238c`, unchanged iOS 27
24A5430a / iPhone17,3 / T8140. The auxiliary channel has byte evidence;
the same GPU workload over that channel is still untested. Preserve SPTM,
TXM, migrated disk lineage, the existing display/input services, and software
rendering. Do not merge pending warm-boot work to make this test pass.

## Current collection assignment: existing evidence only

Terra/high owns read-only collection from the durable UART and auxiliary
archives. It must not boot a VM, mount a disk, change a driver/helper, repair
an artifact, or reinterpret a failed observation as success. The parent owns
this specification and final analysis. Collection stops when AUX14, AUX15,
AUX16 and the final nine-operation UART run are accounted for, or after
20 minutes. Missing or invalid evidence must be returned explicitly.

Required outputs are per-operation CSV, host-event CSV, source hashes and
line/JSON locations, independent payload validation, and observed findings.
Capture these quantities without omitting outliers:

| Quantity | Definition / grouping |
| --- | --- |
| Auxiliary bulk call time | Guest monotonic elapsed for one 1 MiB read and write, per trial |
| Auxiliary RTT | Guest interval from request write start through valid reply; sequences 1–8 separate from 9 and 10 |
| Timeout overshoot | Sequence 9 elapsed minus its nominal 1 s polling budget |
| Recovery | Sequence 10 elapsed, poll count and exact reply validation |
| Host peer events | Adjacent post-handling event timestamps within the host peer's own monotonic clock; not request-arrival timestamps |
| Input | READY, fresh sync send and ACK timestamps within the runner's host clock; early sync tracked separately |
| Bytes | Independently regenerate seed and expected output; compare all 1,048,576 bytes per direction |
| UART GPU operation | Each guest commit/total time and each matching host wall/GPU/delay interval; preserve generation and pass |
| Overall verdict | Keep AUX15's interrupted input check distinct from its completed byte subset |

Do not calculate one-way latency by subtracting unrelated host/guest clocks.
Do not subtract host GPU time from a guest elapsed measurement and label the
remainder transport time. Do not infer a GPU speedup from byte-only tests.
Eight ordinary replies do not support population p95/p99 or frame-rate claims.

The current `aux_probe.py` records its timestamp after request validation and
reply publication (or deliberate withholding). It has no separate receive
and publish timestamps. Its epoch also differs from the runner's elapsed
epoch, and that offset was not retained. The old records cannot establish
host processing time or exact one-way transit, despite both processes using
monotonic clocks. Input ACK latency uses two runner timestamps and is valid
within the precision of the rounded serial-event timestamp.

## Next live experiment: specification, not an executed result

The implementation gate is a small auxiliary transport adapter for the
existing process-local forwarding proof. Until that adapter exists, passes
host checks and receives parent review, Terra must not improvise a live run.
The current byte probe cannot execute the following GPU workload.

Reuse the exact QuartzCore AIR slice selected by `guest_work.m`:
2,705,796 bytes, SHA-256
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`,
function `read_write_surf_compute`. A digest-checked host cache is permitted;
the guest must select and hash its own library and request the same bytes.
Do not replace it with a host-authored shader.

Keep the existing nine-operation workload: three IOSurface generations,
three passes each, 64×48 BGRA8, 256-byte rows, 12,288 bytes in each direction,
9×7 groups of 8×8 threads. Use one client and one outstanding GPU operation.
Retain the first operation's deliberate 200 ms host-response delay; report it
separately. Preserve input snapshot mutation after commit, rejection of reads
before completion, host destination sentinel, GPU completion status, wrapper
readback and direct guest IOSurface equality.

Two sequential trials are defined. Each uses a fresh qcow2 child and newly
created auxiliary backend, without saved RAM or a different guest build:

1. `GPU_AUX_RENDER_EARLY1`: launch the helper independently during boot,
   as in the byte proof. Also require a fresh original-input sync ACK after
   READY, before declaring the whole trial successful.
2. `GPU_AUX_RENDER_READY1`: gate the first GPU request on input READY and
   its fresh sync ACK. Call this **post-input-ready**, not a proven idle or
   stable warm-boot state. A helper restart invalidates its session.

No other VM may be stopped, resumed, instrumented or rebuilt. Do not overlap
these trials or a QEMU build. Use the immutable QEMU3 binary and record its
hash; changing it requires a new reviewed experiment specification.

### Required timing instrumentation

Log monotonic timestamps and command ID, surface generation, session epoch,
byte count and success/error for each point below. Report durations within
one clock only. Keep raw records so the parent can recompute every interval.

| Guest milestone | Exact boundary |
| --- | --- |
| G0 | Before command commit begins |
| G1 | Last request byte and publication marker successfully written |
| G2 | Entire response validated: epoch, ID, length and content integrity |
| G3 | Returned pixels copied to guest IOSurface and write unlock succeeds |
| G4 | Wrapper readback and direct IOSurface verification both succeed |

| Host milestone | Exact boundary |
| --- | --- |
| H0 | Entire request available and validated |
| H1 | Host Metal command buffer committed |
| H2 | Host Metal completion observed and successful status checked |
| H3 | Output texture/IOSurface readback and expected-pixel checks finish |
| H4 | Entire reply and publication marker written |

Record GPUStartTime/GPUEndTime separately as the device-reported execution
interval, checking validity before reporting a duration. Record library
selection/cache validation, Metal library creation, pipeline creation and
resource allocation outside the per-command interval. Use monotonic timing
for new instrumentation: the old GPU harness uses NSDate wall-clock timing,
so its reported values are historical evidence rather than a synchronized
clock reference for the new test.

Derived guest intervals: G1−G0 request submission, G2−G1 completion wait,
G3−G2 IOSurface copy/unlock, G4−G3 verification, G3−G0 usable-output time,
G4−G0 full verified operation. Host intervals: H1−H0 preparation,
H2−H1 completion wait, H3−H2 readback/verification, H4−H3 publication.
Do not subtract H timestamps from G timestamps. Record poll counts and every
auxiliary user-client return code. Hash captured protocol streams and retain
per-operation expected/actual pixel hashes.

### Stop conditions and verdicts

These budgets bound collection, not a promise of real-time behavior:

- Before launch: stop on any pinned-input/backing-chain mismatch, unavailable
  isolated output directory, unresolved target ownership, or failed required
  host tests/shell checks. Never silently substitute inputs.
- Startup: stop if the helper cannot establish its guarded NS6 client, if
  input READY plus a fresh ACK is absent after 240 host seconds when required,
  or if library/pipeline setup has no completed stage for 30 host seconds
  after the workload is released. A precondition failure is not proof that
  host Metal or the byte transport is impossible.
- Work: stop immediately on wrong bytes/epoch/ID/length, unsupported private
  call, namespace open/I/O failure, Metal error, unexpected early-read success,
  guest panic, helper restart, worker exit, or incomplete/stale completion.
  Do not retry a GPU operation blindly; preserve the first failed contract.
- Watchdog: stop if an operation lacks a validated response for 30 host
  seconds, or the entire VM trial exceeds 360 host seconds. Guest polling
  deadlines alone cannot bound a synchronous syscall or scheduling pause.
- Success: stop after all nine GPU operations and both pixel oracles pass,
  three resource generations are accounted for, final guest report is
  acknowledged, resources/session are closed, and the required input ACK is
  observed. Freeze only the owned VM for final capture, then terminate it.
- Stop the matrix after its first unexpected failure. Return artifacts for
  parent analysis; do not add patches, increase budgets, change guest versions,
  disable protection, or launch extra variants to obtain a pass.

Feasibility success means the real workload traversed the new adapter with
verified guest IOSurface output. It does **not** mean a system-discovered Metal
plugin, accelerated SpringBoard, sustained 60 fps, zero-copy memory, or live
checkpoint restore. Those remain separate contracts. A slow but correct run
is a feasibility pass with a performance problem, not a performance pass.

Report all nine samples and min/median/max for the eight non-delayed operations
per trial. A later performance study needs more samples, controlled load and
a matched CPU/software-rendering workload before claiming useful UI speedup.

## Reviewed collection results

Terra collected the existing artifacts; no new VM or GPU run was performed
for this audit. Parent review independently matched twelve auxiliary inputs
against the archive's SHA-256 index, checked all thirty sequence results, and
reran `verify_roundtrip.py` on the retained exact-guest COLD3 request/response
streams. All nine GPU payloads and guest/host verification oracles passed.

| Retained trial | 1 MiB read / write call (ms) | Median of replies 1–8 (ms) | Timeout overshoot (ms) | Recovery reply 10 (ms) |
| --- | --- | --- | --- | --- |
| AUX14 | 2.433 / 2.312 | 8.455 | 1413.602 | 348.827 |
| AUX15 | 2.772 / 2.003 | 9.071 | 754.667 | 718.607 |
| AUX16 | 2.121 / 1.584 | 8.980 | 504.297 | 445.198 |

Each median has eight samples; it is descriptive, not a tail guarantee.
All six independently regenerated 1 MiB regions matched. AUX15 completed its
byte subset but was interrupted during the input check; AUX14 and AUX16 passed
their respective overall runner gates. AUX16's fresh input ACK took about
233 ms after send, and arrived after the byte workload had completed. It
does not prove input responsiveness during sustained GPU traffic.

COLD3's nine guest-reported GPU-operation intervals total 401.382155 s; host
worker intervals total 33.347 ms, including 1.153 ms of device-reported GPU
time. These are different scopes and timing domains. No valid UART-to-auxiliary
GPU speedup follows from comparing them with the byte table above.

Two collection issues were caught in parent review and corrected: a draft
selected a host-only UART control instead of COLD3, and it initially called
post-handling peer timestamps request arrivals. The final collector must use
COLD3 with `host_only=false` and retain the unavailable timing fields rather
than invent a decomposition of the latency.

The evidence supports implementing the small GPU adapter and running the two
specified trials. It does not establish which layer causes the late replies.
The missing receive/publish timestamps and the old NSDate timing are concrete
measurement gaps that the next implementation must close before claiming
performance improvement. This audit changes the measurement plan, not the
status of GPU forwarding over the auxiliary channel: that remains untested.

## Reproduction and retained outputs

The reviewed collector is `tools/gpu/collect_gpu_measurements.py`. It requires
a new output directory, emits every sample with source hashes/locations, and
returns failure if its expected data contracts fail. Its input paths select
these specific retained historical trials; it is not a live VM runner.

```sh
python3 tools/gpu/collect_gpu_measurements.py \
  --output /tmp/dvm/GPU_MEASURE_REVIEW1
```

Choose a new output name when replaying. Parent replay produced CSVs identical
to Terra's corrected collection. Additional parent checks validated all nine
UART command IDs, generations, statuses, delay flags and resource-recreation
witnesses; an attempted reuse of the output directory was rejected without
changing existing evidence. The existing byte-stream verifier independently
checked the COLD3 pixel payloads rather than trusting a `passed` field alone.

Durable outputs:
`/Users/jdolbe1/dvm-artifacts/research/gpu-measurements-ios27-20260905/`.
`GPU_MEASURE_REVIEW1/` contains the reviewed replay CSVs and evidence JSON;
`GPU_MEASURE_AUDIT1/` retains Terra's collection and findings;
`GPU_MEASURE_PARENT1/` records independent parent checks. `index.json` hashes
the archived files. The timing audit performs no new GPU execution and does
not change the migrated baseline, guest helper, QEMU, display, or input path.
