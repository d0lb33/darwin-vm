# IOMFB link protocol (DCP endpoint 0x37)

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (iPhone 16, t8140/H17P), kernelcache
`firmware/bootkc` (`MH_FILESET`, extracted 2026-09-02). All addresses below are
unslid VAs from that kernelcache; `file_offset = VA - 0xfffffff007004000`,
spot-checked at `link_send_message`'s prologue (bytes match at both the VA and
the computed file offset). The protocol code lives in the fileset entry
`com.apple.iokit.IOMobileGraphicsFamily-DCP` (`LC_FILESET_ENTRY` offset
`0x988fe0`, header addr `0xfffffff00798cfe0`; its own `__TEXT_EXEC.__text` is
`0xfffffff00a0b2080`-`0xfffffff00a0dc230` -- headers are packed low, code is
not, exactly as flagged in the task). `AppleDCPLinkServiceSoC`, the leaf class
XNU actually matches on, lives in the SoC kext
`com.apple.driver.AppleMobileDispH17P-DCP`; the wire protocol itself
(`link_send_message`, `link_handle_message`, `link_protocol_priv.h`) is in the
shared base class `AppleDCPLinkService` / `IOMobileFramebufferAP`, in
`IOMobileGraphicsFamily-DCP`. Both kexts are fully stripped (`nsyms=0`); every
function name below is `fcn.<addr>`, identified by string cross-reference, not
by symbol.

Live-verified against the running guest tonight: an existing boot at
`/tmp/dvm/probe/IOMFB4.{serial,stderr}.log` (`DARWIN_DCP_IOMFB=2`, the
echo-probe already wired into `dcp_handle()`) shows the guest panics one
message after we echo its own opening message back. That result is folded in
below and is the strongest single piece of evidence in this document: it rules
out the one reply shape that's easiest to reach for.

## Summary

Endpoint 0x37 is not AFK and not EPIC; the 64-bit RTKit mailbox field *is* the
message, with a structured 16-bit header in the low bits and a class-dependent
payload in the high 48. The AFK-style split the model currently uses for
logging (`bits[63:48]` as a 16-bit opcode) is wrong for this endpoint — it
happens to read `0x0100` for the observed message only because the high bit of
a shifted DVA lands there, not because there is a type byte at the top. The
driver's own send-side (`link_send_message`) and receive-side
(`link_handle_message`) code construct and dispatch on the **low 16 bits**,
bit-for-bit consistent with each other. The AP's opening message,
`0x0100000000000040`, decodes as **class 0 (unidirectional notify), subkind 1,
tag 0, ack 0, payload = a DART DVA (0x10000000000) in bits [63:16]** — the AP
telling the firmware the DVA of a heap it just allocated ("AppleDCPLinkService
heap size increment: 0x0" fires immediately before "AP DRIVER START!" in the
boot log). A QEMU model needs to: parse the low-16 header this way, *not*
answer with the same class/subkind (verified fatal), and most likely answer
with a **class 1** message to advance the AP's local init-ack state machine.

## Register / message format

### The field split (verified, not guessed)

`link_send_message(void *chan, void *ctx, uint64_t val)` at
`fcn.fffffff00a0cecd0` (file 0x30cacd0) builds the final 64-bit header word
like this (paraphrased from the disassembly):

```
counter_ptr = ctx ? (ctx + 4) : (chan + 0x70)
counter     = *counter_ptr                      // small per-channel state byte
tag16       = ctx ? ((*ctx & 0x3f) << 10) : 0    // 6-bit tag from *ctx, at bits[15:10]
low16       = (val & 0x2ff)                      // val's bits[7:0] and bit 9, NOT bit 8
low16       = bfi(low16, counter, bit 8, width 1)// bit 8 <- counter's bit 0
low16       = low16 | tag16
header      = low16 | (val & 0xFFFFFFFFFFFF0000) // val's bits[63:16] pass straight through
// then, only if val's bits[47:32] == 0:
//   if (header & 3) == 2 && (header & 0xc0) == 0x40: REQUIRE-fail / panic
//     (AppleDCPLinkService.cpp:0x27c-ish -- class 2 + subkind 1 is invalid
//     for a direct outbound send; that combination is reply-only, see below)
send(header)   // via a vtable call at chan_or_something+0xe8
```

`link_handle_message(void *chan, uint64_t *msgptr)` at `fcn.fffffff00a0cfac0`
(file 0x30cbac0) dispatches on the identical bit positions on receive:

```
msg     = *msgptr
class   = msg & 3            // bits[1:0]
subkind = (msg >> 6) & 3     // bits[7:6]
```

So the low-16-bit header is:

| Bits | Field | Evidence |
|---|---|---|
| [1:0] | **class** — primary dispatch selector | `and w26, w28, 3` @ `0xa0cfb04`; same mask (`x2 & 3`, `header & 3`) built on send @ `0xa0ced08`/`0xa0ced2c` |
| [5:2] | unused by the dispatcher (0 in every message observed) | not read by any `ubfx`/`and` in either function |
| [7:6] | **subkind** — secondary dispatch selector, meaning depends on class | `ubfx w0, w28, 6, 2` @ `0xa0cfb14` (class 0) and `0xa0cfc28` (class 2/3); same field built by `bfi`/mask on send (`x2 & 0xc0` check @ `0xa0ced2c`, `0xa0ced38`) |
| [8] | a flag sourced from bit 0 of a per-channel state byte at `chan+0x70` (or `*(ctx+4)` if a tag context is passed) | `ubfiz`/`bfi ..., 8, 1` @ `0xa0ced10`; consumed as `(msg>>32)&1`-style "ack bit" @ `0xa0cfc44`-`0xa0cfc48` (`bfxil x8, x28, 0xa, 6` neighbor) |
| [9] | passed straight through from the sender's `val`; not independently tested by the dispatcher we traced | `x2 & 0x2ff` keeps bit 9 (mask = `0b10_1111_1111`), bit 8 excluded and reinserted separately |
| [15:10] | 6-bit **tag**, only non-zero when the sender passes a `ctx` pointer (numbered RPCs); 0 for the class-0 handshake messages | `(*ctx & 0x3f) << 10` @ `0xa0ced00`; extracted back as `bfxil(x8, x28, 10, 6)` @ `0xa0cfc4c`/`0xa0cfd18` |
| [63:16] | class-dependent payload, passed through unmodified by `link_send_message` | `val & 0xFFFFFFFFFFFF0000` @ `0xa0ced14`, OR'd back in @ `0xa0ced1c` |

For the AP's opening message `0x0100000000000040`:

```
low16   = 0x0040  -> class=0, reserved[5:2]=0, subkind=1, ack(bit8)=0, tag=0
high48  = 0x010000000000 >> 16... i.e. msg>>16 = 0x10000000000  (= 2^40, a DVA)
```

That is confirmed structurally: the caller of `link_send_message` that prints
"IOMFB: AP DRIVER START!" (see below) builds `val` at `fcn.fffffff00a0cdfe0`
(file 0x30c9fe0, called `cdfe0` below) as

```
x8 = *(chan + 0x230)          // a DVA, cached earlier from chan+0x1e0
val = 0x40 | (x8 << 16)       // orr x2, w9(=0x40), x8, lsl 16      @ 0xa0ce074-0xa0ce078
link_send_message(chan+0x1f0, NULL, val)
```

Solving `0x40 | (x8<<16) == 0x0100000000000040` gives `x8 == 0x10000000000`,
i.e. exactly the DVA — this is not a coincidence, it is the caller's own
arithmetic, read directly out of the disassembly. **The task's guessed
"type 0x01 at bits[63:56]" is not a real field — that bit only happens to be
set because it is bit 56 of a specific DVA value.** With a different heap DVA
the top byte would be different.

### What `0x40` means, and why it is not an AFK opcode

`0x40` low-byte = class 0 (`bits[1:0]=00`), subkind 1 (`bits[7:6]=01`). This is
the shape `link_send_message`'s own guard treats as reply-only when combined
with class 2 (see the `REQUIRE`-fail check above: `class==2 && subkind==1` is
invalid to *send* directly) — consistent with subkind 1 being a distinguished
"carries an address" marker across multiple classes, not a value specific to
class 0.

### Against the `dcpep`/"length" reading of the opening message

The coordinator's parallel probe decoded the same message under the M1-era
Linux `dcpep` layout (`TYPE[3:0]`, `ACK` bit 6, `CONTEXT[11:8]`,
`OFFSET[31:12]`, `LENGTH[63:32]`) and got `len 0x01000000` (16 MB), landing
suspiciously next to the `heap size increment: 0x0` log line. Worth settling
directly, since it is the natural alternative to "this is a DVA":

- The 48-bit-wide passthrough boundary (`val & 0xFFFFFFFFFFFF0000`, i.e.
  bits[63:16], not bits[63:32]) is not inferred from this one sample — it is
  the literal immediate operand of the `AND` instruction at `link_send_message
  +0x44` (`0xa0ced14`, encoding `48bc7092`, decoded by r2 as
  `and x8, x2, 0xffffffffffff0000`), independently corroborated by the
  caller's own arithmetic in `cdfe0` (`x9(=0x40) | (dva << 16)`, which only
  reconstructs the observed message if the shift lands the DVA starting at
  bit 16, not bit 32). Both give the same 48-bit boundary. This message's low
  32 bits (bits[31:0], other than the `0x40` header) happen to be zero, which
  is exactly why a 32-bit-boundary reading of the *same bytes* also looks
  clean — it is degenerate on this one sample, not independent evidence for a
  32-bit field.
- **Offset/length fields do exist in this protocol, but they live somewhere
  else.** `fcn.fffffff00a0ce46c` (file 0x30ca46c) — cross-referenced from the
  strings `link_shared_alloc` and `link_shared_free`
  (`0xfffffff007992635`... see the earlier table) — is a ring-buffer style
  shared-heap allocator matching the `"shared buffer overflow/underflow"` and
  `ptr_to_shared_memcpy`/`shared_to_ptr_memcpy` error strings. Its own call
  into `link_send_message` (`0xa0ce694`) builds the message as:
  ```
  w24 = offset (low 16 of a 32-bit word) with size bfi'd into bits[31:16]
  w25 = 2                       // class = 2
  w25 = bfi(w25, x23, bit 9, 1) // a flag bit
  val = w25 | (w24 << 16)       // -> bits[31:16]=offset, bits[47:32]=size
  ```
  i.e. a genuine 16-bit-offset + 16-bit-size pair, in **class 2** ring-push
  notifications, at bits [31:16]/[47:32] — a completely different shape from
  the single 48-bit address in the class-0/subkind-1 announce. This is a
  second, independent piece of evidence that the protocol's fields are
  class-dependent unions, and that "length" is not what occupies the top of a
  class-0 message.
- `0xe00002bd` is indeed `kIOReturnNoMemory` (confirmed: `err_system(sys_iokit)
  | err_sub(sub_iokit_common) | 0x2bd`, matching the `mov w8,0x2bc; ...; orr
  w0,w8,1` construction in the disassembly exactly). That the *handler for
  receiving your own announce back* returns "no memory" is consistent with
  either reading, so it does not by itself distinguish them — but see the two
  points above.

None of this fully proves the DVA reading over a coincidental one, but the
instruction-level evidence (an explicit 48-bit AND mask, and a second,
differently-shaped message that demonstrably carries real offset/size fields
elsewhere) is stronger than a decode that is degenerate on the one sample
available. Treat "class 0/subkind 1 carries a 48-bit DVA, no length" as the
better-supported reading, but note it like the rest of this document: settled
by trying a reply and reading the next panic, not by static analysis alone.

## The first message: what it is, and what NOT to reply with

`fcn.cdfe0` is the function that prints `IOMFB: AP DRIVER START!` — wait, that
string is actually printed by the caller: cross-reference of the string
`"IOMFB: AP DRIVER START!\n"` at `0xfffffff00798eb32` (file 0x98ab32) lands
inside `fcn.fffffff00a0b5608` (file 0x30b1608) at `0xfffffff00a0b5668`, right
after that function's "is this instance still active" vtable check. This is
`IOMobileFramebufferAP::start()` (the boot log's very next lines —
`"IOMFB: driver commit: %s\n"` at `0x798eb4b`, `"%s: AP instance name %s\n"` at
`0x798ecc3`, `"IOMFB: Enable low latency shared events mapping..."` at
`0x798edd6`, `"IOMFB: failed to create DART extensible panic log"` at
`0x798ef3c` — are all string literals cross-referenced into this same 3,088
line function, in that address order). `AppleDCPLinkService::start()` (a
*different*, separate function, strings clustered around `0x7992xxx`, e.g.
`"IOMFB: AppleDCPLinkService heap size increment: 0x%x\n"` at `0x799229f`,
`"AppleDCPLinkService: link_state_init failed with 0x%x\n"` at `0x7992227`)
runs earlier and allocates the "heap" (shared memory pool for message bodies)
that `cdfe0` then announces — this matches the boot log order exactly:
`heap size increment: 0x0` fires before `AP DRIVER START!`.

`fcn.cdfe0` is reached indirectly (a vtable call inside `start()`'s ~3,000
lines, not a static `bl`, so no direct call xref was found from `start()`
itself). It *is* called directly, with a static `bl`, from
`link_handle_message`'s class-0/subkind-0 branch (`0xa0cfb50`, see below),
and a third, unnamed function that manages the same per-channel flag bytes
`chan+0x5c0..0x5c3` used by both `cdfe0` and `link_handle_message` reaches it
by tail branch (`b fcn.fffffff00a0cdfe0` at `0xa0ce1a4`/`0xa0ce1c0`, outside
any function r2's `afi` recognizes — likely a case in that same flag-state
switch, not disassembled further here). Given `cdfe0` constructs exactly the
byte pattern the trace shows, and that pattern's low byte (`0x40`) is the
RECEIVE-side's own trigger for "peer just told me its DVA" (next paragraph),
the identification of `cdfe0` as the announce-sender is solid regardless of
which of these paths triggers it during `start()`.

**On receive**, `link_handle_message`'s class-0 branch (`0xa0cfb04`-`0xa0cfb58`
for subkind 0, `0xa0d0008`-`0xa0d0038` for subkind != 0) does two very
different things depending on subkind:

- **subkind 0** (`0xa0cfb04`: `cbnz w0(subkind), 0xa0d0008` not taken):
  loads a channel pointer from `chan+0x68`, checks two flag bytes
  (`chan+0x5c0`, `chan+0x5c3`, and the same-name-shifted-object's `+0x5c1`),
  and if clear, **calls `fcn.cdfe0` again** (`bl fcn.fffffff00a0cdfe0` @
  `0xa0cfb50`) — i.e. resends the same DVA announce. This looks like the
  firmware's way of saying "I didn't get / didn't like that, say it again."
- **subkind 1** (`0xa0d0008`: `cmp w0,1; b.ne 0xa0d02dc`, taken for exactly
  this subkind): extracts `dva = msg >> 16`, stores it at `chan+0x40`
  (`str x8, [x19, 0x40]` @ `0xa0d0014`), then **unconditionally** logs the
  literal string `"AppleDCPLinkService: link_init_ack_callback failed with
  0x6\n"` (the `6` is a hard-coded immediate, `mov w8, 6` @ `0xa0d0018` — this
  is not a real call to a callback named `link_init_ack_callback`; the code
  just reuses that format string with a fixed diagnostic value) and returns
  `w0 = 0xe00002bd` (`mov w8,0x2bc; movk w8,0xe000,lsl16; orr w0,w8,1` @
  `0xa0d002c`-`0xa0d0034`).

**Verified live**: `dcp_handle()`'s existing `DARWIN_DCP_IOMFB=2` probe (echo
the AP's own message straight back) does exactly this — sends the AP its own
`0x0100000000000040` — and the guest immediately hits the subkind-1 branch
above and panics:

```
/tmp/dvm/probe/IOMFB4.serial.log:175: AppleDCPLinkService: link_init_ack_callback failed with 0x6
/tmp/dvm/probe/IOMFB4.serial.log:176: panic(cpu 0 caller 0xfffffff02a0cdab4): "link_message_handler failed with 0xe00002bd" @AppleDCPLinkService.cpp:882
/tmp/dvm/probe/IOMFB4.stderr.log:301-303:
  asc(DCP): AP -> IOP ep 0x37 0x0100000000000040
  dcp: IOMFB ep 0x37 msg 0x0100000000000040 | ...
  asc(DCP): IOP -> AP ep 0x37 0x0100000000000040   <- our echo
  dcp: IOMFB PROBE echo -> 0x0100000000000040
```

That `0xe00002bd` (`kIOReturnNoMemory` = `iokit_common_err(0x2bd)`) return from the subkind-1 handler propagates up to a caller
named (in the panic string) `link_message_handler`, which treats anything
other than success as fatal at `AppleDCPLinkService.cpp:882`. **This settles
one thing conclusively: the correct reply is not an identical echo of the
opening message.** It also corroborates the field-split above end to end —
the guest's own state machine recognizes `class=0, subkind=1` as "peer sent me
a DVA" and reacts exactly as the disassembly predicts.

For comparison, `DARWIN_DCP_IOMFB=1` (current default when the endpoint is
advertised — no reply at all) does *not* panic; the AP just leaves the message
unanswered and the rest of boot proceeds on other threads
(`/tmp/dvm/probe/IOMFB1.serial.log`, `IOMFB2.serial.log`: both reach a shell
normally after logging `dcp: AP -> ep 0x37 msg 0x0100000000000040 (type 0x100,
no protocol modelled)` and nothing further on that endpoint). So today's
"waits" is a silent, non-fatal stall of just the IOMFB start path, not a
watchdog panic — matching the task's framing.

## What to reply with instead (best-supported hypothesis, not yet live-verified)

Three named callback strings exist in this kext:
`link_init_callback`, `link_init_ack_callback`, `link_init_ready_callback`
(all at `0x7992xxx`, cross-referenced only from generic IOReturn-to-string
formatting blocks inside `link_handle_message`, i.e. these three names are
printed as part of failure paths for three distinct outer callers, not
`%s`-parameterized from one site). This strongly suggests a **three-stage
init handshake**: announce -> ack -> ready.

The strongest candidate for the *ack* stage is **class 1**. Evidence:

- `link_handle_message`'s class dispatch checks class==1 first
  (`cmp w26,1; b.eq 0xa0cfb5c` @ `0xa0cfb08`), routing to a block that does
  table-driven name lookups (`adrp x27,...+0xc70` — a `{value,name}` table in
  `__DATA`) purely for logging, then falls through to call
  **`fcn.fffffff00a0ce0f0`** (`bl` @ `0xa0cfc1c`) if two more flag bytes are
  clear.
- `fcn.ce0f0` (file 0x30ca0f0) is:
  ```
  if (*(chan + 0x260) == 1) {
      link_send_message(chan+0x1f0, NULL, 0x0004000000000001);
      // 0x0004000000000001: low16=0x0001 -> class=1, subkind=0, tag=0
      //                      bits[63:48]=4 (NOT a DVA-shifted value this time
      //                      -- confirms the high-48 payload's meaning is
      //                      class-dependent, a small int here vs. a DVA
      //                      shifted <<16 for class 0/subkind 1)
      w0 = lookup_table[link_send_message_return_code];  // maps 0..3 to an IOReturn
  } else {
      chan[0x23c] = 1;
      <vtable call on chan+0x240, likely IOLock/wait-event wake, on chan+0x23c>
      w0 = 0;
  }
  ```
  (`0xa0ce0f4`-`0xa0ce170`). This is called both from *our* send-completion
  path (tail-called from `cdfe0` at `0xa0ce1a4`/`0xa0ce1c0`) and from the
  receive dispatcher's class==1 branch — i.e. it is the shared "advance the
  init-ack state machine" step for both directions, which is exactly the shape
  of an ack handler.

**Best-supported next experiment**: reply to the AP's opening
`0x0100000000000040` with a **class 1** message rather than an echo or a
class-0/subkind-0 resend, most simply `0x0004000000000001` (`link_send_message`
will still patch bit 8 from local state and, since this reply carries no
`ctx`, tag stays 0) or with the firmware's own heap DVA in bits[63:16] and
`0x01` in the low byte (class 1, subkind 0) if the firmware side is expected
to announce its own buffer symmetrically — the disassembly does not
distinguish these two by itself. Whichever is tried, `DARWIN_DCP_IOMFB=2`'s
existing echo probe is the wrong control to leave in place: swap it for a
hand-built class-1 frame and watch for either (a) no panic and a further
message from the AP (progress), or (b) a *different* panic string (which
callback failed next, telling us the reply's other fields are still wrong).

## Class 2 / class 3 (numbered RPC replies, sketched only)

Once past the class-0 handshake, `link_handle_message`'s class==2/3 branch
(`0xa0cfc28` onward) implements a general request/reply correlation
mechanism, evidenced but not fully derived:

- `subkind` (bits[7:6], re-extracted at `0xa0cfc28`) selects between a
  `0xa0cfd0c` path (subkind 1) and, when subkind is 0 and class is exactly 2,
  a **tag + ack-bit keyed callback dispatch** (`0xa0cfc40`-`0xa0cfd08`):
  builds a 40-bit key `(ack_bit << 32) | tag(bits[15:10])`, looks up a
  registered callback via a pointer-authenticated indirect call
  (`blraa` through `chan+0xe8` then a second `blraa` through a table at
  `0xa0cf4ac`/`0xa0d0318`), and invokes it. This is the machinery behind the
  `"%s: link_rpc_lookup callback not found: '%s'"` string
  (`0xfffffff007992778`) — i.e. numbered async RPCs (the ones that use the
  6-bit `tag` field and pass a non-NULL `ctx` to `link_send_message`) get
  their replies routed here by tag, not by class/subkind alone.
- Validation on the *send* side already showed class 2 + subkind 1 is
  reserved/invalid for a direct `link_send_message` call — it must go through
  a different path (plausibly `fcn.ce0f0`'s sibling for class 2, not
  identified here).

This is genuinely a second layer on top of the class-0 handshake and is not
needed to get past the first message; it only matters once real IOMFB
commands (mode set, swap, etc.) start flowing. Flagged here so it is not
mistaken for part of the init handshake.

## The two complaints — neither blocks the handshake

- **`property not found: "audio"`**: the format string is
  `"property not found: \"%s\"\n"` at `0xfffffff0078ff61e` (file `0x8fb61e`),
  in **`com.apple.iokit.IOAVFamily`**, not in the IOMFB kexts themselves — a
  generic device-tree/registry property helper IOAVFamily exports and IOMFB
  calls into. It fires at boot log line 169, *before* `AppleDCPExpert` even
  matches (i.e. before any of the endpoint-0x37 code discussed above runs),
  and boots that don't advertise endpoint 0x37 at all (`IOMFB1`, `IOMFB2`)
  reach a shell normally with this same line present. It is a missing
  "does this display support audio" boolean on our synthetic `disp0`/`dcp`
  device-tree node, defaulted false, and does not participate in the ep 0x37
  exchange at all.
- **`IOMFB: failed to create DART extensible panic log`**: string at
  `0xfffffff00798ef3c` (file `0x98af3c`), inside `IOMobileFramebufferAP::start()`
  itself (`0xa0b5eac`). Disassembly around it (`0xa0b5e1c`: `cbz w0,
  0xa0b5ea8` on the return of the allocation call `fcn.fffffff00a0dc930` @
  `0xa0b5e18`) shows the failure path **falls through to the same common
  continuation** (`0xa0b5eb4`) as the success path — it is a soft-fail: an
  optional DART-backed crash-diagnostics buffer that `start()` logs and then
  continues without. Neither complaint needs a device-tree or DART fix before
  endpoint 0x37 can be answered correctly.

## Open questions

- **The exact bytes of a correct ack reply.** Class 1 is well-supported as the
  right *class*; the right payload in bits[63:16] (a small integer like the
  driver's own `4`, vs. the firmware's own heap DVA) is not distinguished by
  static analysis alone. Settle by trying both against the live guest with
  `DARWIN_DCP_IOMFB` extended to send a class-1 frame instead of echoing, and
  reading which panic (if any) comes next.
- **What follows a successful ack.** `link_init_ready_callback` implies a
  third stage; no call site for it was pinned down (all three xrefs found are
  inside the same generic IOReturn-to-string block in `link_handle_message`,
  used for logging a failure generically, not for identifying where the
  callback itself is invoked). Once class 1 stops panicking, the next unread
  message from the AP is the fastest way to find this stage's trigger.
- **Where `chan+0x1e0`/`chan+0x230` (our own DVA) gets populated** — no store
  to that offset was found inside `IOMobileGraphicsFamily-DCP`; it is set
  either in `AppleDCPLinkServiceSoC` (`AppleMobileDispH17P-DCP`, not
  disassembled for this) or via a device-tree/DART allocation earlier in
  `AppleDCPLinkService::start()`. Not needed to answer the protocol, but
  useful if the model wants to hand back a `dva == 0x10000000000`-shaped
  number for symmetry — note this equals the DVA already observed for AFK
  ring endpoint 0x20 in `docs/re/dcp-bringup.md`; that is very likely
  coincidence (both are the first DVA the guest's IOMMU allocator hands out
  in their respective, separate DART mapper IOVA spaces), not a shared
  address space, but this was not independently confirmed.
- **Class 3** is only inferred to exist from the 2-bit width of the class
  field and an unreached "b.ne 0xd0310"-style error branch; no message of
  this class was observed on either side.
