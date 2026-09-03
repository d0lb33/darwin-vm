# ACM/SCRD response contract for the first userspace requests

## Source metadata

The inspected guest is iOS 27.0 beta 8 / build 24A5430a on iPhone17,3
(t8140).  The executable is
`/tmp/dvm/kexts_all/com.apple.driver.AppleSEPCredentialManager`; its
`__TEXT_EXEC.__text` starts at unslid `0xfffffff0094e9d30` (file offset
`0x18000`), and the tested runtime slide is `+0x20000000`, as documented in
`docs/re/sep-protocol.md`.  The live witness is
`/tmp/dvm/probe/SKS_OP19_BH6_PAD1.{serial,stderr}.log`, and the current model
is `qemu-sptm/hw/arm/darwin_sep.c`.

## Summary

The two five-second failures are not failure to publish `scrd`: the guest logs
`SEP EP 10 enabled` before either request.  `AppleCredentialManager` waits
because the default endpoint-10 branch in `darwin_sep.c` deliberately emits no
reply, while the receive path requires a reply whose OOL bytes contain a
nonzero version, the request sequence, and a bounded header length.  The
smallest code-supported positive control is therefore a 12-byte successful
envelope for command 10 and that envelope plus one zero payload byte for
command 25; the byte's semantic meaning is still unverified, so its effect
must be measured rather than claimed.

## Observed request and response layout

| Item | Layout / branch condition | Evidence |
|---|---|---|
| Endpoint readiness | `sep-endpoint,scrd` is already usable before the timeout. | `SKS_OP19_BH6_PAD1.serial.log:240-246` logs the wait, `SEP EP 10 enabled`, then `SEPEndpoint enabled`. |
| Request mailbox frame | Endpoint is byte 0, tag is byte 1, OOL byte count is bits `[31:16]`, and the upper word is the SEP status on reply. | `qemu-sptm/hw/arm/darwin_sep.c:151-157`; packing implementation at `:719-728`. |
| Live command 10 | 36-byte request, request sequence 1, SUID 0; caller declares no output bytes. | `SKS_OP19_BH6_PAD1.stderr.log:373-378` shows `01 00 1c 00`, sequence 1, `SCRD`, and command byte `0x0a`; `serial.log:575-579` records `inLen=36`, `outLen=0`. |
| Live command 25 | 40-byte request, request sequence 2, SUID -1; caller declares one output byte. | `SKS_OP19_BH6_PAD1.stderr.log:1189-1194` shows the same header, `SCRD`, and command byte `0x19`; `serial.log:624-628` records `inLen=40`, `outLen=1`. |
| Current timeout cause | The endpoint-10 switch logs the request and, unless `DARWIN_SEP_SCRD_FAIL_FAST` is set, falls through without calling `sep_send_raw()`. | `qemu-sptm/hw/arm/darwin_sep.c:1914-1924`. The resulting errors are `0xe00002d6` at serial `:575-579` and `:624-638`. |
| Fast-fail is not success | The opt-in reply has zero OOL length and status 1, which ACM surfaces as an SEP error rather than a completed request. | `darwin_sep.c:159-162`, `:1919-1922`. |
| Response envelope gate | On generic request type 4, `sendSEPCommand` rejects an absent buffer, total OOL length `<= 11`, byte 0 equal to zero, mismatched `u32` at `+4`, or a `u16` at `+2` greater than the total length. | `AppleSEPCredentialManager` unslid `0xfffffff0095291e4-0xfffffff009529224`. Specifically: buffer at service `+0x170`, total length `+0x160`, byte `[0]`, request ID `[+4]`, and header length `[+2]`. |
| Payload handoff | After those checks, the same routine advances by the `u16` at `+2`, sets remaining length to `total-header_len`, and copies that remainder to the caller's output buffer. | `AppleSEPCredentialManager` unslid `0xfffffff00952921c-0xfffffff0095292b4`. |
| Command 25 dispatch | `LibCall_ACMGetEnvironmentVariable`'s local wrapper explicitly supplies command `0x19` (decimal 25) to its transport callback. | `AppleSEPCredentialManager` unslid `0xfffffff009531b84-0xfffffff009531ba4`; its cstring is at unslid `0xfffffff0076fd902`. |
| Existing Env(7) line | The run later says `get Env(7) succeeded -> 2097152`, but it occurs after the command-25 timeout and therefore does not prove that an endpoint-10 reply was decoded in this run. | `SKS_OP19_BH6_PAD1.serial.log:624-638` precedes `:646`, `:654`, and `:658`. |

## Minimal positive-control replies

These proposed byte sequences are a **receiver-minimal contract**, not a claim
that they reproduce all sepOS SCRD semantics.  They satisfy every field read by
the path above and preserve the response tag from the mailbox request.

```
OOL reply for command 10 (12 bytes; request sequence copied from request +4)
  01 00 0c 00  SS SS SS SS  00 00 00 00
  |  |  |       |            `-- unread by the checked path
  |  |  |       `--------------- matching request sequence
  |  |  `----------------------- header length = 12
  `-- nonzero response version

OOL reply for command 25 (13 bytes)
  01 00 0c 00  SS SS SS SS  00 00 00 00  00
                                                `-- one-byte returned value
```

For either reply, write those bytes to endpoint 10's configured OOL-out DVA,
then send `frame(10, request_tag, response_len & 0xff, response_len >> 8, 0)`.
The byte placement follows `frame()` at `darwin_sep.c:726-728`; status zero is
required because the current nonzero status route is explicitly documented as
fast failure at `:159-162`.  `SS` must be copied from the incoming body offset
`+4`, not derived from the mailbox tag: the receiver compares its reply `+4`
against the request body `+4` at unslid `0xfffffff00952920c-0xfffffff009529218`.

The command-10 response has zero residual payload after its 12-byte header,
which matches its live `outLen=0`.  The command-25 response has one residual
byte after the same header, matching its live `outLen=1`; use zero only as a
positive-control value (plausibly "developer mode disabled"), not as a
documented SCRD policy assertion.

## Concrete implementation plan

1. Add a dedicated endpoint-10 helper in `qemu-sptm/hw/arm/darwin_sep.c` rather
   than changing generic mailbox code.  It should DART-write the OOL reply,
   parse only the observed request invariants (`u16 version=1`, header length
   `0x1c`, `SCRD` tag at `+0x1c`, command byte at `+0x20`), and log the
   sequence, command, reply length, and status.
2. Dispatch command byte `0x0a` to the 12-byte envelope and command byte
   `0x19` to the 13-byte envelope.  Leave unrecognized command bytes
   unanswered and dump their OOL body; this keeps unknown SCRD behavior
   observable instead of turning it into an invented success.
3. Run a fresh-child boot with `DARWIN_SEP_DEBUG=1`.  Acceptance requires both
   `sendSEPCommand ... took` lines to report `ioErr=0x0 acmErr=0` without the
   5000-ms delay, and an endpoint-10 model log showing the matching sequence
   and OOL-out write.  A later regression boot must retain ANS, SKS, and DCP
   behavior.
4. If command 25 is accepted but changes policy incorrectly, capture the
   caller's one-byte output with an LLDB breakpoint immediately after
   `0xfffffff009531ba4` and sweep only the two boolean values.  That resolves
   the payload's semantics without widening the modeled protocol.

## Open questions

| Question | What observation settles it |
|---|---|
| Is the `u16` at response `+2` formally a header length or another count whose current use happens to be equivalent? | A captured real-Sep OOL response, or a second producer/consumer that names the field, matching the advance at `0xfffffff00952924c`. |
| Does command 10 require a nonzero field in the unused bytes `+8..+11` for a later path? | A positive-control boot that passes command 10 followed by a breakpoint/log at the next ACM command; a zero-free response is sufficient only if no later failure attributes state to command 10. |
| Does command 25 value zero mean developer mode disabled on this iOS build? | Break after `0xfffffff009531ba4` in a successful run and trace the byte's first conditional consumer, then compare one controlled boot for each value. |
| Are `ACMTRM: _onEnvGet Env(7)` successes using SCRD or cached/local TRM state? | Correlate each line with an endpoint-10 request/reply pair in a debug trace; the supplied log cannot establish this because the first command-25 response timed out before those lines. |
