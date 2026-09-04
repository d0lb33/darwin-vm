# External checkpoint and restore

This implementation pairs a QEMU migration stream with one exact, immutable
ANS qcow2 generation.  It deliberately does not use internal qcow2 snapshots:
the source QEMU is paused, the `ans` backend is explicitly flushed, RAM and
device state are written to a migration file, and the verified source process
is terminated.  Every restore gets a new writable qcow2 child above the
read-only checkpoint disk.

The starting revisions for this work are:

- superproject: `8e97927d57a2343102aa7d886666023518cf4f25`
- `qemu-sptm`: `b284c666f7fd582e090dd33e1ae104f0e3eeba33`

Development is isolated on `codex/resumable-checkpoints-super` and
`codex/resumable-checkpoints-qemu`.  The dirty main checkout and its QEMU
processes are not inputs to this work.

The starting binary identifies itself as QEMU 11.1.0.  Its command-line help
explicitly advertises `-incoming file:filename`, `-incoming defer`, and
`-dump-vmstate`; its HMP table supplies asynchronous `migrate -d`,
`info migrate`, and `migrate_set_parameter`.  The implementation therefore
uses the supported external file migration transport rather than `savevm`.
The owner-authorized build's dump is
`/tmp/dvm/ckpt-aicasc-vmstate-inventory.json` (SHA-256
`7f19e5e8b2a1f2b7dc523710cf31b2d8524675aedd766885c54925cd8bd8811f`).
It contains the QOM sections for AIC, ASC, SEP, ANS, DART, SART, framebuffer,
and AMCC.  AFK, DCP, IOMFB (when configured), and sparse-unimplemented state
are registered non-QOM sections and do not appear in this type-schema dump;
instantiated sections do appear in migration analysis.

## Security model

`vmstate.bin` can contain guest memory, Apple implementation-defined CPU
registers, and SEP/SKS state.  Treat the entire checkpoint directory as secret
and local.  The creator makes the directory mode `0700`, the migration stream
mode `0600`, and the frozen disk mode `0444`.  Do not publish these artifacts.

QEMU virtual time is stopped while no process exists.  Restore starts with
`-S`; execution resumes only after the incoming stream has loaded and the
captured PC matches, so host wall time between create and restore is not
delivered to guest virtual timers as one large jump.

## Launch and create

Start from a disposable qcow2 child, never an established seed parent.  Give
the run a unique tag, monitor, UART, serial log, disk, and GDB port.  The setup
probe can record the exact QEMU argument vector and relevant model environment:

```sh
TAG=CKPT_EARLY_1
CHECKPOINT_LAUNCH_MANIFEST=/tmp/dvm/${TAG}.launch.json \
  tools/re/setup_gate_probe.sh "$TAG"
```

Use a condition-bounded probe from `CLAUDE.md` to stop at the chosen marker.
Then create the checkpoint while that exact QEMU PID still exists:

```sh
python3 tools/create_checkpoint.py \
  --tag "$TAG" \
  --monitor "/tmp/dvm/${TAG}.sock" \
  --pid-file "/tmp/dvm/${TAG}.qemu.pid" \
  --launch-manifest "/tmp/dvm/${TAG}.launch.json" \
  --disk "/tmp/dvm/data-seed/${TAG}.qcow2" \
  --serial-log "/tmp/dvm/probe/${TAG}.serial.log" \
  --marker-regex 'Early boot complete|UIApplicationMain'
```

The creator refuses a stale PID, a non-QEMU process, an argv mismatch, or a
monitor/disk not owned by the recorded process.  It captures registers,
`info qtree`, `info mtree`, `info cpus`, and `info block`; flushes `ans`; waits
for completed migration; quits only the verified PID; checks the qcow2; hashes
the stream, every qcow2/raw backing-chain member, QEMU, and immutable boot
inputs; and writes `manifest.json`.

## Restore twice

Each invocation creates a fresh child disk and unique sockets.  It verifies all
input hashes, starts a new QEMU with `-incoming file:...`, waits for incoming
migration to finish in the paused state, requires an exact PC match, resumes,
observes for 120 seconds by default, samples post-resume state, and leaves that
specific guest running:

```sh
MANIFEST=/tmp/dvm/checkpoints/CKPT_EARLY_1/manifest.json

python3 tools/restore_checkpoint.py "$MANIFEST" \
  --tag CKPT_EARLY_1_R1 --gdb-port 1261 --observe-seconds 120

# After terminating R1 by its exact PID, restore the same immutable checkpoint.
python3 tools/restore_checkpoint.py "$MANIFEST" \
  --tag CKPT_EARLY_1_R2 --gdb-port 1262 --observe-seconds 120
```

The restore report records source/restored/post-observation PCs, time to load,
RSS, serial bytes, repeated boot signatures, first `panic(cpu`, model traffic
counts, the continued leading firmware trace clock (when present), the new PID,
and the unique writable disk.  The source migration stream
and frozen disk hashes provide one checkpoint witness; completed migration plus
an exact pre-resume PC match in a different PID provide an independent restore
witness.

For the traffic-heavy final run, launch with `DARWIN_ANS_DEBUG=1` and
`DARWIN_SKS_REQUEST_DEBUG=1` in addition to the established DCP/IOMFB tracing.
The restore report counts ANS reads and writes, completed SKS replies, DCP/AFK
messages, and IOMFB messages only from stderr bytes written after `cont`; model
initialization before incoming migration cannot satisfy those witnesses.

## Device coverage

`checkpoint-device-inventory.json` is the field-level inventory for CPU/SPTM,
AIC, ASC/RTKit, SEP/SKS, ANS/NVMe, DART, SART, DCP/IOMFB/AFK, framebuffer,
timers, interrupts, UART, AMCC, and sparse unimplemented-register state.

The qemu-sptm implementation covers every listed entry.  AIC and ASC support
was applied after explicit owner authorization in QEMU commit `60e1fd0`: it
serializes pending/masked AIC interrupts and ASC mailbox/RTKit endpoint state,
validates bounds, migrates the optional virtual timer, and reasserts
destination-process IRQ lines in `post_load`.

## Test ladder and acceptance evidence

Run these checkpoints in order, retaining each manifest and restore report:

1. minimal CPU/RAM control;
2. early launchd;
3. encrypted Data/User volume mounted;
4. `Early boot complete`;
5. SpringBoard entering `UIApplicationMain`;
6. recent or active DCP, SEP, and ANS traffic.

For the final checkpoint, keep both independent restores alive for at least
120 seconds.  Confirm no repeated kernel/launchd banner, stable guest process
identity, plausible continued uptime, post-resume serial output, ANS reads and
writes, completed SEP/SKS requests, DCP/IOMFB callbacks and interrupts, and no
first `panic(cpu` or SPTM panic.  Finish with one ordinary cold-boot regression.
The tooling collects evidence but does not convert a migration command's exit
status into a claim that these guest-level checks passed.

## 2026-09-04 runtime results

The bounded control ladder includes both the expected failure before AIC/ASC
support and successful new-process restores after that support was applied:

| Checkpoint | Boundary | Create evidence | Restore evidence | Result |
|---|---|---|---|---|
| `CKPT_CPU_CTRL7` | prelaunch CPU/RAM control using the final allowed-file binary | PC `0x100070a0388`; 414,830,884-byte stream; 1.535 s migration; source PID 61605 exited | PIDs 61787 and 61933 each loaded the exact PC from a fresh disk child, advanced into XNU, emitted 51,129/51,153 new serial bytes, and completed SKS traffic without XNU/SPTM panic in the 15 s control window | control passed; the repeated bootstrap banner is expected because the checkpoint precedes boot |
| `CKPT_EARLY_BOOT2` | after `Early boot complete` and a live SEP/SKS request | PC `0xfffffff02ac72da8`; 1,117,096,894-byte stream; 1.024 s migration; source PID 58979 exited | PID 59233 loaded the exact PC, advanced, emitted new serial output, and did not repeat a boot banner | failed after resume: SKS timeout strikes followed by an IONVMeFamily panic at 27 s |
| `CKPT_AICASC_EARLY2` | after `Early boot complete`, using the migrated Data/User parent and the owner-authorized AIC/ASC build | PC `0xfffffff00709a610`; 663,051,921-byte stream; 1.530 s migration; source PID 65586 exited | PIDs 66009 and 67448 each loaded the exact PC in 0.940/0.941 s from independent fresh disk children and ran 120 s; both showed execution and serial progress, ANS reads/writes, SKS replies, DCP/IOMFB traffic, no repeated boot banner, and no XNU/SPTM panic | passed twice; this directly fixes the prior 27 s failure |
| `CKPT_SB_STAGE1_PROGRESS` | 14+ minutes after restoring the early checkpoint, while Data-volume metadata and SEP traffic were still progressing but before `SB_ADFL_ENTRY` | PC `0xfffffff02aaf76e4`; 3,542,058,441-byte stream; 1.537 s migration; source PID 68274 exited | PID 75975 loaded the exact PC in 1.053 s, advanced for 60 s, emitted 8,318 serial bytes, completed ANS and SKS traffic, and showed no repeated boot banner or XNU/SPTM panic | resumable investigation point, not SpringBoard acceptance evidence |

The first post-resume request printed by the failed pre-AIC/ASC restore was an
already submitted QID 1 NVMe read.  ANS queue indices, disk generation, and
controller state were present in the stream, but the AIC and generic ASC were
absent from `-dump-vmstate`.  The destination therefore lost the pending AIC
delivery and RTKit mailbox state: the NVMe request timed out and SKS accumulated
timeout strikes.  The two `CKPT_AICASC_EARLY2` restores show that preserving
those devices removes that failure; it was not an ANS completion bug.

The progressed SpringBoard-stage run did not hit `SB_ADFL_ENTRY` within its
bounded condition windows.  It also did not reproduce the other display
experiment's runaway DCP restart loop: there was no repeated coprocessor
handshake/power-cycle storm and no endpoint-11 DCP NMI.  This is deliberately
not a claim that the late DCP power path is fixed.  The main checkout has a
separate uncommitted ASC power-transition experiment; it was not copied into
this isolated branch.  `CKPT_SB_STAGE1_PROGRESS` exists so that work can resume
at this exact state after the two checkpoint commits are integrated.

`COLD_REG_CKPT1` was an ordinary 30-second cold boot from a fresh qcow2 child
after the general device-state implementation.  It produced 634 serial lines,
reached the normal early-boot region, and had zero XNU/SPTM panics.  The AIC/ASC
build then independently cold-booted to `Early boot complete` while creating
`CKPT_AICASC_EARLY2`.

The development-only manifest
`/tmp/dvm/CKPT_EARLY_BOOT1.development-bridge.json` was used solely to carry an
older early-boot state across one validator-only rebuild.  It is labeled
`not_acceptance_evidence` and is not one of the results above.
