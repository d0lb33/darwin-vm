# iOS 27 IOMFB A442 wrapper contract

## Source metadata

This report covers iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140 (H17P),
from `firmware/bootkc`.  The relevant stripped fileset image is
`com.apple.iokit.IOMobileGraphicsFamily-DCP`; all code addresses are unslid,
with the live guest slide observed in this project as `+0x20000000`.  Dynamic
wire evidence is `/tmp/dvm/probe/UI_OP19_DCP1.stderr.log`, where the emulator
reports the request as `in=92`, `out=4100`.

## Summary

On this build, A442 is a fixed-size wire wrapper around a variable-size local
request/result bridge, rather than evidence for a two-integer display-size
operation.  The wrapper accepts a 0x5c-byte descriptor, asks the DCP for a
0x1004-byte result, copies only the caller-requested prefix (at most 0x1000
bytes) to its caller, and returns the trailing u32 at output `+0x1000`.
Therefore the current all-zero response reaches the kernel's zero-status
return path, but the copied payload has no local in-kernel consumer at this
call site; it may still be needed by the caller beyond this method and must
not be called a safe semantic reply.

## Wrapper, request, and result layout

| Item | Layout / behavior | Evidence |
|---|---|---|
| Wire construction | The wrapper calls the link RPC vtable with FourCC `0x41343432`, input pointer `sp+0x20`, `in_len=0x5c`, result pointer `x19`, and `out_len=0x1004`. | `IOMobileGraphicsFamily-DCP` unslid `0xfffffff00a0caba0-0xfffffff00a0cabbc`. |
| Wrapper entry | The containing wrapper begins at `0xfffffff00a0caa4c`; `0xfffffff00a0caba4` is the FourCC materialisation within it, not its entry point. | Prologue at `0xfffffff00a0caa4c-0xa0caa90`; FourCC at `0xfffffff00a0caba4-0xa0caba8`. |
| Input `+0x00` | A caller-supplied u64 (`x1`) is copied verbatim.  Its semantic type is unverified. | `str x27,[sp,#0x20]` at `0xfffffff00a0cab48`; live values begin `c0 5c ef 03 e3 ff ff ff` in `UI_OP19_DCP1.stderr.log:8609`. |
| Input `+0x08`, `+0x0c` | Two caller u32 values (`w2`, `w3`) are stored.  The first varies across live calls (`0x4c`, `0x4a`, `0x79`), so it is not a constant geometry field. | `stp w26,w25,[sp,#0x28]` at `0xfffffff00a0cab4c`; live traces at `:8609`, `:8621`, `:8633`. |
| Input `+0x10..+0x4f` | Up to eight u64s from caller pointer `x4`; count is `w5`.  The wrapper rejects count `>8`.  The observed calls use count zero, leaving the wrapper's `0xaa` initialisation in this field. | bound at `0xfffffff00a0caab8-0xa0caabc`; copy loop `0xfffffff00a0cab50-0xa0cab74`; count store at `0xfffffff00a0cab78`; observed zeros/`aa` at log `:8609-8613`. |
| Input `+0x50` | Duplicate u32 table count (`w5`). | `str w23,[sp,#0x70]` at `0xfffffff00a0cab78`; observed zero at log `:8613`. |
| Input `+0x54` | Unaligned u64 caller result length (`x7`).  It is limited to `<=0x1000`; live requests ask for `0x808`, `0xc5c`, `0x20`, and later `0x2c` bytes. | limit `cmp x20,1,lsl #12` at `0xfffffff00a0cab14-0xa0cab18`; store `stur x20,[sp,#0x74]` at `0xfffffff00a0cab7c`; log `:8613`, `:8625`, `:8637`, `:12382`. |
| Output `+0x000..+0xfff` | The wrapper copies exactly the requested `x7` prefix from its 0x1004-byte temporary result to caller pointer `x6`; no copy occurs when `x7==0`. | copy loop guarded by `cbz x20` at `0xfffffff00a0cabc0-0xa0cabd8`; destination is `x21` (saved from `x6`) at `0xfffffff00a0caa74`. |
| Output `+0x1000` | A u32 is loaded and converted to the wrapper's return value after the prefix copy.  On this little-endian kernel it is the native little-endian status word. | `ldr w0,[x19,#0x1000]` and call at `0xfffffff00a0cabdc-0xa0cabe4`. |
| Current response | The logged model completion is status zero with the entire 4100-byte output zeroed; that makes both copied payload and trailing status zero. | `UI_OP19_DCP1.stderr.log:8605-8615`, `:8617-8627`, `:8629-8639`. |

## Consumers and scanout relevance

The immediate caller is the `IOMobileFramebufferAP` vtable method at slot
`+0x728`, `0xfffffff00a0b773c`.  Its normal branch loads vtable slot `+0x9e8`,
which resolves to the A442 wrapper, forwards the original eight arguments, and
uses only the wrapper's integer return before returning itself:

| Item | Behavior | Evidence |
|---|---|---|
| AP virtual entry | `IOMobileFramebufferAP::fn_0x728()` is the immediate caller; the same implementation also appears in the AppleCLCD2 and UnifiedPipeline-derived vtables. | `/tmp/dvm/cpp_iomfbap.txt:231`; `/tmp/dvm/cpp_appleclcd2_d120.txt:231`; `/tmp/dvm/cpp_unifiedpipeline2_d120.txt:231`. |
| A442 dispatch | The AP entry loads virtual `+0x9e8` then sends its original arguments as `x0..x7` to the wrapper. | `0xfffffff00a0b78c8-0xa0b791c`. |
| No kernel payload branch | After A442 returns, the AP entry stores `x0` and returns it.  It neither reads the result destination (`x6`) nor compares a byte of the copied result. | `mov x19,x0` at `0xfffffff00a0b7920`, followed by bookkeeping and return at `0xfffffff00a0b7924-0xa0b795c`. |
| State precondition | The AP entry calls A442 only while bit 0 of `self+0x5870` is clear; set bit returns `0xe00002d8` before the RPC. | `ldrb`/`tbz` at `0xfffffff00a0b77fc-0xa0b7808`; error construction at `0xfffffff00a0b780c-0xa0b7818`. |
| Observed order | Three variable A442 calls occur immediately before A030 in the early sequence, and a later A442 occurs before A385/D575/A420/A421.  The same log has no A407/A408 or surface-map line. | `UI_OP19_DCP1.stderr.log:8605-8641`, `:12374-12412`; absence checked in that file with `rg 'A407|A408|surface_map|swap'`. |

The static evidence proves that an all-zero reply does **not** fail the local
kernel wrapper: it supplies a trailing zero status and the immediate AP method
has no payload-dependent branch.  It does **not** prove that zero payload is
harmless: the wrapper deliberately writes a caller-provided destination, but
this local control-flow slice does not establish whether that destination is
subsequently read.  Thus A442 is a plausible UI-path blocker, but its causal
relation to scanout is unverified; the existing trace alone cannot distinguish
an informational request from a required client configuration record.

## Smallest evidence-backed reply and decisive live observation

The only response shape supported by present evidence is a full 0x1004-byte
output: payload bytes `+0..+0xfff`, then a zero u32 at `+0x1000` to preserve
the current successful return.  No nonzero payload field can be assigned a
meaning from this kernelcache, so inventing dimensions or a structure header
would be speculative.

For a decisive observation, run the existing reproduction with QEMU's gdbstub
and stop just after the RPC call:

```
breakpoint set --address 0xfffffff02a0cabc0  # A442 result ready (+0x20000000 slide)
breakpoint set --address 0xfffffff02a0b7920  # immediate AP caller post-return
continue
register read x19 x20 x21 x0
memory read --format x --size 4 --count 16 $x19
memory read --format x --size 4 --count 1 `$x19 + 0x1000`
thread backtrace
continue
```

At the first breakpoint, `x19` is the temporary 0x1004-byte result, `x20` is
the requested copy count, and `x21` is the caller destination (from the
wrapper saves at `0xfffffff00a0caa70-0xa0caa78`).  At the second, the
backtrace and the saved user/caller destination settle who consumes the payload
and whether a nonzero result is a prerequisite for the first A407/A408 or
surface-map operation.

## Open questions

| Question | Observation that settles it |
|---|---|
| What do input `+0x00`, `+0x08`, and `+0x0c` designate? | Stop at runtime `0xfffffff02a0b78f4` and trace the source of `x1`, `w2`, and `w3` from the caller backtrace. |
| Which code reads the copied payload? | At runtime `0xfffffff02a0cabc0`, record `x21`; set a hardware/watchpoint or follow the returned external-call stack until that buffer is read. |
| Is a nonzero A442 payload required for scanout? | Run one controlled reply change with trailing status still zero, then compare for first A407/A408, surface-map, and swap-submit hits against the all-zero positive transport control. |
| What response bytes are valid? | A capture from a physical DCP trace for the same input, or a guest consumer observation that identifies the first tested field. |
