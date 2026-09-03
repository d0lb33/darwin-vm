# Post-Early-Boot AppleSEPKeyStore selectors

## Source metadata

| Item | Value |
|---|---|
| iOS build / device evidence | `RaveSeed24A5430a.D47DeveloperOS` is the System volume named in `/tmp/dvm/probe/PERSIST_DCP_D120_OP09_1.serial.log:...`; the extracted `AppleSEPKeyStore` banner identifies version `2383.2.1` in that log line 128. The device-tree and kernelcache slide used for this kext are the project's iOS 27/T8140 boot inputs; kext static addresses below are unslid. |
| driver | `/tmp/dvm/kexts/com.apple.driver.AppleSEPKeyStore`, arm64e Mach-O, SHA-256 `4e94489161384d9fc1b14704ad064e27e674fd2e586055b5bf388a95c92bf273`; `__TEXT_EXEC.__text` starts at VA `0xfffffff0095425b0` (file offset `0x8000`). |
| positive control | `SKS_OP19_BH6_PAD1` accepts op `0x19` request bodies of 96 bytes and emits authenticated 96-byte replies (`/tmp/dvm/probe/SKS_OP19_BH6_PAD1.stderr.log:463-474`). The run has no public selector 7/17/35 failures; selector 7 reaches its success-side diagnostic at serial line 640, and ACMTRM reports successful environment-state reads at lines 646, 654, and 658. The independently decoded op `0x09` reply demonstrates that this transport's authenticated IPC-v1 header is 76 bytes and that its payload begins at request/reply offset `0x4c`. |

The failing public selectors are not SEP wire opcode numbers: the dispatcher subtracts
one before indexing its 169-entry switch at `0xfffffff009552aa0` and
`0xfffffff009552b04-3c`.  Public selectors 7, 17, and 35 all reach the
same generated SEP operation, wire opcode `0x19`, while public selector 68
reaches wire opcode `0x3d`.  Selector 7 is the demonstrated near-term blocker:
after every failure, ACMTRM says it cannot acquire either initial keybag device
state and schedules another retry. A padded selector-zero blob reply now clears
all observed selector 7/17/35 failures and lets ACMTRM acquire its initial state.

## Dispatcher and wire contracts

| Public selector | Static path and externally checked shape | SEP wire operation | Confirmed request/reply layout | Evidence |
|---|---|---|---|---|
| 7 | Switch index 6 at `0xfffffff009555038`; requires one input scalar and one output scalar (`...5058-506c`). It calls `fcn.fffffff009547360` at `...50b0` with variant/state argument zero. | `0x19` | **Confirmed framing:** 96-byte IPC-v1 request: `u32 body_size=0x48` at `+0`, opaque 76-byte header, then `u32 request_selector=0` at `+0x4c`, `u64 context` at `+0x50`, `i32 requested_state` at `+0x58`, `u32 zero` at `+0x5c`. **Confirmed reply:** 20-byte payload `{le32 selector=0, le32 length=9, 31 07 0c 02 62 68 02 01 fa, 00 00 00}`. The DER is `SET { UTF8String "bh", INTEGER -6 }`; the final three bytes are blob-codec alignment. | Dispatch is explicit at `0xfffffff009552aa0`, `...2b1c-3c`, and `...5038-50b0`. Exact codec `0xfffffff009560e94` decodes selector zero at `...560f40-5c` and the response blob at `...560fac-fc4`. The consumer copies the blob at `0xfffffff00956e770-794`; the live success witness is serial lines 640 and 646. |
| 17 | Switch index 16 at `0xfffffff009554c5c`. It accepts zero or one scalar (`...4c6c-4c90`); both paths join `0xfffffff009556e24-3c` and call the same `fcn.fffffff009547360`. | `0x19` | Same request framing and blob-result contract as selector 7. Variant/input state differs; do not collapse it to selector 7 semantically. | Case 16, join, and call addresses above; `fcn.fffffff009547360` invokes generated codec `fcn.fffffff00957c36c` at `0xfffffff009547404-420`, which emits `w1=0x19` at `0xfffffff00957c3d8-3e0`; the shared blob result is proved by `...7418-420` and `...744c-454`. |
| 35 | Switch index 34 starts at `0xfffffff009554c54`, clears `w27`, and falls through into index 16 at `...4c5c`. | `0x19` | Same request framing and blob-result contract as selector 17; its public method has no independent wire codec. | Fall-through is adjacent instructions at `0xfffffff009554c54-5c`; shared operation and output pair proof are as above. |
| 68 | Switch index 67 at `0xfffffff009554458`; it requires one input scalar and no structure output (`...445c-46c`). | `0x3d` | **Not yet decoded. Do not send a status-only reply.** The generated codec construction at `0xfffffff0095545b0-5dc` calls `fcn.fffffff00957dd54`, which invokes wire opcode `0x3d` at `0xfffffff00957ddc0-28`. Its payload and response union require a live capture. | The public selector appeared once as `sel:68` in `/tmp/dvm/probe/PERSIST_DCP_D120_OP09_1.serial.log:587`; static chain addresses above. A prior QEMU trace records unknown `0x3d` as status-only, which is not a positive control. |

### Exact codec and positive-control result

`fcn.fffffff009547360` supplies a callback to the generated codec and gives
it writable `blob_ptr*`/`blob_len*` outputs (`0xfffffff009547418-420`).  Only
after the generated call returns zero does it reload the pair
(`...744c-454`) and pass it to `fcn.fffffff00956e6ac`, which rejects a null
pointer or length above `0x144`, copies the bytes, and records their decoded
type (`0xfffffff00956e6e8-720`, `...770-794`).

The exact bidirectional codec is `fcn.fffffff009560e94`. Its selector helper
accepts only selector zero (`0xfffffff009560f40-5c`); its request branch encodes
the context/state fields (`...560f74-fa4`), while its reply branch invokes the
blob helper for the output pair (`...560fac-fc4`). The blob helper computes
`(length + 3) & ~3` at `0xfffffff00957f868-880`, bounds-checks the rounded body
at `...57f880-88c`, and advances over it at `...57f8c8-8dc`. Therefore the
nine-byte DER requires three trailing bytes and the complete reply is 96 bytes.
The earlier 93-byte attempt failed this bound before the DER parser ran.

In `SKS_OP19_BH6_PAD1`, selector 7 reaches its success diagnostic at serial
line 640 and ACMTRM successfully reads Env(7) at lines 646, 654, and 658. No
selector 7, 17, or 35 failure occurs. The remaining `apfs_new_key_bulk` error
and selector 43/68/93/101/107 errors are separate operations and must not be
attributed to op19.

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

The accepted reply bytes at `+0x4c` are:

```text
00 00 00 00  09 00 00 00  31 07 0c 02 62 68 02 01 fa  00 00 00
```

The parser descriptor global `0xfffffff00b821680` points to literal DER
`0c 02 62 68`; encoder `0xfffffff00958103c-1050` pairs it with signed field
`record+0x2a`, and parser `...5812f0-2f8` writes it back there. The consumer
accepts `-6` or `-10` at `0xfffffff00956e70c-720`. `srcd` is a different field
and is not a valid substitute.

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

The positive control completes both test stages: the generated codec accepts
the padded selector-zero blob, the parser accepts `bh=-6`, the consumer caches
it, and ACMTRM's previously repeating keybag-state failure disappears.

## Implementation boundary

Retain the exact padded selector-zero response above. Do not copy the request's
`-501` state into `bh`: the fixed `bh=-6` response cleared all observed users of
the codec, including requests carrying `-501`. For `0x3d`, first capture the
request body and stop in `0xfffffff00957dd54` plus its response decoder; a
guessed empty/status-only reply would repeat the already disproved no-op pattern.

## Open questions

| Question | Observation that would settle it |
|---|---|
| Are selectors 17 and 35 independently required for SpringBoard/Welcome? | The same fixed response clears them dynamically, but only process evidence plus a rendered frame will establish their higher-level role. |
| What is the exact `0x3d` payload and reply union for selector 68? | Run one `DARWIN_SEP_DEBUG=1` isolated child and record the raw authenticated request, then break at the codec's response decoder; require both an echoed wire capture and a successful public selector-68 return. |
