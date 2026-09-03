# Post-Early-Boot AppleSEPKeyStore selectors

## Source metadata

| Item | Value |
|---|---|
| iOS build / device evidence | `RaveSeed24A5430a.D47DeveloperOS` is the System volume named in `/tmp/dvm/probe/PERSIST_DCP_D120_OP09_1.serial.log:...`; the extracted `AppleSEPKeyStore` banner identifies version `2383.2.1` in that log line 128. The device-tree and kernelcache slide used for this kext are the project's iOS 27/T8140 boot inputs; kext static addresses below are unslid. |
| driver | `/tmp/dvm/kexts/com.apple.driver.AppleSEPKeyStore`, arm64e Mach-O, SHA-256 `4e94489161384d9fc1b14704ad064e27e674fd2e586055b5bf388a95c92bf273`; `__TEXT_EXEC.__text` starts at VA `0xfffffff0095425b0` (file offset `0x8000`). |
| positive control | The same boot's QEMU trace accepts op `0x19` request bodies of 96 bytes and emits authenticated 84-byte replies, e.g. `/tmp/dvm/probe/PERSIST_DCP_OP09_CLASS3_1.stderr.log:529-532`. The independently decoded op `0x09` reply demonstrates that this transport's authenticated IPC-v1 header is 76 bytes and that its payload begins at request/reply offset `0x4c`; see `qemu-sptm/hw/arm/darwin_sep.c:525-530` and the accepted op-09 trace at the same log's lines 517-524. |

The failing public selectors are not SEP wire opcode numbers: the dispatcher subtracts
one before indexing its 169-entry switch at `0xfffffff009552aa0` and
`0xfffffff009552b04-3c`.  Public selectors 7, 17, and 35 all reach the
same generated SEP operation, wire opcode `0x19`, while public selector 68
reaches wire opcode `0x3d`.  Selector 7 is the demonstrated near-term blocker:
after every failure, ACMTRM says it cannot acquire either initial keybag device
state and schedules another retry; the present `0x19` frame is transport-valid
but its returned scalar has not satisfied that caller.

## Dispatcher and wire contracts

| Public selector | Static path and externally checked shape | SEP wire operation | Confirmed request/reply layout | Evidence |
|---|---|---|---|---|
| 7 | Switch index 6 at `0xfffffff009555038`; requires one input scalar and one output scalar (`...5058-506c`). It calls `fcn.fffffff009547360` at `...50b0` with variant/state argument zero. | `0x19` | **Confirmed framing:** 96-byte IPC-v1 request: `u32 body_size=0x48` at `+0`, opaque 76-byte header, then `u32 request_selector=0` at `+0x4c`, `u64 context` at `+0x50`, `i32 requested_state` at `+0x58`, `u32 zero` at `+0x5c`. **Confirmed result kind:** the generated operation receives `blob_ptr*` and `blob_len*`; normal success must therefore supply a bounded blob, not the current 8-byte scalar response. | Dispatch is explicit at `0xfffffff009552aa0`, `...2b1c-3c`, and `...5038-50b0`. The generated call receives the two output addresses at `0xfffffff009547418-420`; the caller reloads them at `...744c-454` and the consumer copies the blob at `0xfffffff00956e770-794`. |
| 17 | Switch index 16 at `0xfffffff009554c5c`. It accepts zero or one scalar (`...4c6c-4c90`); both paths join `0xfffffff009556e24-3c` and call the same `fcn.fffffff009547360`. | `0x19` | Same request framing and blob-result contract as selector 7. Variant/input state differs; do not collapse it to selector 7 semantically. | Case 16, join, and call addresses above; `fcn.fffffff009547360` invokes generated codec `fcn.fffffff00957c36c` at `0xfffffff009547404-420`, which emits `w1=0x19` at `0xfffffff00957c3d8-3e0`; the shared blob result is proved by `...7418-420` and `...744c-454`. |
| 35 | Switch index 34 starts at `0xfffffff009554c54`, clears `w27`, and falls through into index 16 at `...4c5c`. | `0x19` | Same request framing and blob-result contract as selector 17; its public method has no independent wire codec. | Fall-through is adjacent instructions at `0xfffffff009554c54-5c`; shared operation and output pair proof are as above. |
| 68 | Switch index 67 at `0xfffffff009554458`; it requires one input scalar and no structure output (`...445c-46c`). | `0x3d` | **Not yet decoded. Do not send a status-only reply.** The generated codec construction at `0xfffffff0095545b0-5dc` calls `fcn.fffffff00957dd54`, which invokes wire opcode `0x3d` at `0xfffffff00957ddc0-28`. Its payload and response union require a live capture. | The public selector appeared once as `sel:68` in `/tmp/dvm/probe/PERSIST_DCP_D120_OP09_1.serial.log:587`; static chain addresses above. A prior QEMU trace records unknown `0x3d` as status-only, which is not a positive control. |

### What the existing `0x19` evidence does and does not establish

`fcn.fffffff009547360` supplies a callback to the generated codec and gives
it writable `blob_ptr*`/`blob_len*` outputs (`0xfffffff009547418-420`).  Only
after the generated call returns zero does it reload the pair
(`...744c-454`) and pass it to `fcn.fffffff00956e6ac`, which rejects a null
pointer or length above `0x144`, copies the bytes, and records their decoded
type (`0xfffffff00956e6e8-720`, `...770-794`).  Thus an authenticated response
and a syntactically accepted IPC frame are insufficient: the present
`{selector=2, scalar=0}` implementation at `qemu-sptm/hw/arm/darwin_sep.c:532-547`
and `1643-1652` does not supply the normal success output at all and returns
`e00002bc` from all three public operations.

The causal witness is stronger for selector 7 than for the other selectors:
after selector failures, ACMTRM's `_currentDeviceStateFromKeybag` fails for
both `system_keychain_handle` and `device_keybag_handle`, then retries once per
second (`/tmp/dvm/probe/PERSIST_DCP_OP09_CLASS3_1.serial.log:762-800`).  This
does not by itself prove that clearing selector 7 produces Welcome, but it
proves that returning an acceptable device-state value is required before
credential-manager startup can complete.  Selectors 17 and 35 are co-occurring
users of the same codec; their process-level semantics remain unverified.

### Dynamic rejection witness (not a transport timeout)

An isolated `DARWIN_SEP_DEBUG=1` child, `SKS_USERSEL_OP19_DYN_1`, captured an
actual selector-7 request: its body ends
`58 99 67 c0 9f 7b 1c a2 fa ff ff ff 00 00 00 00`, i.e. context
`0xa21c7b9fc0679958`, state `-6`, and trailing zero
(`/tmp/dvm/probe/SKS_USERSEL_OP19_DYN_1.stderr.log:458-468`).  It accepted
the existing 84-byte `{2,0}` response at the SEP transport, but an LLDB
breakpoint immediately after generated codec `fcn.fffffff00957c36c` returned
to its caller at runtime `0xfffffff029547424` with `x0 = 0xe00002bc`
(`/tmp/dvm/SKS_USERSEL_OP19_DYN_1.lldb.log`, breakpoint 1).  This establishes
that the bad argument originates in the guest's op-19 codec/callback decode,
not in the serial path, an endpoint timeout, or an unobserved request shape.

There is a useful but **non-authoritative** comparison in the checked-out
reference `sep-sim.c:919-950`: its older `0x19` reply uses
`{u32 selector=0, u32 blob_length=N, byte blob[N]}`.  That layout matches the
iOS-27 caller's independently recovered pointer/length contract and is the
implementable **candidate wire arm**: retain the observed 76-byte header,
place selector zero at `+0x4c`, little-endian length at `+0x50`, and its bytes
at `+0x54` (total reply length `0x54+N`).  The reference labels its particular
eight-byte blob as guessed DER, so neither its contents nor selector zero is an
established iOS-27 fact until a candidate reaches the post-call success path.

### Blob-consumer acceptance gate

The response callback itself is not the state decoder.  The generated wrapper
`fcn.fffffff00957c36c` submits opcode `0x19` at `0xfffffff00957c3d8-3e0`; its
registered callback starts at `0xfffffff009547544`, builds two closures, and
hands them to the imported IPC dispatcher through `fcn.fffffff0095825d0` at
`...75c0-5d4`.  Consequently, the callback's common queueing machinery does
not establish additional selector values; the reliable downstream contract is
the direct state consumer below.

| Item | Required behavior | Evidence |
|---|---|---|
| blob envelope | Non-null pointer and `N <= 0x144` (324 bytes). | `fcn.fffffff00956e6ac:0xfffffff00956e6e8-704` rejects null/oversize before calling the parser. |
| parse result | The fixed-field parser must return zero.  It produces a 0x54-byte local record. | `...e704-708` branches to the error path on nonzero return; `fcn.fffffff009581144:...81180-190` initializes 0x54 output bytes. |
| accepted state kind | The signed 32-bit field written at parsed-record offset `+0x2a` must equal `-6` or `-10`; every other value takes the reject path. | `...811f0-2f8` writes the field; consumer tests `cmn w22, 6` and `ccmn w22, 0xa` at `0xfffffff00956e70c-720`. |
| cache on success | The raw input blob is copied into one of two cached records, selected by that state kind. | `...e73c-794`; `-6` selects byte size `0x148`, while the other accepted kind selects `0x298`. |
| integer encoding detail | The parser has a signed, variable-width integer-field helper, so all-zero arbitrary bytes are not a demonstrated valid state record. | `fcn.fffffff009580158:0xfffffff009580178-1b4` sign-extends the first field byte then folds the remaining bytes. |

This establishes two safe, bounded test stages.  Stage A can test only the
reply union by emitting selector zero plus a length-prefixed blob and breaking
after `fcn.fffffff00957c36c`; it must not be called success unless the blob
pointer/length pair is populated.  Stage B must provide a parser-valid record
whose `+0x2a` field is `-6` for the observed selector-7 request (or `-10`),
then prove passage through `0xfffffff00956e770` and disappearance of the
ACMTRM keybag-state failure.  The exact tag/order/values needed to construct
that record are not established by static code here; calling it DER is an
unverified hypothesis inherited only from the older reference.

## Implementation boundary

An implementer may retain the existing authenticated IPC-v1 envelope and the
`0x19` request parser, but must remove `{selector=2,scalar=0}` from the normal
success path: both the dynamic return witness and the iOS-27 blob outputs
disprove it.  The minimum union candidate is the selector-zero length-prefixed
blob layout above; an empty blob can only test that arm's decoding and must not
be treated as a state-success candidate.  Instrument the generated callback
beginning at `0xfffffff009547544` (runtime address plus the kernel slide), and
the direct consumer at `0xfffffff00956e6ac`; log the decoded selector, blob
pointer/length, parser result, and state kind before running a bounded
isolated-child sweep.  For `0x3d`, first capture the request body and stop in
`0xfffffff00957dd54` plus its response decoder; a guessed empty/status-only
reply would repeat the already disproved no-op pattern.

## Open questions

| Question | Observation that would settle it |
|---|---|
| Which response-union arm and payload make selector 7 succeed? | First try the derived candidate `{selector=0, length=N, bytes[N]}` and break at runtime `0xfffffff029547544` and `...7424`; record selector, blob pointer/length, and return value, then show `ACMTRM: _currentDeviceStateFromKeybag` succeeds in the serial log. The present selector-2/scalar-zero arm is ruled out by `SKS_USERSEL_OP19_DYN_1.lldb.log`. |
| What bytes make the parsed device-state kind `-6` or `-10`? | Log the 0x54-byte parsed record immediately after `0xfffffff009581144` returns, from a real successful device/SEP trace or a candidate sweep.  The decisive value is its signed `+0x2a` field, checked at `0xfffffff00956e70c-720`; the current static analysis does not establish the preceding field tags or order. |
| Are selectors 17 and 35 independently required for SpringBoard/Welcome? | With selector 7 cleared, compare a reproducible boot where each public-method error disappears against process evidence for `SpringBoard`/Setup Assistant and a rendered frame. |
| What is the exact `0x3d` payload and reply union for selector 68? | Run one `DARWIN_SEP_DEBUG=1` isolated child and record the raw authenticated request, then break at the codec's response decoder; require both an echoed wire capture and a successful public selector-68 return. |
