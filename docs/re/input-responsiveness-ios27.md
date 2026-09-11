# Exact-guest touch responsiveness

The active priority is responsive interaction, not boot duration. Preserve iOS
27 24A5430a, SPTM/TXM, native SMC, migrated disk lineage and rendering fallback.
Use one isolated VM between tests; readiness has a deadline, the interactive
session does not have a fixed lifetime.

## Measurement and acceptance

Measure host event receipt → guest receipt → HID submission → visibly causal
presentation. ACK means receipt/queueing, not that UIKit handled the input.
Separate press duration, startup, app launch and display wake from steady
interaction. Verify touch ownership/release and zero failed/dropped edges.

Working engineering targets for "near zero": transport should consume less
than one 60-Hz frame (16.7 ms at p95), with touch-to-visible change targeted
at 50 ms p95 in an already visible UI. These are targets, not observed results
or a substitute for the user's requirement that the device feels responsive.
Measure slow samples and continuous drag tracking, not only median taps.

The existing status JSON updates every 250 ms, and the CLI polls it every
50 ms. CLI completion elapsed time therefore includes observation delay.
`input_to_present_ms` can match an unrelated frame. Neither is an acceptance
oracle. Use existing `DARWIN_INPUT_TIMING` timestamps and opt-in actual
presentation thumbnails to inspect the visible transition. Report capture
cost and clock-domain limitations; QEMU scanout is not VNC client display.

## Initial control

Durable evidence root:
`/Users/jdolbe1/dvm-artifacts/research/input-responsiveness-20260911`.
`baseline.json` pins the prior direct-HID built-in-touch v16 candidate and
accepted QEMU executable, with input timing and opt-in transition capture.
It creates a fresh child of the immutable migrated cellular/SMC lineage.
This initial baseline uses software rendering. GPU compositor responsiveness
is a separate comparison, not established by these measurements.

`INPUT_RESP_0911A` is the owned runtime. Stop reuse on ownership failure,
helper epoch loss or failed release; recover before sending another gesture.


## Initial observed interaction

The owned VM is running with helper PID184, epoch4 after startup recovery.
Startup had one timeout and a helper restart; the following actions themselves
had no new timeouts, failed dispatches or dropped edges. Home visibly opened
the app grid. Settings opened after a single tap, but its five-second capture
still showed the pressed icon; app-launch latency is not a steady scroll test.

For the first 400-ms requested Settings swipe (epoch4, seq115–127): all 13 HID
operations dispatched and contact ownership released. The CLI reported
874.9 ms to observe completion; this includes gesture duration and status
publication/polling delay. Individual guest HID calls took 1.4–16.7 ms, which
excludes queueing and does not measure UIKit handling. QEMU wire timestamps
show the generated down/up span was 497.417 ms, so CLI per-step sleeps also
stretched the requested gesture.

The legacy first-presentation metric reported **6 ms**. Actual transition
capture `0209` at **15.921 ms** had no UI-content difference. `0210` at
**503.608 ms** visibly adds a scroll indicator; `0211` at **1050.533 ms**
shows the list moving and the large Settings title collapsing. These are
wire-preparation-to-scanout times, not full host/client end-to-end timings.
Capture cost for these frames was 294–316 microseconds. The pixels were
visually reviewed. This single scroll is a failing responsiveness baseline,
not a p95 result. It demonstrates why unrelated first frames cannot pass.

`tools/input/report_touch_frames.py` creates a review report from the actual
captured thumbnails and pre-input screenshot, excluding status-bar clock and
bottom affordances from its content comparison. Pixel thresholds only nominate
frames for visual review. `scroll1-frames.json`, `actions.jsonl`, screenshots,
and `initial-evidence/` retain the evidence under the durable root.

Next measurement: timestamp actual guest receipt and HID completion relative
to host injection, then separate transport/queue delay from UIKit/compositor
response during repeated already-open Settings drags. Do not attribute the
remaining second to rendering solely from a short HID call duration. Keep
this VM alive between tests; no boot optimization remains in scope.

## Snapshot-driven interaction loop

The first interaction checkpoint attempt intentionally became a capture-point
negative control: it restored the exact PC in 1.39 seconds but retained a
black transitional framebuffer. It was discarded after establishing that a
successful migration does not establish a useful UI fixture.

The durable Home fixture is now:

`/Users/jdolbe1/dvm-artifacts/research/input-responsiveness-20260911/checkpoints/INTERACTION_HOME_READY1/manifest.json`

It was captured only after Home was visible, the display's last complete A484
transition was ON and native input was released. Independent restores loaded
the exact source PC in 1.331, 1.481 and 1.601 seconds, produced the same Home
screen, created a fresh helper epoch and did not repeat an iOS boot banner or
panic. The corresponding cold run needed 118.097 seconds to reach its first
visible UI. This proves the development-time reduction for this exact state;
it does not prove arbitrary live GPU resources are migratable.

The settled Settings fixture is:

`/Users/jdolbe1/dvm-artifacts/research/input-responsiveness-20260911/checkpoints/INTERACTION_SETTINGS_SETTLED1/manifest.json`

It retains a fully visible Settings process after a 30-second observation.
Its post-relocation restore loaded the exact source PC in 1.514 seconds. Both
fixtures retain their sealed top qcow2 disk beside the manifest. Their full
backing chains and QEMU inputs are hash-pinned, so clearing `/tmp` no longer
destroys them. `tools/relocate_checkpoint_disk.py` upgraded the legacy
captures; `tools/create_checkpoint.py` now retains new disk generations this
way directly. Checkpoint RAM contains guest and SEP material and remains a
local secret.

`tools/input/measure_snapshot_action.py` makes a fresh disk child and QEMU for
each action, captures one before state, sends one tap/Home/swipe, retains the
source-clock input/presentation/completion timeline and final pixels, then
stops only that QEMU. Routine trials retain changed thumbnails rather than
every identical frame. A failed trial keeps its `/tmp` runtime for diagnosis;
a completed trial removes it unless `--keep-runtime` is set.

For manual work, `tools/input/snapshot_vnc_session.py start` restores a fixture
with no fixed session deadline and serves noVNC. `stop` checks both recorded
process identities before terminating them. The currently tested URL is:

`http://127.0.0.1:6089/vnc.html?autoconnect=true&resize=scale&shared=true`

## Clean Settings launch measurements

Two one-tap trials began from independent restores of the same Home fixture.
Both dispatched exactly one down/up pair with no new timeout, rejection,
failed HID call or ownership loss. The earlier apparent "second successful
tap" was invalid as a launch trial because its before screenshot already
showed Settings; it was excluded.

| Trial | Restore | first small presentation | enlarged launch icon | full Settings frame | median scanout | median D594 completion |
|---|---:|---:|---:|---:|---:|---:|
| `settings-tap-quiet1` | 1.310 s | 46.774 ms | 12,817.105 ms | 13,867.303 ms | 0.985 ms | 1.177 ms |
| `settings-tap-quiet2` | 1.334 s | 584.827 ms | 9,382.463 ms | 10,494.258 ms | 0.949 ms | 1.128 ms |

The thumbnails and final full-resolution screenshots were visually reviewed.
The 1% pixel threshold identifies the enlarged Settings icon, not completion
of app launch; the full-screen frame was the next roughly 97%-different frame.
During the second 25-second trial QEMU used 510.9% host CPU on average (96
`ps` samples) and resident memory grew by 611.5 MiB. All 58 measured displays
had a matching successful native completion. Therefore input injection and the
native DCP acknowledgement are not the 9–14 second gap. Work before submission
can still include process launch, framework initialization, software rendering
and compositor scheduling, so this does not isolate the gap to any one guest
component or prove that GPU acceleration cannot improve it.

Routine successful IOMFB protocol logging was also removed from the measured
path behind `DARWIN_DCP_IOMFB_QUIET=1`. The first control produced 39,415 lines
and 3.4 MiB in under four minutes. Quiet mode reduced a comparable short run to
711 lines/72 KiB. `DARWIN_DCP_IOMFB_TIMING_TRACE=1` adds only compact
presentation/completion records, preserving pacing evidence while routine
errors, A484 power transitions and opt-in thumbnails remain visible.

## Already-open Settings measurements

Restoring inside Settings removes the 9–14 second app launch from scroll
tests, but it does not make interaction responsive yet:

| Source state | pre-action wait | requested gesture | source-clock gesture | first presentation | first meaningful list change |
|---|---:|---:|---:|---:|---:|
| Settings-ready | 2 s | 400 ms | 490.938 ms | 1,072.611 ms | 1,907.618 ms |
| Settings-ready | 30 s | 400 ms | recorded in report | 177.436 ms | 1,214.760 ms |
| Settings-settled | 2 s | 400 ms | recorded in report | 790.248 ms | 1,352.599 ms |

Every reported display completed successfully in roughly 1–2 ms after
scanout. A separate four-second settled-state trial dispatched 12 records,
coalesced one move and produced no frame before the deadline; before/after
pixels were identical. Its TCG JIT counters grew from 217,525 to 486,344
translation blocks and partial TLB flushes grew from 6,071 to 22,716 during
the bounded window. QEMU remained near five fully occupied host cores even
after a 30-second wait. These are observations of sustained emulator work,
not identification of the responsible guest process. They make guest/TCG CPU
pressure the leading performance hypothesis while continuing to keep input,
render production and DCP completion as separate stages.

## Next bounded diagnosis

The next instrumentation should mark guest process launch and scene lifecycle
without perturbing every frame: SpringBoard dispatch, runningboardd spawn,
Preferences `main`, first scene commit, first compositor submission and first
DCP presentation. A small boot-integrated or already-trusted telemetry ring is
appropriate if existing logs cannot expose those events. Stop once the 9–14
second interval is assigned to those stages; do not add broad tracing first.

In parallel, use the settled Settings fixture for repeated scroll tests and
the Home fixture for cold app-launch tests. A driver or QEMU revision only
needs a new cold boot when it changes boot-integrated code, device tree or
non-migrated device state. User-space driver revisions can use the proven
backboardd reload loop; host-only display/input instrumentation can be tested
by restoring these exact fixtures with an explicitly recorded QEMU override.
