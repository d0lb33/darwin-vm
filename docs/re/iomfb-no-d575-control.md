# No-D575 display control

## Scope

`UI_NO_D575_SWAP1` tested whether the scripted D575 hotplug callback was the
only thing preventing a real swap.  It used a fresh qcow2 child
`/tmp/dvm/data-seed/ui-no-d575-swap1.qcow2` of
`sks-op09-complete-2.qcow2`, the persistent-NVMe device tree
`/tmp/dvm/data-seed/dt_nvme_welcome.bin`, and the merged system-volume trust
cache.  The framebuffer was `1179x2556`, `DARWIN_DCP_IOMFB=4`, RPC tracing was
enabled, and the callback list contained only:

```
D120::4,D586:9b040000fc090000:4
```

The established method overrides were unchanged:

```
A401=01,A000=01,A454=01000000,A033=4152474200000000000000000000000000000000000000000000000000000000000000000000000001000000,A453=9b040000fc090000,A412=01000000
```

The complete logs are
`/tmp/dvm/probe/UI_NO_D575_SWAP1.serial.log`,
`/tmp/dvm/probe/UI_NO_D575_SWAP1.stderr.log`, and
`/tmp/dvm/UI_NO_D575_SWAP1.lldb.log`.

## Swap-path instrumentation

LLDB attached after QEMU opened its GDB socket and armed these runtime
addresses from `iomfb-d575-external-cancel.md` before userspace display
activity:

- H17P `swap_submit` entry/check/cancel:
  `0xfffffff02918e3c4`, `0xfffffff02918e42c`,
  `0xfffffff02918e438`, `0xfffffff02918e488`.
- Generic submit entry and implementation:
  `0xfffffff02a0c366c`, `0xfffffff02a0c36ac`.
- Primary/secondary `surface_map_dcp` call and return edges:
  `0xfffffff02a0c40d4`, `0xfffffff02a0c40d8`,
  `0xfffffff02a0c41cc`, `0xfffffff02a0c41d0`.
- Post-fence and A408/A407 selection edges:
  `0xfffffff02a0c44c8`, `0xfffffff02a0c452c`,
  `0xfffffff02a0c45fc`, `0xfffffff02a0c4664`,
  `0xfffffff02a0c477c`.

The debugger log installs all 15 breakpoints through line 58 and resumes at
line 59.  It contains no breakpoint stop or recorded marker.  Its only later
stop is the probe's final HMP stop, reported as SIGINT at lines 61-64.
Therefore H17P `swap_submit` did not enter, its external-display cancellation
branch did not run, and execution never reached the generic submit or either
surface-map call.

The independent RPC trace agrees: it contains zero A407, A408, D589, D591, or
`surface_map` records and zero D575 records.  A385 remained active: its first
request is at stderr lines 2327-2331, and it reached occurrence 6771 at lines
37121-37128.  D120 and D586 were each delivered and completed with status zero
at stderr lines 189-190, 284-286, and 289.

## Boot and framebuffer witnesses

The 180-second guest boot had no first XNU panic and no `Copying` line.  It
mounted persistent Preboot, Data, Hardware, and User volumes at serial lines
428, 488, 496, and 588, then reached `Early boot complete` at line 639.
The IOMFB user client mapped display type 0 at serial line 651.  The fresh
overlay grew from 197,120 bytes to 24,969,216 bytes during the run.

While HMP still reported `VM status: running`, a screendump was captured near
153 seconds:

| Artifact | SHA-256 | Pixel result |
|---|---|---|
| `/tmp/dvm/UI_NO_D575_SWAP1-live.ppm` | `43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb` | all 9,048,240 RGB bytes are zero |
| `/tmp/dvm/UI_NO_D575_SWAP1-live.png` | `751b436d4a028fa873ce9bbc5bbac0d943765aff73631917b3b48230117798ed` | PNG conversion of the same black frame |

The guest was then stopped by the exact
`unix:/tmp/dvm/UI_NO_D575_SWAP1.sock` tag; no QEMU process was left behind.

## Paired D575 trailing-Boolean-zero control

`UI_D575_BOOL0_1` repeated the same 180-second boot with a fresh child
`/tmp/dvm/data-seed/ui-d575-bool0-1.qcow2`.  Its callback sequence was:

```
D120::4,D586:9b040000fc090000:4,@A385,D575:01000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000001000000:76
```

The hex decodes to an 88-byte D575 input, not a 76-byte input: only offsets
`+0` and `+0x54` are one.  Thus event remains one, the trailing Boolean at
`+0x53` is zero, and the optional-null flag at `+0x54` remains one.  The final
`:76` is the callback output length.

The first A385 request and barrier release appear at
`/tmp/dvm/probe/UI_D575_BOOL0_1.stderr.log:2227-2232`.  D575 was then sent and
transport-completed with status zero at lines 2233-2237.  This is only a
transport witness.  LLDB armed the concrete hotplug target
`0xfffffff02a0bcad8`, assertion post-latch edge `0xfffffff02a0bcb94`, and
Boolean-zero edge `0xfffffff02a0bcba0`, followed by the same 15 swap/map
breakpoints, at `/tmp/dvm/UI_D575_BOOL0_1.lldb.log:14-71`.  None hit.  The
debugger's only subsequent stop was the probe's HMP SIGINT at lines 77-80.
Consequently this run did not provide a value for `self+0x430`; callback
completion must not be reported as proof that the concrete hotplug target
latched or preserved that byte.

There were independently zero A407, A408, D589, D591, or `surface_map`
records.  A385 continued through occurrence 6779 at stderr lines 36962-36965.
Persistent storage and boot remained healthy: Preboot, Data, Hardware, and
User mounted at `/tmp/dvm/probe/UI_D575_BOOL0_1.serial.log:428`, `:488`,
`:496`, and `:588`; Early Boot completed at line 639; display type zero mapped
at line 648; and searches found zero `panic(cpu` and zero `Copying` lines.  The
fresh overlay grew from 197,120 to 19,398,656 bytes.

The live screendump near 179 seconds was again entirely black and
byte-identical to the no-D575 control:

| Artifact | SHA-256 | Pixel result |
|---|---|---|
| `/tmp/dvm/UI_D575_BOOL0_1-live.ppm` | `43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb` | all 9,048,240 RGB bytes are zero |
| `/tmp/dvm/UI_D575_BOOL0_1-live.png` | `751b436d4a028fa873ce9bbc5bbac0d943765aff73631917b3b48230117798ed` | PNG conversion of the same black frame |

The full paired logs are
`/tmp/dvm/probe/UI_D575_BOOL0_1.serial.log`,
`/tmp/dvm/probe/UI_D575_BOOL0_1.stderr.log`, and
`/tmp/dvm/UI_D575_BOOL0_1.lldb.log`.  The guest was stopped by the exact
`unix:/tmp/dvm/UI_D575_BOOL0_1.sock` tag.

## Conclusion

Correction: the statement that all swap/map breakpoints were zero is invalid
as a debugger conclusion.  One matching stop was hidden by the LLDB 21
repeated-`-o` command trap; see `docs/re/lldb-breakpoint-command-trap.md`.
The independent RPC trace still has zero D575/A407/A408/D589/D591/surface-map
records, and the storage, no-panic, and entirely black framebuffer witnesses
remain valid.  The no-D575 control's zero-hit claim is valid only because its
independent trace has no hidden breakpoint dependency; the D575-bool0 swap
localization must be repeated with the corrected callback form.

Omitting D575 prevents the known false external-display assertion without
regressing persistent storage or boot health, but it does not create the
missing producer event.  The blank display in this control occurs before
H17P `swap_submit`, not in the D575-selected cancellation block, generic
surface mapping, or A407/A408 submission.  The paired trailing-Boolean-zero
callback also produced no swap and the same black framebuffer.  Because its
concrete hotplug target did not hit, it rules out that exact scripted transport
as a sufficient producer stimulus but does not establish the post-callback
value of the external-display byte.
