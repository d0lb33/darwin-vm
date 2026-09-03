# D575 can select the external-display swap-cancellation path

## Scope and address convention

This note covers iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140.  Static
addresses are boot-kernelcache fileset virtual addresses.  The live kernelcache
slide is `0x20000000`, so every runtime address below is
`runtime = static + 0x20000000`.  The relevant extracted images have static
`__TEXT_EXEC` bases `0xfffffff0091792c0` for
`com.apple.driver.AppleMobileDispH17P-DCP` and `0xfffffff00a0b2080` for
`com.apple.iokit.IOMobileGraphicsFamily-DCP`.  These are already the kext load
addresses in the Mach-O fileset; no second kext-base adjustment is required.

## Conclusion

The scripted D575 payload is not a readiness assertion.  Subject to the
existing listener/state preconditions, its `event=1, trailing_bool=1` pair sets
the framebuffer's external-display byte at `self+0x430`; H17P `swap_submit`
then has an explicit branch which cancels a swap when that byte is set and
`[self+0x58f4]` is zero.  The first control should therefore omit D575, not
invent data for its 76-byte output.

## D575 producer/consumer contract

The D575 handler is static `0xfffffff00a0d9e4c` (runtime
`0xfffffff02a0d9e4c`, kext offset `0x27dcc`).  It requires input length at
least `0x58` and output length at least `0x4c`.  With input byte `+0x54` clear,
`0xfffffff00a0d9eec-0xfffffff00a0d9f0c` copies exactly `0x4c` bytes from
input `+8..+0x53` into the callback output.  With `+0x54` set it suppresses
that copy and passes null for the optional structure.  The virtual call at
`0xfffffff00a0d9f40-0xfffffff00a0d9f7c` is:

```
(self, *(uint64_t *)(input + 0), optional_tiled_info,
 input[0x53] & 1)
```

The concrete target is static `0xfffffff00a0bcad8` (runtime
`0xfffffff02a0bcad8`, kext offset `0xaa58`).  Its prologue saves `x0`, `x1`,
and `x3`, but not `x2`, so this implementation does not consume the optional
`0x4c`-byte structure.  Therefore the 76-byte callback output is a DCP-facing
return buffer, not an AP-side readiness input.  The current script sets
`+0x54=1`, so even that copyback is disabled.  The live trace records the exact
input bytes and call arguments at
`/tmp/dvm/PERSIST_DCP_DELAYED_D575_A385_1.iomfb.jsonl:67-68`; transport
completion alone is visible at
`/tmp/dvm/probe/PERSIST_DCP_DELAYED_D575_A385_1.stderr.log:2452-2456`.

## `self+0x430` is the external-display state

This identification is based on independent accesses, not only the D575
function:

- H17P initialization reads the property named `external` and converts its
  value to a Boolean before storing it at `self+0x430`, static
  `0xfffffff009188860-0xfffffff009188888` (runtime
  `0xfffffff029188860-0xfffffff029188888`).
- Default-framebuffer construction branches on the same byte at static
  `0xfffffff00a0bea7c` and `0xfffffff00a0c0488`; the first branch supplies the
  external-mode `1920 x 1080` dimensions at `0xfffffff00a0bea84-0xa0bea8c`.
- `hotPlug_notify_gated` contains the diagnostic string
  `Powering off external and powering on Internal from hpd notification` and
  reports its input as `hpdState %llu` in another diagnostic.  The strings are
  at static `0xfffffff007990003` and `0xfffffff007990062`.

Inside `hotPlug_notify_gated`, when `self+0x1a0` is non-null and
`self+0x389 bit0` is already set, the sequence at static
`0xfffffff00a0bcb6c-0xfffffff00a0bcb94` tests the old `self+0x38a`, compares
the event in `x20` with `1`, and tests the trailing Boolean in `w21`.  If the
old bit is clear and both supplied values are one, it stores one to both
`self+0x38a` and `self+0x430` at `0xfffffff00a0bcb88-0xa0bcb90`.

The current payload has exactly those two supplied values: event `1` and
trailing Boolean `1` (`PERSIST_DCP_DELAYED_D575_A385_1.iomfb.jsonl:67-68`).
The record at `:69` stopped at runtime `0xfffffff02a0d9f7c`, which is the
actual `blraa` call instruction, despite the record's attempted
`hotplug_entry` label and `pc_matches_expected=false`.  Its bounded pre-call
memory shows `self+0x1a0` non-null, `self+0x389=1`, `self+0x38a=0`, and
`self+0x430=0`.  Together with `x1=1`, `w3=1` at `:68` and the completed
callback at `stderr.log:2452-2456`, those observations satisfy every branch
condition for the stores at `0xfffffff00a0bcb88-0xa0bcb90`.  The current run
therefore did assert both `self+0x38a` and `self+0x430`.  A post-latch stop is
still useful as an independent witness because the existing
`hotplug_post_latch` breakpoint recorded zero hits (`iomfb.jsonl:70`).

## H17P cancellation path

The concrete H17P `swap_submit` starts at static `0xfffffff00918e3c4`
(runtime `0xfffffff02918e3c4`, H17P kext offset `0x15104`).  Its identity is
supported by three references to the literal `swap_submit` string at static
`0xfffffff00918e83c`, `0xfffffff00918efe4`, and
`0xfffffff00918f26c`, plus swap-specific diagnostics in the same function.

At static `0xfffffff00918e42c-0xfffffff00918e438`, it reads
`self+0x430`; if that byte is one and the word at `self+0x58f4` is zero, it
branches to `0xfffffff00918e488`.  That block references the exact diagnostic:

```
Submitting swap after power down at entry. Returning failure, cancelling swap
```

It then invokes the framebuffer virtual at `+0x6e0` at
`0xfffffff00918e494-0xfffffff00918e4bc` rather than continuing through the
normal submit body.  The precise semantic name of `self+0x58f4` remains
unproved; only its zero/nonzero branch behavior is claimed here.

On paths which do submit, H17P calls the import stub at static
`0xfffffff00919deb4`.  The bootkc chained pointer at
`0xfffffff00808f880` decodes from raw `0x80110000030bf66c` to the generic
wrapper `0xfffffff00a0c366c`; that wrapper dispatches through vtable `+0x928`
to `0xfffffff00a0c36ac`.  The generic producer calls `surface_map_dcp` at
`0xfffffff00a0c40d4` and `0xfffffff00a0c41cc`, then selects vtable `+0x998`
(A408 call at `0xfffffff00a0c4664`) or `+0x990` (A407 call at
`0xfffffff00a0c477c`).  Thus a missing A407/A408 can mean either that H17P
never received a swap or that it rejected one before the generic producer;
the existing RPC log cannot distinguish those explanations.

## Bounded positive-control test

Do not run this while another probe owns QEMU.  For the next controlled boot:

1. Use the current display harness but omit D575 entirely.  Keep the existing
   A407/A408 all-zero reply behavior unchanged; those requests have not yet
   occurred, so changing their replies cannot create them.
2. Break at runtime `0xfffffff02918e3c4` (H17P `swap_submit`),
   `0xfffffff02918e42c`, `0xfffffff02918e438`, and
   `0xfffffff02918e488`.  At entry record `x0`, `x1`, byte `[x0+0x430]`, and
   word `[x0+0x58f4]`.
3. Also break at runtime `0xfffffff02a0c366c`, `0xfffffff02a0c36ac`,
   `0xfffffff02a0c40d4`, `0xfffffff02a0c40d8`,
   `0xfffffff02a0c41cc`, `0xfffffff02a0c41d0`,
   `0xfffffff02a0c4664`, and `0xfffffff02a0c477c`.  Record `w0` after each
   mapping call.  This distinguishes no producer call, H17P cancellation,
   generic mapping failure, and a submitted A407/A408 request.
4. Only after the no-D575 control, run one callback variant with the same
   event and only input byte `+0x53` changed from `1` to `0`.  Static code then
   cannot take the `0xfffffff00a0bcb88-0xa0bcb90` assertion path.  Add a
   post-latch stop at runtime `0xfffffff02a0bcb94` and read bytes
   `self+0x389`, `self+0x38a`, and `self+0x430` before comparing first
   A407/A408 and surface-map hits against the no-D575 control.

This does not assign connect/disconnect meaning to event values `0` or `1`;
that polarity is still ambiguous.  It tests only the branch behavior proved by
the AP code.
