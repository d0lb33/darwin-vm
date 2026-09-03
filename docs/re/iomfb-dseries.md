# The D-series callback direction on DCP endpoint 0x37 (DCP -> AP)

Companion to `docs/re/iomfb-link.md`, which covers the class-0/class-1
handshake and the AP -> DCP (`A`-series) half of the class-2 RPC layer. This
document covers the other half: the requests the **firmware** originates and
the AP answers, which is what the DCP does to drive the rest of display
bring-up on real hardware and which our model did not implement at all.

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/H17P), kernelcache
`firmware/bootkc`. Unslid VAs; `file_offset = VA - 0xfffffff007004000`; the
panic addresses the guest prints are these `+ 0x20000000`. Both DCP kexts are
stripped (`nsyms=0`), so every function name is `fcn.<addr>` identified by
string cross-reference or by call shape.

Two kexts are involved and it matters which:

| kext | `__TEXT_EXEC` | role |
|---|---|---|
| `com.apple.iokit.IOMobileGraphicsFamily-DCP` | `0xfffffff00a0b2080`-`0xfffffff00a0dd120` | the wire protocol (`link_send_message`, `link_handle_message`, `rpc_caller_gated`, `rpc_callee_gated`) and the D350/D400/D450/D550-D6FF/D700 handlers |
| `com.apple.driver.AppleMobileDispH17P-DCP` | `0xfffffff0091792c0`-`0xfffffff00919e9c4` | `AppleDCPLinkServiceSoC`, the top-level `link_rpc_lookup`, and the D000/D100/D200/D300 handlers |

(Ranges read out of each `LC_FILESET_ENTRY`'s own `LC_SEGMENT_64`; the entry
headers are packed low in the file, the code is not.)

## 1. The callee path, end to end

`link_handle_message` (`fcn.fffffff00a0cfac0`) dispatches class 2 on subkind:

```
0xa0cfc28  ubfx w21, w28, #6, #2      ; subkind
0xa0cfc2c  cmp  w21, #1
0xa0cfc30  b.eq 0xa0cfd0c             ; subkind 1 -> a completion for one of OUR requests
0xa0cfc34  cbnz w21, 0xa0d0308        ; subkind 2/3 -> error
0xa0cfc38  cmp  w26, #2               ; class must be exactly 2
0xa0cfc3c  b.ne 0xa0d0310
0xa0cfc40  ...                        ; subkind 0 -> an INBOUND REQUEST
```

Both branches start by looking up an RPC slot with a key built from the
message:

```
0xa0cfc44  lsl    x8, x28, #0x18
0xa0cfc48  and    x8, x8, #0x100000000     ; bit 8 (ack) -> bit 32 of the key
0xa0cfc4c  bfxil  x8, x28, #0xa, #6        ; bits[15:10] (tag) -> bits[5:0]
```

and pass it to the gated lambda `fcn.fffffff00a0d0318`, which is the whole
correlation mechanism in twenty instructions:

```
x10 = chan; w8 = key.tag; w9 = key.ack
w11 = chan[0x70]
x12 = (w9 == w11) ? 0x78 : 0x218          ; 0xa0d0328-0xa0d0334
x11 = chan + x12                           ; base of a 4-entry slot array
if (w8 >= 4) panic                         ; 0xa0d033c, "%s:%d" line 0x254
slot = x11 + w8 * 0x68                     ; 0xa0d0350-0xa0d0360
*found_flag = 0
if (slot[0x60] == 0) {                     ; slot free -> claim it
    slot[0] = tag; slot[4] = ack; slot[0x60] = 1;
    *found_flag = 1;
}
*out = slot
```

So there are **two arrays of four 0x68-byte RPC slots** on the channel, chosen
by whether the message's ack bit equals `chan[0x70]`, and the tag is a
**slot index limited to 0..3**, not a free-running counter.

`chan` is `L + 0x1f0` where `L` is the `AppleDCPLinkService` link-state object
(`link_send_message(chan + 0x1f0, ...)` at `0xa0ce07c`, and `link_state_init`
below writes both). The two arrays therefore live at `L+0x268` and `L+0x408`.

### chan[0x70] is zero, and that fixes the ack bit we must send

`link_state_init` (`fcn.fffffff00a0cf5b8`) does, at `0xa0cf5e8`:

```
str wzr, [x19, #0x260]        ; L+0x260 == chan+0x70 == 0
```

`link_send_message` uses that same byte as bit 8 when no `ctx` is passed
(`counter_ptr = ctx ? ctx+4 : chan+0x70`, `0xa0ced10`), which is why the AP's
own opening announce `0x0100000000000040` has bit 8 clear. Since the lambda
routes `ack == chan[0x70]` to the AP's *own* outgoing array at `chan+0x78`,
**an inbound request from the firmware must set bit 8** or it will collide with
the AP's own outstanding calls. This is a derived fact with two independent
supports (the `str wzr` and the observed message), not a guess.

### The shared heap is one buffer, carved into eight fixed windows

`link_state_init` allocates `0x80000` bytes (`mov w0, #0x80000; bl
0xa0dc2f0` at `0xa0cf5cc`) and hands each slot a 32 KB TX window and a 32 KB
RX window (`0xa0cf654`-`0xa0cf6e0`, both loops four iterations, stride `0x68`):

| slot array | slot+0x08 / +0x10 / +0x14 (TX) | slot+0x18 / +0x20 / +0x24 (RX) |
|---|---|---|
| `chan+0x78` + t*0x68 (AP's own calls) | `heap + t*0x8000`, off `t*0x8000`, len `0x8000` | `heap + 0x60000 + t*0x8000` |
| `chan+0x218` + t*0x68 (**inbound**) | `heap + 0x20000 + t*0x8000` | `heap + 0x40000 + t*0x8000`, off `0x40000+t*0x8000`, len `0x8000` |

and the end of the arena is recorded at `L+0x5a8 = heap + 0x80000`
(`add x8, x8, #0x80, lsl #12`, `0xa0cf6e4`).

`rpc_callee_gated` reads an inbound request from `slot[0x18] + offset`
(`0xa0cf0d0`-`0xa0cf0d4`) and bounds-checks it against `slot[0x24]`
(`0xa0cf054`-`0xa0cf060`). So:

> **The `offset` field of an inbound class-2 message is relative to
> `heap + 0x40000 + tag*0x8000`, not to the start of the heap.**

The same reading fixes a latent bug on the receive side of our model: the AP's
own request for tag `t` lives at `heap + t*0x8000 + offset`
(`ldr x8,[x21]` where `x21 = slot+8`, `0xa0ce608`-`0xa0ce60c`), and we were
reading it at `heap + offset`. That is right only for tag 0, which is the only
tag the AP has ever used so far.

### rpc_callee_gated: what the AP validates, and how it replies

`fcn.fffffff00a0cf020`. `x19` = the slot, `x20` = `chan`:

```
w21 = slot[0x4a]           ; the u32 the receive path stored from msg>>16
w23 = w21 >> 16            ; size          (msg bits[47:32])
x28 = w21 & 0xffff         ; offset        (msg bits[31:16])
w26 = slot[0x48]           ; the 16-bit header
w24 = slot[0x4e]           ; msg bits[63:48]
```

guards, in order, each returning an error rather than panicking:

| check | where | meaning |
|---|---|---|
| `slot[0x24] - offset >= size` | `0xa0cf054`-`0xa0cf060` | request fits the 32 KB window |
| `(header & 3) == 2` | `0xa0cf09c`-`0xa0cf0a4` | class 2 |
| `(header & 0xc0) != 0x40` | `0xa0cf0a8`-`0xa0cf0b0` | **subkind must not be 1** |
| `size >= 0xc` | `0xa0cf0b4`-`0xa0cf0bc` (`w21>>18 > 2`) | room for the request header |
| `slot[0x24] - offset > 0xb` | `0xa0cf0c0`-`0xa0cf0cc` | ditto, against the window |
| `size == 0xc + in_len + out_len` | `0xa0cf0dc`-`0xa0cf0fc` | the two lengths come from the heap, so we control them |

then `name = *(u32*)(slot[0x18] + offset)` (`0xa0cf100`), the name is formatted
big-endian for a trace call (`0xa0cf108`-`0xa0cf134`), and the handler is
looked up through a **virtual method at vtable offset +0x540** of the object at
`chan+0x68`, PAC modifier `0x160b` (`0xa0cf13c`-`0xa0cf168`). That is
`link_rpc_lookup`. A null return takes the `"%s: link_rpc_lookup callback not
found: '%s'"` path (string `0xfffffff007992778`).

The handler ABI, from `0xa0cf1e8`-`0xa0cf1fc`:

```
handler(x0 = chan, x1 = slot, x2 = in, x3 = in_len, x4 = out, x5 = out_len)
```
called `blraa x23, x17` with `x17 = 0x6eb1` — the same PAC modifier every
`link_rpc_lookup` leaf signs its pointer with, which is how the enumeration
below is known to be reading the right table. `in` is
`slot[0x18] + offset + 0xc` (or NULL when `in_len == 0`) and `out` is
`in + in_len` (or NULL when `out_len == 0`).

The completion the AP sends back, `0xa0cf298`-`0xa0cf2b4`:

```
msg = (incoming_header & 0xff3e) | 0x40 | (incoming_bits[63:48] << 48)
link_send_message(chan, slot_as_ctx, msg)
```

`0xff3e` clears bit 0 and bit 6; `| 0x40` sets subkind 1; class stays 2 because
bit 1 survives the mask. Passing the slot as `ctx` makes `link_send_message`
re-derive the tag from `slot[0]` and bit 8 from `slot[4]`, so **the completion
carries our tag and our ack bit**, and bits[47:16] — the status field —
are **zero on success**.

## 2. The dispatch table

`AppleDCPLinkServiceSoC::link_rpc_lookup` is at `0xfffffff00917d328`
(`bti c; mov x0, x1` then a switch on `w2`). It routes by hundreds block; the
five blocks that live in the other kext go through `__auth_got` thunks whose
chained-fixup words decode as shown:

| names | dispatcher | via |
|---|---|---|
| D000-D0FF | `0xfffffff00917a604` | direct `b` at `0x917d34c` |
| D100-D1FF | `0xfffffff009199c40` | `0x917d364` |
| D200-D2FF | `0xfffffff00919ce2c` | `0x917d378` |
| D300-D34F | `0xfffffff0091827bc` | `0x917d398` |
| D350-D3FF | `0xfffffff00a0b2408` | thunk `0x919dd24`, got `0x808f7b8` |
| D400-D44F | `0xfffffff00a0d0538` | thunk `0x919dd34`, got `0x808f7c0` |
| D450-D4FF | `0xfffffff00a0d3068` | thunk `0x919dd14`, got `0x808f7b0` |
| D550-D6FF | `0xfffffff00a0d6cb0` | thunk `0x919dd54`, got `0x808f7d0` |
| D700-D74F | `0xfffffff00a0b2414` | thunk `0x919dd44`, got `0x808f7c8` |
| anything else | `0xfffffff00917f454` | not-found tail |

Note there is **no D500-D54F block**: those names fall straight through to the
not-found tail.

`tools/re/dcp_dtable.py` emulates that whole chain for all 1000 candidate
names and prints the handler each one resolves to, plus the handler's own
argument guards (`cmp w3,#N` for `in_len`, `cmp w5,#M` for `out_len`, both
returning `0xe00002c2` = `kIOReturnBadArgument`). Re-run it on a new
kernelcache rather than trusting this table. A `?` means no guard was found in
the first 48 instructions before the first call — usually a handler that takes
no buffer on that side, but sometimes one whose check is further in.

```
D000  handler 0xfffffff00917a634  in ? out 0x4
D001  handler 0xfffffff00917a688  in ? out 0x4
D002  handler 0xfffffff00917a6dc  in ? out ?
D003  handler 0xfffffff00917a700  in 0x4 out 0x14
D004  handler 0xfffffff00917a7e4  in 0x8 out ?
D005  handler 0xfffffff00917a8b4  in 0x8 out ?
D006  handler 0xfffffff00917a984  in 0x54 out 0x50
D007  handler 0xfffffff00917aa88  in 0x4 out ?
D100  handler 0xfffffff00919a0f0  in ? out ?
D101  handler 0xfffffff00919a15c  in ? out 0x4
D102  handler 0xfffffff00919a1d8  in 0x44 out ?
D103  handler 0xfffffff00919a2f4  in ? out ?
D104  handler 0xfffffff00919a360  in 0x44 out ?
D105  handler 0xfffffff00919a4a0  in 0x80 out ?
D106  handler 0xfffffff00919a5e8  in 0x40 out ?
D107  handler 0xfffffff00919a6ec  in ? out 0x4
D108  handler 0xfffffff00919a780  in ? out 0x4
D109  handler 0xfffffff00919a814  in ? out 0x4
D110  handler 0xfffffff00919a8a8  in ? out 0x4
D111  handler 0xfffffff00919a93c  in ? out 0x4
D112  handler 0xfffffff00919a9d0  in ? out 0x4
D113  handler 0xfffffff00919aa64  in ? out ?
D114  handler 0xfffffff00919ac7c  in 0x10 out ?
D115  handler 0xfffffff00919ad9c  in 0x10 out ?
D116  handler 0xfffffff00919aeb0  in 0xc out ?
D117  handler 0xfffffff00919afa0  in 0x14 out ?
D118  handler 0xfffffff00919b0b4  in ? out ?
D119  handler 0xfffffff00919b234  in 0x4 out ?
D120  handler 0xfffffff00919b338  in ? out 0x4
D121  handler 0xfffffff00919b3cc  in ? out 0x4
D122  handler 0xfffffff00919b460  in ? out 0x4
D123  handler 0xfffffff00919b4f4  in ? out 0x4
D124  handler 0xfffffff00919b588  in 0x64 out ?
D125  handler 0xfffffff00919b718  in 0x48 out ?
D126  handler 0xfffffff00919b8fc  in 0x4 out ?
D127  handler 0xfffffff00919ba00  in ? out ?
D128  handler 0xfffffff00919bb80  in 0x40 out ?
D129  handler 0xfffffff00919bc90  in 0x1c out ?
D130  handler 0xfffffff00919bddc  in 0x8 out ?
D131  handler 0xfffffff00919bec8  in 0x10 out ?
D132  handler 0xfffffff00919c008  in 0x4 out ?
D133  handler 0xfffffff00919c104  in ? out 0x4
D200  handler 0xfffffff00919cfd0  in ? out 0x4
D201  handler 0xfffffff00919d044  in 0xc out ?
D202  handler 0xfffffff00919d204  in 0x18 out ?
D203  handler 0xfffffff00919d388  in ? out 0x4
D204  handler 0xfffffff00919d3dc  in ? out 0x4
D205  handler 0xfffffff00919d430  in 0xc out ?
D206  handler 0xfffffff00919d558  in ? out 0x4
D207  handler 0xfffffff00919d5ac  in ? out 0x4
D208  handler 0xfffffff00919d600  in 0x4 out ?
D209  handler 0xfffffff00919d6a8  in ? out 0x8
D210  handler 0xfffffff00919d6fc  in 0x4 out 0x51c
D211  handler 0xfffffff00919d7e0  in 0x4 out 0x14
D300  handler 0xfffffff0091827e4  in 0x10 out ?
D400  handler 0xfffffff00a0d08c0  in 0x4c out 0xc04
D401  handler 0xfffffff00a0d0ac0  in 0x50 out 0xc04
D402  handler 0xfffffff00a0d0c08  in 0x4c out 0xc04
D403  handler 0xfffffff00a0d0d1c  in 0x4c out 0xc04
D404  handler 0xfffffff00a0d0e6c  in 0x48 out 0xc04
D405  handler 0xfffffff00a0d0f78  in 0x4c out 0xc04
D406  handler 0xfffffff00a0d10c8  in 0x48 out 0xc04
D407  handler 0xfffffff00a0d11d4  in 0x48 out 0xc04
D408  handler 0xfffffff00a0d12e4  in 0x8 out ?
D409  handler 0xfffffff00a0d13fc  in 0xc out ?
D410  handler 0xfffffff00a0d1528  in 0x14 out ?
D411  handler 0xfffffff00a0d1670  in 0x10 out ?
D412  handler 0xfffffff00a0d17c0  in 0x448 out ?
D413  handler 0xfffffff00a0d19a8  in ? out ?
D414  handler 0xfffffff00a0d1bb0  in 0x50 out ?
D415  handler 0xfffffff00a0d1d68  in 0x4c out ?
D416  handler 0xfffffff00a0d1f0c  in 0x84 out ?
D417  handler 0xfffffff00a0d20c0  in 0x448 out ?
D418  handler 0xfffffff00a0d227c  in ? out ?
D419  handler 0xfffffff00a0d2458  in 0x50 out ?
D420  handler 0xfffffff00a0d25dc  in 0x4c out ?
D421  handler 0xfffffff00a0d2744  in 0x84 out ?
D422  handler 0xfffffff00a0d28c4  in 0x48 out ?
D423  handler 0xfffffff00a0d29fc  in 0x44 out ?
D424  handler 0xfffffff00a0d2b58  in 0x44 out ?
D450  handler 0xfffffff00a0d3098  in 0x8 out ?
D451  handler 0xfffffff00a0d31d0  in 0x14 out ?
D452  handler 0xfffffff00a0d3318  in 0x8 out ?
D453  handler 0xfffffff00a0d3434  in 0x8 out ?
D454  handler 0xfffffff00a0d3550  in 0x4 out ?
D550  handler 0xfffffff00a0d7440  in 0x444 out ?
D551  handler 0xfffffff00a0d7638  in ? out ?
D552  handler 0xfffffff00a0d786c  in ? out ?
D553  handler 0xfffffff00a0d7a84  in ? out ?
D554  handler 0xfffffff00a0d7cb8  in 0x4c out ?
D555  handler 0xfffffff00a0d7e80  in 0x4c out ?
D556  handler 0xfffffff00a0d8048  in 0x48 out ?
D557  handler 0xfffffff00a0d81f4  in 0x80 out ?
D558  handler 0xfffffff00a0d83a8  in 0x80 out ?
D559  handler 0xfffffff00a0d855c  in 0x444 out ?
D560  handler 0xfffffff00a0d8728  in ? out ?
D561  handler 0xfffffff00a0d8930  in ? out ?
D562  handler 0xfffffff00a0d8b1c  in ? out ?
D563  handler 0xfffffff00a0d8d24  in 0x4c out ?
D564  handler 0xfffffff00a0d8ec0  in 0x4c out ?
D565  handler 0xfffffff00a0d905c  in 0x48 out ?
D566  handler 0xfffffff00a0d91d4  in 0x80 out ?
D567  handler 0xfffffff00a0d935c  in 0x80 out ?
D568  handler 0xfffffff00a0d94e4  in 0x80 out ?
D569  handler 0xfffffff00a0d9638  in 0x44 out ?
D570  handler 0xfffffff00a0d9784  in ? out ?
D571  handler 0xfffffff00a0d996c  in 0x8 out ?
D572  handler 0xfffffff00a0d9aa4  in 0x4 out ?
D573  handler 0xfffffff00a0d9bcc  in 0x4 out ?
D574  handler 0xfffffff00a0d9d0c  in 0x4 out ?
D575  handler 0xfffffff00a0d9e4c  in 0x58 out ?
D576  handler 0xfffffff00a0d9fcc  in 0x4 out ?
D578  handler 0xfffffff00a0da0e4  in ? out ?
D579  handler 0xfffffff00a0da180  in 0x4 out ?
D580  handler 0xfffffff00a0da2b4  in ? out ?
D581  handler 0xfffffff00a0da350  in ? out ?
D582  handler 0xfffffff00a0da3ec  in 0xc out ?
D583  handler 0xfffffff00a0da4f8  in 0x4 out ?
D584  handler 0xfffffff00a0da634  in ? out ?
D585  handler 0xfffffff00a0da6d0  in 0xc out ?
D586  handler 0xfffffff00a0da804  in 0x8 out ?
D587  handler 0xfffffff00a0da91c  in ? out ?
D588  handler 0xfffffff00a0da99c  in 0x18 out ?
D589  handler 0xfffffff00a0daa9c  in 0xe4 out ?
D590  handler 0xfffffff00a0dac24  in 0x14 out ?
D591  handler 0xfffffff00a0dad28  in 0x4 out ?
D592  handler 0xfffffff00a0dae7c  in ? out ?
D593  handler 0xfffffff00a0daf18  in 0x8 out ?
D594  handler 0xfffffff00a0db034  in 0x730 out ?
D595  handler 0xfffffff00a0db1a8  in ? out ?
D596  handler 0xfffffff00a0db3b4  in 0x14 out ?
D597  handler 0xfffffff00a0db4c0  in 0x4 out ?
D598  handler 0xfffffff00a0db5fc  in 0x4 out ?
D599  handler 0xfffffff00a0db720  in 0x4 out ?
D600  handler 0xfffffff00a0db844  in 0x4 out ?
D601  handler 0xfffffff00a0db968  in ? out 0x4
D602  handler 0xfffffff00a0dba10  in ? out 0x4
D603  handler 0xfffffff00a0dbaac  in ? out ?
D700  handler 0xfffffff00a0b243c  in 0x4 out ?
# 139 callbacks accepted by link_rpc_lookup
```

`D577` is absent — the D570-D579 switch has no case for it (`0xa0d6ee0`
jumps straight from D576 to D578), which is a good sanity check that the
emulation is following the real control flow rather than assuming a dense
range.

Nothing above is a *semantic* claim. These are the names the AP will accept
and the buffer sizes its handlers demand; what any of them means is not in the
disassembly, and this file deliberately does not guess. The Asahi `iomfb.c`
callback list is a tempting cross-reference (it formats the same `D%03u`
names) but its numbering is from M1-era firmware and has already been shown
wrong for the `A` side of this build, so it is not used here.

## 3. What the model implements

`qemu-sptm/hw/arm/darwin_iomfb.c`, all behind `DARWIN_DCP_IOMFB` as before:

- the **transport** for the callee direction: build a request in the inbound
  window, send class 2 / subkind 0 with bit 8 set, correlate the AP's class-2 /
  subkind-1 completion by tag, log the status and the bytes the AP wrote back.
  Every field of that is sourced above.
- an **experiment harness**, `DARWIN_DCP_IOMFB_CB`, which issues a scripted
  list of callbacks once the AP goes quiet. It is not a model of anything: it
  exists so a boot can answer "does the AP accept this name, and what does it
  do next", which is the only way the next gate gets named.

It does *not* implement any callback's semantics, and it does not send any
callback by default.

## 4. Measured: callback transport and nested AP work

Five boots, `-enable dcp`, `DARWIN_DCP_IOMFB=4`, differing only in what the
model sends. Logs in `/tmp/dvm/iomfb2/probe/`. All reach a shell with 0 panics
and 11/11 AFK endpoints.

| tag | what we sent | what the AP did |
|---|---|---|
| `R5` | nothing (`DARWIN_DCP_IOMFB` unset) | 0 `iomfb:` lines, 0 `ep 0x37` lines |
| `L4` | level 4, no `_CB` script | 0 callbacks sent; the A-series flow is unchanged |
| `CB3` | `D000`, bit 9 clear | answers `0x...0102`, nothing further |
| `CB5` | `D000`, bit 9 set | answers `0x...0102`; **request window read back unchanged** |
| `CB6` | `D000`, then one class-2/subkind-1 on the same slot | `0x...0342`, status 0, output `01 00 00 00` |

The `CB6` exchange in full:

```
iomfb: ep 0x37 callback #1 'D000' (0x44303030) in 0 out 4 -> heap+0x40000 (tag 0) size 0x10
iomfb: IOP -> AP ep 0x37 0x0000001000000302  (class-2 callback 'D000')
iomfb: AP -> IOP ep 0x37 0x0000001000000102 | class 2 subkind 0 ack 1 flag9 0 tag 0
iomfb:   cb window after +0000: 30 30 30 44 00 00 00 00 04 00 00 00 00 00 00 00
iomfb: IOP -> AP ep 0x37 0x0000000000000142  (PROBE kick: class-2 subkind-1 on our own slot)
iomfb: AP -> IOP ep 0x37 0x0000000000000342 | class 2 subkind 1 ack 1 flag9 1 tag 0 payload 0x0
iomfb:   callback 'D000' completed, status 0x0, out: 01000000
```

### Why this is the guest doing the work, not us seeing what we hoped

Two independent checks, because "no failure appeared" is not evidence:

1. **The completion is arithmetically derived from our request.**
   `rpc_callee_gated` builds its reply as `(stored_header & 0xff3e) | 0x40`
   (`0xa0cf298`-`0xa0cf2a8`). Our request header was `0x302`, and
   `(0x302 & 0xff3e) | 0x40 == 0x342` — exactly what came back. Note the
   message that *triggered* the reply carried bit 9 **clear** (`0x142`), so
   `0x342` cannot be an echo of it; it can only have been rebuilt from the
   header the AP stored when it took our request. The reply is the handler's.
2. **The output byte is one we never wrote.** We zero the whole request
   window before sending, and the `cb window after` dump in `CB5` proves the
   AP had not touched it. In `CB6` the same region reads `01 00 00 00`, which
   is what `D000`'s handler at `0xfffffff00917a634` computes and stores with
   `strb w8, [x19]` (`0xfffffff00917a674`).

Read together, `CB5` and `CB6` also rule out the lazy reading of `CB5`: the AP
had received and acknowledged the request there too, and still ran nothing.

### The named next gate

An inbound class-2 request is **not** dispatched on arrival. The AP answers
with `class 2 / subkind 0 / ack 1 / bit 9 cleared`, carrying our own offset and
size back, and then stops. `rpc_callee_gated` only runs after a further
class-2 / **subkind 1** message on the same slot.

The old `CB6` interpretation was wrong. Its apparent `PROBE kick` was a
synthetic class-2/subkind-1 message generated by the harness, not evidence of
a firmware “reflection” or third leg. The immediate-kick experiment is
negative: QEMU commit `a7045ca` caused the guest's first panic to be
`rpc_callee_gated: message is not ok: 0x142` at `link_protocol_priv.h:921`.
The synthetic kick must not be used as a model or recommended fix.

The corrected positive control sends `D120` after `A353` and lets the AP drive
the nested transaction. In `/tmp/dvm/probe/IOMFB_NESTED_D120_FIX1.stderr.log:111-176`,
the callback-context AP requests are `A389`, `A454`, `A103`, `A453`, `A104`,
`A105`, `A024`, `A478`, `A032`, and `A000`; the model answers each, then the
AP sends the genuine `D120` completion with status `0` and output
`00000000`. The `D120` handler is at `0xfffffff00919b338`; zero is expected
for this zeroed probe, not a failure. The relevant addresses are `boot` at
`0xfffffff009188040`, the first `A389` wrapper at `0xfffffff00917f11c`, and
the link geometry at `0xfffffff00a0cf654-0x6e`.

The run reaches `Early boot complete` at
`/tmp/dvm/probe/IOMFB_NESTED_D120_FIX1.serial.log:267`, reaches the shell, and
has no `panic(` line. This proves nested callback transport and the real
handler/completion path; it does not prove `default_fb_gated` or scanout.

Historically, the synthetic second message took
`link_handle_message` to `0xa0cfd0c`, whose slot lookup finds the slot *in
use* (we claimed it with the request), so it falls to `0xa0cfda0` and the
lambda at `0xa0d03f8`, which stores the word at `slot+0x48` and then calls
`[slot+0x58]`'s `vtable+0x108` — a wake. Two readings fit equally well:

- **a wake we are tripping by accident**, and the real dispatch trigger is
  something else entirely that we have not found; or
- **the protocol's third leg**, i.e. inbound requests are a three-message
  handshake (request, receipt, confirm) and a real DCP sends the third too.

The corrected D120 run is the transport-positive control; it supersedes the
synthetic-kick recommendation.

## 5. Historical sweep: 23 zero-input callbacks

Probe `SW1`, one boot, `DARWIN_DCP_IOMFB=4 DARWIN_DCP_IOMFB_CB_KICK=1` and a
23-entry script of every enumerated name whose handler declares no input
guard. 312 serial lines, 0 panics, shell reached, 11/11 AFK endpoints.

**All 23 completed with status 0**, and each returned a different value, which
is the strongest available evidence that the dispatch is real and per-name
rather than a fixed reply we are misreading:

| returns 1 | returns 0 | other |
|---|---|---|
| D000 D001 D107 D108 D110 D206 D207 | D101 D109 D111 D112 D120 D121 D122 D123 D133 D200 D203 D601 D602 | D204 -> `02`, D209 -> `0616000000000000` (0x1606) |

D002 has no output and completed cleanly. No name was rejected, so
`link_rpc_lookup` resolving these 23 is confirmed live, not only by emulation.

This is historical harness evidence only; the synthetic kick is not a model.
**The negative result still matters.** After all 23, the
AP issued no further A-series RPC: the boot still shows only `A401`, `A465`,
`A353`, exactly as it does with no callbacks at all. So none of the
zero-input getters is the event that resumes the pixel path. Whatever restarts
the AP is either one of the callbacks that carries a real input struct (the
`0x44`-`0x730`-byte ones, which we cannot fill in without deriving their
layout) or is not a callback at all.

That bounds the next search usefully: it is no longer worth sweeping the
getters, and the `in`-carrying names are where the remaining signal is.
