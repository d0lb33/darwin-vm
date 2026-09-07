# Minimal cellular-plan service (24A5430a)

This is an opt-in provider for `com.apple.CellularPlanDaemon.xpc`, available
system-wide. It supplies an empty no-modem plan model through the guest's own
`CTCellularPlanClient` NSXPC protocol. It does not patch Settings, emulate radio
hardware, advertise a fake carrier/signal, or replace the other CommCenter
endpoints. Unsupported requests invalidate the connection so the client gets
an XPC error instead of an invented successful result or an unanswered request.

The checked-in encoding inventory is static protocol metadata read from a
frozen exact-guest process, not a trace of all methods being called. The provider
implements the narrow collection/flow queries in `DVMPlanContract`; it does not
claim all 78 methods work. The guest's protocol and private framework are loaded
at runtime. Empty collections carry no plan objects. Flow mask `0x8000` disables
activation in the inspected Settings consumer and leaves positive flow bits clear.

## Evidence inputs

`/Users/jdolbe1/dvm-artifacts/research/cellular-wait-20260907` contains:

- `snapshot1`: Preferences main thread waiting synchronously in
  `CTCellularPlanManager planItemsShouldUpdate:`. CommCenter also had two threads
  waiting for CoreLocation XPC replies; causal connection to this plan request
  was not established. SpringBoard's main thread was in its run loop.
- `cellular-plan-manager.txt`: exact image disassembly; `_connect_sync` at
  `0x2572f4fa8` configures `CTCellularPlanClient`. The synchronous plan call is at
  `0x2572f5fe8`.
- `cache-fetch-full.txt`: Settings cache consumer at `0x2a2961ed0`; plan,
  dangling-plan and pending-transfer calls at `0x2a2961f54`, `0x2a2962004`,
  `0x2a296208c`. Flow-mask bit handling at `0x2a296245c` onward.
- `protocols.txt`, `protocol-contract.json`: selectors and extended encodings.
  `collect_protocol.py` reproduces the checked-in inventory from this snapshot.
- `foundation-objc.txt`: the exact guest includes
  `NSXPCListener initWithMachServiceName:` despite public SDK unavailability.
- `host-test2.log`: 80 synchronous NSXPC replies and an unsupported provisioning
  request producing a connection error, under a ten-second process deadline.

The first guest binary failed before main: snapshot bytes at RAM file offset
`0x8372cd10` contain a dyld error naming `_protocol_getMethodDescription` and
the wrong expected provider, libSystem. `dyld-failure.txt` preserves the surrounding
bytes. This was a linker-declaration defect, not evidence that the iOS service
contract was impossible. The builder now derives import providers from exact-cache
exports rather than symbol-name guesses; build4 binds that symbol to libobjc.

The rejected Settings-only patch was never installed. `stage1` is an unused
artifact of that abandoned candidate, and is not an input to the service runs.

## Exact-guest acceptance

`CELL_SERVICE3` cold-booted a child of `CELL_SERVICE_INSTALL2`, using build4 of
the provider and the original backboardd boot carrier. The validated GPU queue
fix was staged as revision 3 into backboardd PID 365. The service remained PID
68; `service-allproc/service.json` and `symbolicated.txt` show it in its normal
run loop. Its `/dev/console` logging did not appear in the collected UART log;
absence of those strings was not evidence of a second startup failure.

`about-after.png` shows the real Settings About page, including iOS 27.0,
Carrier Not Available and no modem firmware. `home-after-about-after.png` shows
the subsequent return Home. No Settings/shared-cache instructions were changed.
The `cellular-about` capture verifies exact source-to-console delivery with zero
conversion and display errors. At 791 seconds the new GPU generation had 3,119
presentations and native completions, with no host errors, ownership failure or
quarantine. A bounded texture-policy rejection was recorded separately.

This establishes the previously blocked UI path in this isolated configuration.
It does not trace each of the twelve implemented selectors individually in iOS,
prove every telephony client works, or establish sustained animation performance.
Initial app launch and some navigation captures were delayed; a five-second
screenshot occasionally still showed the prior page. The second tap intended
for General reached AirDrop because the first navigation had subsequently
completed. Do not classify that observation as another cellular deadlock.

## Build and isolated installation

Use unique output paths. `prepare.py` only mounts the copied restore ramdisk
through `safe_attach.sh`; System modifications happen inside a disposable restore
VM. The installer checks the original launch cache and replacement bytes.
It moves only the plan endpoint out of the cellular CommCenter launch job;
the noncellular job did not publish this endpoint in the exact cache.

```sh
python3 tools/comm/build.py NEW_BUILD --stubs BOOTSTRAP_BUILD/stubs
python3 tools/comm/prepare.py NEW_BUILD BOOTSTRAP_STAGE/launchd.plist \
  BOOTSTRAP_STAGE/system.tc NEW_STAGE
python3 tools/gpu/run_guest_install.py --manifest ORIGINAL_GPU_MANIFEST \
  --stage NEW_STAGE --tag UNIQUE_INSTALL --mmio-restore
python3 tools/comm/derive_gpu_manifest.py ORIGINAL_GPU_MANIFEST \
  /tmp/dvm/UNIQUE_INSTALL/warm-manifest.json NEW_GPU_MANIFEST
python3 tools/comm/test_prepare.py
```

Retain the original manifest as rollback. This is not enabled in the default
rootfs profiles. Keep exact guest runtime results separate from host tests.

## Preserved validated package

Evidence root: `/Users/jdolbe1/dvm-artifacts/research/cellular-wait-20260907`.
`validated-package/manifest.json` references preserved copies of the cellular
service and GPU bootstrap overlays. The live run used the original boot carrier
followed by dynamic GPU revision 3 staging; the preserved boot carrier alone does
not include that GPU revision.

The accepted `build4/dvm-cellular-plan` binary SHA-256 is
`6f04455f0ef856236ad87d4f2361894209480f6b502ca8e1972b2c7f6448c3d5`.
