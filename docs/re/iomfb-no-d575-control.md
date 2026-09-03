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

## Conclusion

Omitting D575 prevents the known false external-display assertion without
regressing persistent storage or boot health, but it does not create the
missing producer event.  The blank display in this control occurs before
H17P `swap_submit`, not in the D575-selected cancellation block, generic
surface mapping, or A407/A408 submission.  A D575 variant with only its
trailing Boolean cleared can test hotplug state safely, but cannot by itself be
treated as a fix for the absent upstream swap producer.
