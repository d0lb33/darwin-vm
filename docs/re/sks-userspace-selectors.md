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
| 7 | Switch index 6 at `0xfffffff009555038`; requires one input scalar and one output scalar (`...5058-506c`). It calls `fcn.fffffff009547360` at `...50b0` with variant/state argument zero. | `0x19` | **Confirmed framing:** 96-byte IPC-v1 request: `u32 body_size=0x48` at `+0`, opaque 76-byte header, then `u32 request_selector=0` at `+0x4c`, `u64 context` at `+0x50`, `i32 requested_state` at `+0x58`, `u32 zero` at `+0x5c`. The current reply shape is 84 bytes: 76-byte header + `{u32 selector=2,u32 scalar}`. **The required scalar value is unverified.** | The off-by-one dispatch is explicit at `0xfffffff009552aa0` and the table base/index at `...2b1c-3c`; case 6 call at `...5058-50b0`; opcode literal at `0xfffffff00957c3d8-3e0`. Field offsets and live accepted frame are `darwin_sep.c:539-547`, `1380-1412`, and `PERSIST_DCP_OP09_CLASS3_1.stderr.log:529-532`. |
| 17 | Switch index 16 at `0xfffffff009554c5c`. It accepts zero or one scalar (`...4c6c-4c90`); both paths join `0xfffffff009556e24-3c` and call the same `fcn.fffffff009547360`. | `0x19` | Same IPC frame and scalar-union reply as selector 7. Variant/input state differs; do not collapse it to selector 7 semantically. | Case 16, join, and call addresses above; `fcn.fffffff009547360` invokes generated codec `fcn.fffffff00957c36c` at `0xfffffff009547404-420`, which emits `w1=0x19` at `0xfffffff00957c3d8-3e0`. |
| 35 | Switch index 34 starts at `0xfffffff009554c54`, clears `w27`, and falls through into index 16 at `...4c5c`. | `0x19` | Same IPC frame and scalar-union reply as selector 7; its public method has no independent wire codec. | Fall-through is adjacent instructions at `0xfffffff009554c54-5c`; shared operation proof is the selector-17 path above. |
| 68 | Switch index 67 at `0xfffffff009554458`; it requires one input scalar and no structure output (`...445c-46c`). | `0x3d` | **Not yet decoded. Do not send a status-only reply.** The generated codec construction at `0xfffffff0095545b0-5dc` calls `fcn.fffffff00957dd54`, which invokes wire opcode `0x3d` at `0xfffffff00957ddc0-28`. Its payload and response union require a live capture. | The public selector appeared once as `sel:68` in `/tmp/dvm/probe/PERSIST_DCP_D120_OP09_1.serial.log:587`; static chain addresses above. A prior QEMU trace records unknown `0x3d` as status-only, which is not a positive control. |

### What the existing `0x19` evidence does and does not establish

`fcn.fffffff009547360` supplies a callback to the generated codec and later
uses its decoded output (`0xfffffff0095473f4-420`, `...742c-4c8`).  Thus an
authenticated response and a syntactically accepted IPC frame are insufficient
evidence that the public method succeeded.  The current model documents and
returns `{selector=2, scalar=0}` at `qemu-sptm/hw/arm/darwin_sep.c:532-547`
and `1643-1652`, yet the current full boot still returns `e00002bc` from all
three public operations.

The causal witness is stronger for selector 7 than for the other selectors:
after selector failures, ACMTRM's `_currentDeviceStateFromKeybag` fails for
both `system_keychain_handle` and `device_keybag_handle`, then retries once per
second (`/tmp/dvm/probe/PERSIST_DCP_OP09_CLASS3_1.serial.log:762-800`).  This
does not by itself prove that clearing selector 7 produces Welcome, but it
proves that returning an acceptable device-state value is required before
credential-manager startup can complete.  Selectors 17 and 35 are co-occurring
users of the same codec; their process-level semantics remain unverified.

## Implementation boundary

An implementer may retain the existing authenticated IPC-v1 envelope and the
`0x19` request parser, but must not treat scalar zero as a demonstrated device
state.  Instrument the generated response callback beginning at
`0xfffffff009547544` (runtime address plus the kernel slide), log the decoded
selector and scalar, and run a two-value response sweep only on an isolated
child.  For `0x3d`, first capture the request body and stop in
`0xfffffff00957dd54` plus its response decoder; a guessed empty/status-only
reply would repeat the already disproved no-op pattern.

## Open questions

| Question | Observation that would settle it |
|---|---|
| Which scalar value makes the selector-7 device-state method return success? | Break after the `0x19` response is decoded in `fcn.fffffff009547360`/its callback, record the decoded scalar and return value for a real reply candidate, then show `ACMTRM: _currentDeviceStateFromKeybag` succeeds in the serial log. |
| Are selectors 17 and 35 independently required for SpringBoard/Welcome? | With selector 7 cleared, compare a reproducible boot where each public-method error disappears against process evidence for `SpringBoard`/Setup Assistant and a rendered frame. |
| What is the exact `0x3d` payload and reply union for selector 68? | Run one `DARWIN_SEP_DEBUG=1` isolated child and record the raw authenticated request, then break at the codec's response decoder; require both an echoed wire capture and a successful public selector-68 return. |
