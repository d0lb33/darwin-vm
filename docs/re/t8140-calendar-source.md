# T8140 calendar-clock source path

**Status (2026-09-05).** The native route described here is now implemented
and verified: `-enable spmi` plus `qemu-sptm/hw/arm/darwin_spmi.c` and
`darwin_pmu.c`; see [native-rtc-spmi.md](native-rtc-spmi.md) for the
controller evidence and the runs.  One correction to the text below: the
fractional argument of `getGMTTimeOfDay`/`setGMTTimeOfDay` is
**nanoseconds**, not microseconds.  `AppleDialogSPMIPMURTC::getGMTTimeOfDay`
multiplies the residual ticks by 1,000,000,000 and shifts right by 15
(`0xfffffff009622184-0xfffffff009622194`), and the set path converts with
the same constant (`0xfffffff009621d54-0xfffffff009621d74`).

**Scope.** This note is static analysis of the iPhone17,3 / T8140
`24A5430a` artifacts in this checkout.  It records the platform ABI and the
native PMU path that the guest kernel already contains.  It does not claim
that the present QEMU machine implements the SPMI transport, and it does not
use a PMU model from a different SoC as hardware evidence.

## Result

The native path is present and is more specific than the former generic
`AppleARMRTC` lead:

```text
PEGetUTCTimeOfDay
  -> AppleARMPE::{get,set}GMTTimeOfDay
  -> resource named "IORTC" / callPlatformFunction
  -> AppleDialogSPMIPMURTC (AppleSPMIPMU.kext)
  -> AppleDialogSPMIPMU SPMI register read/write
  -> /arm-io/nub-spmi0/pmu-main (Dialog "baku" PMU)
```

`AppleDialogSPMIPMURTC` is a concrete subclass of `AppleARMRTC`; its vtable
overrides the calendar get and set slots.  This is target-build evidence, not
an inferred class relationship: `ipsw kernel cpp --methods` on
`firmware/bootkc` reports the class in `com.apple.driver.AppleSPMIPMU`, with
the inherited `AppleARMRTC` vtable entries and concrete slots at
`0xfffffff0096220f4` and `0xfffffff0096221a8`.  The extracted target kext is
`/tmp/dvm/RTC_SOURCE1.kext/AppleSPMIPMU`.

The current workaround cannot provide time.  `dt_fixup.py:630-642` deliberately
sets root `no-rtc` and creates an empty root `rtc` nub only to bypass the
`IORTC` wait.  It does not create an `IORTC` object which implements the
platform calls below.  This accounts for the zero/epoch behavior noted in
[`warm-boot-stability.md`](warm-boot-stability.md#first-frame-calendar-and-battery-remain-open).

## Exact platform ABI

All addresses below are unslid static virtual addresses in the target
`firmware/bootkc`.  `AppleARMPlatform` was extracted to
`/tmp/dvm/RTC_SOURCE1.kext/AppleARMPlatform` before disassembly.

| Operation | Caller and evidence | Required responder ABI |
| --- | --- | --- |
| get | `AppleARMPE::getGMTTimeOfDay` at `0xfffffff0085cdfd0`: at `0xfffffff0085ce078` it passes the string `getGMTTimeOfDay`, `wait = false`, `x3 = x1` and `x4 = x2` to the `callPlatformFunction` virtual slot.  On a non-zero `IOReturn`, `0xfffffff0085ce0c0` writes zero to `*x2` and `0xfffffff0085ce0c4` writes zero to `*x1`. | `callPlatformFunction("getGMTTimeOfDay", false, uint64_t *seconds, uint32_t *nanoseconds, NULL, NULL) -> IOReturn`.  Return `kIOReturnSuccess` and initialize both pointed-to values. |
| set | `AppleARMPE::setGMTTimeOfDay` at `0xfffffff0085ce0d8`: it saves incoming `x1` (64 bits) and `w2` (32 bits), then at `0xfffffff0085ce17c` passes `setGMTTimeOfDay`, `wait = false`, and pointers to those saved values as parameters one and two. | `callPlatformFunction("setGMTTimeOfDay", false, uint64_t *seconds, uint32_t *nanoseconds, NULL, NULL) -> IOReturn`. |

The resource lookup immediately before each dispatch uses the `IORTC` string
at static `0xfffffff007179510`.  The generic `AppleARMRTC` dispatcher at
`0xfffffff0085db6ec` compares the function names at `0xfffffff0085db724`
and `0xfffffff0085db784`, and calls its virtual slots `+0x578` and `+0x580`.
Its normal get wrapper at `0xfffffff0085dba3c` stores a 64-bit seconds value
and writes a zero 32-bit fractional value.  A calendar provider that
implements the ABI directly should still return real nanoseconds: the native
Dialog implementation does.

`AppleARMRTC::start` at `0xfffffff0085daeac` registers its service and, at
`0xfffffff0085db0e8`, calls the resource-publish path with the literal
`IORTC` and its own service.  Thus an `rtc` DT node alone is not the protocol;
a live IOKit service published as `IORTC` is the required boundary.

Apple's open XNU sources corroborate the outer contract:
[`IOPlatformExpert.cpp`](https://github.com/apple-oss-distributions/xnu/blob/main/iokit/Kernel/IOPlatformExpert.cpp)
contains the default platform time methods and
[`clock.c`](https://github.com/apple-oss-distributions/xnu/blob/main/osfmk/kern/clock.c)
initializes calendar time through the platform expert.  The target addresses
above establish the concrete behavior for this build.

## Native T8140 device-tree facts

The target tree is `firmware/dtree`, decoded by the repository's
`decode_node` routine (which decodes integer properties little-endian).
These values are direct DT facts:

| Node/property | Value | What is established |
| --- | --- | --- |
| `/arm-io/nub-spmi0/reg` | `(0xf8714000, 0x4000)`, `(0xf8704000, 0x4000)`, `(0xf8700000, 0x4000)` | Three controller register ranges, each 16 KiB.  Their individual controller roles are not established here. |
| `/arm-io/nub-spmi0` | `AAPL,phandle = 0x96`, `device_type = interrupt-controller` | The parent used by the PMU interrupt reference. |
| `/arm-io/nub-spmi0/pmu-main/reg` | first 32-bit cell `0x0e`; full raw value is `0e0000000300000000000000040000000000000000000000` | A PMU-side register descriptor.  The target driver/model binding must establish the complete cell format; only the first cell is used as the SPMI slave ID by the reference implementation cited below. |
| `/arm-io/nub-spmi0/pmu-main/hw-name` | `baku` | The PMU hardware name presented by this T8140 DT. |
| `info-rtc` | `0xf802` | The RTC upcount register address used by the target driver. |
| `info-leg_scrpad` | `0xf700` | The legacy scratchpad offset property consumed by the target driver for clock-offset handling. |
| alarm properties | control `0xf800`, control-enable mask `0x40`, event `0xf80c`, alarm value `0xf808`, IRQ-mask offset `0xf80e`, alarm mask `0x1`, monitor mask `0x1` | The target DT supplies these addresses and masks.  Alarm behavior still needs a transport trace before it is implemented. |
| interrupt | `interrupt-parent = 0x96`, `interrupts = 0x2` | The PMU's RTC/alarm-side interrupt relation in the target tree. |

The fixup's normal compatibility-pruning pass (`dt_fixup.py:141-161`) removes
`compatible` properties outside its explicitly selected emulated features.
There is no RTC/SPMI feature in `EMULATED_FEATURES`.  Independently, a source
tree search found no Apple SPMI controller or SPMI bus implementation under
`qemu-sptm/hw` or `qemu-sptm/include`.  Restoring a DT match alone therefore
cannot make the native driver functional.

## Target-driver register semantics

The following is from the actual `AppleDialogSPMIPMURTC` code in the target
`AppleSPMIPMU` kext, so it is the implementation basis for an RTC model.

* The concrete get method is `0xfffffff0096220f4`.  It obtains an upcount,
  adds its calendar offset, stores `total >> 15` as seconds, and converts
  `total & 0x7fff` to nanoseconds at `0xfffffff009622178-0xfffffff009622194`.
  This establishes a **32,768-Hz tick unit** and the seconds/fraction split.
* The concrete set method is `0xfffffff0096221a8`.  It converts the supplied
  seconds and nanoseconds to a desired 32,768-Hz tick count, reads the
  current upcount, and calls its offset-setting path at
  `0xfffffff009621bd8`.  It therefore sets calendar time by changing an
  offset, rather than writing an absolute value into the running upcounter.
* The native read method at `0xfffffff00962255c` asks the PMU transport for
  six bytes at its DT-derived `info-rtc` address.  At
  `0xfffffff009622724-0xfffffff009622750` it decodes them exactly as:

  ```c
  uint64_t ticks = ((uint64_t)b[0] >> 1)
                 | ((uint64_t)b[1] << 7)
                 | ((uint64_t)b[2] << 15)
                 | ((uint64_t)b[3] << 23)
                 | ((uint64_t)b[4] << 31)
                 | ((uint64_t)b[5] << 39);
  ```

  This is a 47-bit, little-byte-order packed counter with bit 0 unused.
* The offset-setting path begins at `0xfffffff009621994`.  Its strings and
  DT-property reads identify the two persistence choices: NVRAM
  (`com.apple.System.rtc-offset`) and the `info-leg_scrpad` path.  The exact
  PMU write transaction for the scratchpad path is now traced: seconds at
  `info-leg_scrpad + 4` (4 bytes) and ticks at `+ 0x15` (2 bytes),
  `0xfffffff0096211dc`/`0xfffffff0096211e4` and the writer at
  `0xfffffff009621878`; see native-rtc-spmi.md.

The kext's strings make the DT coupling independently visible: `info-rtc`,
`info-rtc_alarm_ctrl`, `info-rtc_alarm_event`, `info-rtc_alarm_offset`,
`info-rtc_irq_mask_offset`, `info-leg_scrpad`, and the offset/NVRAM names all
appear in `/tmp/dvm/RTC_SOURCE1.kext/AppleSPMIPMU`.  Its concrete vtable also
implements the raw `AppleARMRTC` slots `+0x548` and `+0x550`, rather than the
pure-virtual stubs in the abstract base.

## Minimal implementation decision

### Fast, explicit virtual calendar provider

For calendar correctness without first implementing an unverified SPMI
controller, add a small early guest IOKit provider which publishes `IORTC` and
implements the two ABI calls in the table above.  Its source of time should be
a host UNIX-epoch base supplied once at launch plus guest monotonic elapsed
time; `setGMTTimeOfDay` changes an in-memory offset.  The provider must start
before `IOKitInitializeTime`, return success for both calls, and report a
normalized `uint64_t` seconds / `uint32_t` nanoseconds pair.

This is the smallest justified virtual interface because it targets the
already-proven resource-and-function boundary.  A DT scalar by itself is
insufficient: no target code is shown consuming one, while the existing fake
`rtc` node demonstrably only suppresses the wait.  Treat the root `no-rtc`
workaround as temporary: once the provider is reliably published, test
removing it rather than assuming it coexists correctly with a real `IORTC`
resource.

This option needs a guest-kext/injection delivery mechanism; none was added in
this investigation.  It intentionally does not claim RTC alarm or persistent
PMU behavior.

### Native PMU route

For full calendar persistence and alarms, implement the Apple SPMI controller
and PMU endpoint, preserve the required real DT binding, and let the existing
`AppleDialogSPMIPMURTC` publish `IORTC`.  The initial PMU model may implement
only the proven RTC read packet, the offset persistence mechanism after it is
traced, and the named alarm registers.  Do **not** copy controller register
opcodes, transaction completion semantics, or interrupt behavior from a
different SoC without a trace from this guest.

`/Users/jdolbe1/dvm-artifacts/ref/inferno/hw/misc/apple-silicon/spmi-pmu.c`
is useful only as an implementation reference: lines 17-23 define the same
32,768-Hz and alarm-bit choices; lines 153-160 serialize the same six-byte
tick layout; and lines 197-211 map the same DT property names.  It supports a
candidate model shape, but it is not evidence for T8140 controller registers
or protocol.

## Required next evidence and acceptance checks

1. Instrument one bounded guest boot to log the three `nub-spmi0` MMIO ranges,
   SPMI opcodes, address, byte count, response status, and IRQ acknowledge
   sequence while `AppleDialogSPMIPMURTC` starts.  This resolves controller
   roles and the `info-leg_scrpad` write path without guessing.
2. Verify native binding before modelling the whole PMU: the target must log
   `AppleDialogSPMIPMURTC started`, publish `IORTC`, and complete a six-byte
   `0xf802` read with the packet formula above.
3. For either implementation, prove `get` returns non-epoch UTC, a guest time
   set round-trips through `get`, time advances at wall-clock rate, and a
   cold reboot obtains the current host-derived time.  The native route also
   needs alarm arm/fire/ack and an IRQ check.

No VM was booted, no QEMU build was run, and this investigation changes only
this note.
