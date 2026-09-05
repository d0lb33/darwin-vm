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
The A408 call at `0xfffffff00a0c4644` receives that same input record. Its
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
