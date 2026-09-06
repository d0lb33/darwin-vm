# ACM/SCRD response contract for the first userspace requests

## Early control0x33: ratchet status (2026-09-06)

APP_SCRD33_EARLY1 restores the pre-request APP_EARLY_BOOT_SEED1 checkpoint
with SEP tracing and stops on the first unanswered request at38.142 active
seconds. The72-byte request is saved as
`/tmp/dvm/APP_SCRD33_EARLY1/scrd33-ratchet-status.bin`, SHA-256
`74d3691dfa9796595f81946256ae5fa40d582e5b55fe68f073913b93fb6b17fa`.
After the36-byte outer header it contains a null16-byte context, u32 parameter
count0, u32 input length12, then the three u32 words `{1,2,0}`.

LocalAuthenticationCore's `+[LACACMHelper ratchetStatusWithConfig:]`
at0x20628a9ac..0x20628a9b8 constructs exactly those three words; its call at
0x20628aa4c passes12 bytes to `_ACMSEPControl`. The low-level call selects
command0x33 at0x2063ab5c0. This identifies the early72-byte request; it does
not identify the later208-byte0x33 request.

The response serializer/deserializer at0x2063a4f08/0x2063a4f7c requires u32
length followed by that many bytes after the standard SCRD envelope. The
helper's response block at0x20628ab1c wraps them in NSData. The ratchet parser
`_configFromRatchetState:` at0x206285f58 copies56 bytes at offset0, while
`_statusFromRatchetState:` at0x206285f9c copies75 bytes at offset0x100. Thus
the consumer reads through offset0x14a. This proves a minimum extent331,
not the full response size or initial configuration values. A zero-length
successful response is invalid for this consumer. The helper separately
handles nonzero ACM status at0x20628aa50..0x20628aaac. No new reply is modeled
until the appropriate initial state or error contract is established.

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
| Live command 10 | 36-byte request, request sequence 1, SUID 0; caller declares no output bytes. | `SKS_OP19_BH6_PAD1.stderr.log:373-378` shows `01 00 1c 00`, sequence 1, logical FourCC `SCRD` serialized as bytes `44 52 43 53` (`DRCS`), and command byte `0x0a`; `serial.log:575-579` records `inLen=36`, `outLen=0`. |
| Live command 25 | 40-byte request, request sequence 2, SUID -1; caller declares one output byte. | `SKS_OP19_BH6_PAD1.stderr.log:1189-1194` shows the same header and wire-order `DRCS`, then command byte `0x19`; `serial.log:624-628` records `inLen=40`, `outLen=1`. |
| Former timeout cause | The endpoint-10 switch logged the request without replying. The resulting errors were `0xe00002d6`. | Historical control `SKS_OP19_BH6_PAD1.serial.log:575-579` and `:624-638`. |
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
The byte placement follows `frame()` at `darwin_sep.c:726-728`; these
successful operations use status zero. A nonzero status takes the failure
route; the later opt-in unsupported-transport experiment tests that route.  `SS` must be copied from the incoming body offset
`+4`, not derived from the mailbox tag: the receiver compares its reply `+4`
against the request body `+4` at unslid `0xfffffff00952920c-0xfffffff009529218`.

The command-10 response has zero residual payload after its 12-byte header,
which matches its live `outLen=0`.  The command-25 response has one residual
byte after the same header, matching its live `outLen=1`; use zero only as a
positive-control value (plausibly "developer mode disabled"), not as a
documented SCRD policy assertion.

`UI_SCRD_DCP2` is the dynamic positive control. QEMU records matching-sequence
12- and 13-byte replies at stderr lines 391 and 401. The guest emits none of
the former command-10/25 timeout or `0xe00002d6` error lines, reaches Early
Boot at serial line 640, remains panic-free for the 100-second probe, and
continues into the IOMFB hot-plug path at serial line 745. An earlier test that
compared bytes at `+0x1c` with ASCII `SCRD` timed out exactly like the control;
its hexdump established the on-wire `DRCS` order before this successful run.

## Concrete implementation plan

1. Add a dedicated endpoint-10 helper in `qemu-sptm/hw/arm/darwin_sep.c` rather
   than changing generic mailbox code.  It should DART-write the OOL reply,
   parse only the observed request invariants (`u16 version=1`, header length
   `0x1c`, wire-order `DRCS` tag at `+0x1c`, command byte at `+0x20`), and log the
   sequence, command, reply length, and status.
2. Dispatch command byte `0x0a` to the 12-byte envelope and command byte
   `0x19` to the 13-byte envelope.  Leave unrecognized command bytes
   unanswered and dump their OOL body; this keeps unknown SCRD behavior
   observable instead of turning it into an invented success.
3. The fresh-child `UI_SCRD_DCP2` run supplies the positive control above. A
   later regression boot must retain ANS, SKS, and DCP behavior.
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

## Fresh-provisioning context creation (0x24), 2026-09-05

The fresh run `APP_SCAN_COCOA_BASE1` reaches a 40-byte command 0x24 request
with no reply. See `fresh-migration-performance.md` for the captured request
hash and failed-state checkpoint. Disassembly in
`/tmp/dvm/APP_FRESH1/re/acm-disasm.txt` establishes a 21-byte payload:
`LibCall_ACMContextCreate` selects the command at
`0xfffffff00952d270..0xfffffff00952d29c` and checks length at
`0xfffffff00952d2a8`. The generic envelope parser still applies, so a
12-byte response header plus this payload would total 33 OOL bytes.

The first consumer at `0xfffffff00952d3dc..0xfffffff00952d3fc` separates
payload 0..15 (opaque context handle), byte 16 (optional caller output), and
bytes 17..20 (context object offset 16). This is structural evidence, not
proof of the latter fields' semantics. The handle is subsequently sent back
in context-delete command 2 (`0xfffffff00952d9b4..0xfffffff00952d9d4`) and
context-info command 0x2e (`0xfffffff009533640..0xfffffff009533688`). A
successful creation response therefore needs a corresponding handle
lifecycle, not just a padded envelope. This command remains unimplemented
pending that contract. Neither reference emulator supplies SCRD semantics
(`inferno-t8030-reuse.md`, "Two things this does not give us").

### Remaining creation fields resolved in LocalAuthenticationCore

The matching dyld-cache image was extracted to
`/tmp/dvm/APP_FRESH1/re/LocalAuthenticationCore`.
`_ACMContextGetTrackingNumber` at 0x2062234ac..0x2062234b4 reads context+16.
`_ACMContextCreateWithFlags` passes the address of `__logLevel` as the fifth
LibCall argument at 0x2063940ec..0x206394114. The LibCall consumer at
0x2063a804c..0x2063a8050 stores payload byte 16 through that pointer.
Thus payload 17..20 is the tracking number, and byte 16 is logging level,
not an authentication result. The first 16 bytes are still opaque tokens.

An experimental `DARWIN_SCRD_CONTEXTS=1` model now handles only the exact
40-byte create family, generates an empty context with a UUID token, and
returns tracking number plus error-level log policy (0x46). These are explicit
host model choices, not recovered sepOS token generation. No credentials or
authentication grants are synthesized. The owner, handle, and tracking state
are preserved in an optional `darwin-sep/scrd-contexts` VMState subsection;
old checkpoints load with an empty context table. Creation state is committed
only after the OOL response DMA write succeeds. The model is opt-in because
follow-on operations, deletion/reuse, and end-to-end behavior still need runtime
validation. Unknown requests remain unanswered and observable.

The capture parser accepts varying request sequence and matching SUID fields,
but rejects a bit-zero mutation at each other byte position of the captured shape, every
truncation, and an overlong body. Its two unit tests pass; 69 host regressions
also pass (`APP_SCRD_CREATE-host-tests.log`).

`APP_SCRD_CONTEXT1` was an uninstrumented headless replay from the earlier
`APP_MIGRATION_NATIVE_180` performance checkpoint; it was manually stopped
before observing a create request. The authoritative original failure launch
record instead names `APP_MIGRATION_EXTENSIONS1`. `APP_SCRD_CONTEXT2` uses
that later healthy checkpoint to reach the captured request sooner. Neither
run is a fresh-disk validation or a completed credential lifecycle test.

### Creation accepted; external-form export is the next gate

`APP_SCRD_CONTEXT2/qemu.stderr.log:7526..7541` records creation of CS[1] for
SUID 501, a 33-byte command-0x24 response, then native command 0x19, then an
unmodelled command 0x13. The watch pauses at 17.899 active seconds with no
LLDB/plugins. The next request is captured through the DART at
`APP_SCRD_CONTEXT2/scrd13.bin` (52 bytes), SHA256
`eb57c15390d1b79228b4410edb593569792e88fda8cb2bbbb620f7182a79e6ce`.
The failed-state checkpoint `APP_SCRD13_WAIT1` includes the first populated
context-table VMState subsection. It is not a healthy provisioning parent.

`_ACMContextGetExternalForm` at 0x206394720..0x20639473c sends command 0x13,
16 input bytes, and no output buffer. On success, 0x206394744..0x206394758
passes those same 16 bytes to the caller's export block. The next opt-in
model iteration acknowledges only a well-framed export carrying a handle
already issued to the request's SUID. It does not accept arbitrary tokens.
`APP_SCRD_CONTEXT3` replays from the healthy extensions checkpoint to validate
that operation. Deletion, external-form import, table reclamation, and full
migration validation remain open.

The import consumer is also located for the next probe:
`_LibCall_ACMContextCreateWithExternalForm` sends 16-byte input with command
0x25 and requires nine payload bytes (0x2063a8158..0x2063a8178). At
0x2063a81d8 it tests the first u32 for existence; when nonzero it copies the
original input token, payload bytes 5..8 as tracking number, and byte 4 as
logging level (0x2063a81fc..0x2063a8214). Its fallback command 0x12 expects
five payload bytes. No import behavior is implemented solely from this
static contract; the next live request will establish the actual framing.

`APP_SCRD_CONTEXT3/qemu.stderr.log:4141..4156` accepts creation and export;
line 6684 then records command 0x02, body 52. Captured request
`APP_SCRD_CONTEXT3/scrd02.bin` SHA256
`7b1f1e5af3cf5a769289e2d7369573ceff828f9d864511b285fe28b548e97261`
uses the same request envelope and the newly created token. The next model
iteration accepts deletion only for an active token and its recorded owner,
clears its slot after successful response DMA, and reuses freed slots with
fresh tokens and increasing tracking numbers. Optional context VMState is
now version 2; version 1's append-only count initializes the tracking counter
on load. `APP_SCRD_CONTEXT4` tests this full observed sequence from the healthy
checkpoint. Import has not yet appeared in this sequence.

`APP_SCRD_CONTEXT4` completes the observed creation -> environment query ->
export -> deletion sequence: stderr lines 7118..7130 and 10649..10650.
The uninstrumented replay then runs to its 120.048-second limit without an
additional unanswered SEP request. This is positive evidence for that
sequence, not proof that every provisioning SEP operation is implemented.
The frozen process walk finds 307 processes; DataMigrator (process name
`com.apple.datami`, pid 123) remains alive with 19 threads. Several wait in
DataMigration XPC handling and libdispatch ownership waits; migration is not
yet demonstrated complete. Read-only on-demand evidence is in
`APP_SCRD_CONTEXT4/followup/migration-processes.json`, generated by the new
repository tool `tools/re/inspect_migration_processes.py` without LLDB or
reading the entire RAM image. `APP_SCRD_LIFECYCLE1` saves this later state for
continued diagnosis and version-2 context-state restore validation.

Both paused restore compatibility checks succeed with matching checkpoint PC:
`APP_SCRD_STATE_V2` restores the post-deletion version-2 table, and
`APP_SCRD_STATE_V1` restores the earlier version-1 table with an active handle.
Both report slots=1 and last CS[1] in `qemu.stderr.log:54`. This tests reading
both serialized layouts, not use of the restored active token in a new call.
The diagnostic VMs were quit after verification. Final host regressions:
69 pass; SCRD parser tests: 3 pass; existing SKS tests: 26 pass. Logs are
`/tmp/dvm/APP_SCRD_FINAL-{host,unit,sks}-tests.log`.

### Empty context-data reset (0x28), experimental context model

Paused `APP_SCRD28_CALLER1` identifies coreauthd pid 144's blocked thread
0xffffffe89d859120 through LocalAuthenticationCore's `setData:type:encoded:error:`
and ModuleACM runtime 0x1030da0d8. The module's live Mach-O at 0x1030cc000
has UUID 4DB51F53-FDEA-31A7-8958-C9A00E9F4C59, matching the IPSW
`LocalAuthentication.framework/Support/ModulePlugins/ModuleACM.bundle/ModuleACM`.
Its static 0xdd30..0xdd40 and 0xe0a8..0xe0d8 select credential type -8,
pass nil when the supplied credential is nil, and map it to context data type 5.
The observed wire request is consequently a clear/reset, not a supplied secret.

The opt-in context model now accepts only the captured 73-byte 0x28 shape:
existing v1 header, owner, issued active handle16, data-type 5, zero data length,
one parameter of type 14 with length 1 and value 0. All contexts remain empty
because no credential-addition or nonempty data setter is implemented. Clearing
that data is therefore idempotent and needs no new persistent state. The
reply carries only the existing 12-byte success envelope, as required by
AppleSEPCredentialManager 0xfffffff009532b24..0xfffffff009532b44.
Any future populated-context implementation must add real stored data/clear
semantics before extending the accepted setter shapes.

Parser tests cover the valid owner/token outputs, every truncated length,
extra bytes, and mutations of every constrained byte. Active-handle and owner
checks remain in the device handler, shared with export/delete. These parser
tests do not replace stateful device-negative tests or full fresh-image validation.

### Context encoding-seed query (0x29), unresolved

`APP_SCRD29_CALLER1` restores the failed request checkpoint exactly. Paused
coreauthd pid 144 thread 0xffffffe8996f98f0 is in `ACMContextGetData` through
ModuleACM runtime 0x1030daae4, static 0xeae4 in the UUID-verified module above.
At 0xea94..0xeab4 it checks `checkOriginatorCanAccessEncodingSeed:error:`;
0xead0..0xeae0 requests data type 13. This is an encoding seed, not the
previously cleared type-5 credential data. Empty success is not justified.

Correct wire interpretation of the 61-byte capture: header36, handle16,
data-type u32 13 at +52, **one-byte length-query flag 1 at +56**, then parameter
count u32 zero at +57. The apparent aligned u32 1 is not a parameter count.
Kernel serializer 0xfffffff00950976c..0xfffffff009509794 writes exactly that
layout. LocalAuthenticationCore 0x2063a7cc0..0x2063a7cf4 first requests a
four-byte length; 0x2063a7cfc..0x2063a7d60 allocates the reported length and
reissues the query with flag zero to retrieve the bytes.

ModuleACM 0xeafc..0xeb20 hands the resulting data back to the native client.
LACContextCredentialCoder encode:seed:error: at 0x206271038..0x20627104c
forwards the seed plus external context reference to LACACMHelper;
0x2062898e0..0x2062898fc chooses ACMEncryptDataEx version 2. Its native
crypto_generateKeyFromSharedInfo path at 0x2063a52e4..0x2063a5354 performs
HKDF with variable input lengths, context-associated salt and acm_transport
info, producing a 32-byte key. That output key length does not establish
SEP's seed length, generation policy, or seed lifetime. Those details still
need evidence; do not substitute a constant public token for a seed or grant
credentials as a side effect of retrieving it.


## Opt-in unsupported-transport experiment (2026-09-06)

DARWIN_SCRD_UNSUPPORTED=1 answers only the captured61-byte type13
length query and72-byte initial ratchet query with kIOReturnUnsupported
(0xe00002c7), a12-byte version/sequence-matched envelope, and no data payload.
This is a diagnostic transport failure, not a successful seed/ratchet model.
All other requests retain their previous behavior; bootstrap defaults are
unchanged. The strict parser rejects other data types, fetch mode, nonempty
parameter lists, altered header fields, and the later208-byte ratchet request.

Native evidence: AppleSEPCredentialManager loads the mailbox status at
95291e4, validates the envelope and echoed sequence at95291f4..9529218,
and tests status at9529310. LAC's length-query caller tests the return at
2063a7cf8 before reading/allocating the seed length. Initial ratchet's
20628aa50..aaac creates an error for a nonzero return. The host SDK's
IOKit/IOReturn.h:109 defines unsupported as iokit_common_err(0x2c7).
This does not establish a native ACM policy-error number; none is invented.

Build and five SCRD unit tests pass (strict framing mutations included), as do
all75 host tests. APP_SCRD_ERROR1 restores APP_SPLASHBOARD_WAIT1 with the new
flag and no debugger/plugin. qemu.stderr.log:1782 records the explicit29
error; serial.log:1047 reports the same SEP status and length12 from the
native receiver. AssertMacros logs propagate the error through the kernel
and userclient at1051..1057; these are error-path logs, not a kernel panic.
The guest then deletes CS[3] atstderr:2014. This proves response delivery and
context cleanup, not successful credential encoding or full migration.
Artifacts live under checkpoints/APP_SPLASHBOARD_WAIT1/restores/APP_SCRD_ERROR1.
