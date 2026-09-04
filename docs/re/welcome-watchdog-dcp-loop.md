# Welcome launch watchdog and DCP restart-loop result

Run `ROOT_WELCOME_WATCHDOG2` on 2026-09-04 used the persistent NVMe parent,
a fresh child overlay, the first-surface observer, and three opt-in diagnostic
changes: SpringBoard's positive FrontBoard watchdog durations were raised to
600 seconds, SpringBoard's switcher-layout assertion branch was skipped, and
an uncommitted DCP probe injected firmware state 4 after every observed AP
state-3 transition. The QEMU state-4 probe has been removed; the run remains
useful because it separates a real Setup launch from the eventual DCP failure.

The preserved artifacts are:

```
~/dvm-artifacts/probe-logs/ROOT_WELCOME_WATCHDOG2_CONT.lldb.log
~/dvm-artifacts/probe-logs/ROOT_WELCOME_WATCHDOG2.serial.log
~/dvm-artifacts/probe-logs/ROOT_WELCOME_WATCHDOG2.stderr.log
```

## Positive result: real Setup reaches UIApplicationMain

`tools/re/frontboard_watchdog_callbacks.py` observes the final positive
duration load in
`-[FBApplicationProcessWatchdogPolicy watchdogPolicyForProcess:eventContext:]`
(static `0x1c49eb354`). It is read-only unless
`DVM_EXTEND_FRONTBOARD_WATCHDOGS=1` is set; in that mode it changes the
provision builder's double at `+0x18`. The boundary runs in SpringBoard and is
not Setup-specific, so it scales every positive provision SpringBoard builds.

The callback wrote four relevant provisions before Setup launch: 20, 30, 20,
and 10 seconds became 600 seconds at LLDB log lines 318, 320, 325, and 329.
Setup then entered `_UIApplicationMain` at lines 327-328:

```
TRACE_JSON {"label":"UIAPPLICATION_MAIN", ... "progname":"Setup", ...}
WELCOME_UIAPPLICATION_MAIN progname=Setup ...
```

This proves the guest launched the real `/Applications/Setup.app/Setup`
process far enough to enter UIKit under those diagnostic conditions. It does
not prove that the watchdog extension alone caused the launch: the same run
also skipped SpringBoard's switcher-layout assertion at LLDB lines 321-324,
and both interventions must be separated with checkpoint replays. It does not
prove scene connection or any rendered pixels.

The Setup executable's runtime return address from `_UIApplicationMain` was
`0x100631d74`; subtracting its static return `0x100005d74` gives image slide
`0x62c000`. The seven bounded BuddySceneDelegate checkpoints were installed
at LLDB lines 346-353. None fired before the later DCP failure. Because they
were installed after Setup had entered `_UIApplicationMain`, this is not
valid evidence that UIKit never called the delegate. The FrontBoardServices
scene-pipeline checkpoints at lines 614-622 were also installed late and have
the same limitation. Install both from process launch, or restore a checkpoint
before scene creation, before treating an absent hit as evidence.

No `NONBLACK_IOSURFACE` event was produced. The capture path in
`tools/re/first_surface_callbacks.py` is ready to decode IOSurface metadata,
copy at most 64 MiB from guest memory, calculate pixel statistics, and stop on
a non-black surface. `tools/re/iosurface_to_ppm.py` converts a captured raw
surface to a PPM after validating its SHA-256 and geometry. Neither path has
yet been positively exercised by Setup.

## Negative result: unconditional state 4 is a restart loop

The discarded QEMU probe echoed AP short state-2/state-3 frames and, after
each state 3, sent a 12-byte firmware state-4 frame to all eleven DCP AFK
endpoints. One early state-4 sequence had previously released the blocking
AppleFirmwareKit transition, but repeating it for every later transition is
not a protocol implementation.

In this run it produced:

| Witness | Count |
|---|---:|
| DCP coprocessor handshakes | 485 |
| all-endpoint state-4 sends | 5,390 (490 bursts x 11 endpoints) |
| completed display power-off transitions | 485 |
| completed display power-on transitions | 484 |

The first state-4 burst begins at device-model log line 1881. The last burst
ends at line 436009. The serial log then records the first real panic at line
232934, immediately after the final power transition at line 232933:

```
panic(cpu 0 caller 0xfffffff02a7e4e98): DCP NMI: DCPEndpoint11:
send msg=0x851c001c801c00 error=0xe00002d7
```

The later identical panic text is nested panic-printer output and is not a
second root cause. The state-4 injection repeatedly restarts the DCP state
machine, eventually overruns or wedges endpoint 11, and must not be used as a
general wake completion. `qemu-sptm/hw/arm/darwin_dcp.c` was restored to its
pre-probe contents before this record was committed.

## Checkpoint-oriented next experiment

The next display experiment should restore before SpringBoard creates Setup's
scene, then install all of these observers before continuing:

1. `frontboard_watchdog_callbacks.py`, with the 600-second extension enabled;
2. `scene_pipeline_callbacks.py` for the shared-cache scene boundaries;
3. `setup_scene_callbacks.py`, after deriving Setup's image slide from its
   `_UIApplicationMain` return address; and
4. `first_surface_callbacks.py`, stopping on `NONBLACK_IOSURFACE`.

If a process appears idle, `setup_stall_callbacks.py` can sample unique
threads at `mach_msg2_trap` without scanning guest RAM. Set its module-level
`TARGET` before `install()` when sampling a process other than Setup.

Do not reintroduce a state-4 reply until a frozen transition supplies its
endpoint, tag, requested state, generation, and exact expected response. A
valid change must be bounded to that transaction and prove the state-machine
return without creating another DCP handshake. Stop immediately on a second
handshake, a repeated power cycle, or the first `panic(cpu` line. This turns
the former long-running loop into a checkpoint replay measured in seconds.

The run used diagnostic guest-memory/register writes, so it is not a storage
or display regression result. No QEMU device-model behavior from this run is
committed.
