# DCP AFK state acknowledgements

The 12-byte interface-zero StateTask messages are requests and acknowledgements,
not states to echo. In the iOS 27 b8 receiver at
`0xfffffff008b9267c..0xfffffff008b92804`, receiving **2** installs a type-2
desired-state-3 claim and eventually transmits **3**. Receiving **3** only sets
the outstanding-handshake phase at `StateTask+0x4b` to 2. Receiving **4** clears
that phase, transmits **5**, and replaces the type-2 claim with desired state 1.
Receiving **5** clears the outstanding close phase. Consequently the passive
firmware model must answer AP 2 with 3 and AP 4 with 5. It must not originate
additional peer open/close requests.

The framing is little-endian `u16 sequence, u16 interface, u32 length, u32 state`.
The observed interface and QE channel/type are zero, and payload length is four.
`darwin_afk_build_state_ack()` accepts exactly this framing and preserves the
sequence; other traffic continues to the EPIC decoder. State acknowledgements
are independent of the optional canned EPIC command responder.

## Controlled runtime evidence, 2026-09-04

All runs descend from `/tmp/dvm/data-seed/root-welcome-checkpoint1.qcow2` through
new writable overlays. The only fresh boot, `DISPLAY_WARM1`, had no
`set_dir_stats` attribution phase. Shared-cache slide `0x4b30000` was resolved
from live instruction bytes; kernel slide is `0x20000000`.

| Replay | Intervention and result |
| --- | --- |
| `DISPLAY_DCP_R3` | Host LLDB echoed only the observed 2/3 messages on their own endpoints. All eleven initial waits completed. Later the ep-0x20 task remained at bytes `03 03 00 02` while its type-3 descriptor requested state 1 and the spurious type-2 descriptor retained state 3. SpringBoard remained in `CASGetDisplays`. This is a negative control, not a usable protocol. |
| `DISPLAY_PROTOCOL_R1` | Restored the original pre-intervention wait and acknowledged 2→3 / 4→5 through the existing AFK ring sender. All eleven endpoints sent actual close requests and accepted ack 5. backboardd and SpringBoard then returned non-null `CADisplay` objects. SpringBoard reached BKS migration check-in without the historical check-in bypass. Six bounded power cycles completed, with zero XNU panics; six RTKit handshakes accompanied them. This is not yet a first-frame or stable-display acceptance result. |

Positive-control artifacts:

- `/tmp/dvm/DISPLAY_AFk_STATE_PROTOCOL.disasm.txt`: exact receiver instructions.
- `/tmp/dvm/DISPLAY_PROTOCOL_R1.host-events.jsonl`: requests and exact replies,
  including bootstrap ep 0x2a and every sequence number.
- `/tmp/dvm/DISPLAY_PROTOCOL_R1.guest-lldb.log`: `CADISPLAY_MAIN_RETURN` in both
  processes and the subsequent `BKS_CLIENT_WAIT_BRANCH` (`x22=1`).
- `/tmp/dvm/checkpoints/DISPLAY_DCP_WAIT1/restores/DISPLAY_PROTOCOL_R1/serial.log`
  and `qemu.stderr.log`: power transitions and model traffic.
- `/tmp/dvm/DISPLAY_UI_R1.sb-chains.json`: the negative control's main-thread
  stack, `CASGetDisplays → query_displays → ensure_displays → CADisplay.displays
  → FBSDisplayMonitor → FBDisplayManager → FBSystemShellInitialize`.

Native C packet tests replay ep 0x20's captured open sequence 17 and close
sequence 18. They also reject acknowledgements (preventing reply loops), other
interfaces/channel/types, malformed lengths, and reserved state bytes.

## Native model replay

`DISPLAY_NATIVE_R1` and `DISPLAY_NATIVE_R2` restored `DISPLAY_WARM_READY1`
with the native 2→3 / 4→5 responder. Both returned non-null CADisplay objects
in backboardd and SpringBoard without host-injected replies or userland bypasses
(`/tmp/dvm/DISPLAY_NATIVE_R{1,2}.guest-lldb.log`). The subsequent BKS wait was
backboardd's data migration, not a missing display object.

R1's migration workers exposed a separate malformed SKS device-state DER reply;
see [the corrected parser evidence](sks-userspace-selectors.md). R2 uses both
native fixes. Its photo migration worker finished after first-unlock became true.
The remaining installation worker synchronously calls
`+[IXAppInstallCoordinator cancelCoordinatorForAppWithIdentity:withReason:client:error:]`
(shared-cache static return `0x1c22981e8`). installcoordinationd waits for pending
installs at its static `0x10004a478` dispatch-group call. Its executable base is
`0x10083c000`, verified by an exact unique 32-byte instruction match from physical
memory at runtime `0x100856a20` to file offset `0x1aa20`; guessing the base from
another binary's main address gave misleading symbols.

`/tmp/dvm/DISPLAY_NATIVE_R2.installcoord3.json` records active installation workers,
including `IXTerminationAssertion acquireWithCompletion:` and protected file
operations. The pending-install group returned once at runtime `0x10088647c`, but
migration did not complete. Later protected-file writes blocked behind an
unanswered SKS op0f User-volume/class-1 request; the guest eventually panicked
with `AppleSEPManager ... sks request timeout`. `DISPLAY_NATIVE_SKS_WAIT2`
was captured after that panic and is evidence-only, not a resumable seed.
These observations do not establish rendered pixels. The read-only `tools/re/physical_task_memory.py` walker obtains
sleeping processes' user stacks without scanning RAM or modifying guest registers.

## Reusable checkpoints

Manifests live below `/tmp/dvm/checkpoints/<name>/manifest.json`:

- `DISPLAY_WARM_READY1`: before the first DCP StateTask exchange, migrated disk;
  use this to validate the native model with no injected replies.
- `DISPLAY_DCP_WAIT1`: exact first StateTask sleep, PC `0xfffffff02ab002a8`,
  outstanding ep-0x2a open already consumed by the old model. It needs one
  explicit acknowledgement when used to test a newer model.
- `DISPLAY_DCP_ACKED1`: **negative-control echo state**, not a production seed.
- `DISPLAY_PROTOCOL_UI1`: after the bounded correct-ack experiment, waiting
  at BKS check-in. The last ep-0x2a open was deliberately left unanswered when
  the host probe reached its limit; do not mistake it for an unmodified native
  checkpoint.

The host debugger needs `SIGUSR1`/`SIGUSR2` passed without stopping. Calls into
the device model must own QEMU's BQL. At `dcp_afk_recv` the CPU thread already
owns it; out-of-band injection must acquire `bql_lock_impl()` and release BQL
in the same expression. The initial failed lockless injection in
`DISPLAY_DCP_R2` was discarded and is not positive evidence.

No non-black IOSurface or Setup welcome screen has yet been established by
these experiments. Follow the remaining BKS check-in before claiming success.


### Latest healthy replay anchor

`/tmp/dvm/checkpoints/DISPLAY_INSTALL_PROGRESS9/manifest.json` preserves all
native DCP/DER/SKS fixes, including op10 class 13 and User media-key transfers
3→1, 1→3, 3→4 and 4→3. Installation cleanup has passed both the iBooks and
Weather-poster coordinator waits. Capture took 2.04 seconds for migration,
12.831 seconds including evidence and hashing; paired disk remains descended
from `/tmp/dvm/data-seed/root-welcome-checkpoint1.qcow2`.

`DISPLAY_NATIVE_R10` resumes this state without another boot. SpringBoard
ADFL, Setup Welcome and non-black IOSurface remain the acceptance conditions;
none is established by the intermediate SKS/coordinator returns.

## Installation cleanup after the native key fixes

The R10 physical task walks resolve the remaining BKS migration wait to
installcoordinationd startup cleanup of 14 persisted coordinators. The array at
`0x76a0d28000` contains verified `IXSCoordinatedAppInstall` objects (runtime
class `0x100bad370`). `tools/re/inspect_install_coordinators.py` reads their
identities and dispatch-group counts without scheduling or writing the guest.
The first two coordinator waits have completed; VoiceMemos still has two
outstanding entries in `DISPLAY_NATIVE_R10.coordinators3.json`. A coordinator's
`complete` flag alone does not establish that its cleanup group has drained.

Worker stacks pass through `IXRemoveItemAtURL` (static `0x1c22e9ec8`, the
return from `removefile`) and the recursive removefile tree walker (static
`0x2c210a094`). A saved worker's x23 points to a live path under
`InstallCoordination/PromiseStaging/4E720B5A-2AE7-480E-96C8-BA54DAB907EE/`
`PridePosterApp.app/cs.lproj`. R10's bounded process-scoped
`IC_REMOVEFILE_RETURN` observer records 24 native zero-status returns between
wall-clock timestamps 1788557315.614 and 1788557380.244. This establishes real
cleanup progress, not a completed migration or a rendered frame. R10 includes
the separately documented one-provision watchdog timing diagnostic.

The next bounded probes are the native dispatch-group leaves in
`_runAsyncBlockWithDescription:onLaunchServicesQueue:` (static
`0x10003a488`) and its uninstall-queue counterpart (static `0x10003a348`).
At t=1788557607.527, `IC_LS_QUEUE_LEAVE` records group `0x76a106abc0`
(the VoiceMemos group) with pre-leave state `f9ffffff01000000`; its block
contains coordinator `0x101404790`. This is one of the two outstanding leaves.
The main queue has not yet returned at that point.

At t=1788557640.553, the second VoiceMemos LS-queue callback leaves the same
group from pre-leave state `fdffffff01000000`. The main-thread wait then
returns natively at t=1788557640.668 (`x0=0`, coordinator `0x101404790`).
`DISPLAY_CLEANUP_PROGRESS10_TIMING` preserves this progress, with Clock now
holding the main queue, and all sampled original cleanup coordinators except
GradientPoster untracked. The captured PC is `0xfffffff02aa60400`; capture
took 2.069 seconds for migration / 12.485 seconds total, with 4,619,402,786
state bytes and 222,298,112 paired disk bytes. This branch includes the
one-provision watchdog timing diagnostic; `DISPLAY_INSTALL_PROGRESS9` remains
the earlier unchanged-guest control. `tools/re/install_cleanup_callbacks.py`
reinstalls the four process-scoped cleanup boundaries on a verified image base.

R11's physical task snapshot (`DISPLAY_RESUME_R11.stacks.txt`) shows no
remaining recursive-removal worker among the sampled installcoordinationd
threads: active work is LaunchServices installation progress, placeholder
metadata update, and native MIBundle validation in installd. Clock's final
LS callback leaves at t=1788557941.004 and its main wait returns at
t=1788557941.130. Safari's corresponding events are t=1788558034.126 and
1788558034.241. All observed main wait returns have `x0=0`.

The last original coordinator (GradientPoster) drains its group at
t=1788558131.086; the main wait returns at t=1788558131.208. Across R9/R10/R11,
all 14 original coordinator waits have now returned natively. The migration
wrapper's previously blocked cancel call returns at t=1788558131.700 and its
subsequent calls return in roughly 0.15 seconds each. The next live stack is
`IXPlaceholder placeholderForRemovableSystemAppWithBundleID:client:installType:error:`
(static `0x1c22c1a8c`), through `IXPromisedInMemoryDictionary` construction
(static `0x1c22ee348`). Startup cleanup completion is distinct from completion
of the full Installation migration plugin. `DISPLAY_INSTALL_CLEANED11_TIMING`
is the checkpoint at this later boundary; it retains the earlier one-shot
watchdog timing diagnostic in its lineage.

In R12, the Installation plugin's Mach-O header is verified at runtime
`0x100404000`. Its native wait at `0x1004063d0` uses
`systemAppInstallGroup` from object `0x742b05c000`, group `0x742b04dcc0`.
The timeout calculation at `0x1004064b0..4c4` is
`40 * pendingAppInstalls.count` seconds, converted to nanoseconds at
`0x1004063b4..3c4`. Captured strings identify the current phase as demoting
system apps. The saved timeout is 1440 seconds; successive read-only group
snapshots drop from 20 to 14 outstanding operations (low words
`ffffffb1` and `ffffffc9`). Container-manager's live worker writes a metadata
dictionary through `MCMMetadata _writeFileURL:dictionary:options:error:`
(static `0x206912c2c`); installd waits on its container-regeneration/query
RPCs. See R12's `plugin-wait.disasm.txt`, `plugin-loop.disasm.txt`,
`installd-stacks.txt`, and `container-stacks.txt` for the exact evidence.

At t=1788558949.773, the measured Installation worker
(`tpidr_el1=0xffffffe18f40b830`) returns `x0=1` to the wrapper's post-plugin
call boundary at runtime `0x10028f708` / static `0x100003708`.
`DISPLAY_INSTALL_DONE12_TIMING` preserves this completed phase at
PC `0xfffffff02aa60400`: 2.037 seconds migration / 12.641 seconds total,
5,450,413,325 state bytes and 403,374,080 paired disk bytes. This is the
latest efficient replay point. Overall DataMigration/backboardd has not yet
returned; a new migration wrapper is present at proc `0xffffffdfc37438e8`.
The one-provision timing diagnostic remains explicitly part of this lineage.
