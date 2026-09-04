# Multicore / CPU-cache handoff, 2026-09-04

## Latest result and what to pull

**The warm-boot SKS blocker below is fixed** in QEMU commit `480870d` on
`origin/codex/multicpu-warm-display`. The same branch in the parent repository
records that submodule revision, the reusable warm-boot probe and the evidence.
Fetch it in both repositories:

```sh
git fetch origin codex/multicpu-warm-display
git -C qemu-sptm fetch origin codex/multicpu-warm-display
```

Merge into saved display work in a separate checkout, preserving any newer
display fixes. If the display agent already integrated the CPU-only branch
and wants only the new SKS change, cherry-pick **QEMU `480870d` inside
qemu-sptm**, then record the resulting submodule revision in its parent repo.
This avoids reimporting our earlier snapshot of its display changes.

The fix accepts the captured **Data protection-class 2-to-3 transfer**, keeping
the existing request-format and volume checks. All 19 SKS tests and 18 host
tests pass. A fresh six-CPU boot accepted the request at **12.56 seconds**,
completed 40 such transfers and ran for **300 seconds with zero kernel panics
or SKS timeout strikes**. DCP handshakes began at 86.03 seconds. The remaining
visible behavior was repeated display power cycling and a black framebuffer;
Setup migration completion and a rendered welcome screen are not established.
See [capture, fix and runtime evidence](../re/sks-data-class2-transfer.md).

For reference, before the fix, one matched warm-boot run per CPU count with
the cache enabled reached the rejection at **15.00 seconds (one CPU)** versus
**12.45 seconds (six CPUs)**. This short boundary is not a full-boot benchmark.
Evidence: `/tmp/dvm/SMP_WARM_TIMING_1V6/results.json`.

## Branches

Both repositories use `origin` on GitHub under `d0lb33`:

| Branch in darwin-vm and qemu-sptm | Contents |
| --- | --- |
| `codex/multicpu` | CPU work only: experimental 2–6 CPUs, WFE event-stream fix, PAuth mask cache, and fast-build/probe tools. Parent `24ad2da`, QEMU `d71bf46`. Does not include the new SKS fix. |
| `codex/multicpu-warm-display` | CPU work plus the display-source snapshot, new SKS fix `480870d`, warm-boot probe and latest evidence. This is an integration/test branch, not a replacement for newer display work. |

Fetch the desired branch in **both** repositories. Preserve/commit the display
agent's own work before merging, resolve the QEMU merge in its own repository,
and record the resulting submodule commit in the parent. Do not reset its
working tree or rebuild the executable used by its running instance.

The integration snapshot includes DCP AFK state acknowledgements, SEP/SKS
DER/class/key-transfer fixes, incoming run-state normalization and extra ARM
timer restoration. It includes the untracked AFK header/test. It does not copy
the display agent's unrelated parent-repository tools, assets or documentation.
Those snapshot changes were imported as-is. The subsequent Data 2-to-3 fix
was implemented and validated separately after the user's explicit request.

## Running six CPUs with the cache

In an isolated checkout with firmware available:

```sh
tools/build_qemu_fast.sh
python3 tools/run_smp.py --fast --cpus 6
```

This launches the restore shell. For the display agent's **system-disk** boot,
prepare the separate, hash-checked kernel first:

```sh
python3 tools/re/smp_pv_patch.py firmware/bootkc /tmp/dvm/SMP_PV.bootkc
```

Keep the display boot's existing disk, device tree, trustcache, boot arguments
and DCP environment. Make these substitutions/additions:

- executable: `qemu-sptm/build-fast/qemu-system-aarch64`;
- environment: `DARWIN_SMP_PV=1`;
- QEMU arguments: `-smp 6 -accel tcg,thread=multi`;
- kernel: `-bootkc /tmp/dvm/SMP_PV.bootkc`.

For `tools/probe.sh`, set `DVM_QEMU` to the fast executable, use its
`--bootkc` option, and place `-smp 6 -accel tcg,thread=multi` after `--`.
`setup_gate_probe.sh` does not itself expose a CPU-count option.

The PAuth mask cache defaults **on** for the Apple CPU model.
`DARWIN_PAUTH_CACHE=off` disables mask reuse; `verify` compares each cached
result against the original calculation and asserts equality. Do not benchmark
in verify mode. Cache-off still retains the independent unused-work removal
and compiler optimizations. See [performance evidence](../re/arm-tcg-performance.md).
The combined changes reduced a partial migration sample's median elapsed time
by 14%; this is not a full-boot speedup or a six-versus-one-core measurement.

Use a fresh writable qcow2 child and unique monitor/UART/GDB/QMP endpoints.
Cold boot to initialize six CPUs; do not restore a one-CPU RAM checkpoint with
`-smp 6`. Multicore checkpoint restore, suspend and hotplug remain unvalidated.
The code retains portable TCG paths; Windows was not tested here.

## Historical control before the SKS fix

Parent: `/tmp/dvm/data-seed/display-warm1.qcow2`, SHA256
`07631b3182f9bc6106975bb5e4f9adcf356256ccfb6f9e330d7190c889115c5d`.

`SMP_WARM_DISPLAY6_1` reached Early boot complete, with no `set_dir_stats`
attribution pass or kernel panic before stopping. It then rejected:

```text
sep(SEP): sks op0f rejected unsupported migration shape: request 164 header 0x48 version 1 variant 3 class 3 record length 0 output capacity 0 output scalar 0; no reply
```

`SMP_WARM_CONTROL1_1` used the identical build/display environment and a fresh
child of the same parent, **one CPU, the stock kernel and PAuth cache off**.
It reached Early boot complete at 9.82 host seconds and the identical rejection
at 14.24 seconds. This reproduces the blocker without multicore or mask reuse;
it does not independently test every compiler/CPU change. The six-core log
also records timeout strikes 0 and 1 before the probe froze it. No device-model
fix was attempted in these initial controls. The later fix and positive boot
are recorded at the top of this page. No Setup welcome screen was established.

Evidence: `/tmp/dvm/SMP_WARM_DISPLAY6_1/{comparison,source-snapshot,launch}.json`,
its serial/stderr logs, and `/tmp/dvm/SMP_WARM_CONTROL1_1/{result,launch}.json`
with matching logs. The source snapshot's tracked patch SHA256 is
`e89ec99aa611a3be5d50816d707d9b5ff72f085013df2dddb9659f261c927b1e`.
Both imported QEMU test suites passed (2 AFK and 17 SKS subtests), and the fast
build retained assertions. Backing-chain sizes/mtimes and the warm parent hash
were unchanged after both probes. Both test processes have exited; their disks
and logs remain. The original display instance was never controlled or edited.
