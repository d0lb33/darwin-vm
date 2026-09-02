# DCP bring-up notes

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (iPhone 16, t8140/H17P),
kernelcache `firmware/bootkc`, device tree `DeviceTree.d47ap`.

## Summary

On A14+ the display is driven by the DCP, a coprocessor running Apple's RTKit
RTOS. The AP talks to it through an ASC mailbox; the display drivers
(`AppleDCPExpert`, `AppleCLCD2`, `IOMobileFramebuffer`) ride on RTKit endpoints.
Getting a picture out of iOS userspace means emulating that coprocessor's
AP-facing protocol, not a framebuffer.

## Device tree shape

`dt_fixup.py` normally strips `compatible` from everything it does not emulate,
which is what keeps unmodelled drivers from binding. `-enable dcp` keeps it on
the display chain:

| Node | compatible | Bound driver |
|---|---|---|
| `/arm-io/dcp` | `iop,ascwrap-v6` | `AppleASCWrapV6` |
| `/arm-io/dcp/iop-dcp-nub` | `iop-nub,rtbuddy-v2` | `RTBuddy` |
| `/arm-io/dcp0-expert` | `dcp-expert-v1` | `AppleDCPExpert` |
| `/arm-io/disp0` | `disp0,t8140` | `AppleCLCD2` |
| `/arm-io/dart-dcp`, `dart-disp0`, `dart-dispgrt` | `dart,t8110` | `AppleT8110DART` |

`/arm-io/dcp` reg[0] is `0x202e00000` (+iobase), 4 interrupts:
`0x2a8, 0x2a7, 0x2aa, 0x2a9`.

## Two blockers found and fixed

**SPTM requires `dart-id`.** SPTM bootstraps every DART whose node still has a
`compatible` and expects iBoot to have assigned each one a unique `dart-id`
property. Without it SPTM spins in a branch-to-self with the message
(read from guest memory at `x3`):

```
.rt.c:t8110dart_bootstrap_instance:2574: error -1 getting dart-id
```

`dt_fixup.py` now numbers the kept DARTs. Note this is an SPTM-level panic, so
nothing appears on the serial console — it must be read out of the frozen guest.
`tools/probe.sh` does this automatically.

**An unmodelled MMIO write panicked the kernel.** `AppleDCPExpert` writes `0x10`
to `dcp0-expert` reg[1] (`0x308008000`, a 4-byte register). With nothing backing
that address the access raised a synchronous external abort, which XNU routed
into its hardware-error path and indexed out of bounds through the
`hwerr_type_*` dispatch table, panicking as:

```
panic(cpu 0 caller ...): PC alignment exception from kernel.
  at pc 0xfffffff02706e459, lr 0xfffffff02ac937c8
```

The misaligned PC is a string address — the branch went through a table entry
one past the end. `darwin_unimp.c` now backs every `/arm-io` range at low
priority, so unknown MMIO reads zero and remembers writes instead of faulting.

With both fixed, the DCP-enabled tree boots to a shell with no panic and gets:

```
RTBuddy(DCP): start(<ptr>) - (Aug 13 2026@22:18:01)
IOMFB: service matched: AppleDCPExpert
IOMFB AP: use_psd_dcp_power2: 0
```

## RTKit endpoints on the DCP

From `IOKitPersonalities` in the kernelcache `__PRELINK_INFO`. XNU names an
endpoint `DCPEndpoint<N>` where `N = endpoint - 0x1f`:

| Endpoint | XNU name | Driver | Framing |
|---|---|---|---|
| 0x00 | — | RTKit management | hello / epmap / start / power |
| 0x01–0x0a | — | crashlog, syslog, debug, ioreport, oslog, tracekit | RTKit system |
| 0x20–0x36 | `DCPEndpoint1`..`23` | `DCPEndpointV2` | AFK / EPIC |
| 0x37 | `DCPEndpoint24` | `AppleDCPLinkServiceSoC` | IOMFB link |

Sub-services attach by `EPICName` on `AFKEndpointInterface`, so the EPIC layer
must report service names: `dcpav-controller-epic`, `dcpav-device-epic`,
`dcpav-service-epic`, `dcpav-video-interface-epic`, `dcpav-power-epic`,
`dcpdptx-port-epic`, and others in `DCPAVFamilyProxy` / `AppleDCPDPTXProxy`.

`AFKFirmwareService` (`IOPropertyMatch role=DCP`, provider `RTBuddyService`)
tries to load real DCP firmware. Ours is not a real coprocessor, so this is a
known open question: either satisfy it or keep it from matching.

## Open questions

- What does `AppleDCPExpert` expect to read back from `dcp0-expert` reg[0..16]?
  Currently every access is a logged no-op and only one write was observed.
- Exact AFK/EPIC framing for iOS 27. m1n1's `fw/afk` and `fw/dcp` implement it
  for macOS-era firmware; the version negotiation may differ.
- Whether `AppleCLCD2` can be driven without real DCP firmware, or whether the
  emulation must impersonate the firmware's responses end to end.
