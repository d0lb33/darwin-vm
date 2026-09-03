# iOS 27 IOMFB hot-plug completion is not a swap gate

## Source metadata

This note covers iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140 (H17P),
`firmware/bootkc`. The protocol code is the stripped
`com.apple.iokit.IOMobileGraphicsFamily-DCP` fileset image extracted as
`/tmp/dvm/kexts_all/com.apple.iokit.IOMobileGraphicsFamily-DCP`; all static
addresses below are unslid and a live guest uses `runtime = unslid + 0x20000000`.
The dynamic control is `/tmp/dvm/probe/PERSIST_DCP_DELAYED_D575_A385_1.stderr.log`.

## Summary

The current D575 handler accepts the callback and calls the AP hot-plug method,
but neither its return value nor its 0x4c-byte output reaches an A407/A408
wrapper or a D589/D591 callback. The concrete hot-plug callee latches two
one-byte state fields only when the callback's u64 event is 1, its trailing
boolean is true, and the display object's bit at `+0x389` is already true.
The observed D575-positive run therefore proves an AP notification/configuration
transition, not the swap path; an A407/A408 hit is the missing independent witness.

## Current wrapper and state layout

| Item | Fields/branch actually consumed | Evidence |
|---|---|---|
| D575 ingress | Requires `in_len >= 0x58` and `out_len >= 0x4c`; reads event u64 at `+0`, boolean at `+0x53`, and optional-structure flag at `+0x54`. | Handler `0xfffffff00a0d9e4c`: guards `+0x1c`/`+0x4c`, u64 `+0x100`, flags `+0x198`/`+0x1f4`; trace `PERSIST_DCP_DELAYED_D575_A385_1.stderr.log:2452-2456` records `in 88 out 76`, status 0. |
| D575 ABI | Clear `+0x54` copies bytes `+8..+0x53` to output; set suppresses that copy and passes NULL as argument 3. The call is `(self, input[0], optional_struct_or_NULL, input[0x53]&1)` via vtable `+0x8c8`. | `0xfffffff00a0d9eec-0xa0d9f0c`, `0xfffffff00a0d9f40-0xa0d9f7c`. |
| Hot-plug target | Resolved target `0xfffffff00a0bcad8` (runtime `0xfffffff02a0bcad8`) saves only `x0`, `x1`, `x3`, therefore does not read the optional D575 structure; writes `7` to `self+0x5858`. | Target `0xfffffff00a0bcad8-0xa0bcb0c`; vtable call `0xfffffff00a0d9f60-0xa0d9f7c`. |
| Hot-plug latch | Given `self+0x1a0 != NULL`, `self+0x389 bit0`, event `x1 == 1`, boolean `x3 bit0`, and clear `self+0x38a bit0`, sets `self+0x38a = 1` and `self+0x430 = 1`. It derives `self+0x384 != 1` for listener-notification branches. | `0xfffffff00a0bcb44-0xa0bcb9c`; alternate paths `0xfffffff00a0bcba0-0xa0bcc14`. |
| No-listener path | With `self+0x1a0 == NULL`, stores pending state at `self+0xa8`, `+0xb0`, `+0x103`; it still sends no DCP RPC. | `0xfffffff00a0bcccc-0xa0bcd04`. |
| Measured post-D575 sequence | Completion is followed by A411 (4/4), A420 (0xc10/0xc10), A421 (0xc10/0xc10), A424 (8/8), A428 (4/4), then A385 polling. No A407, A408, D589, D591, or surface-map line occurs. | `PERSIST_DCP_DELAYED_D575_A385_1.stderr.log:2456-2495`; A420/A421 analysis `docs/re/iomfb-a420-a421.md:31-50`. |
| A407 wrapper | Current A407 has `in_len=0x1d30`, `out_len=0xc`; static size supports a large submission record but not a current method name. | `0xfffffff00a0c8f8c-0xa0c8fa4`, `/tmp/dvm/iomfg_dcp.r2dis.txt:23494-23505`. |
| A408 wrapper | Adjacent A408 has `in_len=0xff4`, `out_len=0xc`, a distinct large record. | `0xfffffff00a0c91d8-0xa0c91f0`, `/tmp/dvm/iomfb_a408_a441_a444_a445.r2.txt:126-137`. |
| D589 callback | Requires 0xe4 input bytes, uses byte `+0xe0` as optional-output flag, then calls `0xfffffff00a0bb73c`; semantic name unverified. | `0xfffffff00a0daa9c-0xa0dabd4`; `docs/re/iomfb-dseries.md:326`. |
| D591 callback | Requires four input bytes and optional four-byte output, passing input bit 0 to `0xfffffff00a0bb134`; semantic name unverified. | `0xfffffff00a0dad28-0xa0dadd4`; `docs/re/iomfb-dseries.md:328`. |

The m1n1 `trace_dcp.py` labels these as swap-related, but has version-dependent
tag maps for an older firmware. It is not evidence for this iOS 27 mapping.

## Concrete next live observation

Run the delayed-D575 positive control with QEMU gdbstub and set these runtime
breakpoints (they already include the `+0x20000000` slide):

```
breakpoint set --address 0xfffffff02a0d9e4c  # D575 handler
breakpoint set --address 0xfffffff02a0bcad8  # hot-plug target
breakpoint set --address 0xfffffff02a0bcb94  # post-latch
breakpoint set --address 0xfffffff02a0c8f8c  # A407 construction
breakpoint set --address 0xfffffff02a0c91d8  # A408 construction
breakpoint set --address 0xfffffff02a0daa9c  # D589
breakpoint set --address 0xfffffff02a0dad28  # D591
breakpoint set --address 0xfffffff02a0b9600  # surface map
```

At `0xfffffff02a0bcb94`, record `x19`, `w20`, `w21` and one byte at
`$x19+0x389`, `+0x38a`, `+0x430`. If A407/A408 then hits, capture its input
(`sp+0x68` at A407, `sp+0x64` at A408) and continue through surface-map. If
the latch is set but neither wrapper hits, D575 is conclusively a notification
event and the next search belongs in the live render-client/swap producer.

## Open questions

| Question | Observation that settles it |
|---|---|
| Does D575 set `+0x389`, `+0x38a`, `+0x430` as predicted? | Post-latch breakpoint with one-byte reads. |
| Which component initiates A407/A408? | First wrapper hit with a backtrace and input record; no current log contains either FourCC. |
| Are D589/D591 prerequisites or completions? | Their first ordering relative to a real A407/A408 and surface-map hit. |
