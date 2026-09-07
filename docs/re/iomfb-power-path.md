# IOMFB display power path and the DCP sleep/wake loop

2026-09-06. Why the DCP model was put to sleep and rebooted every 2.02 s on
the PMGR system-disk boot, which RPCs carry the display power state, and
what the model now answers. Addresses are unslid (runtime - 0x20000000)
in the 24A5430a kernelcache; runs are `/tmp/dvm/SYS_PWRBT1..5` and
`/tmp/dvm/SYS_PWR_A484_*`, all booted with
`tools/re/manifest_probe.sh` from the PMGR manifest, stock kernel, six
CPUs, with `tools/re/qemu_stderr_ts.sh` stamping host seconds on every
model line and `tools/re/gdb_bp_dump.py --bt` for the backtraces.

## The loop, measured

One cycle from `SYS_PWRBT3.stderr.log` (times in seconds since launch):

```
196.101 iomfb: RPC 'A500' in 4 out 0
196.106 dcp: state ep 0x2a..0x20 request 2 ack 3      (11 endpoints)
196.112 asc(DCP): AP requests IOP power state 0x201
196.112 asc(DCP): AP stopped the IOP
196.114 asc(DCP): RTKit handshake complete             (new HELLO)
196.116 dcp: state ep 0x20..0x2a request 4 ack 5
196.125 iomfb: RPC 'A385' in 0 out 4                   (then ~240 more)
198.122 iomfb: RPC 'A500'                              (next cycle)
```

The period is 2.02 s. Sleep to running again takes 20 ms; no AFK ring is
re-initialised and no EPIC service is re-announced (16 announces and 11
`INIT`s in a 480 s run of 110 cycles), so the endpoint state already
survives the restart. The cost of the loop is not the restart; it is what
the AP believes in between.

## Who sleeps it: AppleDCP's timer, not IOKit's idle timer

Breakpoint at the A500 send site `0xa0d6654` (inside
`DCPPowerManager::set_power_state`, `0xa0d6338`), frame-pointer walk:

```
IOMobileGraphicsFamily-DCP  0xa0d6654   set_power_state -> A500
AppleDCP                    0x89f4460   AppleDCPExpert power callback
kernel                      0xb17a3b8
AppleDCP                    0x89f4098 / 0x89f3cf4 / 0x89f3bf0
kernel                      0xb23f784   IOCommandGate::runAction
AppleDCP                    0x89f587c / 0x89f4218
AppleDCP                    0x89ef6f8   DCPEndpointV2::setPowerState(0, ..)
kernel                      0xb217ae8 / 0xb2175e4   thread_call
```

and for the first cycle of every run the frame above `0x89f4218` is
`0x89f2df0`, a tiny handler at `0x89f2dac`: it calls the expert's
`vtable+0x548(0, self)` (release the DCP power assertion; the acquire
form is `(1, self)` at `0x89ef62c`), sets the `BootComplete` property to 1
(`0x89f2e04`, string `0x73aeab4`) and stores 1 at `+0x260`. It is reached
through the thunk `0x89f1a4c` that `0x89f08c4` installs as a timer action
during AppleDCPExpert start. `DCPEndpointV2::setPowerState` itself
carries the strings `"%s power transition defered!"` and
`"%s %p failed release assertion"` (`0x73adcf5`, `0x73add13`).

So: AppleDCP holds the DCP awake during boot and drops that hold on a
timer. On this guest that timer fired 10 s after the first display
transition and every 2.02 s afterwards, each time finding no other
holder, so IOKit PM powered the DCP endpoint down, `set_power_state`
sent A500 and the AP asked RTKit for IOP power state 0x201.

## Who wakes it: the next RPC

Breakpoint at `set_power_state` entry with state 1 (`SYS_PWRBT4` hit 16):

```
0xa0d6338  DCPPowerManager::set_power_state(1)
0xa0d5bd0  DCPPowerManager acquire (0xa0d5cd8 family)
0xa0d4b74  RPC wrapper 0xa0d4ae0: `[x21+0x10]->fn(obj, 1)` before the call
0x917ef94  AppleMobileDispH17P-DCP+0x5cd4
0xa0bc37c  IOMobileFramebufferAP swap-wait, right after its A385 call
0xa0e5dd4  user-client selector 6 (3 scalars)
kernel     is_io_connect_method .. mach_msg
```

Every RPC powers the coprocessor up first if it is off. The RPC that runs
at 60 Hz is A385 from IOMobileFramebufferAP's swap-wait, and that is
driven from userspace: selector 6 of the IOMobileFramebuffer user client
is `kern_SwapWait(fb, swap_id, mode)` in
`IOMobileFramebuffer.framework` (`0x22a3912b4`: `IOConnectCallScalarMethod`
selector 6, inputs `{swap_id, mode, 0}`), and the stops show swap id 0
and mode 3, in pairs 4 ms apart every 17 ms. The dispatch table is at
`0x829a218`, 40-byte entries, selector 6 = `0xa0e5d7c` with 3 scalar
inputs.

## The state the AP consults, and the RPC that sets it

`IOMobileFramebufferAP+0x380` is the display power state. Swap-wait
mode 3 tests it before and after A385 (`0xa0bc3bc`: with it zero the
result is `!A385`, otherwise the call returns `kIOReturnAborted`), the
transition handler `0xa0c6d34` compares it before and after a change
(`0xa0c6ee4`), and it is written by exactly one thing: the A484 stub at
`0xa0cc45c`.

A484 packs 16 input bytes at `sp+0x10`: `u64 state` (the requested
state), `u8 flag_a` (w2), `u8 flag_b` (w3), `u8 flag_c` (w5), `u8 flag_d`
(w6), `u8 (out pointer == NULL)`, three bytes of 0xaa padding; out is 8
bytes at `sp+8`: `u32 state`, which the stub stores through its
`x4` argument into `+0x380` (`0xa0cc4ec-0xa0cc4f0`), and `u32 status`,
converted by the stub at `0xa0dc4a0`. A485 (`0xa0cc50c`) has no input and
a 4-byte output of which the AP keeps bit 0 (`0xa0cc560`). A486..A489
(`0xa0cc574..0xa0cc6bc`) are the rest of the family; A380..A383
(`0xa0cc0c4..0xa0cc40c`) precede it.

Measured on `SYS_PWRBT5` with A484 answered as zeros:

```
0xa0bab68  display power request, x1 = 1 (ON)   t=49.771
           serial: "FB0 Api power state transition request from OFF to ON"
0xa0c6ea8  after the A484 worker: [fb+0x380] = 0, w26 (before) = 1
0xa0c6f98  the retain call vtable+0x958(fb, 1) still ran
           serial: set_device_power on=1 kernelAssertCount=1
                   set_device_power on=0 kernelAssertCount=0
t=62.1     first A500 / 0x201, then every 2.02 s
```

The AP asked for ON, our zeroed A484 told it the display ended up OFF,
and from then on nothing in IOMFB held the DCP: the AppleDCP timer's
release was the last assertion. Userspace kept polling `SwapWait(0, 3)`
because with `+0x380 == 0` the wait cannot succeed, and each poll
rebooted the coprocessor.

A500 is `DCPPowerManager::set_power_state`'s notification to the
firmware, `0xa0d41ec`: in 4 = `{u8 (state == 1), u8 changed, u16 0xaaaa}`,
out 0. On this loop it is always `(0, 1)`.

## What the model does now

`darwin_iomfb.c`, `iomfb_power_rpc()`, on by default,
`DARWIN_DCP_IOMFB_POWER=0` restores the zero answers:

- A484: record `in[0..7]` as the display power state, answer
  `{state, 0}`.
- A485: answer the recorded state (1 at boot: iBoot hands over a lit
  panel and the AP's own `+0x380` reads 1 before its first A484).
- A500: record `(on, changed)`; nothing to answer.
- The RPC trace line now prints inputs of 16 bytes or fewer inline.

The state is deliberately not reset when the AP restarts RTKit: the
firmware keeps its display state across IDLE and the AP does not
re-query it (no A485 after a wake in any run).

## Results

`SYS_PWR_A484_1`: same manifest and boot as the loop runs, model with the
power path on, 480 s, no debugger.

| | loop runs (`SYS_A385_1`, 480 s) | `SYS_PWR_A484_1` (480 s) |
|---|---|---|
| IOP power state 0x201 requests | 110 | 2 |
| coprocessor reboots | 110 | 2 |
| A385 RPCs | 23,916 (60 Hz, all boot) | 8,519 (60 Hz until the display is ON at 134 s, then 1-2/s) |
| A500 | 110 | 2 |
| first panic | none | none |

Timeline of the two remaining power-offs, both real:

```
 57.654  A484 display 1 -> 1      IOMFB start, "OFF to ON" request
128.756  A484 display 1 -> 0      backboardd request_power_change powerstate 0
128.757  A500 (on=0, changed=0)
128.768  IOP power state 0x201    DCP sleeps
130.616  coprocessor booted       backboardd asks for ON again
130.633  A484 display 0 -> 1
134.523  first frame presented (6 frames follow at 1-2 s)
142.240  A484 display 1 -> 0      screen off, 8 s after the first frame
142.242  A500 (on=0, changed=0)
142.248  IOP power state 0x201    DCP stays asleep for the rest of the run
```

So the guest now behaves like a phone left alone on its lock screen: it
turns the display off and lets the DCP sleep. The window shows black from
that point (`iomfb: display off, blanked`); input should wake it. That
changes what a fixed-budget probe sees: `iomfb: presented` stops after the
screen sleeps, so a probe that counts frames has to touch the guest or
stop on the first frame.

Still zero-answered and unmeasured: A414 (in 8, out 8), A466, A412 beyond
the existing `01000000` override, A442 (in 92, out 4100), A486..A489,
A380..A383, and A385 itself, whose value only matters while the display
is ON (`0xa0bc3bc-0xa0bc3c8`: display ON and A385 = 1 takes the success
path through `vtable+0x7e8` and notification 0x99; display OFF returns
`kIOReturnAborted` regardless). The remaining 1-2 A385 calls per second
are not yet attributed.
