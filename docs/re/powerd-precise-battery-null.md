# powerd precise battery read with no physical battery

The frozen `WARM_STABILITY_FRESH1` run retains two powerd fault states with
the same instruction and register values. PID 51's image is based at
`0x104170000`; its PC is `0x104174ea4`, with `x8 = 0`, FAR `0x20`, and ESR
`0x92000006` (EL0 read translation fault). PID 324 retains PC `0x100f68ea4`
and the same `x8`, FAR, and ESR; its executable mapping is already gone.
PID 51's intact mapping establishes the static location `0x100004ea4`.
Evidence: `/tmp/dvm/WARM_STABILITY_FRESH1/process-stacks.json` and its full
frozen `ram` directory. These are fault states, not guessed PCs from nearby
stack symbols.

## Exact native cause

`copy_powersources_info` begins at static `0x100004d54`. It iterates the
40-byte `gPSList` entries at `0x1000b9090`, filtering source type. It makes a
mutable copy of each InternalBattery description. With `preciseInfo` true,
it then assumes `control.internal` exists:

```asm
0x100004e98  tbz  w28, #0, 0x100004eb4  ; non-precise path
0x100004e9c  adrp x8, 0x1000b9000
0x100004ea0  ldr  x8, [x8, #0x330]      ; control.internal
0x100004ea4  ldr  x2, [x8, #0x20]       ; preciseDescription: fault
0x100004ea8  mov  x0, x19               ; mutable description
0x100004eac  bl   0x10007b6e0           ; addEntriesFromDictionary:
0x100004eb0  b    0x100004fa4           ; common health/append path
```

The native structure layout and branch match Apple's PowerManagement
`pmconfigd/BatteryTimeRemaining.m:5106-5165`: the precise branch dereferences
`control.internal->preciseDescription` without checking `control.internal`.
That source is preserved under `/tmp/dvm/POWER_SOURCE1/PowerManagement-src`.
The exported publisher successfully registers a user-space InternalBattery,
but there is no physical battery to populate `control.internal`. A precise
consumer can therefore crash powerd while reading the otherwise valid source.
The observed filter is internal (`x20 = 0`) and precise flag is one
(`x28 = 1`), consistent with BatteryCenter's precise internal-source query.

## Guarded correction

The function already holds the `0x1000b9000` global page in callee-saved `x23`
from `0x100004d9c`, with no intervening write before this block. Two words can
therefore add the missing null check without a trampoline:

```asm
0x100004e9c  ldr x8, [x23, #0x330]
0x100004ea0  cbz x8, 0x100004fa4
```

If the physical object exists, the original precise merge still runs. If it
does not, the already copied virtual-source dictionary continues through the
common health/append path. Non-precise requests keep their original branch.
This adds no invented physical-battery fields and keeps the published 100/100
virtual source intact.

`tools/re/patch_powerd_virtual_battery.py` requires the exact original file
SHA-256 `31254642770ac63ce22a55a5717246cf2289297f478f7ab753d15b6f88436268`
and a 16-byte instruction preimage at file offset `0x4e98`. Only the middle
eight code bytes change. Host codesign regenerates the signature and preserves
identifier, entitlements, requirements, flags, and runtime metadata; the
original and new entitlement plists compare byte-identically.
The new file SHA-256 is
`05c8c33ebbcfa8c52411e888c1511d599ad37ebe7ebdfe08548eed035f969d7d`.

The 844752-byte original came from an in-guest read-only System mount and a
contiguous CRC-checked serial export, then passed host signature verification.
The export initially tried `/usr/libexec/powerd`, which is absent; the successful
recorded command reads `/System/Library/CoreServices/powerd.bundle/powerd`.
No full System image was mounted on the host. Export artifacts are under
`/tmp/dvm/POWERD_EXPORT1` and `/tmp/dvm/POWERD_EXPORT_RUN1`.

## Correcting the publisher observation

The missing serial `POWER_PV_VERIFY` line was not proof of a blocked verifier.
Frozen PID 41's actual `_publisher` at `0x1040ac360` has `ready = 1`,
`initial_publish_complete = 1`, `stopping = 0`, retry generation 7, and a source
whose native psid is 5001. The first logged Create had a different source
pointer. `/tmp/dvm/WARM_STABILITY_FRESH1/publisher-state.json` records these
bytes. This proves the local publication transaction completed; it does not
prove current broker ownership after a later powerd crash or correct UI.

The publisher now retains a separate console descriptor and uses `dprintf`,
following the activation helper's existing pattern, so framework changes to
stderr cannot hide lifecycle records. `POWER_PV_RECOVERY_BUILD7` contains this
logging correction. The powerd guard and helper are staged together in
`POWERD_VIRTUAL_INSTALL1`; fresh runtime validation is required before calling
powerd stability, battery UI, or Setup completion fixed.

## Fresh disk validation: WARM_POWERD_GUARD1

The fresh disk boot uses the guarded powerd and publisher BUILD7, with no
RAM restore or debugger endpoint. Early boot completes at 12.590 seconds and
the first scanout appears at 117.239 seconds, with zero kernel panics. Native
input opens Settings and its Battery page. The original powerd PID 51 remains
present with seven ordinary waiting threads in the complete frozen RAM capture;
there is no second powerd candidate or retained `+0x4ea4` fault in this capture.
`profiled` PID 155 has two threads, unlike the earlier Setup diagnosis with
24 callers queued behind power-management work.

Publisher initial and resync transactions each report Create=0, Set=0,
exact-source matches=1, and READY. The retained console descriptor makes the
whole transaction observable (`serial.log:648-843`). The status-bar battery
is now full and green on lock screen, Home, and Settings. This does not yet
prove all battery consumers agree: Settings > Battery still displays 0%,
and enabling its Battery Percentage switch does not correct that reading.
Screenshots: `WARM_POWERD_GUARD1/battery/final.png` and
`percentage/final.png`. Process evidence: `process-stacks.json`, cache slide
`0x0dafc000`, and the complete 12 GiB `ram` capture.
