# Initial auxiliary header read — exact 24A5430a

## Question and fixed experiment

`GPU_SETTLED_A1` previously reported a successful namespace open and capacity
queries, then no HEADER or WAIT marker before the 450-second stop. That did
not establish whether allocation, scheduling, a helper exit, the synchronous
read, or completion delivery prevented progress. This diagnostic resolves
those boundaries before another latency batch or GPU implementation.

Continue the isolated `codex/gpu-postboot-main` / `codex/gpu-postboot-qemu`
worktrees from project `b1e5825`, QEMU `0b8137b`. Do not import concurrent
uncommitted native-input work from the shared main checkout. Retain the pinned
CLOCK disk baseline and all five existing cached native services, exact guest
BootKC/SPTM/TXM, migrated lineage, software renderer, display/input configuration,
and auxiliary namespace. Snapshot save/restore stays blocked.

Use the same early-start launch service; this experiment does not claim that
transport initialization itself starts after Home. The signed helper emits:

- stage 0: helper/heartbeat initialized, before inventory;
- stage 1: capacity verified, before aligned allocation;
- stage 2: allocation returned, before initializing the page;
- stage 3: page initialized, before synchronous selector-0 read;
- stage 4: read returned, including an error return;
- full-page CRC, magic guard, user-client close and completion;
- a five-second heartbeat with current stage and PID, plus an atexit marker.

Header-only mode returns before bulk I/O or latency work. Host compares the
returned CRC against its own complete 4096-byte page and verifies zero writes
to request, response, release-gate, and bulk-output regions. Positive heartbeat
records establish that a helper thread is alive and logging; missing heartbeat
is not proof of process death. No cross-clock timing subtraction.

QEMU `DARWIN_ANS_AUX_TRACE=1` logs only its first eight decoded auxiliary I/O
commands: NSID/tag/CID/opcode/LBA/PRPs, backend entry/return, data DMA result,
CQE slot/phase/status/DMA result, IRQ pending/mask decision, and the first
subsequent CQ-head write. CQ index is not a physical address. Queue-head
movement does not by itself prove this helper's call returned. Existing CQ-DMA
failure behavior is preserved, including its unconditional tail advance;
the trace reports the DMA boolean rather than silently treating it as success.

Maximum three **sequential** fresh disk boots, `GPU_HEADER_READ1/2/3`.
Stop at the first failure, retaining all records, without retrying that trial.
Each uses a new child and independent 64 MiB auxiliary raw file. Stop on verified
header completion, a guest failure/panic, 30 host seconds without a non-heartbeat
guest probe-stage/progress record, or 120 host seconds total, plus bounded teardown.
Host continuously drains the owned UART. No debugger, RAM restore, manual guest
memory/register changes, input events, GPU registration or benchmark workload.

A header pass requires host/guest page checksum agreement, no auxiliary writes,
and reviewed correlation with one NS6 read and QEMU completion stages. A failing
stage gives a boundary for the next experiment; it does not prove GPU acceleration
impossible. Three successes would show the instrumented path works in those runs,
not that the earlier intermittent stall is fixed or that transport is fast.

## Build and staging

QEMU was rebuilt in its isolated build directory with `make -j18`, then copied
as immutable `/tmp/dvm/GPU_HEADER_QEMU1/qemu-system-aarch64`, SHA-256
`f19a766a18cacf1aac1b2dc8f155ca6733fb20be5a650d7fb8b63f0209041f35`.
The build log confirms darwin_ans.c compilation, relinking and signing. The
baseline manifest is `/tmp/dvm/GPU_HEADER_BASELINE1.manifest.json`.

```sh
bash tools/gpu/build_transport_inventory.sh /tmp/dvm/GPU_HEADER_BUILD1 --uc-probe --namespace-entitlement --class-exception --header-only
python3 tools/gpu/prepare_guest_load.py --build /tmp/dvm/GPU_HEADER_BUILD1 --cache /tmp/dvm/native-services6/launchd.plist --system-tc /tmp/dvm/CLOCK_SOFTWARE_PATCH1/system.tc --output /tmp/dvm/GPU_HEADER_STAGE1 --interactive-load
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/GPU_HEADER_BASELINE1.manifest.json --stage /tmp/dvm/GPU_HEADER_STAGE1 --tag GPU_HEADER_INSTALL1
```

Parent compared the staged cache with the original: removing only the added
GPU helper restores complete equality. Before boot: 50 project tests, 12 prior
auxiliary/readiness tests, one new compiled mock-client test, shell syntax and
both repo diff checks pass. The mock-client test proves one read/no writes and
foreign-magic rejection; real guest/kernel completion remains a runtime question.
Terra independently reviewed the instrumentation and deadline composition.

## First diagnostic and trace refinement

`GPU_HEADER_READ1` completed the guest path: stages 1..4 at33.880,33.881,
33.881,33.888seconds, CRC `d979ea38`, successful guard, user-client close,
completion and atexit. Runner exit0, zero checked output regions. This is
positive guest-header evidence, not yet a correlated device trace.

The first-eight QEMU trace captured earlier NS6 reads of **1 MiB** each, at
LBA 0,256,...1792, all completed. They must not be attributed to the helper's
one-block operation. Collection stopped before READ2 to fix this evidence gap.
The next immutable QEMU restricts both submission and backend trace filters to
NS6, opcode READ, LBA 0, and nlb 1, still capped at eight matching operations.
All other behavior and the installed helper remain unchanged. Rebuilt/copied
`GPU_HEADER_QEMU2` SHA-256:
`39a9dfcba120c8aa50060b30d9e6e9b2fb4f7978495a60babf763620c956f8d4`.

The separate targeted series is `GPU_HEADER_TARGET1/2/3`, at most three
sequential fresh children. The same 120 s total/30 s guest-progress bound applies;
stop at the first failure or missing correlation. No repeated failed trial.
Use `/tmp/dvm/GPU_HEADER_TARGET1.manifest.json`, preserving the same installed
child, helper, DT, TC, BootKC, SPTM/TXM and model environment except trace filter.
Concurrent unrelated host VMs are recorded and left untouched; timings from
these instrumented boots are not a performance benchmark.

## Targeted results and interpretation

Pinned targeted manifest SHA-256:
`9def9dc1098777219d70dead667363b671a4fe869292d1aae990c78cacb28d34`.
Signed helper SHA-256:
`a9dbef18e7bbd4904f2610bad4610bb39598164eeea6838f482eb70a1da1693a`.
Installation reached the restore shell, emitted `GPU_LOAD_INSTALLED`, and had
524 serial lines with zero kernel panics. No shared QEMU executable was rebuilt.

| Run | Exit | Observed result |
|---|---|---|
| GPU_HEADER_READ1 | 0 | Guest read and CRC succeed (`d979ea38`); trace limit consumed by earlier 1 MiB reads, so device correlation is incomplete |
| GPU_HEADER_TARGET1 | 0 | Stages1–4, 4096-byte CRC `4929b362`, guard, close and completion; one compatible NS6 LBA 0/NLB 1 device chain succeeds |
| GPU_HEADER_TARGET2 | 1 | Stops at 53.046 s after 30 s without helper progress; last helper operation reported was IORegistry enumeration, before user-client open or read-entry stage |

TARGET1's host serial receipt timestamps are stage 1=21.938s, stage 2/3=21.939s,
stage 4=22.493s. Its QEMU trace has CID 1, opcode 2, LBA 0/NLB 1, backend offset 0, length 4096,
backend return 0, data-DMA success, CQE status 0/DMA success, pending unmasked IRQ,
and CQ head 21→22. The guest checksum proves its read returned matching data. Separately, the
device trace proves a compatible one-block operation completed. Device records do not contain a caller PID: they are consistent
with this call but are not an independently PID-tagged kernel trace.

Within QEMU's own clock, the logged backend entry→return interval is 0.025 ms,
and submission→CQE is 0.055 ms. The stage 3→4 **host serial receipt gap** is 554 ms.
These include instrumentation, scheduling and logging effects; do not subtract
them to assign an exact guest/kernel delay, call them a clean read-latency
measurement, or infer GPU/CPU speedup. Python monotonic time uses
`mach_absolute_time`, while QEMU's `get_clock()` uses `CLOCK_MONOTONIC`
(`include/qemu/timer.h:838..846`). A host sample found their origins differing
by approximately 11.154 s. Raw timestamps across those clocks cannot be directly
compared; the collector only computes differences within a single clock domain.

TARGET2 emitted stage 0 at 14.074 s and one stage 0 heartbeat at 21.740 s. Its final
helper record at 22.851 s reports the `Leaf` property while enumerating IOMedia.
There are no stage 1–4, user-client-open, metadata, header, or atexit records.
The continuously drained serial log continued to receive IOMFB power messages.
The absence of later heartbeat does not distinguish a process exit, stalled
stdio/console path, or scheduling/other blockage. No stack or corpse was
captured, so the exact cause remains unproven.

TARGET2 also has a completed QEMU NS6 one-block read (CID 19). It cannot be
attributed to this helper: the helper never reached its read-entry stage.
The serial stream independently names `DumpPanic` while it opens an NVMe
namespace user client; that identifies another client, not proof it issued
this CID. Thus **matching namespace/LBA/length alone is insufficient caller
attribution**. This is why the guest markers and result oracle are required.
No TARGET3 was run. The stop rule was enforced rather than retrying the failure.

Parent replay validated TARGET1's checksum, complete non-header raw-file
contents, ordered guest stages and compatible device completion chain. It
rejected READ1's incomplete trace and TARGET2's failed trial. The runner checks
zero request/reply/gate/bulk-output regions; independent raw checks agreed.
All three owned QEMU PIDs were reaped. Post-run checks revalidated all 26 backing
files and 7 QEMU inputs, preserving the migrated baseline and exact firmware.

**Proven in scope:** the instrumented guest can complete the initial4 KiB read
and the modeled device can complete a matching one-block operation. **Unresolved:**
the earlier post-capacity stall was not reproduced, and this series instead
captured a pre-open enumeration/reporting failure. No fix, post-Home reliability,
latency improvement, checkpoint safety or GPU acceleration is claimed.

The smallest next probe should remove unrelated class/property inventory from
the diagnostic, retaining the exact NS6 class/path/uniqueness and capacity guards.
It should capture a bounded helper stack/lifecycle witness if progress stops,
and distinguish initialization after UI readiness from an early-start helper.
The extra diagnostic thread/logging may itself change scheduling; retain an
uninstrumented control when evaluating any eventual fix. A full GPU plugin is
still premature until this transport startup contract is repeatable.

Reproduce the targeted single trial with an unused tag:

```sh
python3 tools/gpu/run_guest_load.py /tmp/dvm/GPU_HEADER_TARGET1.manifest.json --expected-manifest-sha256 9def9dc1098777219d70dead667363b671a4fe869292d1aae990c78cacb28d34 --aux-header-only --seconds 120 --tag GPU_HEADER_TARGET_NEW
python3 tools/gpu/collect_header_probe.py /tmp/dvm/GPU_HEADER_TARGET_NEW --output /tmp/dvm/NEW_HEADER_REVIEW.json
```

Durable records: `~/dvm-artifacts/research/gpu-header-ios27-20260905` contains
commands/exits, serial/model logs, screenshots, raw auxiliary files, signed
helper/TC, both rebuilt QEMU executables, source snapshots and SHA indexes.
System/Data disks and RAM are excluded; the manifests retain their lineage.

The subsequent [guest IOSurface demo](gpu-surface-demo-ios27.md) removes the
broad inventory and sends actual exact-guest shader work over NS6. Its final
run verifies three host-Metal results copied into a persistent guest surface;
the report keeps that proof separate from display adoption and speedup.
