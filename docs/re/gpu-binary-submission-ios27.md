# Binary submission experiment, exact iOS 27 guest

Baseline is project c4e47e9 / QEMU bf4e43d, exact 24A5430a guest, native SMC,
SPTM/TXM, migrated disk lineage and software rendering. Work is isolated in
`codex/metal-driver-ios27`; no new kernel/QEMU/DT behavior is required.

## Preregistered comparison

Run four fresh disposable disk boots in order JSON A1, binary B1, binary B2,
JSON A2. Each uses eight nonce luma trials after native display presentations,
a stable input PID/epoch for ten seconds and a fresh ACK. The same front-end
bundle, host executor and shader workload build serves both modes. Only the
helper's binary-factory selection differs. The host executor supports both
wire formats. No checkpoint restore or debugger is used; existing checkpoint
rejection remains enabled.

Primary metric: median guest `work_us` including upload/encode through command
completion, with verification afterward. Compare all eight trials, including
first submission, per run. Record submit RPC, doorbell, host-service/GPU duration,
CPU reference, request/reply bytes, per-trial samples and resource retirement.
Eight trials do not establish reliable production p99. A 25% reduction in both
binary-run medians against the mean of the two JSON-run medians is the initial
useful-improvement target, not a claim of CPU crossover or native frame rate.

Stop on any guest oracle mismatch, host GPU failure, handle/slot/session/CRC
violation, nonzero resources after retirement, transport deadline, panic or
native display/input regression. Global deadline is 180 seconds per boot;
existing per-request and progress deadlines remain. A failed trial is retained,
not replaced by an undocumented run. No global Metal registration is enabled.

## Wire contract

`driver_binary.h` defines DVB1, an explicitly little-endian, fixed 28,672-byte
submission snapshot: 32-byte version/sequence header, two 128-byte commands,
three 32-byte upload descriptors, two readback handles, and fixed reusable
frame-relative staging slots. A 96-byte buffer uses offset 512, a 16-byte buffer
offset 640, and the 24,576-byte RGBA16Float texture offset 4096. Descriptors carry
persistent resource handles already allocated in the bounded host object table;
they never contain guest pointers. Commands retain the actual pipeline handles,
bindings, uniform bytes, scratch sizes and dispatch geometry. Existing audited
host validation still runs before any uploads or GPU commits.

The 272-byte DVR1 reply carries status, GPU duration, resource handles and raw
96/16-byte readbacks. All readbacks are validated before publishing guest shadows.
The single in-flight command owns its snapshot until completion. Resource
retirement remains ordered after submissions. These are reusable staging slots,
not new zero-copy Metal allocations. Setup, separate texture reads and retirement
retain JSON compatibility. Binary errors use existing JSON error replies;
malformed framing terminates the isolated worker before execution.

The guest frontend avoids base64 conversion for binary command uniforms,
uploads and readbacks. The MMIO transport avoids JSON for those submissions;
the worker decodes typed records and reuses the existing validated dispatch
implementation. The host peer normalizes generated input bytes to base64 only
for evidence after publishing completion. CRC, memory barriers, sequence/session
checks, notification and polling behavior remain unchanged.

## Reproduction and pinned inputs

These are the original output names; use new names to rerun. Both updates are
siblings of `MMIO_METAL_INSTALL4`, checked against `MMIO_METAL_BUILD4`, and
preserve the existing native launchd cache. The installer uses the ordinary
native-SMC boot configuration; the runtime manifest then selects the already
built MMIO BootKC/DT/QEMU package. No QEMU source changed in this experiment.

```sh
bash tools/gpu/build_driver.sh /tmp/dvm/MMIO_BINARY_BUILD1 --mmio-binary
bash tools/gpu/build_driver.sh /tmp/dvm/MMIO_BINARY_CONTROL_BUILD1 --mmio
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/MMIO_METAL_BUILD4 --build /tmp/dvm/MMIO_BINARY_BUILD1 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/MMIO_METAL_STAGE4/system.tc --out /tmp/dvm/MMIO_BINARY_STAGE1
python3 tools/gpu/prepare_driver_update.py --before-build /tmp/dvm/MMIO_METAL_BUILD4 --build /tmp/dvm/MMIO_BINARY_CONTROL_BUILD1 --cache /tmp/dvm/METAL_DRIVER_SMC_INSTALL1/payload/nb-launchd-after --system-tc /tmp/dvm/MMIO_METAL_STAGE4/system.tc --out /tmp/dvm/MMIO_BINARY_CONTROL_STAGE1
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/MMIO_METAL_INSTALL4/warm-manifest.json --stage /tmp/dvm/MMIO_BINARY_CONTROL_STAGE1 --tag MMIO_BINARY_CONTROL_INSTALL1
python3 tools/gpu/run_guest_install.py --manifest /tmp/dvm/MMIO_METAL_INSTALL4/warm-manifest.json --stage /tmp/dvm/MMIO_BINARY_STAGE1 --tag MMIO_BINARY_INSTALL1
python3 tools/gpu/prepare_mmio_manifest.py /tmp/dvm/MMIO_BINARY_CONTROL_INSTALL1/warm-manifest.json /tmp/dvm/MMIO_BOOT_BUILD4 /tmp/dvm/MMIO_BINARY_CONTROL_INSTALL1/mmio-manifest.json
python3 tools/gpu/prepare_mmio_manifest.py /tmp/dvm/MMIO_BINARY_INSTALL1/warm-manifest.json /tmp/dvm/MMIO_BOOT_BUILD4 /tmp/dvm/MMIO_BINARY_INSTALL1/mmio-manifest.json
python3 tools/gpu/run_binary_comparison.py --control-manifest /tmp/dvm/MMIO_BINARY_CONTROL_INSTALL1/mmio-manifest.json --binary-manifest /tmp/dvm/MMIO_BINARY_INSTALL1/mmio-manifest.json --control-build /tmp/dvm/MMIO_BINARY_CONTROL_BUILD1 --binary-build /tmp/dvm/MMIO_BINARY_BUILD1 --tag MMIOBINCMP1
DVM_DRIVER_BUILD=/tmp/dvm/MMIO_BINARY_BUILD1 python3 -m unittest discover -s tools/gpu -p 'test_driver*.py' -v
python3 -m unittest discover -s tools/tests -v
bash -n tools/gpu/build_driver.sh tools/probe.sh tools/re/setup_gate_probe.sh tools/re/setup_gate_sweep.sh
```

The paired binary hashes are recorded in
`/tmp/dvm/mmio-binary-paired-build-hashes.json`:

| Artifact | SHA-256 (same in both unless indicated) |
|---|---|
| DVMProxy bundle executable | `3ebbdd4c6320afe7cd816b6fdc95377061725e66dd0c9544227ee47e996ffc9d` |
| Host worker | `21dc1b0c8c11fabed766a33e85497c3766820a371c392622b471c111fa43a1e2` |
| Workload object | `7287204e30a1679e00b29de1f85ef19d16d681a6bd8e6dd5bb01c9f521c39662` |
| Binary guest helper | `6baeefe92c65d9710378ad897915f23315eba948b9bf35f7c6a94cda48d07cf5` |
| JSON guest helper | `da8ea98e4c052f0c7e71021d5f7b3eb64b53654d5522c3cb6731664fc1e651e3` |

The immutable runtime QEMU is `MMIO_BOOT_BUILD4/qemu-system-aarch64`, SHA
`902c481c6ad5d59d736643a5fb676490b1fde227f4fb0332ffdcbfb6baaf7687`.
BootKC is `ed3ef577af60140ebfca494cdd3ab52ec97f3bddbd5c07a9020263742630df23`;
DT is `79c53207fd921ee6d1f10c7dfa4791fbd28628c347fa717279df4ea1bc642ed4`.
The exact guest QuartzCore AIR remains
`8860e4a17d89783da06429a302db0bc61b2939963f202c0c6ad31189a1021364`.
The install provenance and per-run launch manifests pin the trust cache,
unmodified SPTM/TXM, and complete migrated disk ancestry. Historical restore
arguments in source manifests are removed from the actual fresh-boot launch;
`verify_mmio_metal.py` checks that actual launch.

## Observed results (2026-09-06)

**Proven within this workload:** the binary guest path executes the same exact
QuartzCore AIR through host Metal, with repeatable lower median latency. Four
fresh disk boots passed in preregistered ABBA order. Each verified eight nonce
inputs, six intermediate float4s and the final float4, sixteen actual GPU
dispatches, six persistent host objects reused, and zero objects/bytes after
retirement. Both binary runs also passed independent decoding of all eight raw
request/reply pairs against the host log and shared-RAM audit. No failed or
replacement boot was omitted.

Host: Apple M5 Max, macOS 27.0 build 26A5421a, as recorded in each
`driver-worker.log`. All times below are microseconds. Work includes texture
upload, command encoding, commit, transport, execution, readback and completion;
resource creation/pipeline setup precede it. The CPU reference additionally
includes input generation and half conversion, making it a conservative CPU
comparison (`driver_workload.m`).

| Fresh boot | Format | Work median | Submit RPC median | Doorbell median | Host service median | GPU median | CPU reference median | Work maximum |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A1 | JSON | 5810.500 | 3022.500 | 1130.000 | 840.042 | 25.000 | 529.000 | 35428 |
| B1 | binary | 3547.000 | 1788.000 | 978.000 | 712.229 | 25.000 | 502.000 | 31285 |
| B2 | binary | 3726.000 | 1879.500 | 896.000 | 682.458 | 26.000 | 561.500 | 22822 |
| A2 | JSON | 6474.500 | 2825.500 | 1010.000 | 915.770 | 25.000 | 562.500 | 23118 |

Sources: `/tmp/dvm/MMIOBINCMP1/result.json` and each
`/tmp/dvm/MMIOBINCMP1{A1,B1,B2,A2}/verification.json`, `result.json`,
`driver-host.jsonl`, `driver-audit.jsonl`, `shared-ram.bin`, and `driver-worker.log`.
The per-run verification files retain all eight samples; maxima are real,
including the first submission. In B1, trial 8 also took 13,316 us.

The mean JSON-run median is **6,142.5 us**. Binary B1/B2 are **42.25% / 39.34%
lower**, exceeding the preregistered 25% target in both. This is a four-boot,
eight-sample-per-boot observation; it is not production tail-latency evidence.
The 22.8–31.3 ms binary first submissions remain. Host GPU execution stayed
25–26 us. **CPU crossover is disproven for this exact tiny luma workload**:
the binary full work medians remain roughly 6.6–7.1 times its CPU reference.

For eight hot submissions, binary always carries 229,376 request bytes and
2,176 reply bytes. A1 JSON carries 270,437 request bytes and 1,885 reply bytes;
JSON lengths vary with nonce data and float values. Binary lowers A1 request
bytes about 15.2%, while replies grow because of fixed alignment/padding.
Thus the evidence supports removal of guest encoding overhead as a useful
change; it does not attribute every saved microsecond to a particular routine.
The full RPC counts are 22/22/22/21 in A1/B1/B2/A2. All four still have exactly
eight submissions and six releases; the difference is one additional stats
poll while asynchronous retirement finishes (`driver_probe.m`), outside the
measured eight-submission workload.

Native readiness independently required display presentations, stable input
PID/epoch for ten seconds and a fresh ACK. Final input states remained R;
final screenshots were inspected: A1/B1/B2 show the software-rendered lockscreen;
A2 captures an earlier frame with the clock and bottom icons, without the date
and status bar yet visible. No complete-UI or animation assertion uses A2's image.
These are presentation/input-readiness checks, not gesture latency or accelerated
UI claims. No debugger, NVMe auxiliary namespace or restored RAM participated.
The single NVMe disk was boot storage only. No new kernel, device-tree or QEMU
modifications were introduced; native SMC, original powerd, SPTM/TXM and the
migrated baseline remain pinned by the input manifests.

## Rejections, limits, and next dependency

Actual host-worker negative tests reject an unknown second pipeline handle and
invalid second-dispatch geometry with an error reply, zero submissions and an
unchanged first buffer. Malformed out-of-bounds staging offset `0xfffffff0`,
version 2, and a nonzero reserved command byte each terminate the worker with
exit **3** before dispatch. The Python evidence decoder independently rejects
the invalid slot and reserved-byte cases. These are bounded contract tests,
not a comprehensive adversarial audit. The driver test suite passes 30 tests
(including inherited JSON regression cases); the project suite passes 79.

The format is deliberately limited to the audited 64×48 two-pass luma envelope.
It does not enable arbitrary shaders, render encoders, global device discovery
or Liquid Glass. Guest-to-host copies, CRC over every upload, dictionary-based
frontend encoding, a host worker pipe, and guest completion polling remain.
The host still owns snapshots, not Metal allocations aliased to guest pages.
Live GPU checkpoint/resume remains untested and QEMU migration remains blocked.

The smallest next implementation is a bounded reusable resource arena: register
owned staging ranges once, submit typed offsets/dirty ranges, and retain the
same poison/nonce/release checks. Its unresolved contracts are protection from
writes while the host consumes a slot, completion generations, and reuse only
after retirement. Benchmark it against this binary baseline before extending
to a larger/render-target workload. A CPU win, IOSurface render-target import,
and real render-pass coverage remain separate gates before global acceleration.

## Final checks and retained evidence

After ABBA, all 47 unique pinned disk-parent and firmware/QEMU input hashes
still matched (`mmio-binary-baseline-after.json`). The final source rebuild in
`MMIO_BINARY_FINAL_BUILD1` produces byte-identical DVMProxy, helper, host worker,
and workload object to the measured binary build. `MMIO_BINARY_NVME_BUILD1`
also builds with the default JSON/NVMe option; this compatibility build is not
an additional NVMe guest performance claim. Final 30 driver and 79 project
tests, Python compilation, shell syntax and diff whitespace checks pass.

Durable evidence is retained under
`/Users/jdolbe1/dvm-artifacts/research/gpu-binary-submission-ios27-20260906/`.
`index.json` records every retained file's original path, size and SHA-256.
It includes ABBA plan/results, per-run records/screenshots, owned transport RAM,
binary request/reply captures, installation/build provenance and test outputs.
It excludes disk images, full guest RAM, Apple firmware/libraries and executables.
Use `tools/gpu/preserve_evidence.py --mmio-frames --output NEW_DIRECTORY SOURCES...`
for the same filtered archive; the flag admits only fixed DVB1/DVR1 captures and
the magic/size-checked 16 MiB transport RAM file.
