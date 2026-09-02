# sks feasibility: can we answer AppleSEPKeyStore well enough to mount an encrypted Data volume?

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140, kernelcache `firmware/bootkc`.
`com.apple.driver.AppleSEPKeyStore` extracted with
`ipsw kernel extract firmware/bootkc com.apple.driver.AppleSEPKeyStore --imports -o /tmp/dvm/kexts`
(363,432 bytes; addresses below are unslid file/runtime addresses from that
extraction, cross-checked against `firmware/bootkc` directly with the repo's
`tools/re/kdis.py` flat mapping, `file_offset = VA - 0xfffffff007004000`, the
same mapping already verified in `docs/re/sep-protocol.md`). Two public
QEMU forks were read for architecture only, per the project's existing
practice (facts, not code, AGPL/GPL): `ChefKissInc/Inferno`
`hw/arm/apple-silicon/sep-sim.c` (fetched 2026-09-02,
commit history to `0dc228f6`, 2026-05-31) and `TrungNguyen1909/qemu-t8030`
`hw/arm/apple_sep.c` (has no keystore handling at all — confirmed by grep,
zero hits for "keystore" or "sks"). Analysed 2026-09-02. `AppleKeyStore` (the
non-SEP, software-only keystore driver of older iOS) does **not exist** in
this kernelcache; `AppleSEPKeyStore` is the only keystore kext
(`docs/re/keybag.md`, 324-bundle `__PRELINK_INFO` enumeration).

## VERDICT: feasible, not a hard no

The crux question — is SEP key material opaque or independently verified by
the AP — comes back **opaque with one caveat**: the only check the AP
performs on a keystore reply is a self-consistency hash over bytes it also
receives from us, not a cryptographic proof against a secret we don't have.
Better than that, a **second, independent finding** answers question 4 at the
same time: `AppleSEPKeyStore` already contains a shipped, non-cryptographic
fallback for platforms with no effaceable storage — triggered by the exact
device-tree property this VM already sets.  The implementation results below
replace the original study's static-only qualification.

## Implementation result (2026-09-02)

The `sks` endpoint is sufficient for the encrypted APFS media-key path.  The
implementation is in `qemu-sptm/hw/arm/darwin_sep.c`: IPC v1 framing and the
truncated SHA-256 transport digest are at `sep_sks_hash_response()`, op31
creates a deterministic 64-byte live CPX key plus a 40-byte opaque wrapped
record, and op32 maps that record back to the same live key.  These lengths
come from the consumers, not a guess: APFS copies op31's first blob into the
CPX object at `0xfffffff00a8720e8..0xa872190`, and AppleNVMeRequest accepts
the wrapped/composite form only at length `0x40` at
`0xfffffff00a139230..0xa13925c`.  The rejected 32-byte intermediate is visible
as `Invalid key length for unwrapped key 32` in
`/tmp/dvm/probe/SKS_LIVEKEY_V8A.serial.log:497`.

The fake-key fallback is now confirmed dynamically, with an important scope
qualification.  Merely starting AppleSEPKeyStore does not invoke it: the
endpoint negotiates and reaches a guest shell in
`/tmp/dvm/probe/SKS_V3.stderr.log:81,375..407` and
`/tmp/dvm/probe/SKS_V3.serial.log:363`, while that 388-line serial log has no
`using fake key` entry.  A real guest `newfs_apfs -E -W` key operation does
invoke it at `/tmp/dvm/probe/SKS_LIVEKEY_V9.serial.log:481`; the same run logs
three successful `fs_new_media_key` calls at lines 482, 490 and 497, writes
the media/container keybags at lines 494, 495 and 499, and returns
`GUEST_KEYOP_RC=0` at line 508.

Persistence does not require a second ANS namespace in this machine's
first-party degraded mode.  The requested `DARWIN_ANS_DEBUG=1` diagnostic
shows only Identify (`opcode 0x06`) for `nsid 1` at
`/tmp/dvm/probe/SKS_ANS_DIAG.stderr.log:640,678`; that log has zero instances
of the inactive-namespace message from `darwin_ans.c:720` and zero
`UNMODELLED admin opcode` messages from `darwin_ans.c:897`.  The key-operation
writes are likewise to namespace 1, for example
`/tmp/dvm/probe/SKS_ANS_DIAG.stderr.log:5513`.  Consequently no speculative
effaceable-storage namespace was added to `darwin_ans.c`.

The end-to-end proof uses the guest-created image on a second boot.  op32
restores the key three times in
`/tmp/dvm/probe/SKS_REMOUNT_V10.stderr.log:1585..1597,1687..1699,1769..1781`;
the guest reports successful tags 1, 2 and 13 at serial lines 477..483, obtains
the primary and secondary keys at lines 481 and 484, identifies the volume as
`encrypted` at line 489, and completes the mount at line 491.  The command
returns zero at line 496 and `mount` shows
`/dev/disk1s3 on /private/var (... protect)` at line 500.  No inline-crypto
model was added: this run reaches the requested encrypted-volume outcome
without an ANS error that demands one; the existing omission remains recorded
at `qemu-sptm/hw/arm/darwin_ans.c:134`.

## Question 1 (the crux): opaque or verified?

**Opaque**, on the evidence available. Two separate code paths in
`AppleSEPKeyStore` were traced; neither compares a received value against
anything the AP can compute independently from a secret it holds.

### 1a. The only check on a keystore reply is a self-computed transport hash

`fcn.fffffff00956eab0` (`AppleSEPKeyStore`, called from `fcn.fffffff00956e97c`
at `+0x9e8`) is what runs on every keystore IPC reply. It:

1. Gets a digest context through a stub (`bl fcn.fffffff009582ad0` at
   `0956eaec`, itself a signed indirect jump through a GOT-style slot at
   `0xfffffff00810b330` — no symbol name recovered, but the shape, an
   init/update/finalize triple called with fixed byte counts, is the same
   shape corecrypto's `ccdigest` API always takes).
2. Feeds it five fields from the reply at fixed offsets (`x19+0x10` len 4,
   `+0x14`... in this driver's own decoded copy of the header, not the wire
   header — offsets do not have to match wire layout) via four calls to
   `fcn.fffffff009582a10` (`0956eb30`-`0956eb94`), then a fifth field whose
   length is chosen by a flags test (`0x20` or `0x28` bytes,
   `0956eb98`-`0956ebc4`) plus a caller-supplied `(x23, w24)` pair
   (`0956ebc8`-`0956ebd8`).
3. Finalizes through a **second** vtable call reached via `blraa` with a
   pointer-authentication salt (`movk x17, 0x1bda, lsl 48` at `0956ebf8`,
   `0956ec44`), writes the result into a 16 (or 32) byte stack buffer.
4. Compares 16 bytes of that buffer against the struct's own leading field
   (`fcn.fffffff009582c00(computed, x19, 0x10)` at `0956ec6c`).
5. On mismatch (`cbz w0` **not** taken at `0956ec70`), logs
   `"ipc digest failed"` (`fcn.fffffff00957fe4c`, string at
   `0xfffffff00770668a`) and returns `-0xf` (`0956ed10`).

This is exactly the shape of the `payload_hash` field public reference
`sep-sim.c` implements: `apple_sep_sim_gen_sks_hash()` (their file,
`~line 624`) hashes `ipc_version..end-of-header` plus the payload with
SHA-256 and truncates to the 16-byte `payload_hash` field, computed by the
**simulator itself** over its **own** response — not a secret-keyed MAC, not
a value it has to reproduce from anything Apple-secret. Our disassembly shows
the AP-side driver performing the mirror-image operation: hash what it
received, compare to the field the reply carries. If we (the SEP model)
compute the same hash correctly over the bytes we construct, this check
always passes — it validates transport integrity of *our own* output, not
correctness of key material against a device secret.

The exact digest algorithm was not pinned down (no corecrypto symbol names
resolved through the stub calls); this is an implementation risk, not a
verification risk — see Effort estimate.

### 1b. A shipped, non-cryptographic fallback for exactly our device-tree state

This is the most important single finding of this study. `fcn.fffffff00954a858`,
called from **ten** sites across the driver (`0954ae68`, `0954af2c`,
`0954b0a4`, `009553...`, `009574b00`, `009574cec`, `009574d9c`, `009574ed0`,
`009575a50`, plus a tail-branch at `0955cfe8`) — i.e. it looks like the single
choke point every class-D-key / effaceable-storage-touching operation passes
through — does the following (traced instruction-by-instruction):

1. Calls `fcn.fffffff0095431e0(x0="IODeviceTree:/defaults", x1="no-effaceable-storage", x2=0)`.
2. That helper (also called from 6 other sites in the driver, so it's a
   generic "does this /defaults property exist" utility, not special-cased
   for this property) does `IORegistryEntry::fromPath("IODeviceTree:/defaults")`
   then a property lookup for the name in `x1`; with the third argument (an
   output-value pointer) `NULL`, it returns **`0` if the property is present**,
   **`-1` if the registry path or the property is absent**. (Traced fully:
   `0x9543214`/`0x9543248` return `-1` on "not found"; `0x9543264`/`0x9432d0`
   return `0` on "found, no output requested" — confirmed by reading every
   exit path's `mov w22, ...` and the final `mov x0, x22; retab`.)
3. Back in the caller, `cbz w0, 0xfffffff00954a928` — **branches to the
   fallback exactly when the property is present** (return value `0`).
4. That branch logs, via the standard driver log wrapper,
   `"disabling use of effaceable storage, using fake key, 0x%x"`
   (string at `0xfffffff00770450c`; call site `0954a9cc`), where the `%x`
   value comes from a call through a **statically pointer-signed** function
   reference (`pacia x16, x17` with context `0x392e` at `0954a92c`-`0954a944`,
   target `0xfffffff009547544`, invoked via `bl fcn.fffffff00955d944`) — a
   single 32-bit identifier, not a key-length blob, logged for diagnostics.
5. The alternative branch (property absent) instead dereferences a real
   `AppleEffaceableStorage`-backed object pointer at `x19+0x90`
   (`0954a8b4`-`0954a924`) and calls two further helpers
   (`fcn.fffffff00954e7a0`, `fcn.fffffff00954eaec`) that do real
   effaceable-storage I/O.

**Our device tree already carries this exact trigger.** Decoding
`firmware/dtree` with the repo's own `ADTNode` parser
(`dt_fixup.py`) finds:

```
/device-tree/defaults  no-effaceable-storage  <NULL>   (0-byte, presence-only)
```

which is precisely what step 2's lookup needs — it never inspects the
property's *value*, only whether `IORegistryEntryCreateCFProperty` returns a
non-null object, and a 0-length "boolean-style" property satisfies that.
This is not a speculative match: `docs/re/data-volume.md` already documents
that `no-effaceable-storage` sits on our tree "alongside `cpx-encryption-mode`",
and this study is the first to trace what `AppleSEPKeyStore` *does* with it.

**Runtime qualification:** startup alone does not call the fallback
(`/tmp/dvm/probe/SKS_V3.serial.log`, 388 lines, has no `using fake key`), but
the first real guest key operation does
(`/tmp/dvm/probe/SKS_LIVEKEY_V9.serial.log:481`).  This resolves the original
static-only caveat without claiming the fallback runs before it is needed.

### 1c. What the public reference actually answers with, and what it admits it doesn't know

`sep-sim.c`'s keystore switch (`apple_sep_sim_handle_keystore_msg`) answers
Create/Copy/Load/Unload Keybag and Change Lock State with hand-picked
constants — `'BAG1'` as a keybag handle, 16 bytes of `0xAF` as "Copy Keybag"'s
key payload, comments reading "Mostly guessing" (Change Lock State) and
"Uh... who?" (Null D Key) next to fields it fills with `0`. **Case `0x08`
"KC Wrap" is explicitly not implemented** (commented out, replies with an
empty ack). None of this required knowledge of a real device secret; it is
literally memset/constant construction, matching the "opaque, deterministic
constants work" branch of the crux question. The project's own recent commit
history shows active maintenance of exactly the hash-generation function this
study also found the AP side checking (`0dc228f6`, "sep-sim: fix bug in
gen_sks_hash caused by optimised assert macro", 2026-05-31), which is
circumstantial evidence the path is exercised by real boots in that project,
not dead code — but no README/issue claims an encrypted-volume success and
none was found in a search of that repo's issue tracker for
"FileVault"/"encrypted"/"keystore" (0 hits), so treat this as "the mechanism
is real and actively used," not "someone has already proven the end state we
want."

## Question 2: opcode inventory

The reference implementation's dispatch (`msg_code = tag & 0x7F`) handles 11
distinct message codes plus a default (echo-back) path:

| code | name (from `sep-sim.c` logging) | matching string found in **our** iOS 27 `AppleSEPKeyStore`, with address |
|---|---|---|
| 0x01 | Create Keybag | `"created identity, keybag handle = %d"` @ `0xfffffff007704a5d` |
| 0x02 | Copy Keybag | not matched by string search (payload-only op) |
| 0x03 | Load Keybag | `"loaded keybag, handle = %d"` @ `0xfffffff007704a20` |
| 0x04 | Change Lock State | `"Sending lock state change %s (%d) for handle %d (0x%x)"` @ `0xfffffff00770391a` |
| 0x05 | Unload Keybag | `"unloaded keybag"` @ `0xfffffff007704aa4` |
| 0x08 | KC Wrap | **not implemented even in the reference** |
| 0x0A | Null D Key | plausible match: `"Failed to unwrap class D key. Continuing anyways."` @ `0xfffffff0077048dc` |
| 0x0C | Unwrap D Key | `"Cannot unwrap d_key from effaceable."` @ `0xfffffff007704895` |
| 0x0D | Make System Keybag | not matched by string search |
| 0x19 | Get Device State | `"cant get vek blob state"` @ `0xfffffff007703c03` (adjacent concept) |
| 0x1B | Client Terminate | not matched by string search |
| default | echo the request back with the reply bit set | n/a |

**Count: roughly 10-12 distinct operations**, cross-validated by finding six
of the eleven concept-names as near-identical log strings in our own iOS 27
binary (`AppleKeyStoreHelper.cpp`/`AppleKeyStore.cpp`/`ipc.c` per the
`__TEXT.__cstring` file-path strings already documented in
`docs/re/sep-protocol.md`). This is good evidence the *operation set* is
stable across iOS versions; it is **not** proof the *byte-level opcode
numbers or the 92-byte-vs-84-byte header layout* are unchanged — our own
captured first request (`docs/re/sep-protocol.md`, 92 bytes,
`header_body_size = 0x48`) does not numerically match the reference's
84-byte, `header_body_size = 0x50` constant, which is an open discrepancy
(most likely an `ipc_version`-gated header revision — the kext logs
`"negotiated to ipc header theirs:v%llu, ours:v%u"` at
`0xfffffff00954cd10`, so the format is explicitly versioned) that a full
implementation would need to re-derive from this kernelcache rather than
port from the reference.

**A count of ~10-12 is a project measured in days, not weeks** — this is
the same order of magnitude as `scrd` (2 commands captured) and `xars`/`xarm`
(ack-everything), both of which are already working in `darwin_sep.c`.

## Question 3: what does effaceable storage have to be?

**Moot for the path exercised here.** Finding 1b traces a code path in which
`AppleSEPKeyStore` never touches `AppleEffaceableStorage` at all when
`no-effaceable-storage` is present — which our tree already guarantees. Two
supporting facts:

- `com.apple.driver.AppleEffaceableStorage` has **zero `IOKitPersonalities`**
  in this kernelcache's `__PRELINK_INFO` (parsed directly:
  `{}` for that bundle). It does not independently bind to any device-tree
  node in this configuration, consistent with it being a library the fake-key
  branch is designed to let a platform skip entirely.
- `AppleEffaceableStorage`/`AppleEffaceableBlockDevice` are present as kexts
  (confirmed via `ipsw kernel extract --all`), and the boot logs an
  `EffaceableStorage(1.0)` UUID (per the task brief) — but nothing in this
  study ties that log line to the `sks` code path specifically; it was not
  chased further within the timebox. **Open question**, flagged rather than
  guessed at.

The runtime trace closes the namespace hypothesis: Identify Namespace only
targets nsid 1 at `/tmp/dvm/probe/SKS_ANS_DIAG.stderr.log:640,678`, while the
inactive-namespace branch in `darwin_ans.c:720` and the unmodelled-admin branch
at `darwin_ans.c:897` are absent from the log.  Backing a speculative second
namespace would therefore not model any request made by this key operation.

## Question 4: is there a cheaper legitimate path?

**Yes — and it's the same discovery as 1b.** `no-effaceable-storage` is not
a hack we invented to unblock APFS's encryption check; it is a real Apple
device-tree property that `AppleSEPKeyStore` itself checks and responds to
with a **documented-in-code, first-party degraded mode** ("using fake key").
The work this reframes is not "fake real cryptography" — it's "answer the
`sks` wire protocol well enough that the driver's own already-built no-op
path gets reached," which is a materially smaller and better-defined task
than modelling key derivation.

No other cheaper path was found in the time available: no boot-arg, no
`/chosen` flag analogous to `sepfw-load-at-boot` or `protected-data-access`
that skips data-protection outright was located in `seputil` or
`AppleSEPKeyStore` beyond what `docs/re/seputil-data-protection.md` already
documents (`protected-data-access` skips `gigalocker_init`, not the Data
volume's encryption requirement — those are different subsystems: gigalocker
is xART/`/private/xarts`, the Data volume gate is `apfs_vfsops.c:2399`, fed
by AKS/`sks`, a separate driver). `newfs_apfs`'s host-side switches were not
re-examined here; `docs/re/data-volume.md` already established the volume
mounts and is refused purely for lacking encryption, which is a kernel-side
runtime check, not something `newfs_apfs` controls.

## Result versus the original estimate

The original 3–6 day estimate was conservative.  Live acceptance identifies
the transport digest as SHA-256 truncated to 16 bytes: replies generated by
`sep_sks_hash_response()` are accepted at
`/tmp/dvm/probe/SKS_V3.stderr.log:382..407`, with no `ipc digest failed` in
the 388-line serial log.  The encrypted-volume path needs op31 and op32 plus
the already-observed negotiation and set-environment messages, rather than
all historical operations listed in Question 2; unrecognized messages remain
explicit status-only no-ops in `sep_handle_sks()` and are logged by code and
request ID.

## Open questions

- **What `EffaceableStorage(1.0)` in the boot log actually is**, and whether
  it is on the `sks` path at all (Question 3) — not chased.
- **The remaining status-only messages:** the successful mount still logs two
  `AKS check_class failed with unexpected error = e00002bc` warnings at
  `/tmp/dvm/probe/SKS_REMOUNT_V10.serial.log:486..487`.  They do not block the
  encrypted mount at lines 489..500, but their reply schemas remain unknown
  and `sep_handle_sks()` therefore identifies them as logged no-ops.
- **Inline confidentiality:** `darwin_ans.c:134` records that the controller
  does not implement inline AES.  The guest's APFS encryption state and key
  lifecycle are working (`SKS_REMOUNT_V10.serial.log:477..500`), but the QEMU
  block backend itself does not transform sectors cryptographically.
