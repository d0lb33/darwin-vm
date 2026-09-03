# Display-power SMMachine investigation (2026-09-03)

## Result and escalation

This investigation **did not establish an implementation-ready firmware
completion event**.  In particular, it did not establish a message type,
endpoint, payload, or ordering which may safely be added to the DCP model.
The two permitted bounded probes both lost their focused callback path before
it could observe the producer side of the wait.  Therefore an absent focused
hit is not evidence that a corresponding AppleFirmwareKit callback did not
run.  This is an escalation under the display-power handoff's breakpoint
positive-control condition, rather than a reason to guess an AFK or RTKit
packet.

The narrow, testable hypothesis left for the next investigation is that a
DCP-originated asynchronous power/state indication drives the
AppleFirmwareKit client-state callback below.  It is **not** confirmed to be
an AFK/EPIC packet, an IOMFB endpoint-0x37 callback, or an RTKit management
message.  `DARWIN_DCP_EPIC=off` still reproduces the wait, which rules out the
announced EPIC-service proxies as the required producer.

## Reproduction and baseline evidence

The established baseline was retained; no boot was spent merely reproducing
it.

```
CALLBACKS=dcp_sm_machine_callbacks \
GDB_PORT=1260 SECS=180 PROBE_STALL_SELECTOR=0x4f PROBE_STALL_SECS=30 \
AUTO_POSTMORTEM=stall KEEP_GUEST=1 \
tools/re/setup_gate_probe.sh TERRA_DCP_STATE1

CALLBACKS=dcp_sm_machine_callbacks \
GDB_PORT=1260 SECS=180 PROBE_STALL_SELECTOR=0x4f PROBE_STALL_SECS=30 \
AUTO_POSTMORTEM=stall KEEP_GUEST=1 \
tools/re/setup_gate_probe.sh TERRA_DCP_STATE2
```

`FASTPROBE_VALIDATE2` remains the valid blocked-stack capture:

* [`/tmp/dvm/FASTPROBE_VALIDATE2.postmortem.txt`](/tmp/dvm/FASTPROBE_VALIDATE2.postmortem.txt)
  lines 167--185 show the display-off work-loop owner through
  `AppleFirmwareKit+0x3102c`, `+0x30d38`, `+0x26ea8`, `+0x26f6c`, and
  `+0x4937c`, then RTBuddy, AppleDCP, and IOMobileGraphicsFamily-DCP.
* The same file, lines 248--252, shows the separate backboardd caller parked
  below `IOMobileGraphicsFamily-DCP+0x23abc`; this is the selector-79
  `GetBlock` waiter behind the display-power object's recursive lock.
* [`/tmp/dvm/probe/FASTPROBE_VALIDATE2.stderr.log`](/tmp/dvm/probe/FASTPROBE_VALIDATE2.stderr.log)
  lines 2741--2747 show the final endpoint-`0x37` request, `A500`, with the
  four-byte body `00 00 aa aa`, followed by the model's status-zero class-2
  completion.  This confirms that a successful A500 reply alone does not
  finish the transition.

The new bounded run reached the same point without an XNU panic:

* [`/tmp/dvm/probe/TERRA_DCP_STATE2.stderr.log`](/tmp/dvm/probe/TERRA_DCP_STATE2.stderr.log)
  lines 2589--2594 record `A500` and the status-zero class-2 completion.
* [`/tmp/dvm/TERRA_DCP_STATE2.lldb.log`](/tmp/dvm/TERRA_DCP_STATE2.lldb.log)
  lines 107--114 prove the four focused breakpoints were installed at their
  runtime addresses.  The established selector callbacks fired (for example,
  lines 116--145), so the LLDB command namespace and the general callback
  path were live.
* Focused breakpoint `IOMFB_A500` fired at lines 1154--1155.  LLDB then shows
  the instruction bytes at lines 1167--1172 (`sub sp, sp, #0x20; ...; mov
  w8, #0xaaaa`), a same-module positive control for that address.

The focused result is invalid for negative inference: lines 1156--1164 show
the callback attempting `ReadMemory(0, 0)` because this helper has `x1=x2=0`.
LLDB subsequently quit (line 1174), so it could not record a later client
state, SMMachine, or wakeup event.  The callback has been corrected to treat
an empty buffer as valid, but it was not rerun: the two bounded boots are
already consumed.  `TERRA_DCP_STATE1` was also invalid: its log lines 18--26
show the initial callback-namespace attachment failure.

## Static reconstruction

`tools/re/kc_text_map.py firmware/bootkc` maps AppleFirmwareKit to
`0xfffffff008b5b4c0..0xfffffff008baa314`.  The relevant addresses are:

| Frame | Static VA | Runtime VA with the established kernel slide `0x20000000` | Finding |
| --- | --- | --- | --- |
| `AppleFirmwareKit+0x4937c` | `0xfffffff008ba483c` | `0xfffffff028ba483c` | Return immediately after the AFK client's state-request helper call. |
| state-request helper | `0xfffffff008b82378` (`+0x26eb8`) | `0xfffffff028b82378` | Selects one of object fields `+0x130`, `+0x138`, `+0x140` based on a requested state 2, 0, 1 respectively, then calls through the object at `+0x110`. |
| `AppleFirmwareKit+0x26f6c` | `0xfffffff008b8242c` | `0xfffffff028b8242c` | Continuation of that state-request path. |
| `AppleFirmwareKit+0x30d38` | `0xfffffff008b8c1f8` | `0xfffffff028b8c1f8` | Transition/work-loop path feeding the executor. |
| `AppleFirmwareKit+0x3102c` | `0xfffffff008b8c4ec` | `0xfffffff028b8c4ec` | Inside the transition executor, between its stage-2 operation and the stage-3 write. |

The containing client callback begins at static
`0xfffffff008ba47b0`; it maps two input values to the three state requests:

| Callback input | Requested state helper argument |
| --- | --- |
| first value 3, 4, or 5 | 0 |
| first value 1 and second value 1 | 2 |
| first value 0 and second value nonzero | 1 |

This is a real state-selection boundary, not a proof of the message that
supplies those two inputs.  AppleFirmwareKit's local strings include
`Starting`, `Ready`, `RTBuddyOn`, `RTBuddyOff`, `RTBuddySleep`, `SMEvent`,
`SMState`, `SMTransition`, `SMMachine`, and `AFKWorkloop`; they support the
state-machine interpretation but do not map the three numeric values to those
names.  No numeric-to-name mapping is claimed here.

The executor uses the machine and transition objects (`x0`, `x1`) and invokes
per-transition functions indirectly.  It resets a field at a subordinate
object offset `+0x30`, then advances a stage field through 1, 2, and 3.  The
blocked return address at `+0x3102c` is therefore in the executor's stage
progression; it is not by itself the address of a hardware mailbox receive or
the actual sleeping primitive.

## Traffic timeline

1. AP requests DCP IOP power state `0x220`; the existing model logs and
   acknowledges it (`FASTPROBE_VALIDATE2.stderr.log` lines 51 and 86, and
   `darwin_asc.c` `MGMT_SET_IOP_PWR` handling).
2. The AFK endpoints start and the IOMFB link is operating.  This is a
   positive control that both RTKit transport classes are available before
   display-off.
3. Endpoint `0x37` handles `A484`, then the final observed display request is
   A500 (`FASTPROBE_VALIDATE2.stderr.log` lines 2735--2747).  The model emits
   a status-zero class-2 completion for each.
4. The display-off work-loop then enters AppleDCP / RTBuddy /
   AppleFirmwareKit and holds the IOMFB lock at the stack above.
5. backboardd enters selector 79 and waits behind that lock; it has no
   matching return in the baseline capture.

The logger positively captures endpoint-`0x37` class-2 traffic: it captured
both A484 and A500 request bodies and completions.  That proves only the
visibility of that traffic class.  It does **not** prove absence of an AFK,
RTKit-management, or client-callback completion after A500, because the
focused LLDB session detached before that observation.

## Implementation contract

No exact implementation contract is justified yet.  The only safe contract
for the model is the existing one:

* **Request observed:** AP-to-DCP endpoint `0x37`, IOMFB class-2 A500, four
  bytes `00 00 aa aa` in the baseline.
* **Response observed:** DCP-to-AP endpoint `0x37`, word
  `0x0000000000000042`, class-2 success with zero output.
* **What it does not guarantee:** completion of the AppleFirmwareKit
  SMMachine display-power transition or release of the IOMFB recursive lock.

Candidate work to resume only after a working focused callback records the
producer:

1. Capture entry/return at `0xfffffff028ba47b0`,
   `0xfffffff028b82378`, and `0xfffffff028b8c2d0`, including `x0..x2`, object
   fields, and any return event.
2. In the same run, prove a mailbox/AFK receive breakpoint fires after A500,
   then correlate it with the client callback.  This determines whether the
   producer belongs in `darwin_iomfb.c`, `darwin_afk.c`, or RTKit management.
3. Only then specify payload, ordering, multiplicity, error behavior, and
   wakeup semantics.  Do not add a synthetic IOMFB D-series completion or
   infer an AFK/EPIC packet from its absence in current logs.

The repaired, read-only callback module is
[`tools/re/dcp_sm_machine_callbacks.py`](../../tools/re/dcp_sm_machine_callbacks.py).
It changes no guest registers or memory and has not been used for a third
boot.
