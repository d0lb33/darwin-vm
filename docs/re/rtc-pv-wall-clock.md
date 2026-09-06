# Opt-in PV wall-clock read for 24A5430a

**Superseded (2026-09-05).** The native SPMI PMU RTC in
[native-rtc-spmi.md](native-rtc-spmi.md) replaces this adapter: boot with
`DARWIN_RTC_PV=0`, an unpatched (or SMP-only) kernelcache and a tree built
with `-enable spmi`.  Do not combine the two; each publishes a calendar
source.  The register and patch tool remain for reference only.

This is an intentionally small virtual-platform calendar source for the
iPhone17,3 / T8140 `24A5430a` kernelcache.  It supplies a fresh host wall-clock
read at the platform-expert boundary.  It is not an emulation of T8140's PMU
RTC; the native SPMI path remains documented in
[t8140-calendar-source.md](t8140-calendar-source.md).

## ABI and namespace check

With `DARWIN_RTC_PV=1`, QEMU exposes a read-only register:

```text
MRS Xt, S3_0_C15_C15_1     # DVM_PV_RTC_NS
return value: Unix epoch nanoseconds, uint64_t
```

It uses `qemu_clock_get_ns(QEMU_CLOCK_HOST)`, which is QEMU's host wall-clock
source.  There is no per-vCPU state, timer, migration state, or write
accessor.

The originally proposed `S3_0_C15_C15_6` is unavailable: the generated Apple
register map defines it as `HID24` in
`qemu-sptm/scripts/darwin/sysregs.py:930`.  `S3_0_C15_C15_7` is `HID35` at
line 931 and is the opt-in CPU-start override in
`qemu-sptm/hw/arm/darwin_smp.c:63-91`.  The same generated map has no
definition for `_1`, and a static scan of the supported target kernelcache
found no `MRS` instruction for any `S3_0_C15_C15_{0..7}` encoding.  `_1` is
therefore the explicit opt-in PV ABI; it does not collide with the SMP bridge
or the current generated Apple register table.

Without `DARWIN_RTC_PV=1`, QEMU does not register `DVM_PV_RTC_NS` and retains
its prior behavior.  A kernelcache patched for this experiment must not be
booted without that environment setting.

## Kernelcache patch

`tools/re/rtc_pv_patch.py` replaces exactly the first ten instructions of
the target `AppleARMPE::getGMTTimeOfDay` at static
`0xfffffff0085cdfd0`:

```asm
bti  c                         // retain the required indirect-call landing pad
mrs  x3, S3_0_C15_C15_1
movz x4, #0xca00
movk x4, #0x3b9a, lsl #16    // x4 = 1,000,000,000
udiv x5, x3, x4              // seconds
msub x3, x5, x4, x3          // remainder nanoseconds
str  x5, [x1]
str  w3, [x2]
mov  w0, #0                  // kIOReturnSuccess
ret
```

The input convention is target-derived: static disassembly of the unmodified
function shows `x1` is a `uint64_t *seconds` and `x2` a `uint32_t
*nanoseconds`. `AppleDialogSPMIPMURTC::getGMTTimeOfDay` at
`0xfffffff0096220f4` divides its 32768 Hz residual ticks by way of a
`1,000,000,000` multiplier and stores the result at `x2` (`0xfffffff009622194`).
Its normal `IORTC` dispatch begins at
`0xfffffff0085ce078`.  This patch bypasses only that read dispatch.  It does
not change IOKit readiness, the scheduler, date validation, or the native
RTC's set and alarm paths.

The tool requires the original `LC_UUID`
`16FF5BB5-E04D-6DD5-50F2-C6623CF19A56` and the original ten instruction
words as a guard.  The ninth original word is `str x8, [sp, #8]` at
`0xfffffff0085cdff0` and the tenth is `adrp x0, #0xfffffff007179000` at
`0xfffffff0085cdff4`; this makes the guarded span 40 bytes. The runtime
probe `RTC_PV_RESTORE1` proved that the old leaf-only replacement faults at
this indirect target with `Kernel BTI failure (BTYPE=0x0002)`.  The initial
`bti c` keeps the target valid for that call path while the leaf remains
stackless and returns with the caller's link register. The native outer
method falls through from a zero `IOReturn` on the successful
`callPlatformFunction` path; the explicit `mov w0,#0` preserves that observed
success return. The pristine
`firmware/bootkc` SHA-256 is
`dc0f5b6a6fa848053c301949c8376c216c6223c047203b93e408a93d3440f906`.
Derived inputs, including `DISPLAY_SMP6.bootkc`, are accepted only when both
the target UUID and this unmodified entry guard match.  The tool refuses an
already patched or unknown image and refuses to overwrite either its input or
an existing output.

Example generation only (no QEMU build or boot):

```sh
python3 tools/re/rtc_pv_patch.py firmware/bootkc /tmp/dvm/RTC_PV.bootkc
python3 tools/re/rtc_pv_patch.py /tmp/dvm/DISPLAY_SMP6.bootkc /tmp/dvm/DISPLAY_SMP6_RTC_PV.bootkc
```

## Deliberate limits

This is read-only host synchronization.  It does not persist a guest RTC
offset, emulate `setGMTTimeOfDay`, make `settimeofday` durable, program
alarms, or emulate PMU wake/interrupt behavior.  The host source can also
jump when the host clock is adjusted.  Native SPMI RTC support remains the
route for persistence and alarms.

Before integrating it with the signed display runtime, verify a fresh boot
returns non-epoch UTC, seconds advance at wall-clock rate, reboot receives a
fresh host value, and guest time-setting behavior is understood separately.


## Runtime validation of the corrected ABI

`RTC_NS_RESTORE1` uses the corrected QEMU register and newly generated restore
kernelcache. `tools/probe.sh --secs 15` reports `xnu panics: 0` and
`reached shell: yes`; launchd reports UTC `2026-09-05 17:58:02`. Logs are
`/tmp/dvm/probe/RTC_NS_RESTORE1.{serial,stderr}.log`, with the complete verdict
at `/tmp/dvm/native-services4/restore-verdict.log`.

`WARM_NATIVE_RTC_NS1` is an independent disk boot of the same services3 parent,
using `/tmp/dvm/warm-runtime-qemu3/qemu-system-aarch64` and
`/tmp/dvm/RTC_SOURCE1/DISPLAY_SMP6.rtc-ns.bootkc`. It reaches Early Boot in
9.864 s and the graphics probe completes all native bitmap stages at 189.888 s.
Its virtual IOPS battery also verifies successfully. The observation stops at
that graphics condition, with zero display presentations and zero kernel panics;
it does not establish full UI startup or Settings stability. The previous
microsecond/status-mismatched candidate did not pass those service waits over
300 s. This is a controlled positive follow-up, not proof that every earlier
startup difference was caused by the RTC ABI.
