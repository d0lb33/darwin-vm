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

Update, same day: the class-1 ack this document derived was implemented and got the guest past that panic entirely; it now fails at a **firmware hash check** instead, fully explained in the "Addendum" section below (local is a reproducible `crc32`, remote is a field of the same class-1 message, and the fix is what byte value to put where).

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

## Addendum (2026-09-02): firmware hash check, after a correct class-1 ack

The coordinator implemented the class-1 ack derived above (`0x0004000000000001`
in reply to the AP's class-0/subkind-1 announce). The
`"link_message_handler failed with 0xe00002bd"` panic is gone. The guest now
gets one stage further and panics differently:

```
IOMFB: Firmware hash checksum mismatched: local=0x15A5C96B, remote=0x00000000
panic(cpu 0 caller 0xfffffff02a0d0308):
  "IOMFB: Firmware hash checksum mismatched: local=0x15A5C96B, remote=0x00000000"
  @AppleDCPLinkService.cpp:624
```
(`/tmp/dvm/probe/IOMFB6.serial.log:174`, reproduced with `DARWIN_DCP_IOMFB=2`;
stderr confirms only two ep-0x37 messages were ever exchanged before this —
the AP's announce and our one ack, `/tmp/dvm/probe/IOMFB6.stderr.log:144-147`.
So this check runs as part of processing that same class-1 ack, not a third
message we failed to send.)

**Runtime-to-static address relationship, confirmed exactly**: the reported
`"Kernel text exec slide"` field is *not* the right value to subtract from a
panic's `caller` address for this kext. The right one is the flat
`+0x20000000` this project's own convention already documents for `bootkc`:
`0xfffffff02a0d0308 (runtime, from the panic) − 0xfffffff00a0d0308 (static,
found below) == 0x20000000` exactly, and `0xfffffff00a0d0308` is precisely the
instruction after the `bl` that logs-and-panics (`0xa0d0304`, see below) — an
exact, non-coincidental match, not an approximation.

### Where this check lives, and where `local`/`remote` come from

The whole check is inside `link_handle_message`'s **class-1** branch — the
same branch that calls `fcn.ce0f0` (the ack-advance step from the section
above). It runs unconditionally on every class-1 message, immediately after
the class/subkind dispatch, *before* `fcn.ce0f0` is reached:

```
                                                       // x28 = the raw incoming 64-bit message
x27 = &G                    // adrp x27,0xb880000; add x27,x27,0xc70   @ 0xa0cfaec-0xa0cfaf0
x22 = &V                    // adrp x22,0x798e000; add x22,x22,0x8ca   @ 0xa0cfaf4-0xa0cfaf8
x26 = crc32(0, x27, 4)                                 // @ 0xa0cfb60-0xa0cfb6c
if (strlen(x22) != 0):                                 // @ 0xa0cfb74-0xa0cfb7c
    x26 = crc32(x26, x22, min(strlen(x22), 0x28))       // @ 0xa0cfb8c-0xa0cfb94
x23 = x28 >> 16                                         // @ 0xa0cfb9c  <- "remote"
if ((uint32_t)x26 != (uint32_t)x23):                    // @ 0xa0cfba0, cmp w26, w23 (32-bit!)
    log("IOMFB: Firmware hash checksum mismatched: local=0x%08X, remote=0x%08X\n",
        x26, x23)                                       // @ 0xa0cfbd0, string 0x7992000+0xa13
    if (w20 == 0 || w25 == 0): panic(...)                // @ 0xa0d02e8 -> 0xa0d0304 bl panic-helper
                                                          //   (w20 defaults to 4, set @ 0xa0cfaf0;
                                                          //   this branch is not normally taken)
// falls through either way to phase/flag bookkeeping, then fcn.ce0f0
```

- **`local`** (`x26`) is `crc32(seed=0, data, len)`, standard zlib/IEEE CRC-32
  calling convention (`x0`=seed, `x1`=buf, `x2`=len) — `fcn.fffffff00a0dcf70`
  (file `0x30d8f70`) is an imported `__auth_stubs` trampoline (no name
  resolved by `ipsw`/`r2`, both report 0 symbols for this stripped kext), but
  the call shape and the neighbouring panic string's own name,
  `"AppleDCPLinkService::check_firmware_hash_crc32() 0x%08X\n"` (`0x7992aa8`,
  logged right after this comparison at `0xa0cfbf4`), both say what it is.
  **Verified, not just plausible**: the seed data is 4 bytes read from
  `0xfffffff00b880c70` inside this kext's own `__DATA.__data` (file offset
  `0x487cc70` in `firmware/bootkc`, spot-checked directly: `d3 00 00 00 00 00
  00 00`), and
  ```
  python3 -c "import zlib; print(hex(zlib.crc32(bytes([0xd3,0,0,0]))))"
  -> 0x15a5c96b
  ```
  reproduces the panic's `local=0x15A5C96B` **exactly**, byte for byte, from
  first principles. The second `crc32` call is a no-op in this build: `x22`
  (`0xfffffff00798e8ca`, file `0x98a8ca`) points at an **empty string** (a
  bare `\0`; the real, non-empty string `"%s: property %s not found in
  EDT..."` starts one byte later at `0x798e8cb` — confirmed with `px`/`psz`
  against the live bytes), so `strlen(x22) == 0` and the `cbz x0, 0xa0cfb9c`
  branch (`0xa0cfb7c`) skips the second call entirely.
- **`remote`** (`x23`) is simply `msg >> 16`, **truncated to 32 bits by the
  comparison instruction itself** (`cmp w26, w23` uses the 32-bit registers,
  `0xa0cfba0`) — i.e. it is `(msg >> 16) & 0xffffffff`, equivalently **bits
  [47:16] of the incoming message**, not the full 48-bit "payload" field this
  document earlier described for the class-0 announce. The class-1 wire shape
  therefore further subdivides that region:

  | Bits | Field | Evidence |
  |---|---|---|
  | [15:0] | header (class=1, subkind, tag, ack) — as already documented | unchanged from the base header table |
  | [47:16] | **remote firmware CRC-32**, compared against `local` by 32-bit equality | `lsr x23,x28,0x10` @ `0xa0cfb9c`; `cmp w26,w23` (32-bit) @ `0xa0cfba0` |
  | [63:48] | a small "phase/count" value; `max(this, 4)` is stored at `chan+0x48` | `lsr x25,x28,0x30` @ `0xa0cfbe4`; `cmp w25,4; csel w8,w25,w20,hi; str w8,[x19,0x48]` @ `0xa0cfbf8`-`0xa0cfc00` |

  Our ack `0x0004000000000001` puts its `4` in bits [63:48] (via
  `movk x2,4,lsl 48`, exactly matching the disassembly above), leaving bits
  [47:16] all zero — which is **exactly** why the panic reports
  `remote=0x00000000`: not a missing message, but our own ack literally
  encoding zero in the field this check reads.

### Answering the coordinator's three questions

1. **Where `remote` comes from**: bits [47:16] of the very ack we already
   send (class 1), not a separate message. See table above.
2. **Is echoing `local` sufficient?** Yes, structurally: the comparison is a
   plain 32-bit equality (`cmp w26,w23; b.eq`, `0xa0cfba0`-`0xa0cfba4`) against
   a value computed *before* the incoming message's payload is even read for
   this purpose — `local` has no dependency on anything the DCP model sends,
   ever. It also should not need to be recomputed at runtime by the model:
   the seed byte (`0xd3`) lives in this kernelcache's own `__DATA` and is a
   plain scalar (shape `d3 00 00 00 00 00 00 00`, not a `0xfffffff0...`-shaped
   pointer), which is not what a chained-fixup-rebased slot looks like, so
   `local` should reproduce as `0x15A5C96B` on every boot of this exact
   `firmware/bootkc` regardless of KASLR seed — this inference was not tested
   against a second, independent boot (only nested-panic reprints of the same
   boot were available, `IOMFB6.serial.log:174,227,292,369`, all identical by
   construction). **Practical answer: build the reply as
   `(uint64_t)0x15A5C96B << 16 | header`**, e.g.
   `0x000015A5C96B0001` for a bare ack (class 1, subkind 0, tag 0, ack-bit
   patched by `link_send_message` as before, phase left at the default 4 by
   omitting bits[63:48] — or set bits[63:48] to `4` explicitly, i.e.
   `0x00040000000000` `| (0x15A5C96B << 16) | 1` = `0x000415A5C96B0001`, to
   also satisfy the phase/count field documented above without relying on the
   `w20` default).
3. **What comes after**: assuming the hash matches, the same class-1 handler
   falls through (`0xa0cfc00`-`0xa0cfc1c`) to set `chan+0x5c2 = 1`, then gates
   on two more per-channel flag bytes already used elsewhere in this document
   (`chan+0x5c3` must be set, `chan+0x23c` must be clear — the same offsets
   the class-0/subkind-0 "resend" path and `cdfe0`'s own tail checked) before
   calling **`fcn.ce0f0` again** (`bl` @ `0xa0cfc1c`) — the same ack-advance
   function documented above. Not independently verified here whether those
   two flags are already in the right state by the time this runs (they are
   plausibly set earlier, locally, by `AppleDCPLinkService::start()`'s own
   `link_state_init()`, which runs before any message traffic per the boot
   log ordering already established) — but if they are, `fcn.ce0f0` will, per
   its already-documented body, either send **another outbound class-1
   message** (if `chan+0x260 == 1`) or locally wake a waiter that most likely
   unblocks `IOMobileFramebufferAP::start()`'s stall. **Next experiment**:
   send the corrected class-1 ack above and watch `/tmp/dvm/probe/*.stderr.log`
   for a *second* `AP -> IOP ep 0x37` line — if one appears, its low byte will
   again be `0x01`-shaped (class 1) per `fcn.ce0f0`'s own send call
   (`0x0004000000000001`, `0xa0ce108`-`0xa0ce118`), and answering it the same
   way is the fastest path toward whatever unblocks `AppleCLCD2`.

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

## Addendum 2 (2026-09-02, later): the class-2 RPC layer, and AppleCLCD2 binds

Three things happened in this pass. The first is a bug in our own model, the
second is the protocol layer above the handshake, the third is the payoff.

### 0. The class-1 ack was never actually sent

`a63e509` ("pass the IOMFB firmware hash check; the link handshake completes")
rewrote the ack construction and, in the same hunk, deleted the
`darwin_asc_send(d->asc, ep, ack_msg)` line beside it. The model computed the
corrected ack, logged it, and dropped it on the floor. That is visible in the
very boot the commit cited as evidence:

```
/tmp/dvm/probe/IOMFB7.stderr.log:689  asc(DCP): AP -> IOP ep 0x37 0x0100000000000040
/tmp/dvm/probe/IOMFB7.stderr.log:690  dcp: IOMFB ep 0x37 msg ... class 0 subkind 1 ...
/tmp/dvm/probe/IOMFB7.stderr.log:691  dcp: IOMFB PROBE class-1 init-ack -> 0x000015a5c96b0001
                                      (and no "IOP -> AP" line ever follows)
```

So "0 panics, reaches the shell" was true but for the wrong reason: the model
had silently reverted to `DARWIN_DCP_IOMFB=1` behaviour. **A boot that gets
further is the test; a boot that stops panicking is not.** With the send
restored the hash check genuinely runs and genuinely passes -- the guest now
prints the success-side log, not the mismatch one:

```
/tmp/dvm/iomfb/probe/M1.serial.log:171
  AppleDCPLinkService::check_firmware_hash_crc32() 0x15A5C96B
```

(`0xfffffff007992aa8`, logged at `0xa0cfbf4`, i.e. after the `b.eq` at
`0xa0cfba4` was taken.) And the AP immediately sends a message it had never
sent before.

### 1. Class 2 is a shared-heap RPC, and here is its whole shape

```
asc(DCP): AP -> IOP ep 0x37 0x0000001000000202
```

Decoded with the header table above: class 2, subkind 0, tag 0, ack 0, bit 9
set. The rest is `rpc_caller_gated` (`fcn.fffffff00a0ce46c`; the name is its
own `%s` argument, the string `rpc_caller_gated` at `0xfffffff0079923ee`,
passed to every one of its five error format strings).

**Message split** (`0xa0ce65c`-`0xa0ce688`):

```
mov x24, x28              ; x28 = byte offset into the shared heap
bfi w24, w25, 0x10, 0x10  ; w25 = total size
mov w25, 2                ; class 2
bfi x25, x23, 9, 1        ; bit 9 = a caller flag
orr x2, x25, x24, lsl 16  ; -> bits[31:16] offset, bits[47:32] size
bl  link_send_message
```

**Request layout in the heap**, written at `heap + offset` by
`0xa0ce608`-`0xa0ce654`:

| Offset | Size | Field | Evidence |
|---|---|---|---|
| +0x00 | u32 | method name, a FourCC | `stp w24, w26, [x9]` @ `0xa0ce610`; the same `w24` is unpacked byte-by-byte big-endian into a 4-char string at `0xa0ce4a8`-`0xa0ce4c4` for the trace call at `0xa0ce4d4` |
| +0x04 | u32 | `in_len` | same `stp`, second register |
| +0x08 | u32 | `out_len` | `str w22, [x9, 8]` @ `0xa0ce614` |
| +0x0c | in_len | input | `memcpy(base+off+0xc, x27, w26)` @ `0xa0ce644`-`0xa0ce654` |
| +0x0c+in_len | out_len | output, filled by the IOP | read back at `0xa0ce794`-`0xa0ce7dc` |

and `size == 0xc + in_len + out_len` (`add w11, w26, 0xc; adds w25, w22, w11`
@ `0xa0ce4d8`-`0xa0ce4dc`). Every observed message matches.

**The completion the IOP must send** (`0xa0ce750`-`0xa0ce7dc`):

```
ldrh w9, [x19, 0x48]   ; the reply's low 16 bits, stashed by link_handle_message
and  w9, w9, w23       ; w23 = 0xc3  (mov w23, 0xc3 @ 0xa0ce6bc)
cmp  w9, 0x42          ; class 2 + subkind 1
b.eq <success>
...
ldr  w8, [x26, 0x4a]!  ; status = (u32)(reply >> 16); non-zero -> fail
cbnz w8, <error>
memcpy(caller_out, heap + off + 0xc + in_len, out_len)
```

`0xc3` masks exactly class (`[1:0]`) and subkind (`[7:6]`); `0x42` is class 2,
subkind 1. That is also the one combination `link_send_message` REQUIREs you
not to send directly (`0xa0ced2c`-`0xa0ced38`) -- reply-only, which
corroborates it. `link_handle_message` routes the reply back to the blocked
caller by a key built as `(ack_bit << 32) | tag` (`0xa0cfc44`-`0xa0cfc4c`), so
**the completion must echo the request's tag and ack bit**.

Implemented in `qemu-sptm/hw/arm/darwin_iomfb.c`. Live:

```
iomfb: ep 0x37 RPC #1 'A401' (0x41343031) in 0 out 4 at heap+0x0 size 0x10
iomfb: IOP -> AP ep 0x37 0x0000000000000042  (class-2 completion for 'A401')
```

### 2. The method namespace, and how to enumerate it

The FourCCs are `u32` immediates built by `MOVZ`/`MOVK` pairs in generated
stubs, not strings -- searching `bootkc` for `"A401"` finds nothing. Scanning
for `MOVZ Wd,#lo` followed within six instructions by `MOVK Wd,#hi,LSL 16`
where `hi` decodes to `A0`-`A9`/`D0`-`D9` finds **258** distinct names:
`A000`-`A041`, `A100`-`A137`, `A200`-`A206`, `A350`-`A399`, `A400`-`A492`,
`A500`-`A501`, and the `D`-series the AP dispatches on the way back
(`D130`-`D133`, `D210`-`D211`, `D300`, `D420`-`D424`, `D570`-`D579`,
`D600`-`D603`, `D700`). The scanner is 25 lines and is worth rerunning on any
new kernelcache. `A`-names are AP->DCP calls; the `D` block at
`0xa0d05ac`-`0xa0d0680` is a nested switch inside `link_rpc_lookup` mapping
incoming names to PAC-signed handlers, i.e. the callbacks the AP will accept
from the firmware.

Stub shapes read directly:

- `A401` @ `0xfffffff00a0c8a80`: `in_len 0, out_len 4`, returns `out[0] & 1`
  (`ldurb w8,[x29,-4]; and w0,w8,1` @ `0xa0c8ad4`). A **bool**. Note
  `movi v0.16b, 0xaa; stur s0, [x29,-4]` @ `0xa0c8a90`: the AP poisons its own
  out buffer with `0xaa` before the call, so an IOP that writes nothing hands
  the driver `0xaaaaaaaa`.
- `A465` @ `0xfffffff00a0cb980`: `void A465(u32, u32)`, `in_len 8`, no output.
  Observed input `00 08 00 00 20 00 00 00` = (0x800, 0x20).
- `A353` @ `0xfffffff00917d610`: `u64 A353(void)`, `out_len 8`. Its only caller
  (`bl` @ `0xfffffff0091889f0`) feeds the result to
  `0xfffffff00919e814` and logs `"IOMFB: reported 0x%llx (ns) as Time period
  to exclaves_display_healthcheck_rate"` (`0xfffffff007620b86`) -- a
  healthcheck period in nanoseconds. Returning 0 skips the block
  (`cbz x0` @ `0x91889f4`), which is why zero is a safe answer here.

The same 0xaa poison shows up in *inputs* too: `A500`'s stub
(`0xfffffff00a0d41f0`) writes two bytes and leaves `0xaaaa` behind them, which
is exactly the `00 01 aa aa` our trace printed.

### 3. What A401's return value gates, and AppleCLCD2

`A401`'s vtable slot is `+0x970`; the only call site is `0xfffffff00a0b5fe4`,
inside `IOMobileFramebufferAP::start()`, immediately after the log line our
boot log used to end on:

```
0xa0b5fa4  adrp x0, ...+0x21   ; "%s: fRackDebugSwapWaitTimeoutSec = %d"  (0x798f021)
0xa0b5fac  bl   <log>
0xa0b5fd4  ldr  x9, [x16, 0x970]
0xa0b5fe4  blraa x9, x17       ; <- A401()
0xa0b5fec  tbz  w0, 0, 0xa0b6154
   true :  build an OSSerializer and setProperty("IOMFB Debug Info")  (0x798f047)
   false:  str wzr, [x19, 0x260]   @ 0xa0b6154, then rejoin at 0xa0b58a0
```

(The call site was found by decoding the chained fixup at
`0xfffffff00808c378`, whose low 32 bits rebase to `0xa0c8a80`, taking its
16-bit PAC diversity `0x9516`, and searching for the single
`movk x17, 0x9516, lsl 48` in the image.)

The *meaning* of true/false is not in the disassembly, but the effect is
measured. Four otherwise identical boots, `-enable dcp`, `io=0x1f`,
`/tmp/dvm/iomfb/probe/`:

| tag | ep 0x37 | class-1 ack | class-2 answered | A401 | result |
|---|---|---|---|---|---|
| `C0` | not advertised | – | – | – | no `AppleCLCD2` line at all |
| `C2` | advertised | yes | no | – | `IOMFB: AP DRIVER START!`, nothing further |
| `M3` | advertised | yes | yes, all-zero | `00` | `AppleCLCD2[0x1000003bb]::start took 596 ms`, **no** `Registering:` line; driver then runs `IOMFB_POWER_DART: set_power_state powerState=0` |
| `M4`/`L4` | advertised | yes | yes | `01` | `Registering: ../disp0@0/AppleCLCD2` **and** `AppleCLCD2[0x100000397]::start took 657 ms`; two further RPCs (`A465`, `A353`) |

All four reach the shell with 0 XNU panics and 11/11 AFK endpoints.

`AppleCLCD2`'s personality, from `__PRELINK_INFO` at `0xfffffff00b434400`, is
`IOProviderClass = AppleARMIODevice`, `IONameMatch = [disp0,t8140,
dispext0,t8140]` -- it matches the IODeviceTree nub for `/arm-io/disp0`
(`compatible = disp0,t8140` in our tree), not `IOMobileFramebuffer`. So
nothing in the device tree was missing; the driver was simply never allowed to
finish `start()`.

### 4. Where this leaves the pixel path

The AP has finished bringing the link up and goes quiet after `A353`. It has
not powered the DCP on and has not asked for a framebuffer: on this `rd=md0`
restore-ramdisk boot there is no `IOMobileFramebuffer` client to trigger a
first-client-open, and the `D`-series callbacks (which the firmware would
originate) are not modelled at all. Those two -- a client, and the callback
direction -- are what stands between here and a swap.
