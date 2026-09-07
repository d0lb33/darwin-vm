# Compositor queue deadlock, 2026-09-07

Scope: isolated iOS 27 24A5430a, iPhone17,3/T8140, existing boot carrier,
SPTM/TXM, native SMC and migrated-data overlay. No baseline or QEMU changes.

## Failed contract and fix

The frozen INPUT_MANUAL3 guest had the driver's transport queue synchronously
waiting for QuartzCore's submission workloop, while cleanup targeting that
workloop synchronously waited for transport in setPurgeableState. The display
thread waited for command completion. Stack and queue-object evidence is in
`/Users/jdolbe1/dvm-artifacts/research/input-reliability-20260907/about-thread-capture`.

`test_submission_reentrancy` reproduces that cycle with a cleanup queue targeting
the submission workloop. Before the fix it fails its three-second deadline with
`FAIL cleanup blocked by circular queue wait`. Afterward two commands and both
callbacks complete, along with cleanup's purge RPC.

A separate device FIFO now waits for client admission without occupying the
transport. Execution, residency revalidation and completion bookkeeping remain
serialized on transport. Resource RPCs may precede commands awaiting admission;
tests verify volatile-resource cancellation before GPU execution.

The first fixed guest revision then exposed the old device-wide CPU-transfer
guard: `texture upload while GPU work is in flight`. Revision 3 tracks pending
uses per underlying allocation, including texture views and buffer aliases.
Unrelated transfers are allowed; transfers touching a pending allocation remain
rejected. Command completion releases these counts, including error completion.
This does not promise safety for application data races or unsupported aliases.

The revision builder also declares compiler-emitted ARC helper imports before
checking them against this exact guest's exports; `_objc_retain_x27` required
this on the new build. Signing/import checks passed.

## Validation and reproduction

Durable evidence root:
`/Users/jdolbe1/dvm-artifacts/research/gpu-queue-deadlock-20260907`.

```sh
bash tools/gpu/build_host_driver_tests.sh NEW_BUILD_DIRECTORY
# Run test_submission_reentrancy and test_submission_queues directly.
# Host-rendering tests require DVM_DRIVER_HOST pointing to driver_host;
# library loading also requires DVM_DRIVER_LIBRARY pointing to QuartzCore.metallib.
PYTHONPATH=tools/gpu DVM_DRIVER_BUILD=BUILD_DIRECTORY \
  python3 -m unittest test_shared_render.SharedRenderTests.test_owned_surface_frontend -v
python3 tools/gpu/build_system_revision.py BOOT_BUILD NEW_REVISION --revision 3
python3 tools/gpu/session_cli.py capture --session /tmp/dvm/QUEUE_FIX2 --name NAME
```

Host results: all 15 standalone tests in `host3/results.json` passed; the owned
IOSurface Python fixture also passed. Tests cover FIFO/admission, exact callback
counts, unrelated uploads/readback, pending texture and buffer-alias rejection,
completion release, preflight cancellation and native-versus-forwarded pixels.
The mixed-command negative fixture now exceeds DVM_ORDERED_COMMANDS instead of
assuming the obsolete 64-command limit.

Exact guest: QUEUE_FIX2 dynamically loaded revision 3 into backboardd PID 337.
Binary SHA-256: `f4e1abfb5b58165760c1c87660004fe7c8f589ff6683572f3ec8a3ffd0b01d0d`.
The old generation retired both imports, relinquished ownership and quarantined
none. Restart took 39.964 seconds; early Home attempts encountered helper
readiness failures. After stabilization, `rev3-test.json` records 13 submissions,
12 presentations and 12 completions with successful Home dispatch. The initial
capture verified exact scanout pixels. These results do not establish fast restart.

At 711 seconds, the VM remained running with 658 presentations/completions in
the new generation, no host errors and no ownership failure. A bounded texture
rejection remained recorded; this is not universal Metal coverage. Final capture
`queue-fix-final` has zero conversion/display errors and exact source-to-console
agreement. It verifies delivery, not all compositor scene semantics. Small
runtime evidence is preserved under `final-evidence`; no sustained-animation or
memory-leak acceptance is claimed from this mostly static observation.

## Remaining Settings blocker

Settings and General rendered, but tapping About did not visibly navigate.
An immutable RAM clone captured while stopped, then analyzed offline, shows
Preferences PID 423's main thread waiting for synchronous NSXPC reply inside
`CTCellularPlanManager planItemsShouldUpdate:`, reached through
`PSUICellularPlanManagerCache _fetchPlanItemsIfNeeded` and
`isActivationCodeFlowSupported` while loading a preferences controller.
See `rev3-about-stacks/preferences-main.txt` and `process-stacks.json`.

The clock/presentation continued. The final Home press was acknowledged and
dispatched with no timeout, but its screenshot still showed General; a changed
frame alone does not prove Home recovery. Thus full Settings/About and post-About
UI recovery remain unresolved despite the fixed GPU cycle. Next inspect the
cellular service endpoint and reply path, and SpringBoard's response to Home,
before applying another driver change. No cellular bypass was installed.

The interactive VM was left running for inspection; software fallback and the
original boot bootstrap are preserved.
