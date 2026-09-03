# A420/A421 on the iOS 27 IOMFB link

## Source metadata

The primary sample is iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140
(H17P), kernelcache `firmware/bootkc`, with the boot slide convention
`runtime = unslid + 0x20000000`.  The stripped AP-side code is the
`com.apple.iokit.IOMobileGraphicsFamily-DCP` fileset entry; its
`__TEXT_EXEC.__text` is `0xfffffff00a0b2080`--`0xfffffff00a0dc230`, and the
corresponding extracted Mach-O used below is
`/tmp/dvm/kexts_all/com.apple.iokit.IOMobileGraphicsFamily-DCP` (UUID
`BCADE513-BD2C-39D1-A3D6-C3A21F3AD63E`).  The live positive control is
`/tmp/dvm/probe/PERSIST_DCP_DELAYED_D575_A385_1.stderr.log:2463-2477`, where
the guest sends both requests and accepts zero-status completions before
continuing to `A424`, `A428`, and later `A385` requests.

## Summary

`A420` and `A421` are two 0xc10-byte table RPCs in this iOS 27 kernelcache,
not width/height or scanout requests.  Each AP wrapper copies 0xc0c bytes
between its optional caller buffer and the RPC buffer, then consumes the
u32 at output `+0xc0c` as its return status; no field in either reply selects
the built-in panel geometry.  The existing all-zero replies already satisfy
the observable return path and do not lead to `A407`/`A408`/`D589`/`D591`, so
there is no evidence-based nonzero response to add for the 1179x2556 panel.

## Wire layout and AP consumption

| RPC | AP wrapper / vtable slot | Request and reply layout | What the AP reads or branches on | Evidence |
|---|---|---|---|---|
| `A420` (`0x41343230`) | wrapper entry `0xfffffff00a0c9834`; `IOMobileFramebufferAP` vtable `+0x650` (`/tmp/dvm/cpp_appleclcd2_d120.txt:204`) | `in[0..0xc0b]`: optional caller table copied by `memcpy`; `in[0xc0c]`: null-pointer flag (0 when supplied, 1 when absent); `in[0xc0d..0xc0f]`: initialized padding.  `out[0..0xc0b]` is copied back to the supplied table and `out[0xc0c..0xc0f]` is the returned `u32` status. | `cbz x19` at `0xa0c9908` only decides whether to copy `out[0..0xc0b]`; `ldr w0, [sp,#0xc0c]` at `0xa0c991c` consumes the status before tail-calling the normal status helper at `0xa0dc4a0`. | The wrapper constructs FourCC at `0xa0c98ec-0xa0c98f0`, passes `in_len=out_len=0xc10` at `0xa0c98f4-0xa0c98f8`, copies the outgoing table at `0xa0c9880-0xa0c9890`, and copies the reply at `0xa0c990c-0xa0c9918`.  The live request is `in=3088,out=3088` at `PERSIST_DCP_DELAYED_D575_A385_1.stderr.log:2463-2469`. |
| `A421` (`0x41343231`) | wrapper entry `0xfffffff00a0c993c`; `IOMobileFramebufferAP` vtable `+0x658` (`/tmp/dvm/cpp_appleclcd2_d120.txt:205`) | Identical 0xc10 envelope: `0xc0c` copied table bytes, byte `+0xc0c` null-pointer flag in the request, three padding bytes, and a `u32` status at reply `+0xc0c`. | `cbz x19` at `0xa0c9a10` decides only table copyback; `ldr w0, [sp,#0xc0c]` at `0xa0c9a24` is the only reply scalar consumed by this wrapper. | The FourCC and equal lengths are set at `0xa0c99f4-0xa0c9a00`; the table marshal/copyback sequences are `0xa0c9988-0xa0c9994` and `0xa0c9a14-0xa0c9a20`.  The live request is `in=3088,out=3088` at `PERSIST_DCP_DELAYED_D575_A385_1.stderr.log:2470-2477`. |
| `A422` (positive control for the distinction) | wrapper entry `0xfffffff00a0c9a44`; vtable `+0x660` (`/tmp/dvm/cpp_appleclcd2_d120.txt:206`) | A scalar selector plus a 0x48-byte 3x3-u64 matrix: input length `0x50`, output `0x4c`. | This is the nearby matrix-shaped RPC, not either 0xc10 call. | `A422` moves `w1` and copies `0x48` bytes from `x2` at `0xa0c9a74-0xa0c9aa4`, then sends `in=0x50,out=0x4c` at `0xa0c9ae0-0xa0c9aec`. |

The 0xc0c payload size and the adjacent A422 matrix ABI put A420/A421 in the
large colour/gamma-table family.  The older m1n1 tracer labels `A420` as
`set_gamma_table` and `A421` as `get_matrix`
(`/tmp/dvm/m1n1-smp/proxyclient/hv/trace_dcp.py:561-564`), but that `A421`
label cannot describe this iOS 27 AP wrapper: it has one pointer argument and
the 0xc0c table marshal above, whereas the actual local matrix ABI is A422.
Treat the old tag-to-name association as a cross-version hint only, not an
iOS 27 wire contract.

## Consequence for the model

The live positive control demonstrates that the model's existing zeroed
0xc10 reply puts `0` in both status words: after A420/A421 it immediately
accepts A424 and A428 (`stderr.log:2478-2491`) and continues polling A385
(`:2493+`).  Therefore preserve a zero `u32` at reply `+0xc0c`, but do not
invent a gamma LUT, a matrix, or panel dimensions: none is read as a
display-ready condition at these wrappers, and no subsequent swap RPC is
observed in that run.  The separately established 1179x2556 geometry belongs
to `A453`/`D586`, not this pair.

## Open questions

| Question | Observation that would settle it |
|---|---|
| Which of A420/A421 is the getter versus setter in this exact iOS 27 build? | Break on `0x2a0c9834` and `0x2a0c993c` in a live slid guest and record the caller plus the pre-call 0xc0c-byte table; a caller consuming the post-call copyback identifies the getter. |
| What is the internal 0xc0c-byte table schema? | Capture one real nonzero table from a physical t8140 DCP trace or locate a second iOS 27 AP call site that initializes named fields before either wrapper. |
| Can colour-table success affect a later swap once the actual scanout path is reached? | Run the existing delayed-D575 harness with an identity table only after a real `A407`/`A408` request occurs, and compare both the request sequence and screendump against the zero-table control. |
