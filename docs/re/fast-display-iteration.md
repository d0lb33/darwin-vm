# Fast display iteration

The display probe used to spend most of its wall time waiting for a fixed
timeout and then scanning all 12 GiB of guest RAM. The validated fast path
stops on the observed stalled call, freezes the guest, and scans a 2 GiB
prefix before falling back to the untouched suffix when evidence is missing.

## Validated run

`FASTPROBE_VALIDATE2` completed in about 225 seconds. The device-model timing
record ends at `t=225.257` in `/tmp/dvm/probe/FASTPROBE_VALIDATE2.stderr.log:3395`.
The guest reached `Early boot complete` at serial line 640 in
`/tmp/dvm/probe/FASTPROBE_VALIDATE2.serial.log:640` and had zero XNU panics;
the probe verdict is recorded in `/tmp/dvm/FASTPROBE_VALIDATE2.probe.out`:

```
STOPPED ON CONDITION : selector-deadline selector=0x4f call_id=81 age=30.1s
serial lines : 868
xnu panics   : 0
```

The LLDB event log contains four selector-79 entries and three returns in the
validated run. The unmatched call is call ID 81:

```sh
grep -c 'PROBE_EVENT event=iokit-entry.*selector=0x4f' \
  /tmp/dvm/FASTPROBE_VALIDATE2.lldb.log   # 4
grep -c 'PROBE_EVENT event=iokit-return.*selector=0x4f' \
  /tmp/dvm/FASTPROBE_VALIDATE2.lldb.log   # 3
```

The durable reason is `/tmp/dvm/FASTPROBE_VALIDATE2.stop`; the watcher log is
`/tmp/dvm/FASTPROBE_VALIDATE2.watch.log`.

## Protocol and lifecycle

`display_iokit_callbacks.py:125-135` creates an atomic pending event for the
configured selector, and `:153-160` removes it and records a return. The
callback does not sleep. `probe_watch.py:111-155` watches those events and
writes an atomic stop request after the configured deadline. `probe.sh:174-225`
observes the stop file, stops QEMU, and prints the condition verdict.

`setup_gate_probe.sh:182-201` starts the watcher and `:203-262` drains LLDB,
runs the optional post-mortem, then either leaves the VM frozen (`KEEP_GUEST=1`)
or sends QEMU `quit` (`KEEP_GUEST=0`). For this run, PID 63210 is recorded in
`/tmp/dvm/FASTPROBE_VALIDATE2.qemu.pid`; the empty `ps` result after collection
proved that QEMU exited.

## RAM scan semantics

`ramscan_stall.py:102-149` dumps each physical-RAM chunk once and scans both
thread signatures and kext return addresses from the same mapping. The
post-mortem defaults to a 2 GiB first pass (`stall_postmortem.py:117-124`). It
labels that result `partial-first-pass` (`:158-160`) and scans only the
untouched suffix when fewer than the configured minimum correlated stacks are
found (`:164-173`). Scratch files are isolated under
`/tmp/dvm/ramscan/<TAG>` (`:152-156`).

`FASTPROBE_VALIDATE2` produced five correlated stacks in
`/tmp/dvm/FASTPROBE_VALIDATE2.postmortem.txt`; the raw scan is
`/tmp/dvm/FASTPROBE_VALIDATE2.ramscan.txt` and the thread rows are
`/tmp/dvm/FASTPROBE_VALIDATE2.threads.txt`. The partial result includes
`IOMobileGraphicsFamily-DCP+0x23a24` at post-mortem line 21,
`+0x23b50` at line 185, and `+0x23c7c` at lines 186 and 249.

## Reproduction

Single run:

```sh
CALLBACKS=display_iokit_callbacks SECS=180 \
  tools/re/setup_gate_probe.sh UI_FAST1
```

The established EPIC controls can be run in small parallel batches:

```sh
MAX_PARALLEL=2 tools/re/setup_gate_sweep.sh UI_EPIC off all
```

The sweep assigns tags and GDB ports (`setup_gate_sweep.sh:65-89`); the probe
and post-mortem scripts derive per-tag sockets, logs, overlays, and scan
scratch paths. Do not rebuild QEMU during a sweep, and keep the guest count
within host RAM (`setup_gate_sweep.sh:38-45`).

## Host-only validation

The non-boot regression tests completed successfully:

```sh
python3 -m unittest discover -s tools/tests -v
```

The recorded result in `/tmp/dvm/fast-probe-tests.log` is `Ran 12 tests` and
`OK`. These tests cover event deadline/return races, log-regex stopping,
chunk-boundary overlap, dynamic DRAM discovery, and rejection of stale
`pmemsave` chunks. They do not prove a rendered display frame.
