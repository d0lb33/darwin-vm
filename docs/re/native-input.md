# Native input development (24A5430a)

Status (2026-09-05, NATIVE_HOME_UART_R2): native helper v4 is installed in the
migrated six-core Home image. Fixed UART full/empty ambiguity; 50 unpaced pings
all ACKed (median 17.89 ms, maximum 127.90 ms). A native tap opened Home Search,
and native Home down/up returned to the icon grid. No per-touch LLDB callback
was installed. This proves visible response, not uniformly fast application
launches or completed verification of the user's Cocoa clicks.

The user authorized a native iOS helper for this development image. Display
continues to use the existing snapshot and allocation/watchdog accommodations;
removing those display callbacks is deferred.

## Home input transport fix

`/tmp/dvm/NATIVE_HOME_R1.wire.jsonl` records a bounded read-only breakpoint
immediately after helper v4's `fgets`, executable `0x10000424c` plus slide
`0x22ec000`, reading the line at SP+0xa4. Sent:
`DVMINPUT1 1788615036986 S 0 0 0\n`; received: `DVMINP6 S 0 0 0\n`.
Exactly 16 bytes vanished. This occurred with a single registered helper,
so competing console readers do not explain this later failure.

`qemu-sptm/hw/char/exynos4210_uart.c` advertised all 16 free bytes, then
`fifo_store()` wrapped `sp` onto `rp`; `fifo_elements_number()` interpreted
equal cursors as empty. The fix retains a full bit, clears it on retrieval and
reset, and serializes it in an optional `exynos4210.uart.fifo/full` subsection.
The old v1 cursor/data layout is unchanged and old checkpoints default to
full=false. Full stores cannot overwrite unread bytes. A regression compiles
the actual device FIFO routines and verifies capacity, wraparound, exact byte
ordering and backpressure across sizes 1..256, including the 16-byte console.

`relay.py` now relies on UART backpressure: no default per-byte sleeps, padding
after records, or periodic blank-line nudges. `--ping --ping-count 50` exercises
the transport without HID. An ACK timeout still stops rather than resubmitting
an ambiguous touch. One newline at connection startup terminates a prior
writer's incomplete record.

Runtime evidence (all under `/tmp/dvm`):

- `checkpoints/NATIVE_HOME_UART_BEFORE1/manifest.json` captured the Home grid,
  registered v4 helper PID 211 and no active injected call/breakpoints.
- `checkpoints/NATIVE_HOME_UART_BEFORE1/restores/NATIVE_HOME_UART_R2/restore-report.json`:
  new binary loaded the old snapshot at exact PC `0xfffffff02aa654c8` in 1.419 s.
- `NATIVE_HOME_UART_R2.ping50.jsonl`: all 50 successful, unpaced, no retries.
- `NATIVE_HOME_UART_R2.input.jsonl`: Search tap at `(16300,28200)` submitted
  down/up in 2.41/5.43 ms; subsequent Settings-coordinate tap was sent while
  Search was opening, so it is **not** evidence of launching Settings.
- `NATIVE_HOME_UART_R2.before.png`, `settings.png` (actually Search),
  `home-later.png`: icon grid -> Search -> icon grid. Home packets in
  `NATIVE_HOME_UART_R2.home.jsonl` ACKed in 10.27/18.62 ms.
- Helper nice was restored from diagnostic -20 to 0 using native
  `setpriority`/`getpriority` at `0x237cd46e8`/`0x237ce3a20` plus shared-cache
  slide `0x6fa0000`; both returned zero. No scheduler-memory edits.
- `NATIVE_UART_V5_BASELINE.verdict.log`: `tools/probe.sh --secs 45`, default
  restore ramdisk, reached shell **yes**, XNU panics **0**.

Installed helper files are in `native-home-v4/` (checksum 3524758031, 53744
bytes); its build/install path did not repeat the expensive data-attribution
migration. The QEMU app copy with the UART fix is
`native-input-uart-v5/Darwin VM Touch Test.app`. Keep helper and QEMU version
labels distinct. The historical sections below describe earlier experiments.

At handoff, NATIVE_HOME_UART_R2 remains visible and running; its live relay
uses `NATIVE_HOME_UART_R2.input2.jsonl`. The preceding input log contains 18/18
successful native submissions (median 8.60 ms, maximum 37.43 ms), including a
12-state drag. These ACKs alone do not prove that the drag changed pages.
The next Settings tap, after restoring normal helper priority, had both ACKs
(15.44/35.53 ms), but no completed Settings launch was established. User
confirmation of Cocoa left-clicks is pending. No computer-use APIs were used.

To resume the saved Home image on another unique tag, use the BEFORE1 manifest
above with the v5 QEMU app executable, `--observe-seconds 0 --leave-paused`,
`--display cocoa,zoom-to-fit=on,show-cursor=on`, and that tag's
`DARWIN_TOUCH_EVENTS` path. Attach LLDB to its chosen GDB port and install
`home_display_callbacks.install(lldb.debugger, 0x6fa0000)` before continuing.
Run the relay against the new tag's events/UART paths. The checkpoint already
contains the helper; do not spawn a second copy. This saved snapshot predates
the priority reset and therefore retains nice -20, unlike the live R2 guest.

## Why move touch out of LLDB

The prototype in `tools/re/touch_bridge.py` records the completed gesture and
constructs a native Recap stream one function call at a time. Each debugger
stop pauses every virtual CPU. A shared CF observer also stops unrelated
processes. QEMU `accel/tcg/cpu-exec.c:check_for_breakpoints_slow` forces
single-instruction translation on a breakpoint's page, so callback hit counts
alone understate the cost of leaving such a breakpoint installed.

Read-only QMP samples (`tools/re/runtime_perf_sample.py`):

| Run/sample | Running fraction | Presentations / second |
|---|---:|---:|
| SETUP_TOUCH_R23.perf-broad.json | 78.2% | 0 |
| SETUP_TOUCH_R23.perf-narrow.json | 95.3% | 0 |
| DISPLAY_PERF_R24.no-touch.json | 99.0% | 14.33 |
| DISPLAY_PERF_R24.timer.json | 63.7% | 0 |

All JSON artifacts are under `/tmp/dvm`. These are different guest intervals,
not matched animation benchmarks. Presentations can contain identical pixels.
R23's narrow selector had no hits, so its measurement did not prove working
input. R24 proved that selector can fire and execute idle resets, but it still
causes frequent global stops. No end-to-end latency improvement is established.

R23's saved SpringBoard main stack was inside `CA::Render::Message::send_message`
at static `0x184404fb0` (the call uses MACH_SEND_MSG without a timeout).
R24 later had an ordinary main-run-loop receive wait. A single blocked stack
does not establish a persistent display-server deadlock.

## Proposed native path

QEMU's existing absolute-mouse capture file -> `tools/input/relay.py` -> the
existing UART -> `dvm-input` -> a persistent native Recap virtual HID service.
The relay preserves button edges, coalesces pending motion, and measures ACK
latency. The installed v1 helper ACKs event allocation plus a void native call;
this can falsely report success. Updated source checks HID service registration
and returns the underlying dispatch BOOL. Even that means submission, not that
an application processed the event. No Apple touch-controller MMIO is invented.

The helper uses runtime symbols and Objective-C selectors, not fixed addresses.
24A5430a native evidence:

- `+[RCPEventSenderProperties touchScreenDigitizerSenderForDisplayUUID:]`,
  `0x29b208234`, provides the same explicit main-display association used by
  the successful LLDB prototype.
- `-[RCPEventDeliveryServicePool deliveryServiceForSenderProperties:]`,
  `0x29b1fc1e8`, obtains and starts the service.
- `-[RCPVirtualHIDService postHIDEvent:]`, `0x29b1fff38`, dispatches through
  HIDVirtualEventService. The helper retains this service between packets.
- Device IOKit exports digitizer/finger constructors at `0x18f009520` and
  `0x18f0094ec`. Home uses native consumer-page usage `0x0c/0x40`, also
  observed in Recap `_createIOSButtonHIDEventWithButtonType:down:`.

The constructor declarations and display-integrated digitizer shape were also
cross-checked against the upstream
[vphoned HID implementation](https://github.com/Lakr233/vphone-cli/blob/main/scripts/vphoned/vphoned_hid.m).
Our path uses Recap's registered virtual service instead of copying its fixed
sender ID. Coordinate mapping and down/move/up delivery still need runtime proof.

## Build, install, and bootstrap evidence

`bash tools/input/build.sh` builds the arm64/iOS executable and adds its exact
CDHash to a derived trust cache. It does not replace the existing system cache.
`python3 tools/input/test_input.py` compiles a host validator that never loads
HID frameworks or submits host events, and checks framing/coalescing.

`prepare_ramdisk.sh` attaches only a new copy of the small restore ramdisk,
through `safe_attach.sh`. `install_in_guest.sh` installs into a writable child
of the migrated System disk inside iOS. No host attachment of the large iOS
System/Data container is needed. The restore image has no umount executable;
sync and stop the owned staging VM before using that child for normal boot.

NATIVE_INPUT_INSTALL1: installed checksum `3310005994`, size 53184;
`/libexec/dvm-input --validate` printed `DVM_INPUT_ACK 1 1` inside iOS.
Evidence: `/tmp/dvm/native-input/install.console.log` and
`/tmp/dvm/probe/NATIVE_INPUT_INSTALL1.serial.log`.

NATIVE_INPUT_BOOT1 uses `/tmp/dvm/native-input/disk.qcow2`, a child of
DISPLAY_SMP6_QEMU_SCANOUT8's migrated disk. It loads the new trust cache at a
fresh six-CPU boot; resolved shared-cache slide is `0x1bc94000`. Early boot
completed at guest 12.90 seconds. The expensive file-attribution migration was
not repeated. The helper was absent from the live process list, so a one-time
native spawn/bootstrap probe is being used. A RunningBoard-context spawn
returned EPERM; the exact denied operation is not yet established. An earlier
BackBoard-context spawn did not return before that process restarted.

`tools/input/bootstrap_helper.py` stages native mmap, file actions and
posix_spawn at a Mach-message return. It writes argument data only, preserves
raw general/SIMD registers and flags, and disables its breakpoint on completion.
Do not reinitialize it during an active call. If its process dies, establish
that fact before clearing its saved state; never restore another thread's
registers from an abandoned call.

Once actual input works, take a new immutable RAM/device/disk checkpoint, then
compare native ACK and visible UI response with the touch breakpoints disabled.

## Bootstrap connection resolved, UI response still open

NATIVE_INPUT_BOOT2 was a fresh six-core boot of a writable child of the migrated
native-input disk. Early boot completed at guest 9.795558 seconds; the long file
attribution phase did not repeat. Shared-cache slide is `0xa09c000`, kernel slide
`0x20000000`. A direct launchd spawn initially created helper PID 248, but its
`_bootstrap_port` was zero. Recap returned objects with a nil underlying
`HIDVirtualEventService.serviceClient`. IOHID bootstrap lookup returned
`MACH_SEND_INVALID_DEST` (`0x10000003`), explaining the false READY/ACK results.

Do not retry spawning from notifyd. Both tested spawn boundaries killed it;
the exit records establish namespace 25 (SANDBOX), code `0xdd`, flags `0x1042`.
`task_inspect_for_pid` returned -1 and launchd's `task_for_pid` returned 5 for
notifyd, so neither yielded a usable task/bootstrap right. No port-table writes
or copied cross-process port names were used.

The working startup path is implemented in `tools/input/bootstrap_helper.py`:

1. At launchd's verified EL0 `sendto` return, static `0x237cd6214`, call native
   `_kernelrpc_mach_ports_lookup3` (`0x237cfb494`) on its own task.
2. Check the first returned port with `mach_port_type`. In R4 this was `0x1e03`;
   the script discovers it dynamically. Never hardcode that name in a child.
3. Pass that send right to `posix_spawnattr_setspecialport_np` (`0x237cdb870`)
   with `TASK_BOOTSTRAP_PORT=4`, then native `posix_spawn`.
4. Destroy spawn objects, release the cloned rights, unmap argument storage,
   restore raw registers, and disable the breakpoint.

R4 returned success at every native setup stage and spawned PID 1093. Its own
bootstrap name was `0x807`. Its touch service `0x1047be270` owns HID object
`0x7def0641b0`, with non-null service client `0x7deec2d360`. Bounded probes then
observed `HIDVirtualEventService dispatchEvent:` return 1 at static
`0x2678a7220` and the events arriving in BackBoard at `0x22a352848`.

The obsolete helper ignored SIGTERM and competed for console bytes. A physical
read of the new helper's input buffer showed interleaved/missing characters.
After verifying the old process was still PID 248, native SIGKILL stopped it;
complete packets and ACKs resumed. Updated source adds a singleton file lock
and resets inherited SIGTERM/SIGINT dispositions and the thread signal mask.
Those safeguards require installing the new binary; they are not in v1.

With all touch probes disabled, a seven-update swipe completed with 131–186 ms
submission ACKs at 2 ms character pacing. A 12.106-second QMP sample found 112
running observations and zero new presentations. This is evidence of a working
packet/submission path, not responsive UI or an animation FPS measurement.

Native Recap also differs from the initial external reference: its synthetic
stream defaults to transducer type 2 (`0x29b1fcb74..84`), passes options `0x40`
to parent and child constructors (`0x29b213a50`, `0x29b213ca8`), and sets built-in
field 4 (`0x29b213b58`). Updated source follows these fields. A bounded live
constructor-argument probe reached BackBoard's touch/display handling but did
not establish visible response. All argument-changing probes were disabled
before checkpoint capture. Do not silently reinstate them as a runtime fix.

Evidence under `/tmp/dvm`:

- `NATIVE_INPUT_BOOT2/lldb.log` includes both BOOT2 and R4 debugger sessions.
- `NATIVE_INPUT_R4.exit-reasons2.txt` contains the notifyd sandbox exit records.
- `NATIVE_INPUT_R4.helper-root.json` records the registered helper's mapping and
  thread stacks; `NATIVE_INPUT_R4.native-clean.jsonl` contains the full swipe.
- `NATIVE_INPUT_R4.native-clean-perf.json` records the zero-presentation sample.
- `native-input-v3/dvm-input` and `native-input-v3/system.tc` are the rebuilt,
  signed candidate and merged trust cache. **Not installed in the running VM.**

## Resume without repeating the bootstrap probes

`/tmp/dvm/checkpoints/NATIVE_INPUT_REGISTERED1/manifest.json` preserves helper
PID 1093 with its registered HID service and no active injected calls. Capture
took 17.144 seconds including 7.639 seconds of migration. The first restore,
NATIVE_INPUT_R5, took 1.266 seconds and matched PC `0xfffffff02ac63b24` exactly.
After HMP `cont`, native Home packets were ACKed in 160 and 63 ms with LLDB
detached. The visible Cocoa VM remains a six-core development system with the
stale Welcome screen, not an accepted display-liveness checkpoint.

Restore to a unique tag, preserving the immutable disk/backing chain:

```sh
python3 tools/restore_checkpoint.py \
  /tmp/dvm/checkpoints/NATIVE_INPUT_REGISTERED1/manifest.json \
  --tag NATIVE_INPUT_NEXT --gdb-port 1511 \
  --qemu qemu-sptm/build-fast/qemu-system-aarch64 \
  --leave-paused --display cocoa,zoom-to-fit=on \
  --model-env DARWIN_TOUCH_EVENTS=/tmp/dvm/NATIVE_INPUT_NEXT.events.jsonl
python3 tools/hmp.py /tmp/dvm/NATIVE_INPUT_NEXT.restore.sock cont
python3 tools/input/relay.py \
  --events /tmp/dvm/NATIVE_INPUT_NEXT.events.jsonl \
  --uart /tmp/dvm/NATIVE_INPUT_NEXT.restore.uart.sock \
  --log /tmp/dvm/NATIVE_INPUT_NEXT.input.jsonl
```

Do not spawn another helper after this restore. For fresh boots, plist autoload
is still unresolved and `/bin/launchctl` is absent. The one-time launchd native
spawn above is established; fully autonomous cold startup is not.

Checkpoint fixes now follow `-serial chardev:<id>` instead of assuming
`probe_uart`, and restored launches write a recapturable `launch.json`.
Validation: 29 host tool tests, two native-input host tests, Python compilation,
and the signed iOS cross-build pass. The SDK linker still emits the existing
iOS-versus-macOS libSystem stub warning; runtime testing of v3 remains required.


## QEMU window input validation (2026-09-05, in progress)

`TOUCH_NATIVE3` cold-started the migrated native-input-v3 disk with six cores
and the nested D594 completion fix. The installed helper is 53,408 bytes,
`cksum` 3407591787, and launched once from launchd as PID 335.
`serial.log:24447` reports both native services registered. The image needs
that one-time launchd bootstrap; automatic launchd startup is not yet proven.

A Cocoa center click in its 221-by-480-point content area produced normalized
coordinates `(16258,16242)`, close to `(16384,16384)` with window/pixel rounding.
The initial click only captured the mouse and was lost. The Cocoa fix captures
an absolute-input press before forwarding it, and uses NSView's
`convertPoint:fromView:nil` to map window coordinates through the view's guest-
pixel bounds, including resize and fullscreen offsets. Runtime resized-window
and actual UI-action validation remains pending.

The first native swipe timed out because the **entire guest panicked**, not
because an ACK proved touch delivery. `serial.log:24533` names an SKS timeout;
CPU 3 spins at kernel static `0xfffffff00ac6a908`, with the other five CPUs
waiting at `0xfffffff00ac6ff90`. No input arrived in the helper's first fgets
buffer. The rejected endpoint-18 request was recovered read-only via host
LLDB `sep_dma`, and saved as `/tmp/dvm/TOUCH_NATIVE3/rejected-request.bin`,
SHA256 `5ff82948088905d0ae29bd4d2347e257cdcd059eb76f5e4f879990277d26bc81`.
It is the existing 164-byte variant-3 User-volume tagged record, with source
class 2 at +0x68, destination 1 at +0x6c, record length 28 at +0x84, and the
unchanged User tag at +0x90. Native class-transfer ABI evidence remains
`0xfffffff0095730a0..0x957311c`. The parser now accepts this exact class pair,
without relaxing framing/volume checks. The capture replay plus malformed
packet and truncation tests pass (24 SKS cases; 29 host cases). This still
requires an unmodified-model runtime success witness.

The dead TOUCH_NATIVE3 VM was terminated. Its migrated disk is preserved,
with helper v3 installed; it must not be treated as a healthy RAM snapshot.


## Visible host cursor / current resume point (2026-09-05)

The user paused computer-use testing, then requested a visible pointer over
QEMU. No computer-use actions were performed for this change. Cocoa already
supports `show-cursor=on`: `ui/cocoa.m` sets `cursor_hide=0`, so its hide/unhide
methods leave the macOS pointer visible when input is captured. This keeps the
standard contrasting black/white outlined pointer at host speed, independent
of guest frame or touch latency. `tools/input/boot.py` now defaults to
`cocoa,zoom-to-fit=on,show-cursor=on`; the checkpoint restore CLI accepts the
same combination. No QEMU rebuild was needed. All 29 host regressions pass.

`TOUCH_NATIVE4` was saved with no active native call and all guest debugger
breakpoints removed to `/tmp/dvm/checkpoints/TOUCH_NATIVE_CURSOR1/manifest.json`.
The checkpoint contains helper v3, nested display completions, and the
User-class 2-to-1 parser correction; it is an input debugging state, **not**
proof of working unlock or app launch. Its shared-cache slide is `0x17930000`.
`TOUCH_CURSOR_R1` restored the exact PC `0x19b9fb6ec` in 1.431 seconds with
the new display option. Current monitor and QMP are
`/tmp/dvm/TOUCH_CURSOR_R1.restore.sock` and `.restore.qmp.sock`; UART is
`/tmp/dvm/TOUCH_CURSOR_R1.restore.uart.sock`. Native relay reads the inherited
`/tmp/dvm/TOUCH_NATIVE4/events.jsonl` from its end, with outputs under the
checkpoint's `restores/TOUCH_CURSOR_R1/`. Display/UI callbacks remain attached
using the correct slide; no touch-injection callbacks are installed.

Before this cursor change, a real Cocoa swipe reached BackBoard at
`(589,2505)` and `(589,923)`, matching its normalized captured positions.
Native ACKs were 112–226 ms; SpringBoard window hit testing saw the expected
half-size UIKit coordinates. It did **not** visibly unlock. A later Home
packet timed out after reconnecting UART. Native helper startup also took
several minutes: the first helper ultimately registered both services, while
the second exited on the singleton lock. A priority-change diagnostic was
never applied (task lookup failed); its scratch mapping was freed and the
original caller restored. Do not claim the slow-start cause is established.
Further touch reliability/gesture work remains paused at the user's request.
