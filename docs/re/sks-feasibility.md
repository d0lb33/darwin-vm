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
device-tree property this VM already sets — that a live boot has not yet been
observed to reach, but that static analysis traces cleanly to our current
tree state.

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

**Caveat, stated precisely so it isn't overclaimed:** this is a static trace,
not an observed runtime hit — `sks` is not currently advertised
(`darwin_sep.c`'s `sep_default_eps`), so this function has never actually run
in a boot we've captured. The claim is "the code path exists and our tree
already satisfies its trigger condition," not "we have watched it fire."
Confirming that is the cheapest first experiment for whoever picks this up
(see Effort estimate).

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

**Possibly moot for this path.** Finding 1b traces a code path in which
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

If 1b turns out not to fire at runtime (the open caveat above), the fallback
position is exactly what `docs/re/data-volume.md` already scoped: back a
small NAND-sized region behind `AppleEffaceableStorage`'s reg range. That
remains a bounded, previously-scoped task and is not blocked by anything
found here.

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

## Effort estimate

**3-6 agent-days**, in this shape:

1. (0.5 day) Confirm 1b actually fires: advertise `sks`, let
   `AppleSEPKeyStore` reach the point where it would call
   `fcn.fffffff00954a858` (needs at minimum the version-negotiation reply and
   Create Keybag to succeed first, or it may never reach a class-D-key
   operation at all), and grep the serial log for
   `"disabling use of effaceable storage, using fake key"`. This is the
   single highest-value confirming experiment and should run before anything
   else here.
2. (1-2 days) Nail the header layout and digest algorithm for iOS 27
   specifically: reconcile the 92-byte-vs-84-byte discrepancy, identify the
   corecrypto digest the vtable in 1a resolves to (try SHA-256/16 first,
   since that's the reference's choice and the field width matches; iterate
   against the guest's own `"ipc digest failed"` log line as the pass/fail
   oracle exactly like the `A401`/`bootCompleteGated()` gradient described in
   `CLAUDE.md`'s DCP section).
3. (1-2 days) Implement the ~10-12 opcodes deterministically in
   `darwin_sep.c`, following the pattern already proven for `scrd`/`xars` —
   build outward from Create Keybag / Load Keybag / Unload Keybag, since
   those are the three with the cleanest string match to our own kernelcache.
4. (0.5-1 day) Iterate `tools/probe.sh` boots to `apfs_vfsops.c:2399` and past
   it; expect at least one more gate beyond encryption (per-file `cprotect`,
   Keychain) that this study did not scope.

**Riskiest unknown:** whether the digest in 1a is a plain hash (as the
reference assumes and this study's static trace is consistent with) or
something keyed that the SEP alone could compute — the disassembly shows a
vtable-dispatched digest object, not an inline HMAC construction, which
argues for "plain hash," but the exact corecrypto function was not resolved
by symbol name and this was not empirically confirmed against a real device
capture (only requests were captured in `docs/re/sep-protocol.md`, never a
reply). If it turns out to be keyed with something we cannot reproduce, step
2 above is where that would surface, at low cost (a boot that keeps logging
"ipc digest failed" no matter what's tried), not at the end of the project.

## Open questions

- **Does 1b actually fire at runtime?** Static trace only; needs the
  confirming experiment in step 1 of the effort estimate.
- **Exact digest algorithm and byte ranges for the `payload_hash` check**
  (1a) — corecrypto symbol not resolved from the stub calls; needs either
  more vtable tracing or the iterative-boot approach.
- **Header layout for iOS 27**: our captured 92-byte request vs. the
  reference's 84-byte struct do not numerically agree; the `ipc_version`
  negotiation log (`0xfffffff00954cd10`) confirms the format is versioned,
  so this is a "re-derive for this iOS," not "the reference is wrong."
- **Exact byte-level `msg_code` numbering for iOS 27** — the opcode *names*
  cross-validate against our kernelcache's own log strings, but the specific
  case values (0x01, 0x02, ...) were taken from the reference, targeting an
  older iOS/device, and not independently re-derived from our binary's own
  switch table within this timebox.
- **What `EffaceableStorage(1.0)` in the boot log actually is**, and whether
  it is on the `sks` path at all (Question 3) — not chased.
- **What happens after encryption succeeds** — per-file `cprotect` and
  Keychain both lean on data protection per `docs/re/data-volume.md`; this
  study only scoped getting past the one panic at `apfs_vfsops.c:2399`, not
  the full data-protection surface.
