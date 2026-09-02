# TXM selector 38 / error 42

Source: iOS 27.0 (24A5430a), iPhone17,3, kernelcache `firmware/bootkc` (FILESET
Mach-O, UUID `16FF5BB5-E04D-6DD5-50F2-C6623CF19A56`, `LC_SOURCE_VERSION` not
present but TXM itself reports `217.0.2.0.0`), and `firmware/txm` (Mach-O
arm64e, `LC_SOURCE_VERSION 217.0.2.0.0`, `LC_UUID 6C13F0E7-D19C-3181-AB43-C79FE2785DDD`).
Extracted 2026-09-02.

**Correction to the original task framing:** the format string is not in
`firmware/txm`. It is in `firmware/bootkc` (`com.apple.kernel`), verified
directly:

```
$ strings -a firmware/txm  | grep -c "selector: "   -> 0
$ strings -a firmware/sptm | grep -c "selector: "   -> 0
$ strings -a firmware/bootkc | grep -c "selector: "  -> 28
```

So `TXM [Error]: selector: 38 | 42` is XNU logging the outcome of a call it
made *into* TXM, not something TXM printed about itself. Everything below is
AP-side (`com.apple.kernel`, compiled from a source file XNU itself calls
`txm.c` — confirmed by an embedded `__FILE__` string, see below).

**Address convention:** all addresses below are static link addresses read
directly out of the `firmware/bootkc` Mach-O's `LC_SEGMENT_64` tables (no
slide applied — this is exactly what you get from `ipsw macho info` / `r2`
against the file on disk). The runtime kernelcache in this project's QEMU
guest is slid **+0x20000000** from these values (stated by the orchestrator;
not independently re-derived in this pass). To go from a live/serial-log
address to a file offset here: `static = runtime - 0x20000000`. The reverse
for the addresses cited in this doc:

| Static (this doc) | Runtime (+0x20000000) |
|---|---|
| `0xfffffff0070a6640` (format string) | `0xfffffff0270a6640` |
| `0xfffffff00b043da4` (wrapper function entry) | `0xfffffff02b043da4` |
| `0xfffffff00b044384`/`0xfffffff00b044388` (generic-case emit site) | `0xfffffff02b044384`/`0xfffffff02b044388` |

## Summary

`TXM [Error]: selector: %u | %u` is the **unclassified fallback** of a
5-way domain switch inside one function in XNU's `txm.c`
(`fcn.fffffff00b043da4`, 0x830 bytes, 103 basic blocks) that validates and
logs the result of every XNU→TXM call. Both `%u` are printed with `%u`
(unsigned decimal) — **38 and 42 are decimal, not hex, no ambiguity.** 38 is
the selector number XNU used for the call (read straight out of the log
record XNU built); 42 is the raw "domain" field of TXM's packed return value,
and it hit the fallback specifically *because* it did not match any of the
four domains XNU knows how to name (TrustCache=3, CodeSignature=4, Errno=5,
Image4_V2=43). I could not recover TXM's own selector-name table or a
symbolic name for domain 42 in the time available — see Open Questions.

A QEMU model cannot do anything about this directly: it is XNU-side logging
about a TXM call outcome, not a register TXM/SPTM expose to the guest. The
actionable fact is that domain 42 is *not* TrustCache/CodeSignature/Errno/
Image4_V2, which rules out "malformed code signature blob" and "trust cache
lookup miss" as XNU's own classification of the failure (see §4).

## 1. The generic string, its address, and its call site

`strings -a -t x -n 4 firmware/bootkc` around file offset `0xa2200`–`0xa2660`
turns up five sibling formats, all `TXM [Error]:`-prefixed, plus four
`@%s:%d`-suffixed assert-style messages and the literal filename `txm.c`:

| File offset | Static VA | String |
|---|---|---|
| `0xa22bc` | `0xfffffff0070a62bc` | `txm.c` (used as the `%s` for four asserts below) |
| `0xa22c2` | `0xfffffff0070a62c2` | `received fatal error for a selector from TXM: selector: %u \| 0x%0llX @%s:%d` |
| `0xa230e` | `0xfffffff0070a630e` | `received fewer than expected return words from TXM: selector: %u \| %llu @%s:%d` |
| `0xa24ed` | `0xfffffff0070a64ed` | `received excessive return words from TXM: selector: %u \| %llu @%s:%d` |
| `0xa2532` | `0xfffffff0070a6532` | `invalid number of arguments to TXM: selector: %u \| %u @%s:%d` |
| `0xa256f` | `0xfffffff0070a656f` | `TXM [Error]: TrustCache: selector: %u \| 0x%02X \| 0x%02X \| %u` |
| `0xa25ad` | `0xfffffff0070a65ad` | `TXM [Error]: CodeSignature: selector: %u \| 0x%02X \| 0x%02X \| %u` |
| `0xa25ee` | `0xfffffff0070a65ee` | `TXM [Error]: Errno: selector: %u \| %d` |
| `0xa2615` | `0xfffffff0070a6615` | `TXM [Error]: Image4_V2: selector: %u \| %u` |
| `0xa2640` | `0xfffffff0070a6640` | **`TXM [Error]: selector: %u \| %u`** ← the one we're chasing |

I found every reference to these nine strings by scanning `__TEXT_EXEC`
(file 0x13fc000–0x4350000, VA `0xfffffff008400000`–`0xfffffff00b354000`) for
`adrp`+`add` pairs whose computed target matches each string's VA (script:
`find_adrp.py`, plain struct-based ARM64 instruction decode, no disassembler
dependency). All nine addresses resolve to code inside one contiguous
address range:

```
0xfffffff00b0442cc  adrp/add -> Image4_V2       (0xa2615)
0xfffffff00b044330  adrp/add -> TrustCache       (0xa256f)
0xfffffff00b04434c  adrp/add -> CodeSignature    (0xa25ad)
0xfffffff00b044368  adrp/add -> Errno            (0xa25ee)
0xfffffff00b044380  adrp/add -> generic          (0xa2640)   <-- our string
0xfffffff00b044488  adrp/add -> excessive_return_words (0xa24ed)
0xfffffff00b0444b0  adrp/add -> invalid_num_args (0xa2532)
0xfffffff00b0444d4  adrp/add -> fatal_error_selector (0xa22c2)
0xfffffff00b0444fc  adrp/add -> fewer_return_words (0xa230e)
0xfffffff00b044430/0xb044460/0xb0444a4/0xb0444ec (x4) -> "txm.c" (0xa22bc), used as __FILE__ for the four asserts
```

`r2`'s `af`/`afi` at `0xfffffff00b043da4` confirms this is **one function**,
0x830 bytes (`0xfffffff00b043da4`–`0xfffffff00b0445d4`), 103 basic blocks,
524 instructions. The embedded literal `txm.c` filename (read directly:
`ps @ 0xfffffff0070a62bc` → `txm.c`) proves this function is compiled from
XNU's own `txm.c`, not a generic/shared kernel-wide logger it merely happens
to reuse.

This function does two jobs, both visible in its body:

1. **Validates the low-level XNU↔TXM call protocol** — argument count,
   returned-word count — and hard-panics (`bl 0xfffffff00b331ec4`, an
   assert/fault helper) with `txm.c` + a line number if those are wrong
   (the four `@%s:%d` strings above; e.g. line `0x1ad`=429 for
   `invalid number of arguments`, at `0xfffffff00b0444c0`).
2. **Classifies and logs an error domain** from a completed call (the five
   `TXM [Error]:` strings) — this is the part relevant to selector 38.

I could not find a `bl`/`hvc`/`smc` instruction anywhere in this function's
524 instructions that performs the actual XNU→TXM transition — it is not
here. This function receives an already-completed call's result (a small
record built by its caller) and only validates/logs it; the code that
issues the call and knows the *specific selector number* used lives
elsewhere and was not conclusively located (see Open Questions).

## 2. Exact dataflow for the generic (domain-unclassified) case

Traced by hand through the disassembly (`r2 pdf @ 0xfffffff00b043da4`,
saved locally, not committed):

```
0xfffffff00b044290  ldr  w8, [x9]          ; x9 = record ptr = arg1(x0), saved to [sp,0x38] at entry
                                            ; w8 = *(u32*)record  == SELECTOR  (never overwritten before the print)
0xfffffff00b044294  lsr  x11, x25, 0x20
0xfffffff00b044298  lsr  x9,  x25, 0x28
0xfffffff00b04429c  lsr  x10, x25, 0x30    ; x25 loaded earlier at 0xb04404c: ldr x25, [x23, 8]
                                            ; x23 = [x26+8] = the per-call staged "args" record;
                                            ; x25 = *(u64*)(x23+8) is a packed TXM return value

0xfffffff00b0442a0  cmp  w20, 3            ; w20 = w25 & 0xffff  (low 16 bits of x25 = "domain")
0xfffffff00b0442a4  b.le 0xb044314         ; w20 in {0,1,2,3}: 0=success(no log), 3=TrustCache, else->generic
0xfffffff00b0442a8  cmp  w20, 4
0xfffffff00b0442ac  b.eq 0xb04433c         ; domain 4 -> CodeSignature
0xfffffff00b0442b0  cmp  w20, 5
0xfffffff00b0442b4  b.eq 0xb044358         ; domain 5 -> Errno
0xfffffff00b0442b8  cmp  w20, 0x2b         ; 43 decimal
0xfffffff00b0442bc  b.ne 0xb044378         ; not 43 -> generic (THIS is where domain=42 lands)
                                            ; == 43 -> Image4_V2

; --- generic fallback, reached for domain in {1,2,6,7,8,...} including 42 ---
0xfffffff00b044378  and  w9, w25, 0xffff   ; w9 = domain field again = 42 in our case
0xfffffff00b04437c  stp  x8, x9, [sp]      ; args to the printf-ish emitter: (selector=38, domain=42)
0xfffffff00b044380  adrp x0, 0xfffffff0070a6000
0xfffffff00b044384  add  x0, x0, 0x640     ; x0 = "TXM [Error]: selector: %u | %u"
0xfffffff00b044388  bl   0xfffffff00b1e1060  ; the actual compact-log emit primitive
```

**Confirmed: the second `%u` (42) is literally the same 16-bit "domain"
field used to pick which of the five strings to print** — for the four
named domains, XNU also decodes extra byte fields packed into `x25` bits
[32:39]/[40:47]/[48:55] (the `%02X`/`%u` extra args for TrustCache and
CodeSignature); for the generic fallback it just echoes the raw domain
value back as the "error code."

## 3. The domain table (as far as recovered)

| Domain value (`w20` = low 16 bits of packed TXM return `x25`) | XNU's handling | Evidence |
|---|---|---|
| 0 | Success — function returns without logging (`cbz w20, 0xb0443d8`, falls to `mov w0,0; ret`) | `0xfffffff00b044314` |
| 1, 2 | No dedicated string — falls through to generic | inferred: only `cmp w20,3` is checked before the `<=3` block, values 1/2 don't hit the `==3` case either |
| 3 | `TXM [Error]: TrustCache: selector: %u \| 0x%02X \| 0x%02X \| %u` | `0xfffffff00b044318`/`0x044330` |
| 4 | `TXM [Error]: CodeSignature: selector: %u \| 0x%02X \| 0x%02X \| %u` | `0xfffffff00b0442a8`/`0x04433c` |
| 5 | `TXM [Error]: Errno: selector: %u \| %d` | `0xfffffff00b0442b0`/`0x044358` |
| 6, 7, 8, 0x29, 0x2a | Not domain names at all — see caveat below | `0xfffffff00b0442dc`–`0xb044300` |
| **0x2b (43)** | `TXM [Error]: Image4_V2: selector: %u \| %u` | `0xfffffff00b0442b8`/`0x0442cc` |
| **anything else — includes 42 (observed)** | `TXM [Error]: selector: %u \| %u` (generic) | `0xfffffff00b0442bc`→`0x044378` |

**Caveat on 6/7/8/0x29/0x2a:** after the generic-fallback log call
(`0xfffffff00b044388`) the function does `cmp w20,7; b.gt 0xb0442e4` and
falls into a *second*, separate small switch (`cmp w20,0x2a`→`mov w0,'1'`;
`cmp w20,0x29`→`mov w0,'.'`; `cmp w20,8`→`mov w0,'8'`; else `mov w0,5/6`).
I confirmed `w0` from this second switch is **not** used again before the
function's epilogue at `0xfffffff00b0443ac` (no store, no further call
between `0xb044390` and the `ldr x9,[sp,0x38]` at `0xb0443ac`) — it looks
like dead/vestigial code from register-allocator reuse rather than a second
print. **I did not fully resolve what this second switch is for; it does
not change the fact that domain 42 already went out through the generic
string before reaching it.** Flagged as open below rather than guessed at.

## 4. What error 42 is

Not resolved to a symbolic name. What is established:

- 38 and 42 are decimal (`%u`), confirmed from the raw format string bytes.
- 42 did **not** match TrustCache (3), CodeSignature (4), Errno (5), or
  Image4_V2 (43) — the four domains XNU's own logging code can name. This
  is a real negative finding, not a guess: the observed log line used the
  2-argument generic format, and the disassembly shows that format is only
  reached when domain ∉ {0,3,4,5,43}.
- I searched both `firmware/txm` and `firmware/bootkc` for a
  selector-name table, a domain-name table, or any string resembling an
  enum name for TXM return domains/codes beyond the four above, and found
  none (`strings -n 4` for `TXM_SELECTOR`, `TXM_SEL`, `TXM_OP`, `kTXM`,
  `TXM_CMD`, `txm_cmd`, `txm_call` all came up empty in both binaries).
  TXM's own numeric error codes appear to have no adjacent string table in
  either binary at this build.

## 5. TXM's own selector dispatch (firmware/txm) — inconclusive

I went back into `firmware/txm` looking for the table this whole log
message is *about* (item 1 of the original task): a selector→handler table
inside TXM itself.

What I found:
- TXM's cold-boot entry (`LC_UNIXTHREAD` PC `0xfffffff017084000`, file
  offset `0x80000`) is a stack-setup trampoline, not the call-handling path.
  Reading it requires `-e io.va=false` and file-relative addressing — with
  `io.va=true` r2 mis-maps this Mach-O's `__TEXT_BOOT_EXEC` segment and
  shows `invalid` bytes that are not what's actually on disk (verified by
  reading the same file offset directly with Python — real ARM64 code is
  there). **Anyone continuing this: use physical/file addressing, not `iaddr`, for `firmware/txm`.**
- The exception vector table at file offset `0x78000`
  (`__TEXT_EXEC.__exc`) is a standard four-per-EL AArch64 vector table
  (`stp x0,x1,[sp,-0x10]!; mov x1,#N; ldr x0,[data]; braaz x0`), i.e. TXM's
  own fault handling, not the AP call-gate.
- I did not find any `smc`/`hvc` instruction anywhere in the 61 XNU-side
  functions that reference the logging function above, nor in TXM's own
  `__TEXT_EXEC.__text` (0x49358 bytes, scanned in full). SPTM/TXM calls in
  this architecture evidently do not use a classic synchronous-exception
  trap — consistent with the `STATE_TXM_CALLED_BY_XNU` /
  `EVENT_CALL_TXM` state-machine names present in `bootkc`'s strings
  (`strings firmware/bootkc | grep TXM_`), which imply a direct,
  SPTM-mediated branch/gate rather than `smc #0`. I did not identify what
  instruction performs that gate.
- I found one internal TXM dispatcher — `image4 dispatch: handler: %llu`
  (file offset `0x27c4` in `__TEXT.__cstring`, static VA
  `0xfffffff0170067c4`, referenced from `0xfffffff01703d560`) — that does
  bounds-check an index against `0x2b` (43 decimal) at
  `0xfffffff01703d69c` (`cmp x21, 0x2b; b.ls ...`). This is suggestively
  the same number as the `Image4_V2` domain (43) XNU names above, but I
  could not establish a causal link between this Image4-internal
  sub-dispatch and the *top-level* selector space that includes 38 — it
  may just be a coincidence of both using "43" as a bound for unrelated
  reasons. Do not treat this as a confirmed match.
- I did **not** find a top-level selector-indexed jump table (`adrp/add`
  table base + `ldr xN,[xB,xI,lsl 3]` + `br`/`braa`) in TXM's
  `__TEXT_EXEC.__text`. I scanned all 78 occurrences of `lsl 3]` addressing
  in that section for a `br`/`braa` within the next few instructions and
  found none. Either the dispatch uses a different codegen shape (e.g. a
  `cmp`/`b.eq` chain, or an outlined callee I didn't walk into), or it
  lives somewhere I didn't check (e.g. `__TEXT_BOOT_EXEC`, which is small,
  0x4060 bytes, and I did not fully disassemble).

**Bottom line on item 1 of the original task: I could not recover the
selector→handler table or name entry 38.** This needs either more targeted
static work starting from wherever TXM reads the incoming selector register
on a live call (which requires first finding the call-gate instruction), or
a dynamic approach (see below).

## 6. Does it retry, or is it per-operation? (item 4)

Not directly traced (would need to identify and instrument the true
XNU-side caller — see §5's conclusion that the 61 raw `bl`-xrefs to the
logging function at `0xfffffff00b043da4` are mostly unrelated: I
disassembled all 61 and only 4 use PAC-authenticated indirect calls
(`blraa`) suggestive of a totally different, non-TXM subsystem reusing the
same generic-shaped log call; several others, e.g.
`0xfffffff00b0417c8`, have no function prologue of their own (no
`pacibsp`/`stp x29,x30`) and read/write fixed globals unrelated to TXM's
own state — these are very likely compiler-outlined shared tail/epilogue
fragments reached by plain branch from real callers elsewhere, not
themselves "the selector-38 call site". None of the 59 candidates
I disassembled contains a `mov w_, #0x26` (38 decimal) selector
constant.).

Reasoning from the evidence the orchestrator already collected (bursty
rate: 18,170 occurrences in the guest second at `00:02:01`, zero at
`00:01:27`, starting right as `launchd` begins spawning services): this
pattern — activity-correlated bursts rather than a constant flat rate from
first occurrence — is more consistent with **many independent calls each
failing the same way** (e.g. once per page fault into a codesigned region,
or once per spawn attempt across many services) than with **one call site
stuck in a tight infinite retry loop**, which would produce a constant high
rate from the moment it starts rather than bursts tied to launchd activity.
This is an inference from timing, not a traced call site — flagged as such.

## Open questions

1. **What issues the XNU→TXM call with selector 38, and what does it call
   TXM to do?** Not found. The 61 `bl`-xrefs to the logging function
   (`0xfffffff00b043da4`) don't include an obvious per-selector wrapper
   with a `#0x26` immediate. Likely needs either (a) walking the ~55
   remaining un-eliminated candidates in `0xfffffff00b0417c8`–
   `0xfffffff00b046140` more carefully to find which real (non-tail-shared)
   function they belong to, or (b) a dynamic breakpoint at
   `0xfffffff00b043da4`+slide with a condition on `*(u32*)x0 == 38`,
   reading the return address / backtrace at that point. `tools/hmp.py`
   and QEMU's gdbstub (`-s -S`) could do this; I did not attempt it in this
   pass since it's out of scope for static RE but would very likely be
   faster than continuing to hand-walk the disassembly.
2. **What is TXM's own selector→handler dispatch table, and what is
   selector 38 named?** Not found in `firmware/txm`. The call-gate
   instruction itself (how XNU transitions into TXM) wasn't identified
   either — settling that would likely make the dispatch table easy to
   find (walk forward from the gate entry point).
3. **What does domain/error 42 mean?** No string table for it in either
   binary. Given it's outside the four named domains, my best-supported
   guess (not elevated to a claim) is that it's a more structural/state
   failure (e.g. "TXM not in the expected state for this selector",
   "resource exhausted", "address-space/ASID error") rather than a
   trust-cache or code-signature content problem — but this is inference
   from what it *isn't*, not a citation of what it *is*.
4. **Confirm retry vs. per-operation dynamically.** A `DARWIN_ASC_DEBUG`-style
   counter or a QEMU breakpoint at the emit site
   (`0xfffffff00b044388`, runtime `0xfffffff02b044388`) that dumps the
   caller's `lr` on each hit would settle whether this is one call site
   spinning or many distinct call sites, definitively and quickly.
