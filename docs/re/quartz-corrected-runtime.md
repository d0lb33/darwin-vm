# Corrected QuartzCore first-surface runtime probe

`UI_QC_CORRECT1` reran the QuartzCore probe with a validated LLDB Python
breakpoint callback.  This supersedes the zero-hit interpretation in
`docs/re/quartz-first-surface-runtime.md`; the earlier command form hid
matching stops.  The guest was iOS 27 beta 8 (24A5430a), iPhone17,3/H17P,
booted from the persistent NVMe image with the established IOMFB level-4
answers and D120/D586 callbacks.

## Same-boot slide and command proof

The resolver observed runtime PC `0x18a0ed5dc`, matched it once in
`dyld_shared_cache_arm64e.01` at file offset `0x1555dc`, and resolved static
PC `0x1805555dc`.  The measured slide was `0x9b98000`, runtime cache base
`0x189b98000`, and `gva2gpa` returned `0x1001e94c000`.  These values are from
`/tmp/dvm/UI_QC_CORRECT1.slide.json`.

The command-list proof is explicit at
`/tmp/dvm/UI_QC_CORRECT1.lldb.log:132`: breakpoint 15 is installed as
`QC_DISPLAY_6490_GATE` using the imported Python callback.  The same callback
produced ordered, thread-attributed hits for `ensure_displays`, its query
call, `query_displays`, internal-display open/count, display construction,
IOMFB open return, CADisplay main entry/return, factory return, the +0x6490
gate, and `fb_swap_set_layer` (`:266-327`, `:334-365`).

## What the corrected run proves

The display list path ran on thread `0x1`: `ensure_displays` entered at
`0x18df7b318`, called the query at `0x18df7b3b4`, and the CAS display query
returned `w0=0` with one display (`display_count_out=1`) at
`0x18e07c84c` (`:272-281`, `:327-339`).  Internal-display open and count ran;
the count returned `x0=1` (`:282-291`).

The IOMFB open returned a non-null value in `x21` and stored an IOMFB member;
the constructed AppleDisplay's `+0x6490` field was non-null
(`0x763f0be490 = 0x000000e763c76800`) at `:297-320`.  CADisplay's global was
non-null on return (`main_global = 0x000000e763d64060`) at `:340-345`.
Therefore the previous strongest hypothesis—failure to discover or retain
the internal display—does not explain this run.

The path reached `fb_swap_set_layer` at runtime `0x18e00a38c`, but its IOSurface
argument was null: `x3=0` (`:321-326`).  No surface allocation, H17P
`swap_submit`, generic submit, `surface_map_dcp`, A407, or A408 hit was
recorded in this run.  The query error edge also ran later with `w0=0x78a808a`
and zero output count (`:346-352`), so the display query has both a successful
one-display result and a later failing/retry result; this is not evidence of
a rendered frame.

The hits are attributed to the debugger's current thread/process context.
They do not by themselves identify whether the originating client was
backboardd, SpringBoard, or another UI process; cross-process attribution
requires a process-aware attach or matching live process metadata.

## Health and artifacts

The serial log selected NVMe `disk1s1` as BSD root at
`/tmp/dvm/probe/UI_QC_CORRECT1.serial.log:314`, ran mount-phase-2 at `:447`,
and reached `Early boot complete` at `:640`.  Searches of the run found no
first `panic(cpu` and no `Copying` line.  The IOMFB/DCP transport remained
active before the debugger stopped the guest; no screenshot from this run is
claimed as a Welcome frame.

| Artifact | SHA-256 |
|---|---|
| `UI_QC_CORRECT1.slide.json` | `9b521e8ae0fb74e2a259c60f6325bd416d4d644ea7cd1c8699c56ac1abe34ed5` |
| `UI_QC_CORRECT1.lldb.log` | `7e5af249f07a927f8c7c14a1125790b442372701346fdbeb282a09f2209b5235` |
| `UI_QC_CORRECT1.setup.lldb.log` | `2eb2d45ce247f5600df0732b011f56552f938ee3d17713f809425dfd62e2c24c` |
| `UI_QC_CORRECT1.serial.log` | `30d3e843248ac62628da565a6ed86241aa6b85d3f1439584684a31aabc4226bf` |
| `UI_QC_CORRECT1.stderr.log` | `437e42bd6e513c6879043d984f21afc68018ca9cc8595f3335d6e42f4e8a8a5c` |
| `UI_QC_CORRECT1-live.ppm` | `43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb` |
| `UI_QC_CORRECT1-live.png` | `751b436d4a028fa873ce9bbc5bbac0d943765aff73631917b3b48230117798ed` |
