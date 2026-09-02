# AppleSEPBooter::initForSEP and the SEP endpoint-publish chain

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140, kernelcache `firmware/bootkc`,
kexts extracted with `ipsw kernel extract` to `/tmp/dvm/kexts/`, extracted/analyzed 2026-09-02.

**Slide convention used throughout:** the boot kernelcache is slid `0x20000000` at
runtime. All addresses in this doc are the *unslid* Mach-O addresses (as they
appear in `firmware/bootkc` / the extracted kext) unless marked "runtime". To get
the runtime/panic-backtrace address, add `0x20000000`; to go the other way,
subtract it. Cross-check: the original panic backtrace gave the kext's load
address as `0xfffffff029582de0` (runtime); `ipsw macho info` on the extracted
kext gives `__TEXT_EXEC` starting at unslid `0xfffffff009582de0` — the
difference is exactly `0x20000000`.

## Summary

`AppleSEPBooter::initForSEP` reads a literal-named IORegistryEntry property,
`"rom-panic-bytes"`, off its provider (the `AppleA7IOPNub` matched by
`IONameMatch = iop-nub,sep`, i.e. `/arm-io/sep/iop-sep-nub`), and hands its
4-byte value to `SEPROMPanicBuffer`'s constructor, which additionally requires
`/chosen/chip-id`. **Both of these gates are already fixed in this repo**
(`dt_fixup.py`'s `fixup_sep`, landed in commit `3cb234c` while this task was in
flight) and a probe run confirms `AppleSEPManager::start()` now completes
without panicking. The blocker has moved: `AppleCredentialManager`'s
`waitForSEPEndpoint` is not waiting on `AppleSEPManager` matching (it already
has) — it is waiting on a *second*, dynamically-created IOService named
`sep-endpoint,scrd`, which `AppleSEPManager` only instantiates from data it
expects to receive from the real SEP over the mailbox. There is no device-tree
property that substitutes for that; closing this gap needs mailbox-level SEP
protocol participation, the same category of work already tracked for DCP's
AFK endpoints.

## What panicBytesData is, and the chain around it

### `AppleSEPBooter::initForSEP(AppleSEPManager *)`

Unslid address `0xfffffff00959af4c` (`com.apple.driver.AppleSEPManager`,
`__TEXT_EXEC` base `0xfffffff009582de0`, `LC_SOURCE_VERSION 928.0.2.0.0`).
Source file, from `LC_SOURCE_VERSION`-adjacent `__cstring` literals:
`.../Sources/AppleSEPManager/AppleSEPBooter.cpp`.

Called from the static factory `AppleSEPBooter::sepBooter(AppleSEPManager*)`
at `0xfffffff00959ae88` (call site `0xfffffff00959aec0`), guarded by
`REQUIRE(aseb != nullptr)` on the `new AppleSEPBooter` result.

| Step | Address | What it does | Evidence |
|---|---|---|---|
| 1 | `0x59af68`-`0x59af8c` | `x0 = manager->vtable[0x370](manager)` — self-call, no extra args, result immediately used as the "this" for a `getProperty`-shaped call. Inferred (not symbol-confirmed) to be `getProvider()`. | disasm; see caveat below |
| 2 | `0x59af90`-`0x59afb8` | `x0 = <result>->vtable[0x118](x1="rom-panic-bytes")` — one-string-arg virtual call whose result is fed straight into an `OSDynamicCast`-shaped helper. Inferred to be `IORegistryEntry::getProperty(const char*)`. | cstring `0xfffffff0077104a3` = `"rom-panic-bytes"`; `axt` xref from `0x59afb0` |
| 3 | `0x59afbc`-`0x59afc8` | `fcn.fffffff0095c5f88(x0=result, x1=<OSData metaclass global>)` — the compiled shape of `OSDynamicCast(OSData, ...)` (load object, load metaclass global, call a shared 4-instruction GOT-stub, test result for null). | disasm pattern, repeated identically at 3 other call sites in this kext |
| 4 | `0x59afcc` | `cbz x0, 0x59b0a8` → **panic** if the cast result is null | `axt 0xfffffff0077104b3` ("panicBytesData != nullptr") → `fcn.fffffff0095bccf8` → `bl fcn.fffffff0095bccf8` target is exactly `0x59b0a8` |
| 5 | `0x59afd0`-`0x59aff4` | `x21->getLength()` (vtable+0xa0), `cmp w0,4`, panic if `!=4` | `axt 0xfffffff007710500` ("panicBytesData->getLength() == sizeof(uint32_t)") → `fcn.fffffff0095bcc90`, call site `0x59aff4` |
| 6 | `0x59aff8`-`0x59b01c` | `x21->getBytesNoCopy()` (vtable+0xd8), `ldr w21,[x0]` (LE u32 load), `tst x21,7`, panic if nonzero | `axt 0xfffffff007710530` ("panic_length % sizeof(uint64_t) == 0") → `fcn.fffffff0095bccc4`, call site `0x59b020` |
| 7 | `0x59b024`-`0x59b038` | `x1 = x21 >> 3` (byte length ÷ 8); `bl fcn.fffffff0095b163c` → tail-jumps to `fcn.fffffff0095b14ec` = `SEPROMPanicBuffer::SEPROMPanicBuffer(size_t)`; stores the resulting object at `this+0x1c0` | disasm |
| 8 | `0x59b03c`-`0x59b0a4` | tail call (`braa`, i.e. `return`) into `booter->vtable[0xE0](booter, manager, ctx=booter, action=&_bootAction, 0, 0xff)` | disasm; PAC-signed pointer built at `0x59b054`-`0x59b064` targets `0x59b0b4` |

**Caveat on step 1/2:** I did not resolve the AppleSEPManager vtable statically
(no exported symbols anywhere in this kernelcache — `nm firmware/bootkc` and
`nm` on every extracted kext return "no symbols"; `LC_DYSYMTAB` shows 0 local/
external syms). The `getProvider()`/`getProperty()` identification rests on
call *shape* (self-call with no args → object; one-string-arg call whose result
feeds `OSDynamicCast` → property), which is unambiguous in context but not a
symbol-table fact. The IOKitPersonality evidence below is what actually pins
down *which* registry node this is.

**Which node `getProvider()` returns:** from `__PRELINK_INFO`'s
`IOKitPersonalities` (parsed via `plistlib` after `otool -l` located the
`__PRELINK_INFO.__info` section at file offset `70615040`, size `0x280000`):

```
personality: A7IOPNub
    IOProviderClass = AppleA7IOPNub
    IONameMatch = iop-nub,sep
    IOClass = AppleSEPManager
```

and from the raw (unstripped) device tree (`/tmp/dvm/dtree_raw`, decoded with
this repo's `dt_fixup.py` decoder): `/arm-io/sep/iop-sep-nub` has
`compatible = iop-nub,sep`. So the provider is `/arm-io/sep/iop-sep-nub` — the
IOP nub, exactly the node CLAUDE.md's DCP table already identifies as where
iBoot-supplied, driver-specific properties belong (`no-firmware-service`,
`pre-loaded`, `region-base`/`region-size`, `quiesced` all live there for DCP;
`rom-panic-bytes` lives there for SEP by the identical pattern).

### `SEPROMPanicBuffer::SEPROMPanicBuffer(size_t length)`

Unslid `0xfffffff0095b14ec` (alias/BTI-landing-pad thunk `0xfffffff0095b163c`
tail-jumps here). Class name recovered from the `site.SEPROMPanicBuffer`
IOLock-group-name string (`0xfffffff007711181`) referenced from
`AppleSEPBooter::romCommand`, and independently from `dt_fixup.py`'s own
`fixup_sep()` comment (committed separately, see below) citing the identical
source line numbers.

| Check | REQUIRE text (recovered from the panic-thunk string operands) | Consequence for `rom-panic-bytes` |
|---|---|---|
| `length > 0` | `0xfffffff007718f5e` | value (after `>>3`) must be nonzero → raw property value must be **nonzero** |
| `length <= 1024` | `0xfffffff007719037` | raw property value must be **≤ 8192** (`1024*8`) |
| `_buffer = static_cast<char*>(IOMallocData(_length*sizeof(uint64_t)*2+1))` | `0xfffffff007719046` | allocation-failure panic; not size-dependent in practice |
| `chosen = IORegistryEntry::fromPath("/chosen", gIODTPlane)` | `0xfffffff007715e17` | needs a `/chosen` node (always present) |
| `entry = OSDynamicCast(OSData, chosen->getProperty("chip-id"))` | `0xfffffff007715e82` | needs `/chosen/chip-id` present and of type `OSData` |
| `sizeof(uint32_t) == entry->getLength()` | `0xfffffff007712d14` | `chip-id` must be exactly 4 bytes |
| `_chip_id = *(uint32_t*)entry->getBytesNoCopy()` | `0xfffffff007719096` | `chip-id` value must be **nonzero** |

Allocation size formula confirms the `>>3`/`<<4` arithmetic seen in
`initForSEP`: `x0 = 1 | (x1 << 4)` at the call into the allocator sub-helper
matches `_length * sizeof(uint64_t) * 2 + 1 = length*16 + 1` exactly.

Combined with `initForSEP`'s own `panic_length % 8 == 0` check, the full
constraint on the **raw `rom-panic-bytes` value** is:

```
value % 8 == 0   AND   8 <= value <= 8192        (LE uint32, 4-byte OSData)
```

## Register map / property map

| Node | Property | Type | Constraint | Evidence |
|---|---|---|---|---|
| `/arm-io/sep/iop-sep-nub` | `rom-panic-bytes` | `u32` (4-byte OSData, LE) | `%8==0`, `8..8192` | `initForSEP` REQUIREs above |
| `/chosen` | `chip-id` | `u32` (4-byte OSData) | nonzero | `SEPROMPanicBuffer` REQUIREs above |

**Both are already applied** in `dt_fixup.py`'s `fixup_sep()` (commit
`3cb234c`, landed during this task): `rom-panic-bytes = u32:0x100` (256; `%8==0`,
`256/8=32` words, within bounds) and `chip-id` derived from the platform name
(`t8140` → `u32:0x8140`). I independently re-derived the same property names,
node, and constraints from disassembly before finding that commit, which
cross-validates it.

Verified in `/tmp/dvm/probe/SEP5.serial.log` (and `SEP2`-`SEPMBOX`, same
family): no `panicBytesData`/`SEPROMPanicBuffer` panic; instead:

```
virtual bool AppleSEPManager::start(IOService *): Setting custom allocator for SEP MPM mapper
virtual bool AppleSEPManager::start(IOService *): SEP memory IS coherent
virtual bool AppleSEPManager::start(IOService *): control endpoints created
```

## The next gate: what `waitForSEPEndpoint` actually waits for

`AppleCredentialManager: waitForSEPEndpointOutsideACMCommandGate: waiting
(cmd=25).` / `ACMTRM: waitForSEPEndpoint: timed out waiting for
AppleSEPManager (timeoutMs=5000).` come from `com.apple.driver.
AppleSEPCredentialManager` (extracted separately; not present under
`/tmp/dvm/kexts/` before this task, `ipsw kernel extract firmware/bootkc
com.apple.driver.AppleSEPCredentialManager -o /tmp/dvm/kexts`).

Disassembly of the function that builds both strings
(`0xfffffff009524ca4(uint32_t timeoutMs)`, xref'd from both the
`"AppleSEPManager"` and `"waitForSEPEndpoint"` cstrings):

1. `0x524cf0`: builds a matching dictionary for **class** `"AppleSEPManager"`
   (cstring `0xfffffff0076fb212`) and calls `waitForMatchingService()`
   (`fcn.fffffff009538e80`) with a computed deadline. **This succeeds** —
   `AppleSEPManager` does match and start, as shown above.
2. `0x524d1c`-`0x524d2c`: builds a **second**, differently-typed matching
   dictionary (a different builder function, `fcn.fffffff009538e10`, vs.
   `...e30` used for step 1 — consistent with `IONameMatch` vs `IOClass`
   matching) for the literal string `"sep-endpoint,scrd"` (cstring
   `0xfffffff0076fb285`), and calls `waitForMatchingService()` again with the
   **same deadline**. **This is the call that times out**, repeating every
   `timeoutMs` (5000 ms per the log) forever.

`"sep-endpoint,scrd"` matches the exact `IONameMatch` shape already
enumerated in `AppleSEPManager`'s own `IOKitPersonalities` for its other
device services (`sep-endpoint,artr`, `sep-endpoint,arts`,
`sep-endpoint,debu`, `sep-endpoint,hilo`, `sep-endpoint,log`,
`sep-endpoint,pair`, `sep-endpoint,unit`, `sep-endpoint,xarm`,
`sep-endpoint,xars` — none of these literally is `scrd`, so `scrd`
"Secure Credential" is a service `AppleSEPCredentialManager` cares about that
isn't in that static list either). Critically, **no static device tree
dump — raw or patched — has a `sep-endpoint,*` child under `/arm-io/sep`**;
`/arm-io/sep` has exactly one child, `iop-sep-nub`.

`AppleSEPManager.kext` itself contains the mechanism that creates these nubs
**at runtime**: a template string `"sep-endpoint,xxxe"` sits in
`__TEXT.__const` (not `__cstring` — it's a mutable stack-init template) at
`0xfffffff007719000+0x1c0`, and `fcn.fffffff009584814(char*, uint32_t,
uint32_t code)` copies it onto the stack and overwrites the last 4 characters
with the 4 bytes of `code` (via `fmov`/`umov`/vector byte-reverse — i.e.
`code` is a packed 4-character ASCII tag, exactly like RTKit endpoint names
elsewhere in this codebase). This helper is called from
`fcn.fffffff0095846ec` and `fcn.fffffff0095a48c0`, i.e. from **two different
call sites that presumably iterate over a table of reported endpoint tags**
(I did not trace where `code` itself comes from beyond this point — it is a
register argument, most plausibly parsed out of a real SEP-side "endpoint
directory" reply, but I have not proven that specific origin).

This is architecturally the same pattern CLAUDE.md already documents for DCP:
named service endpoints (`DCPEndpoint1..23`, the AFK/EPIC framing) are not
static device-tree nodes either — they exist because the coprocessor
announces them over its mailbox after boot. SEP's endpoint directory is the
same idea one layer up (an SEP *application*-level directory, not the
generic RTKit management-protocol endpoint bitmap `darwin_asc.c` already
implements).

## Direct answers to the task's questions

**Where does `panicBytesData` come from?** `getProvider()->getProperty("rom-
panic-bytes")` on the `iop-nub,sep`-matched `AppleA7IOPNub`
(`/arm-io/sep/iop-sep-nub`), cast to `OSData`, required to be exactly 4 bytes,
whose value (LE uint32) is required to be a multiple of 8. **This is not a
`/chosen/memory-map` lookup** — I confirmed the literal string
`"rom-panic-bytes"` occurs exactly once in the entire kernelcache
(`r2 -c 'izz~rom-panic-bytes' firmware/bootkc`), as a direct
`getProperty(const char*)` argument, not a name compared against memory-map
entry names. It is not present in either the raw or patched device tree today
(confirmed by the coordinator's separate dump), but **the fix does not need a
memory-map entry** — it needs the property on the nub, which `dt_fixup.py`
already adds (commit `3cb234c`).

**What does `initForSEP` do with the buffer, and what does it check next?**
It does not write real content into it or hand it to the AP for writing — it
only validates the *length* encoded in `rom-panic-bytes` and constructs a
`SEPROMPanicBuffer` sized from that length (real SEP ROM firmware would write
its panic text there over the mailbox; nothing in this path expects the AP to
populate it). After `SEPROMPanicBuffer` construction succeeds, `initForSEP`
tail-calls into `AppleSEPBooter`'s own boot-action machinery (very likely
`_bootAction`, queued asynchronously) rather than returning synchronously —
so the REQUIRE chain documented here is a *precondition to attempt* talking
to SEP, not the whole gate.

**Ordered list of REQUIRE/panic sites downstream, from the string table**
(`AppleSEPBooter.cpp`, in cstring-table order, which tracks source order):
`sepBooter` (`aseb != nullptr`) → `initForSEP` (the 3 checks above) →
`SEPROMPanicBuffer` ctor (the 5 checks above, 2 already satisfiable) →
`triggerRecoveryMode` / `bootSEP` (many **hard `panic()` calls**, not soft
REQUIREs, of the form `"SEP Boot Failure: ... - 0x%x" @%s:%d`, guarding
alignment checks and "ROM failed to ack {BootTz0,LoadSEPART,...}" conditions)
→ `generateROMNonce` → `_sendSEPART` → `_bootAction` (dispatches SEP status
messages; `REQUIRE(_sep_status != BootStatus::Panicked)`) → `romCommand`.
`bootSEP`'s ack-failure panics are real `panic()` calls, not REQUIREs that
degrade gracefully — but empirically, a 90-second `probe.sh` run with the two
property fixes in place hits **none** of them (only the repeating 5-second
ACM timeouts). I could not determine from static analysis alone whether that
means the async boot action never actually runs, or runs and takes a
soft-timeout branch (`"AppleSEP:WARNING: SEP ROM timeout - no response"`,
which is a warning, not a panic) rather than a hard-ack-failure branch.

**Shortest path to "the endpoint is published," and can it be reached without
a real SEP?** No. The specific endpoint `AppleCredentialManager` blocks on,
`sep-endpoint,scrd`, is created by `AppleSEPManager` at runtime from data it
expects to receive from SEP itself — there is no device-tree property that
substitutes for it, by the same logic that DCP's named AFK endpoints aren't
device-tree nodes either. Getting past `initForSEP`'s REQUIRE chain (already
done) was necessary but not sufficient: it just moved the failure from an
outright panic at driver-start time to an infinite polite timeout inside
`AppleCredentialManager`. Closing this gap for real needs `darwin_asc.c` (or
a dedicated SEP model) to answer SEP's application-level mailbox traffic
enough to report at least the `scrd` endpoint tag — out of this task's scope
and out of this agent's edit lane (`qemu-sptm/hw/arm/darwin_asc.c` is
orchestrator-owned per `CLAUDE.md`).

## Open questions

- **Does `_bootAction` actually run, and does it panic eventually?** A longer
  probe run (the existing ones are ~90s; `LONG.serial.log` is ~170MB / much
  longer) should be grepped for `"SEP Boot Failure"` or `"AppleSEP:WARNING:
  SEP ROM timeout"` to settle whether the async path is silently inert or
  actively retrying. I did not have budget to fully trace `_bootAction`'s
  ~1300-line body or the mailbox message layer it drives.
- **Exact identity of vtable slots `+0x370`/`+0x118`/`+0xE0`** on
  `AppleSEPManager`/`AppleSEPBooter` — inferred from call shape only, no
  symbol table exists anywhere in this kernelcache to confirm names. Would
  need a second, symboled kernelcache (a non-prelinked KDK build) to pin
  down definitively.
- **Where does the `code` argument to the endpoint-name builder
  (`fcn.fffffff009584814`) come from?** I traced it to two call sites
  (`fcn.fffffff0095846ec`, `fcn.fffffff0095a48c0`) but not further back to
  the mailbox message parser. That would show exactly which RTKit/EPIC
  message field carries the endpoint tag, which is the concrete surface
  `darwin_asc.c` would need to answer if SEP protocol emulation is ever
  attempted.
