# Cold/warm Home attempt — 2026-09-07

## Result: comparison untested

COLD_WARM_HOME1 booted the exact isolated 24A5430a guest using the merged
debug bootstrap and V28 host worker. Automatic Home was disabled. Readiness
was reached at 102.212 seconds; backboardd remained PID73, generation1.
No driver replacement or reboot occurred. Neither four-second Home window
showed the Home icon grid: the captured thumbnails show the lock clock and
charging banner disappearing/reappearing. Do not label these a cold/warm
Home pair or use their frame gaps to explain the earlier 647.621 ms hitch.

Durable evidence root:
`/Users/jdolbe1/dvm-artifacts/research/gpu-cold-warm-home-20260907`.
`cold/` and `cold-home/` are attempted-trial names, not successful acceptance
labels. Each contains input receipts, per-window RPC records, source-timestamped
thumbnails, a visual contact sheet, raw stderr and a complete-presentation
prefix for analysis. Full session records and captures are under `evidence/`.

| Observed window | First press | Second press |
| --- | ---: | ---: |
| Home edges dispatched / timeouts | 2 / 0 | 2 / 0 |
| Host requests | 557 | 358 |
| Texture-upload chunks | 0 | 0 |
| Captured completed frames | 99 | 51 |
| Thumbnail overhead p95 | 266 us | 263 us |

There were already 73 texture-upload chunks before the first timed press.
Their presence does not establish that the specific eight Home icons from
CA_DEV_HOME1 were resident. Calling the first press shader/resource-cold would
therefore also be unjustified. The interactive session remained alive during
analysis; the first trial ran well after readiness.

A swipe while the display was off acknowledged input but produced no new
frame. A subsequent Home followed immediately by a 350 ms upward swipe
acknowledged both Home edges and 12 dispatched touch records, with zero
timeouts. The after-image still showed the lock screen. This establishes
input delivery and visible wake activity, not successful unlock or gesture
semantics. Stop condition reached: do not repeat the same inputs and call
lock-screen animation the requested Home workload.

At explicit cleanup: 4,178 presentations and 4,178 native completions,
23,470 host requests, no pending display completion, no reported ownership
failure or quarantine. The full allocation journal reconstructs 34,168,910
bytes peak ordinary logical allocation, 337,027,429 cumulatively released,
and 266,240 final live bytes. These are logical accounting observations,
not native resident memory measurements or a leak-proof lifetime test.

## Capture contract exposed

Both final captures exported fresh witness snapshots, yet pixel verification
failed: 30,362 and 24,616 differing RGB components (not pixels), respectively.
RGBA-half to BGRA conversion itself had zero errors. The console screenshots
were black; stderr records `iomfb: display off, blanked 1179x2556`.

Static implementation explains the mismatch: `iomfb_blank()` changes the
console, but does not invalidate `rgha_witness`. `gpu_present_stopped()` then
exports the retained pre-blank source with a new snapshot ID. Thus export
freshness does not establish that the source describes current display state.
The tool correctly withheld success; these captures do not prove rendering
corruption. No capture behavior was changed for this experiment.

Small next fix: track the console/display-state generation through both A408
presentation and blanking, and expose whether the retained source is the
currently displayed frame. A power-off capture should explicitly report
blanked/no applicable RGhA comparison, not pass via a stale witness. Verify a
capture immediately during a known awake interval separately.

## Reproduction and next experiment

`control.json` preserves the input hashes and exact QEMU package. Its only
changes from the repeat-capture control are source thumbnail tracing and
input timing. The baseline disks were untouched; the VM used a disposable
child and was stopped with session_cli cleanup.

```sh
# Use Python with NumPy/Pillow for the visual report.
python3 tools/gpu/capture_home_trial.py --session <owned-session> \
  --out <new-trial-directory> --seconds 4
python3 tools/gpu/report_home_transition.py <new-trial-directory>
python3 tools/gpu/audit_compositor_budget.py <full-session-run>
```

First establish a repeatable, visually verified route from an awake lock
screen to the Home grid on this package. Inspect the input/unlock contract
and compare the earlier successful CA_DEV_HOME1 startup state; successful
HID dispatch alone does not satisfy that contract. Then record the first
confirmed Home transition and a second transition with the same process,
tracking icon identities and uploads rather than assuming cache residency.
Until that succeeds, the cold-upload hypothesis and recurring TCG cost remain
unresolved. No performance optimization is justified by this failed comparison.
