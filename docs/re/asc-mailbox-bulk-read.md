# ASC mailbox bulk read — how XNU drains the receive FIFO

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (iPhone 16, t8140/H17P),
kernelcache `firmware/bootkc`, kexts `com.apple.driver.AppleA7IOP-ASCWrap-v6`
and `com.apple.driver.AppleA7IOP` extracted to `/tmp/dvm/kexts/` with
`ipsw kernel extract firmware/bootkc <bundle-id> --imports -o /tmp/dvm/kexts`.
Disassembled with `r2 -q -e bin.cache=true -A`. Cross-referenced against the
merged kernelcache directly (`firmware/bootkc`, a `MH_FILESET` Mach-O) to
resolve `__auth_got` stubs into `com.apple.driver.AppleA7IOP`'s code, since
the isolated kext extraction carries no `LC_DYLD_CHAINED_FIXUPS` and its
`__auth_got` entries decode as unresolved `dyld_chained_ptr_arm64e_auth_kernel`
records (`target` = offset from the fileset's `__TEXT` base
`0xfffffff007004000`, confirmed by hand-decoding the bit layout — see
"Resolving imports" below). Extracted and disassembled 2026-09-01.

## Summary

`AppleASCWrapV6::getMailboxBulk(void *mailboxItem, unsigned int *mailboxItemCount)`
(real entry `0xfffffff008538758`) drains the IOP→AP receive FIFO in one call by
looping a raw two-register MMIO pop (`OUTBOX0_RECV0`/`OUTBOX0_RECV1`, our
`I2A_RECV0`/`I2A_RECV1` at +0x830/+0x838) up to the caller-requested capacity.
It reads the status register (`OUTBOX0_CTRL`, our `I2A_CTRL` at +0x114) **once**,
before the loop, purely to gate entry (`FIFOCNT != 0`); it never re-reads it
inside the loop. Instead, the loop's stop condition is driven entirely by the
**upper 32 bits of the 64-bit `I2A_RECV1` register**, which the driver expects
to carry a live mirror of `I2A_CTRL`'s `FIFOCNT` field (same bit position,
[23:20]) — the driver stops as soon as that mirrored field reads exactly 1
(`0x00100000`), meaning "this was the last queued message." Our model's
`I2A_RECV1` currently returns only the endpoint in the low 32 bits and zero in
the high 32 bits, so that stop condition never fires, and the driver keeps
popping (getting zeros) until it exhausts whatever capacity its caller asked
for — this is exactly the "invalid management message 0" bug. The fix is to
OR `(count << 52)` (bits [55:52], i.e. `FIFOCNT` shifted into the top half)
into every `I2A_RECV1` read, using the FIFO occupancy *as of that read,
including the entry just returned*.

## Register map, with evidence

Base of the mailbox block is `+0x8000` inside the ASC wrapper's MMIO
aperture, matching our model's `s->mbox_off` (comment in `darwin_asc.c`
already says `+0x8000`, `+0x110`/`+0x114`/`+0x800..0x838` relative to it).
Apple's own driver strings give the canonical names:

| Absolute offset | Model name | Apple's name | Evidence |
|---|---|---|---|
| 0x8000 | — (unused by model) | `IDLE_STATUS` | format string `"IDLE_STATUS: 0x%08x\nINBOX0_CTRL: 0x%08x\nOUTBOX0_CTRL: 0x%08x\n"` at `AppleA7IOP-ASCWrap-v6` cstring `0xfffffff007154f3b`, printed by the diagnostic dump at `0xfffffff008538a54` (reads 0x8000 at `0xfffffff008538aa0`, w1=0x8000 set at `0xfffffff008538aa0`) |
| 0x8110 | `A2I_CTRL` | `INBOX0_CTRL` | same format string; read with `w1=0x8110` at `0xfffffff008538ad0`; also read by the enable-getter at `0xfffffff008538908` (`mov w1,0x8110` @ `0xfffffff00853892c`) and the empty-getter at `0xfffffff008538944` (`mov w1,0x8110` @ `0xfffffff00853896c`) |
| 0x8114 | `I2A_CTRL` | `OUTBOX0_CTRL` | same format string; read with `w1=0x8114` at `0xfffffff008538b10`; also the register `getMailboxBulk` reads at entry, `mov w1,0x8114` @ `0xfffffff0085387f0` |
| 0x8800 | `A2I_SEND0` | (send FIFO, low) | `mov w11,0x8800` @ `0xfffffff0085388f4`, base of an `stp x8,x9,[base+0x8800]` — see "Send path" below |
| 0x8808 | `A2I_SEND1` | (send FIFO, high) | same `stp`, second word (`[base+0x8808]`) |
| 0x8830 | `I2A_RECV0` | (recv FIFO, low) | `mov w9,0x8830` @ `0xfffffff0085388d4`, base of an `ldp x8,x9,[base+0x8830]` — see "Pop path" below |
| 0x8838 | `I2A_RECV1` | (recv FIFO, high) | same `ldp`, second word (`[base+0x8838]`) |

`INBOX0`/`OUTBOX0` naming is IOP-centric: INBOX = messages going *into* the
IOP (AP→IOP, our `A2I`), OUTBOX = messages coming *out of* the IOP (IOP→AP,
our `I2A`). This matches our model's naming 1:1.

### `A2I_CTRL` / `I2A_CTRL` (INBOX0_CTRL / OUTBOX0_CTRL) bit layout, confirmed from code

| Bits | Meaning | Evidence |
|---|---|---|
| [23:20] | `FIFOCNT`, queue depth | `and w8, w22, 0xf00000` @ `0xfffffff008538810` in `getMailboxBulk`, masking the value just read from `I2A_CTRL` (0x8114); same mask reused inside the pop loop at `0xfffffff008538870` against the `I2A_RECV1` upper-half mirror (see below) |
| [19:18] | `OVERFLOW`/`UNDERFLOW` (2 bits) | mask `0xc0000` @ `0xfffffff008538808` (`mov w2, 0xc0000`), passed to the shared "check-and-log" helper (import stub `fcn.fffffff00853963c`, resolves in the merged kernelcache to `0xfffffff00853be40` in `AppleA7IOP.kext`). That function does `tst w2,w1` (ctrl & mask), and on nonzero logs `"%s Overflow/Underflow: %08x"` (format string `AppleA7IOP.cpp` cstring `0xfffffff007155f1a`) with `%s` = `"Outbox"` (cstring `0xfffffff007155f3f`, used from the `I2A_CTRL`/0x8114 call site) or `"Inbox"` (cstring `0xfffffff007155f46`, used from the `A2I_CTRL`/0x8110 call site at `0xfffffff008538bd8`). This call is diagnostic only — no MMIO write, only a software flag byte at `this+0x134` and a log call |
| [17] | `EMPTY` | `ubfx w0, w0, 0x11, 1` (bit 17) @ `0xfffffff008538978`, reading `A2I_CTRL`/INBOX0_CTRL (0x8110); the symmetric `I2A_CTRL`/OUTBOX0_CTRL empty-getter is the same shape at `0xfffffff008538bdc` (`ubfx w0, w20, 0x11, 1`) |
| [16] | `FULL` | `ubfx w0, w20, 0x10, 1` (bit 16) @ `0xfffffff0085389d4`, reading `A2I_CTRL`/INBOX0_CTRL (0x8110) |
| [0] | `ENABLE` | read-modify-write of only bit 0, preserving the rest: `and w9, w0, 0xfffffffe; orr w9, w9, w19; str w9, [x8]` @ `0xfffffff008538a34`-`0xfffffff008538a3c`, on `I2A_CTRL`/OUTBOX0_CTRL (0x8114, address computed at `0xfffffff008538a2c`-`0xfffffff008538a30` from `w21=0x8114` added to the mapped base at `this+0x100`) |

This confirms bits [23:20]/[17]/[16]/[0] exactly as m1n1's `R_MBOX_CTRL`
layout says (our model's existing comment), and additionally nails down
[19:18] as two log-only overflow/underflow bits with **no observed control-flow
dependency** anywhere in `getMailboxBulk`. `EMPTY` (bit 17) and `FULL` (bit 16)
are **not read anywhere in the `getMailboxBulk` path** — only `FIFOCNT`
[23:20] gates it. `RPTR`/`WPTR` (m1n1 bits [15:12]/[11:8]) are never read by
this driver in any function found in either kext; nothing in the observed
code path needs them to be meaningful.

## `AppleASCWrapV6::getMailboxBulk` walkthrough

Real function body: `0xfffffff008538758` – `0xfffffff0085388e4` (plus the two
`REQUIRE` failure branches at `0xfffffff0085388c4`/`0xfffffff0085388c8`).
**r2 caveat**: `pdf @ fcn.fffffff008538688` merges this function with an
unrelated preceding one (a 128-register dump helper reached only from a
bounds-check panic) because that helper's last live instruction is a call to
a noreturn trap (`0xfffffff008538754`); the linear disassembly falls straight
into `getMailboxBulk`'s real `pacibsp` prologue at `0xfffffff008538758` with
no function boundary in between. Re-running the same `pdf` will reproduce
this; treat `0xfffffff008538758` as the real start.

Signature confirmed by two inlined `REQUIRE`-style assertions whose failure
handlers reference the mangled method name directly:

* `0xfffffff007154eac`: `"virtual IOReturn AppleASCWrapV6::getMailboxBulk(void *, unsigned int *)"`
* assert bodies at `0xfffffff008539578` (line 405, condition string
  `"mailboxItem != nullptr"` @ `0xfffffff007154ef4`) and `0xfffffff008539544`
  (line 406, `"mailboxItemCount != nullptr"` @ `0xfffffff007154f0b`), both in
  `AppleASCWrapCommon.cpp` (filename string @ `0xfffffff007154dca`)

Control flow (`this=x0`→`x20`, `mailboxItem=x1`→`x21`, `mailboxItemCount=x2`→`x19`):

1. `0xfffffff008538774`/`0xfffffff00853877c`: null-check `x1`/`x2`, branch to
   the `REQUIRE` failures above if either is null.
2. `0xfffffff008538788`: `w24 = *mailboxItemCount` — the caller's requested
   capacity, read once at entry.
3. `0xfffffff00853878c`: `bl` an import stub (`fcn.fffffff0085396ec`) that
   resolves (via the merged kernelcache `__auth_got`, see below) to
   `0xfffffff00853b83c` in `AppleA7IOP.kext`. This is a **software readiness
   gate**, not a hardware access: it reads three flag bytes on the object —
   `this+0x121` bit 0, `this+0x108` bit 0, `this+0x134` bit 0 — and only
   returns 0 (proceed) when `+0x121`=0 AND `+0x108`=1 AND `+0x134`=0;
   otherwise it returns an `IOReturn` (`0xE00002D7` by default, or
   `0xE00002C7` if `+0x121` bit 0 is set) and `getMailboxBulk` short-circuits
   at `0xfffffff008538790` with `*mailboxItemCount=0`, touching no hardware
   at all. `+0x134` is the same byte the overflow/underflow logger (above)
   sets to 1 — meaning a latched overflow event on either direction can wedge
   this gate closed until something clears it. Names of the two other flags
   were not recoverable (no matching cstring); **unverified** what
   `+0x121`/`+0x108` specifically track (plausibly "stopped"/"started").
4. `0xfffffff0085387a0`-`0xfffffff0085387cc`: a virtual call through
   vtable slot `+0x620` (`this` only, return value discarded) — shape and
   position are consistent with a lock/critical-section entry, but its
   target wasn't resolved (see "Open questions").
5. `0xfffffff0085387d0`-`0xfffffff0085387f8`: virtual call through vtable
   slot `+0x668` with `w1=0x8114` — this is the generic "read register
   32-bit at offset" accessor (same slot is used with `w1=0x8110` elsewhere,
   and with a sweep of offsets 0x8180..0x837c in the unrelated
   128-register dump function). Result → `x22` = `I2A_CTRL` value.
6. `0xfffffff008538800`-`0xfffffff00853880c`: `bl` an import stub
   (`fcn.fffffff00853963c` → resolves to `0xfffffff00853be40` in
   `AppleA7IOP.kext`) with `(this, ctrlValue, mask=0xc0000)` — the
   overflow/underflow log helper described above. **Return value is not
   used** to decide anything in `getMailboxBulk`; execution proceeds
   unconditionally.
7. `0xfffffff008538810`-`0xfffffff00853881c`: `w8 = ctrlValue & 0xf00000`
   (`FIFOCNT`); combined with `w24 != 0` (capacity) via `ccmp`.
8. `0xfffffff008538820`: `b.ne` to the pop loop (`0xfffffff008538830`) only
   if **both** `FIFOCNT != 0` and requested capacity `!= 0`. Otherwise
   (`0xfffffff008538824`-`0xfffffff00853882c`): set the return `IOReturn` to
   `0xE0002D8` and skip straight to the unlock+epilogue — **no MMIO read of
   `I2A_RECV0`/`I2A_RECV1` happens at all when the FIFO looks empty at this
   one gate check.**
9. Pop loop, `0xfffffff008538830`-`0xfffffff00853887c`:
   * `0xfffffff008538830`: `item[i] = mailboxItem + i*16` — **16-byte stride
     per item**.
   * `0xfffffff008538834`-`0xfffffff008538854`: virtual call through vtable
     slot `+0x658` with `(this, &item[i])`. This slot's implementation is
     adjacent in the same kext, `0xfffffff0085388cc`-`0xfffffff0085388e4`
     (see "Pop path" below) — the actual two-register MMIO pop.
   * `0xfffffff008538858`: `w26 = item[i]+0xc` — reads back the **upper 32
     bits of the `I2A_RECV1` value the pop just stored** (see byte layout
     below).
   * `0xfffffff00853885c`-`0xfffffff008538864`: `bl fcn.fffffff0085396bc`
     (direct call, not virtual) with `(this+0x90, &item[i])` — post-processes
     the popped item (likely enqueues it into a software queue for the
     endpoint dispatcher / kdebug trace point; resolves via the same
     `__auth_got` mechanism to `0xfffffff00853ea58` in `AppleA7IOP.kext`,
     which itself tail-calls `fcn.fffffff00853e64c` with a fixed tag
     `0x87040010` — not analyzed further, not hardware-relevant).
   * `0xfffffff008538868`: `item[i]+0xc` cleared to 0 (software bookkeeping,
     not a register write).
   * `0xfffffff00853886c`: `index++` (`w25`).
   * `0xfffffff008538870`-`0xfffffff00853887c`: `w8 = w26 & 0xf00000`
     (same `FIFOCNT` mask, but now applied to the **snapshot embedded in
     `I2A_RECV1`**, not a fresh register read); `cmp w8, 0x100000`; `ccmp
     w24, w25, 0, ne`; `b.hi` back to the loop top. **This is the actual
     loop-termination logic**: if the embedded count field is exactly 1,
     the loop always stops (flags forced to "not-hi" regardless of
     remaining capacity); otherwise it continues while `index < capacity`.
10. `0xfffffff008538884`-`0xfffffff0085388a0`: virtual call through vtable
    slot `+0x628` (`this` only, return discarded) — shape/position
    consistent with an unlock, symmetric with step 4's lock.
11. `0xfffffff0085388a4`-`0xfffffff0085388c0`: `*mailboxItemCount = w25`
    (actual count filled); return `w22` (the `IOReturn` from step 5/8, or
    the error set in step 8's empty branch).

### Pop path (vtable slot `+0x658`), `0xfffffff0085388cc`-`0xfffffff0085388e4`

```
0xfffffff0085388cc  bti c
0xfffffff0085388d0  ldr x8, [x0, 0x100]     ; x8 = mapped MMIO base (object field +0x100)
0xfffffff0085388d4  mov w9, 0x8830
0xfffffff0085388d8  add x8, x8, x9          ; x8 = base + 0x8830  (I2A_RECV0)
0xfffffff0085388dc  ldp x8, x9, [x8]        ; x8 = *0x8830 (RECV0), x9 = *0x8838 (RECV1)
0xfffffff0085388e0  stp x8, x9, [x1]        ; item[0:8)=RECV0, item[8:16)=RECV1
0xfffffff0085388e4  ret
```

`ldp`/`stp` on Device memory execute as two ordered single-register accesses
(low address first). This is the **only** place `I2A_RECV0`/`I2A_RECV1`
(0x8830/0x8838) are referenced anywhere in `AppleA7IOP-ASCWrap-v6`,
`AppleA7IOP`, or `RTBuddy` (checked with `grep -c '0x8830\|0x8838'` across
all three disassembled kexts — one hit each, both inside this one function).
RECV0 is never read without an immediately following RECV1 read, and vice
versa, anywhere in this driver stack.

Item byte layout (16 bytes, little-endian):

| Item offset | Contents | Evidence |
|---|---|---|
| +0x0..0x7 | `I2A_RECV0` raw 64-bit message payload, unmodified | `stp x8,x9,[x1]`, `x8` = first `ldp` target = `[base+0x8830]` |
| +0x8..0xb | `I2A_RECV1` low 32 bits — endpoint id | consistent with the observed trace (`read 0x08838 -> 0x0` = endpoint 0) |
| +0xc..0xf | `I2A_RECV1` high 32 bits — **must mirror `I2A_CTRL`'s `FIFOCNT` in the same bit position, [23:20] of this 32-bit half (i.e. bits [55:52] of the full 64-bit register)** | `getMailboxBulk`'s loop reads exactly this offset (`item[i]+0xc`) and masks it with the identical `0xf00000` used against a genuine `I2A_CTRL` read |

### Send path (mirror image), `0xfffffff0085388e8`-`0xfffffff008538900`

```
0xfffffff0085388e8  bti c
0xfffffff0085388ec  ldp x8, x9, [x1]        ; caller's 16-byte item: x8=message, x9=endpoint(low32)
0xfffffff0085388f0  ldr x10, [x0, 0x100]    ; mapped MMIO base
0xfffffff0085388f4  mov w11, 0x8800
0xfffffff0085388f8  add x10, x10, x11       ; base + 0x8800 (A2I_SEND0)
0xfffffff0085388fc  stp x8, x9, [x10]       ; *0x8800 = x8 (SEND0), *0x8808 = x9 (SEND1)
0xfffffff008538900  ret
```

Confirms our model's existing assumption that `A2I_SEND0`/`A2I_SEND1` are
always written together (single `stp`, program order low-then-high) — this
is, again, the only reference to 0x8800/0x8808 in any of the three kexts.

## What this means for `darwin_asc.c` (not applied — orchestrator-owned)

Current model (`qemu-sptm/hw/arm/darwin_asc.c:354-363`):

```c
case MBOX_I2A_RECV0:
    if (s->i2a_count) val = s->i2a_fifo[s->i2a_head].msg0;
    break;
case MBOX_I2A_RECV1:
    if (s->i2a_count) {
        val = s->i2a_fifo[s->i2a_head].msg1;   // msg1 is just the endpoint (low bits only)
        s->i2a_head = (s->i2a_head + 1) % MBOX_FIFO_DEPTH;
        s->i2a_count--;
        asc_update_irqs(s);
    }
    break;
```

`msg1` is populated at push time as just the endpoint (`darwin_asc.c:206`,
`s->i2a_fifo[idx].msg1 = ep;`), so the upper 32 bits of every `I2A_RECV1`
read are always zero. Per the walkthrough above, the fix is: when a slot is
valid (`s->i2a_count != 0`), OR the **current** `i2a_count` (the count
*including* the entry about to be popped — i.e. what `I2A_CTRL` would report
if read at that instant, matching step 9's requirement that the last valid
message reports exactly 1) into bits [55:52] of the returned value, i.e.
`val |= (uint64_t)(s->i2a_count & 0xf) << (32 + MBOX_CTRL_CNT_SHIFT)` before
decrementing. `RECV0` needs no change — it is read back raw and unmasked by
the driver (step 9 confirms `getMailboxBulk` never touches
`item[i]+0x0..0x7` beyond the copy). Popping on the `I2A_RECV1` read (as the
model already does) is consistent with every access observed in this driver
— see "Open questions" for why it can't be fully distinguished from
popping-on-`RECV0` given the driver never does one without the other.

## Resolving imports (methodology note)

The extracted kext carries `__auth_got` entries with `no fixups` reported by
`ipsw macho info --fixups`, and `r2`'s `bin.relocs.apply=true` leaves the raw
bytes untouched — both on the isolated kext and when the same address is read
directly out of the merged `firmware/bootkc` fileset image. The raw 8-byte
value (e.g. `0x801100000153783c` at `0xfffffff007ea7780`, the slot behind
`fcn.fffffff0085396ec`) decodes as `dyld_chained_ptr_arm64e_auth_kernel`:
`auth=1, key=0, next=8, addrDiv=1, diversity=0, target=0x153783c`. Adding
`target` to the fileset's `__TEXT` vmaddr (`0xfffffff007004000`, from
`ipsw macho info firmware/bootkc` load command 004) gives
`0xfffffff00853b83c`, which lands in `AppleA7IOP.kext`'s `__TEXT_EXEC` and
disassembles cleanly. All three unresolved call targets used in this
document (`0xfffffff00853b83c`, `0xfffffff00853be40`, `0xfffffff00853ea58`)
were resolved this way and independently confirmed by their surrounding code
making sense (assert strings, format strings, register offsets).

## Open questions

1. **Which function performs the trace's final three reads** (`0x08114`,
   then `0x08110`, then `0x08000`, in that descending order, at the very
   tail of the drain sequence)? The diagnostic-dump function found at
   `0xfffffff008538a54` reads the *same three offsets* but in **ascending**
   order (0x8000, then 0x8110, then 0x8114) as part of one combined
   `IOLog`. The trace's order doesn't match that function, so it's likely a
   different caller (`AppleA7IOP` or `RTBuddy`'s "check for more work /
   idle transition" logic) reading `I2A_CTRL`/`A2I_CTRL`/`IDLE_STATUS`
   individually. Would need a wider disassembly of `RTBuddy.kext`'s
   attention-handling loop to pin down; not blocking, since the *meaning*
   of all three offsets is independently confirmed by the dump function's
   format string regardless of who calls them.
2. **Exact value of the caller-supplied capacity (observed as 9 pairs in the
   trace, i.e. `*mailboxItemCount` = 9 on entry).** Not traced to its
   origin — `AppleA7IOP::getMailboxBulk` (`0xfffffff00853b878`) exists as a
   *different*, generic per-item loop (8-byte item stride, calls vtable
   slot `+0x5a8` once per index) that is architecturally distinct from
   `AppleASCWrapV6`'s own override analyzed here (16-byte item stride,
   direct two-register MMIO pop) and does not itself explain the observed
   single-`I2A_CTRL`-read-then-nine-pairs pattern (that per-item loop would
   interleave a fresh check on every iteration). The call path actually
   exercised in the trace must reach `AppleASCWrapV6::getMailboxBulk`
   directly (or through a thin wrapper), not through `AppleA7IOP`'s generic
   loop; whoever calls it and with what capacity wasn't traced further. This
   doesn't affect the fix — the fix is correct for any capacity, since it
   makes the loop's own stop condition fire at the right point instead of
   never firing.
3. **Vtable slots `+0x620`/`+0x628` (lock/unlock around the pop loop).**
   Their concrete targets weren't resolved (they dispatch through the
   object's own vtable rather than an import stub, and no C++ vtable
   metadata survived in the stripped kext for `avrr` to recover). Their
   return values are discarded and they don't touch any offset in the
   0x8000-0x8fff mailbox block (confirmed by reading their call sites), so
   they're almost certainly a mutex lock/unlock pair, not a hardware access
   — but this is inference, not direct evidence.
4. **Pop-on-RECV0 vs pop-on-RECV1** can't be distinguished from this driver:
   `I2A_RECV0` is read immediately before `I2A_RECV1` in program order in
   the *only* place either is referenced, with nothing observable in
   between. Either hardware model (advance on low-word read, or on
   high-word read) is behaviorally identical for this driver. The model's
   existing choice (advance on `RECV1`) is safe to keep.
5. **`+0x121`/`+0x108` flag names** on the readiness-gate object
   (`0xfffffff00853b83c`) are unverified — no matching cstring was found in
   either kext for what these booleans represent, only that clearing
   `+0x134` (set by the overflow logger) is required for `getMailboxBulk` to
   proceed at all.
6. **`IOReturn` value mismatch**: the panic reports `status = e00002c2`,
   while the readiness gate's own codes are `0xE00002D7`/`0xE00002C7` — close
   in magnitude (same `sys_iokit` family) but not identical, so `0xE00002C2`
   is very likely a distinct, RTBuddy-side error code for "invalid
   management message", not something coming out of the code analyzed here.
   Not chased further since it doesn't affect the register-model fix.
