# SKS opcode 0x0f: APFS media-key migration to a protection class

## Source metadata

iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140. Primary binary:
`/tmp/dvm/kexts/com.apple.driver.AppleSEPKeyStore`, extracted from
`firmware/bootkc` with `ipsw kernel extract`; 363,432 bytes, SHA-256
`4e94489161384d9fc1b14704ad064e27e674fd2e586055b5bf388a95c92bf273`.
Addresses below are unslid VAs; runtime uses the project-standard
`+0x20000000` slide. APFS cross-check:
`/tmp/dvm/kexts/com.apple.filesystems.apfs`, SHA-256
`1eb00b3ccafc79e70fb11e62ab34b9b6acfdd27e7118be11eea17e0e4bb5e131`.

The decisive live witnesses are:

- accepted request and reply: `/tmp/dvm/probe/BOOTSTRAP_SEED_OP0F_ACCEPTED_FINAL.stderr.log:583-598`
- APFS keybag writes and successful migration: `/tmp/dvm/probe/BOOTSTRAP_SEED_OP0F_ACCEPTED_FINAL.serial.log:420-446`
- encrypted Data mount: the same serial log at lines 449-465
- final cold-reboot control: `/tmp/dvm/probe/BOOTSTRAP_SEED_SKS_FINAL_COLD.stderr.log:565-641` and `.serial.log:428-450,586`

## Result

Wire opcode `0x0f` is `fs_migrate_media_key_to_class`. The captured request is
variant 3, contains a 40-byte opaque wrapped-key record, targets class 14, and
provides capacity for a record of up to 64 bytes. The accepted reply is not the
initially inferred 148-byte `{variant, len=64, key[64]}` message. It is a
128-byte authenticated IPC v1 message whose 52-byte body is:

```
le32(3)                         response union selector
le32(40)                        returned record length
sks_wrapped_key[40]             stable opaque wrapped record
le32(14)                        returned class
```

The request's `64` is output capacity, not the required returned length. APFS
rejects a migrated record length of `0x39` or greater at
`0xfffffff00a875648..0xfffffff00a875660`. The returned scalar must match target
class 14 at `0xfffffff00a875598..0xfffffff00a8755a4`.

This shape is confirmed behaviorally. The guest records result zero for
`fs_migrate_media_key_to_class`, writes both media and container keybags,
reports migration success, unwraps the resulting records with opcode `0x32`,
and mounts `disk1s2` at `/private/var` with `protect` enabled.

## Request layout and validation

| Absolute offset | Size | Captured value | Treatment |
| --- | ---: | --- | --- |
| `+0x00` | 4 | header body size `0x48` | require IPC v1 header shape |
| `+0x04` | 16 | truncated SHA-256 digest | verify with existing helper |
| `+0x14` | 4 | IPC version `1` | require |
| `+0x4c` | 4 | variant `3` | require |
| `+0x50` | 4 | `1` | require |
| `+0x54` | 4 | `0` | require |
| `+0x58` | 4 | `0` | require |
| `+0x5c` | 4 | zero alignment slot | require; the following u64 is aligned |
| `+0x60` | 8 | `UINT64_MAX` | require |
| `+0x68` | 4 | `0` | observed but not assigned a semantic name |
| `+0x6c` | 4 | target class `14` | require |
| `+0x70` | 8 | zero | observed but not assigned semantic names |
| `+0x78` | 4 | record length `40` | require |
| `+0x7c` | 40 | opaque record | preserve opaque semantics |
| `+0xa4` | 4 | output capacity `64` | require |
| `+0xa8` | 4 | `0` | require |
| `+0xac` | 4 | request-side scalar `0` | require |

The whole request must be exactly 176 bytes. Unsupported lengths, versions,
variants, classes, fixed fields, or record boundaries are logged and rejected;
they must not receive a status-only success.

Static identity and plumbing evidence:

- the high-level wrapper at `0xfffffff009573024..0xfffffff0095731a0`
  logs `fs_migrate_media_key_to_class`, calls `0xfffffff009574e48`, and reaches
  the opcode-`0x0f` transport call at `0xfffffff00957bc08`
- this is distinct from `fs_new_media_key_wrapped_to_class`, which uses opcode
  `0x31` at `0xfffffff00957d6e4`
- the generated `0x0f` bridge publishes one pointer/length output and one u32
  output at `0xfffffff00957bbcc..0xfffffff00957bc60`
- IPC v1 has a 76-byte header; its 16-byte authentication field is SHA-256 over
  `[+0x14,+0x4c)` followed by the body, truncated to 16 bytes

## Runtime controls

The first implementation followed the incomplete static inference. It
authenticated correctly but failed semantically:

| Reply control | Result |
| --- | --- |
| 148 bytes, variant 3, length 64, key[64], no scalar | Guest returned `e00002bc`; `/tmp/dvm/probe/BOOTSTRAP_SEED_OP0F_148_NEG.stderr.log:595-598`, `.serial.log:420-422` |
| 152 bytes, same key plus trailing scalar 0 | Still rejected; `/tmp/dvm/probe/BOOTSTRAP_SEED_OP0F_152_SCALAR0_NEG.*` |
| 128 bytes, record length 40, class 14, reply variants 0-2 | Transport decoded, but APFS rejected the semantic result; `/tmp/dvm/probe/BOOTSTRAP_SEED_OP0F_SWEEP_VARIANT{0,1,2}.*` |
| 128 bytes, record length 40, class 14, reply variant 3 | Accepted; three requests at stderr lines 583-598, 630-645, and 655-670 |

The accepted boot then supplies 64-byte live keys through opcode `0x32` at
stderr lines 680-714. This distinction is important: opcode `0x0f` returns the
40-byte wrapped record; opcode `0x32` unwraps that record into the 64-byte CPX
key.

## Follow-on opcode 0x10 requirements

Acceptance of `0x0f` exposed a later, separate `apfs_crypto_state_init` panic.
The existing opcode-`0x10` status reply encoded five empty blobs. Static APFS
checks and runtime mask controls show that reply variant 2 requires exactly the
first two outputs to contain 16-byte keys:

- output 0 only (`mask 0x1`) gets past the main key check but panics with
  `invalid iv key length (0)` at
  `/tmp/dvm/probe/BOOTSTRAP_SEED_OP10_SWEEP_MASK1.serial.log:570`
- outputs 0 and 1 (`mask 0x3`) eliminate both invalid-key panics and advance to
  the class-D marker failure at
  `/tmp/dvm/probe/BOOTSTRAP_SEED_OP10_SWEEP_MASK3.serial.log:585`
- APFS enforces the two 16-byte lengths at
  `0xfffffff00a915158..0xfffffff00a915174`

The two keys alone are sufficient for the 112-byte class-availability request,
but not for the 140-byte protected-object request later issued by
`get_new_crypto_id`.  The latter request is the variant-2 IPC v1 shape captured
at `/tmp/dvm/probe/DATA_SEED_VISIBLE.stderr.log:1573-1585`.  Its requested class
is a little-endian u32 at absolute request offset `+0x60`; class 4 is visible in
that capture.  The remainder of the validated shape is:

| Absolute offset | Size | Captured value | Treatment |
| --- | ---: | --- | --- |
| `+0x4c` | 4 | selector `2` | require |
| `+0x50` | 4 | `1` | require |
| `+0x54` | 4 | `0` | require |
| `+0x58` | 4 | `UINT32_MAX` | require |
| `+0x5c` | 4 | `0` | require |
| `+0x60` | 4 | requested class `3` or `4` | return in both output scalars |
| `+0x64` | 4 | tag `2` | require |
| `+0x68` | 4 | size `0x1c` | require |
| `+0x6c` | 4 | varying opaque value | preserve opaque semantics |
| `+0x70` | 4 | `0` | require |
| `+0x74` | 24 | fixed captured object tail | require byte-for-byte |

The complete protected-object request is exactly 140 bytes.  The shorter
availability form is exactly 112 bytes, requests class 1, and has zeroes after
`+0x60`.  Other lengths, fixed fields, selectors, and classes are rejected with
a distinct log and no reply; an unsupported request must not become a silent
success.

The variant-2 reply remains the 140-byte authenticated message established by
the key-length controls: five length-prefixed blobs of lengths
`{16,16,0,0,0}`, followed by two little-endian u32 outputs at payload offsets
`+56` and `+60`.  The protected-object response must place the requested class
in both trailing outputs.  Before this change both were zero, so APFS compared
returned class 0 with requested class 4 at runtime
`0xfffffff02a91c200` and reached the EPERM paths at
`0xfffffff02a915300` and `0xfffffff02a915344`; the resulting failures are
`get_new_crypto_id returned error 1` and named-stream creation failure at
`/tmp/dvm/probe/DATA_SEED_VISIBLE.serial.log:2901-2902`.  The 112-byte request
retains zero output scalars because no runtime evidence requires class echo for
that form.

The implementation therefore returns deterministic 16-byte file and IV keys
in the first two blobs, three zero-length blobs, and—only for the validated
140-byte form—two copies of the requested class.  This is the smallest
runtime-supported response and does not claim persistent SEP secrets.

### Opcode 0x10 runtime acceptance

A fresh disposable overlay verifies the formerly failing ordinary-file and
named-stream copies without relaxing `COPYFILE_ALL`, xattrs, cprotect, or the
helper's fail-closed checks:

- the class-3 and class-4 protected-object requests are accepted and receive
  matching output scalars in
  `/tmp/dvm/probe/DATA_SEED_OP10_CLASSFIX2.stderr.log:712-715,1763-1766,1800-1803`
- the ordinary decmpfs file has matching bytes, metadata, and decmpfs xattr at
  `.serial.log:3507-3512`
- the 3,802-byte ResourceFork is byte-identical at lines 3516 and 3530; the
  containing file's bytes, metadata, and protection witnesses pass at lines
  3532-3537, and the helper exits zero at line 3538
- neither log contains `get_new_crypto_id`, `dstream xattr`, or a first panic

The child overlay grew from 197,016 bytes to 3,276,800 bytes, independently
showing that the guest wrote the Data filesystem.

A fresh full-seed child then reports `AKS=0`, `MKB=0`, 5,993 copied files, and
`UML=0` at
`/tmp/dvm/probe/DATA_SEED_FULL_CLASSFIX.serial.log:2866-2880`, with no named-
stream or `get_new_crypto_id` failure.  The overlay grew from 197,016 bytes to
more than 783 MB.  The helper nevertheless exits 1 at line 2881 before its own
marker witness; a guest shell can create the marker and sync it at line 2898.
This latter result is recorded as incomplete, not promoted to helper success.

## Media-key acceptance

1. The accepted boot logs request length 176, variant 3, class 14, record length
   40, capacity 64, and an authenticated 128-byte reply
   (`BOOTSTRAP_SEED_OP0F_ACCEPTED_FINAL.stderr.log:583-598`).
2. It writes APFS media and container keybags and reports successful migration
   (`.serial.log:420-446`).
3. It obtains primary and secondary volume keys and mounts encrypted/protected
   `disk1s2` on `/private/var` (`.serial.log:449-465`).
4. Following opcode-`0x32` operations continue to return 64-byte keys
   (`.stderr.log:680-714`).
5. Two final boots with no test overrides repeat the encrypted/protected mount.
   The cold control retains 64-byte opcode-`0x32` outputs and authenticated
   opcode-`0x10` replies (`BOOTSTRAP_SEED_SKS_FINAL_COLD.stderr.log:565-641`)
   without a transport digest, keystore timeout, invalid-key-length, or invalid
   IV-key-length failure.

## Current persistent-Data blocker

The file-copy blocker is resolved: a normal system-volume boot uses
`disk1s1` as root, mounts encrypted/protected `disk1s2` on `/private/var`, and
runs mount-phase-2 without a `Copying` line
(`/tmp/dvm/probe/PERSIST_DATA_BOOT1.serial.log:285,406,441-448`).  That proves
the populated Data filesystem is being consumed rather than re-seeded.

The first remaining failure is keybag identity provisioning.  The seeded
filesystem contains the copied `.bootstrapped` marker and several keybag files,
but no `/private/var/keybags/systembag.kb`; `keybagd` reports that exact absence
at `PERSIST_DATA_BOOT1.serial.log:503-505`.  `usermanagerd` later reports
`UMD:FATAL OTI LOAD ERROR ... -536870212` at line 533 and requests recovery.
The eventual `Halt/Restart Timed Out` panic at line 614 is only the known
reboot consequence.  Opcode `0x0f`, both opcode `0x10` forms, and opcode `0x32`
remain healthy in the corresponding stderr log.

The route to the identity failure includes status-only SKS operations whose
payloads and side effects have not yet been decoded.  Their response schemas
must be established from AppleSEPKeyStore firmware/static evidence and live
wire captures before implementation; returning guessed success would recreate
the silent-no-op failure mode this bootstrap is designed to prevent.

As a control only, removing `umVolumeMigration-inprogress.kb` in a child
overlay and enabling the existing diagnostic keybag skip advances past
`usermanagerd`, then stops at a separate missing-data failure in `tzinit`
(`/tmp/dvm/probe/PERSIST_DATA_SKIP_BOOT1.serial.log:552-555`).  This is not a
normal-boot acceptance and is not evidence that keybag provisioning can be
skipped.

## Scope limits

The 40-byte record remains opaque and is never parsed or rewritten. Stable test
material is intentional under `no-effaceable-storage`; this model makes no
claim about real SEP secret generation or persistence. The names of the
unlabelled request fields and the semantics of later status-only opcodes such as
`0x19` and `0x04` remain unverified.


## User-volume class 1 during app installation (2026-09-04)

`DISPLAY_NATIVE_R2` reached protected temporary-file writes in
installcoordinationd after the native DCP state and first-unlock fixes. The
existing parser accepted User-volume class 4 only. It silently dropped a
164-byte variant-3 request for User-volume class 1 at device-model log line
136312. Later op10 callers slept waiting for the request pool at kernel static
`0xfffffff00b240f98..0xb241004`, beneath
`AppleSEPKeyStore 0xfffffff00954ba04`; the guest ultimately panicked with
`AppleSEPManager ... sks request timeout`.

The endpoint-18 OOL buffer survived checkpointing. Host LLDB recovered it with
`sep_dma(..., false)` in `DISPLAY_SKS_CLASS1_R1`, without executing guest code.
The exact capture is `/tmp/dvm/DISPLAY_SKS_CLASS1_R1.request.bin`, SHA-256
`ee5e8c660940ad2f63da2fc0d47b476d9a7aa070980192a36e80c3e48b28155b`.
It carries variant 3 at +0x4c, record kind 3 at +0x68, class 1 at +0x6c,
record length 28 at +0x84, and the existing User-volume fixed bytes at +0x90.
The zero record length in the rejection log was a parser diagnostic limitation:
that field had only been populated for the longer wrapped-key shape.

The native parser now also accepts class 1 with that exact User record shape.
It preserves class 1 in the existing variant-3 response scalar. Capture tests
pin the digest and reject corruption, truncation, and unobserved class 2. `DISPLAY_NATIVE_R4` restores the healthy pre-call boundary and verifies the
native consumer: the model accepts class 1 and emits a 128-byte authenticated
reply at stderr lines 70–73; the guest returns at runtime
`0xfffffff02957bc18` with `x0=0`, output length 40, and scalar class 1
(`SKS_OP0F_RETURN`, `/tmp/dvm/DISPLAY_NATIVE_R4.guest-lldb.log`). No host or
guest override was used. This establishes the SKS round trip, not completion
of all data migration or display rendering. `DISPLAY_NATIVE_SKS_WAIT2` contains the
post-timeout panic and must not be used as a healthy resumable seed.


`DISPLAY_PREOP0F_READY3` is the healthy replacement checkpoint: native DCP,
correct DER state, class-1-aware parser, and the original migrated seed lineage.
The guest is stopped at static `0xfffffff00957bc14` immediately before the
op0f call. `searchd` is the caller in this replay; its decoded request has
kind 3 and class 1. Capture took 1.545 seconds for migration / 11.703 seconds
including evidence and hashes. Restore this boundary for further SKS experiments
instead of repeating userspace initialization.


The next request, captured in `/tmp/dvm/DISPLAY_NATIVE_R4.request.bin`
(SHA-256 `99ab23ebcc515139ece21cfa832d7d3fc3ec3e1c5083fd1be00625365e3815ea`),
has +0x68=1, +0x6c=3, with the same User-volume fixed record. The historical
`record_kind` name cannot mean a fixed volume type. This is the reverse class
transfer: `fs_migrate_media_key_to_class` loads the input record's class from
`input+0xc` at `0xfffffff0095730a0` / `0xfffffff0095730c4`, forwards it alongside
the target at `0xfffffff0095730f0`, and writes the returned class into
`output+0xc` at `0xfffffff009573118..11c`.

The native model accepts the captured 1→3 User transfer too. `DISPLAY_NATIVE_R5`
restores the same pre-call checkpoint and records both successful guest decoder
returns in 81 ms: `SKS_OP0F_RETURN` hits 1 and 2 have status 0, length 40, and
classes 1 and 3 respectively. Hit 3 starts another 3→1 transfer. All are normal
native replies; no debugger writes or injected mailboxes are involved. The
read-only log guard froze R4 on its first rejected SKS request before timeout,
so this second investigation required no repeated userspace initialization.


After class-13 op10 is fixed, `DISPLAY_NATIVE_R8` captures the reverse of the
original User 3→4 transfer: **User 4→3**. Request
`/tmp/dvm/DISPLAY_NATIVE_R8.request.bin`, SHA-256
`b888d00d543ad02af54d71ffc28b2f288959a9b38d481ffc1bde5d5183a74334`, has
`+0x68=4`, `+0x6c=3`, and the exact User fixed record at `+0x90`. The parser
adds this pair while retaining the record and framing checks. Its captured
replay test brings SKS to 17 passing tests (`DISPLAY_USER43.tests.log`).

`DISPLAY_NATIVE_R9` consumes that reply natively in `audioaccessoryd`:
`SKS_OP0F_RETURN` hit 1 reports `x0=0`, record length 40 and output class 3
at runtime `0xfffffff02957bc18`. Evidence is
`/tmp/dvm/DISPLAY_NATIVE_R9.events/progress.SKS_OP0F_RETURN.json` and its
`guest-lldb.log`; no guest override was used.
