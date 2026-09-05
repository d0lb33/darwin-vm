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
