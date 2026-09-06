# Software display surface cache and native swap completion

Evidence: iPhone17,3 24A5430a, `firmware/bootkc`, shared-cache slide
`0x4b30000`, kernel slide `0x20000000`. These are checkpoint experiments,
not a claim that the VM displays Setup. All runs retain the migrated seed
lineage from `/tmp/dvm/data-seed/root-welcome-checkpoint1.qcow2`.

## Allocation gate

QuartzCore `Server::render_update` (`0x184776174`) selects the software
renderer and sets display flags `+0x3b8` bit 10 at `0x1847761a4`.
`IOMFBServer::set_creates_cached_surfaces(bool)` (`0x18477eb3c`) identifies
this bit. `IOMFBDisplay::create_surface` (`0x1847ae2cc`) chooses cache mode
`0x400` when the bit is set, otherwise `0x700` when commpage capability
`kHasUCNormalMemory` (`0xfffffc020 & 0x800`) is present. At `0x1847ae308`,
w6 carries that choice into the allocator.

`CAIOSurfaceCreate` (`0x18453359c`) inserts `kIOSurfaceCacheMode` at
`0x184534a30`. IOSurface's kernel property parser at
`0xfffffff00a2902a8..0xfffffff00a2902d0` stores it at surface `+0x9c`.
The primary mapping function `0xfffffff00a0b910c` calls its getter and
compares the result with `0x700` at `0xfffffff00a0b9190`. Mismatch returns
`0xe00002d1` (`kIOReturnBadMedia`), before any A408. This is not a
compression-only check.

`DISPLAY_RT_R23` observed an explicit `0x400` request, display flags
`0x9780`, and a commpage supporting `0x700`. Both the native IOSurface
and its region carried `0x400`. Thus this was not a missing region.

In `DISPLAY_RT_R24`, `tools/re/surface_cache_probe.py` was explicitly
enabled to request `0x700` at the allocation boundary. It changes no
existing memory mapping, validation branch, or return value. The kernel
surface then carried `0x700`, primary mapping returned 0 at runtime
`0xfffffff02a0c40b4`, and native submission returned 0 at
`0xfffffff02b28f100`.

Evidence files:

- `/tmp/dvm/DISPLAY_RT_R24.events/surface-cache-request.json`
- `/tmp/dvm/DISPLAY_RT_R24.events/primary-map-return.json`
- `/tmp/dvm/DISPLAY_RT_R24.events/swap-submit-return.json`
- `/tmp/dvm/checkpoints/DISPLAY_MIGRATION_RETURN17_TIMING/restores/DISPLAY_RT_R24/qemu.stderr.log`: RPC #293145, first A408, input `0xff4`, output 12.

This is still a diagnostic allocation adaptation. A durable software-display
policy has not been installed in the image.

## Completion ABI

The M1 reference's callback numbers do not match this kernel. Derive these
from the current dispatch table:

| Callback | Current handler | Meaning established by code |
| --- | --- | --- |
| D582 | `0xfffffff00a0da3ec` | 12-byte packed head-of-line completion: u32 ID at 0, Boolean at 4, u32 at 5, Boolean at 9; calls `0xa0c5cac` / `0xa0c5728`. |
| D589 | `0xfffffff00a0daa9c` | `0xe4` input / `0xe0` output, timing record with null flag at `0xe0`; calls `0xa0bb73c` / `0xa0bb660`. |
| D594 | `0xfffffff00a0db034` | Full completion, `0x730` input, no required output; calls `0xa0c58bc`. |

D594 input: swap ID at 0, cancellation Boolean at 4, optional 34-byte
swap-info at 5, eight `0xe0` timing records at `0x27`, record count at
`0x727`, Boolean at `0x72b`, and swap-info null flag at `0x72c`.
The current callee does not consume the sixth argument at `0x72b`.
`0xa0c58bc` locates the ID at each queue item's `+0x3d4`, starting from
framebuffer `+0x4138`. It reports optional timing records, completes the
planes, removes the queue item, and wakes waiters. A zero cancellation
Boolean follows the plane-completion path; the cancellation path is also
independently called with 1 at `0xa0c7258`.

D589's downstream live virtual chain is framebuffer vtable `+0xaa0` ->
`0x918a74c` -> object at framebuffer `+0x5e88`, vtable `+0xb8` ->
`0x917b6d0`. It packages timestamps and swap ID (`record+0x38`) for
registered clients. It does not remove the pending swap queue item.

## Successful one-shot D594 experiment

`DISPLAY_A408_PENDING24` saves the first native A408 pending in queue
`0xffffffdedde74000`, ID 0, mask `0x80000007`. RAM/device capture took
2.556 seconds (13.154 including flush/hash/ownership checks).
`DISPLAY_COMPLETE_R25` restored it in 1.366 seconds.

`tools/re/host_swap_completion_probe.py` appended one D594 to the already
completed callback script, under a verified QEMU BQL. It used the observed
ID 0, zero timing records, null swap-info, and the existing callback
transport. No AP queue or guest return was patched. The native handler
returned 0 at `0xfffffff02a0cf200`; framebuffer `+0x4138` changed from the
pending item to NULL. The AP acknowledged callback #3 with status 0.

Evidence:

- `/tmp/dvm/DISPLAY_RT_R24.pending-swap.bin`
- `/tmp/dvm/DISPLAY_COMPLETE_R25.events/d594-entry.json`
- `/tmp/dvm/DISPLAY_COMPLETE_R25.events/d594-return.json`
- `/tmp/dvm/DISPLAY_COMPLETE_R25.host-events.jsonl`
- `/tmp/dvm/checkpoints/DISPLAY_A408_PENDING24/restores/DISPLAY_COMPLETE_R25/qemu.stderr.log`

The first host expression failed compilation because GLib return types were
absent from LLDB debug information; explicit return casts fixed the probe.
That compilation failure did not send a callback.

Open work: display-client timing, surface DMA/scanout, and visible Setup
pixels. An A408 accepted by a zero-reply model or a successful completion
alone is not evidence of a visible frame.


## A408 swap-ID provenance (six-CPU preparation)

Static iOS 27 IOMobileGraphicsFamily-DCP disassembly identifies the ID without
assuming the first swap is zero. At `0xfffffff00a0c366c`, the input record in
x1 is retained as x24. `0xfffffff00a0c3730` loads w19 from `[x24,#0x98]`;
`0xfffffff00a0c3744` stores it into the pending queue item at `[x23,#0x3d4]`.
The A408 argument setup at `0xfffffff00a0c4644` uses that same input record. Its
wrapper copies the first `0x6e0` bytes verbatim into the wire payload at
`0xfffffff00a0c9088..0xfffffff00a0c9098`. Thus A408 wire `+0x98` supplies
the ID D594 must echo. This establishes the identifier location; it does
not establish a refresh interval or prove that a surface contains pixels.

The wrapper's four optional surface descriptors occupy `0x22c` bytes each,
starting at wire `+0x6e0`; null flags are at `+0xfeb..+0xfee`. The main
record's null flag is `+0xfea`. Preserve those flags when interpreting a
capture instead of treating zero-filled optional descriptors as mapped
surfaces. Disassembly outputs: `/tmp/dvm/DISPLAY_SMP6.a408-wrapper.txt`,
`/tmp/dvm/DISPLAY_SMP6.a408-caller.txt`, and
`/tmp/dvm/DISPLAY_SMP6.map-submit.txt`.

## Six-core visible scanout (2026-09-04)

`DISPLAY_SMP6_VISIBLE_R6` restored the new model's in-flight D594 from
`/tmp/dvm/checkpoints/DISPLAY_SMP6_D594_ACTIVE5/manifest.json` in 1.367 s.
At native handler return `0xfffffff02a0db164`, x0 was zero and the pending
queue at framebuffer `0xffffffeccdfec000 + 0x4138` changed from head
`0xffffffeb03397000` to zero. Model stderr records `swap id 0 D594 completed,
status 0x0`. This verifies both callback execution and migration of an active
completion, rather than just successful enqueueing.

The next native `fb_swap_set_layer` at `0x19940638c` supplied IOSurface
`0x751ad39b00`: 1179x2556, BGRA (`0x42475241`), stride 4864, allocation
12,435,456. Its 12,432,384 captured bytes have SHA-256
`4526e5168a2217c1109f89d82f2ac5ad0eadc3d585ec94be3680afd159ece6c3`.
`/tmp/dvm/DISPLAY_SMP6_VISIBLE_R6.frame.png` shows the battery, home indicator,
and Setup information button; the central Hello is still absent. There are
5,935 pixels with a channel above 32, versus the earlier fade-in's peak of 2.
No synthetic pixels or Welcome bypass produced this frame. The allocation-time
cache-mode 0x700 diagnostic remains enabled and is not yet permanent policy.

`DISPLAY_SMP6_FIRST_VISIBLE6` saves this exact pre-submit point (six CPUs,
2.525 s migration, 13.220 s total capture). `DARWIN_DCP_IOMFB_SCANOUT=1`
adds a host display sink for the measured single-primary BGRA A408 profile:

- Descriptor starts at wire +0x6e0; packed fourcc +0x0b, stride +0x15,
  width +0x21, height +0x25, allocation size +0x29. These values match the
  native IOSurface accessors and the captured R5 A408 body.
- Primary DVA is wire +0xf90, following native packing at `0xfffffff00a0c9128`.
  Null flags at +0xfea..+0xfee must indicate main and primary present, the
  other three absent. Other formats/layers are rejected, not guessed.
- Pixel DMA uses **dart-disp0, SID 0**, MMIO `0x412300000`, separate from
  the RPC heap's dart-dcp/SID 23. A 1 MiB read from DVA `0x10000000000`
  byte-matched the prior native IOSurface (SHA-256 prefix
  `d3fa79f9d36197e59ccd7093ee44c8bd05f6e02e8500608fda76e196003ca840`).
  Evidence: `/tmp/dvm/DISPLAY_SMP6_VISIBLE_R6.disp0-1m.bin.json`.
- Translate each 4 KiB boundary, validate stride/size/overflow, copy rows into
  host-owned display storage before D594 can release the mapping. Guest boot
  framebuffer RAM is untouched. An optional `darwin-fb/scanout` VMstate section
  preserves the latest pixels, with destination geometry/size equality checks.

`DISPLAY_SMP6_COCOA_R8` resumed the saved frame in 1.577 s with the Cocoa
window. Its stderr lines 121-124 record presentation of 1179x2556 BGRA from
DVA `0x10000bfc000` followed by D594. HMP screendump RGB bytes exactly match
the native capture: SHA-256
`3a2754c428f5466d7e9fc3a34719e62de3b576cc2b7b9aa3acf5f0e96a383446`.
Caution: this QEMU revision's `ui/ui-qmp-cmds.c:ppm_save` incorrectly writes
pixman row padding into PPM (3540 rather than 3537 bytes for width 1179).
The byte comparison strips these three padding bytes per row; prefer PNG
screendumps. This export issue is independent of the live console pixels.

Validation: three IOMFB contract tests (completion, malformed requests, bounded
BGRA scanout), all 21 SKS tests, and all 25 host checkpoint tests pass.
Restore now accepts explicit `--model-env DARWIN_...=...` and `--display cocoa`
or `--display cocoa,zoom-to-fit=on`, preserving guest CPU/machine arguments and
recording the resulting argv/environment in the restore report.

`DISPLAY_SMP6_QEMU_SCANOUT8` saves both host pixels and in-flight D594 at
CPU3 PC `0xfffffff02a0db034` (2.528 s migration, 13.178 s total).
`DISPLAY_SMP6_RESIZABLE_R9` restored it in 1.467 s with
`--display cocoa,zoom-to-fit=on`. Its PNG screendump shows the same correctly
aligned frame before any guest execution, verifying scanout-state migration.
PNG: `/tmp/dvm/DISPLAY_SMP6_RESIZABLE_R9.qemu.png`. LLDB GDB port 1395,
monitor `/tmp/dvm/DISPLAY_SMP6_RESIZABLE_R9.restore.sock`; the guest then resumed
with the allocation cache diagnostic and watchdog tracing enabled.

R9 subsequently submitted 58 frames in 27.330 wall-clock seconds: **2.12
presentations/s** under the current tracing and cache-allocation probes. This
counts accepted A408 presentations, not unique images or full-animation FPS.
Evidence: `/tmp/dvm/DISPLAY_SMP6_RESIZABLE_R9.fps-result.json`. The running PNG
now shows the native rotating-language swipe-up instruction as well as the
battery, info button and home indicator; central Hello remains black.
