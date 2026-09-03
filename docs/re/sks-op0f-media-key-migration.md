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

## Follow-on opcode 0x10 requirement

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

The model therefore returns deterministic 16-byte file and IV keys in the first
two blobs, then three zero-length blobs and two zero scalars. This is the
smallest runtime-supported response and does not claim persistent SEP secrets.

## Final acceptance

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

## Remaining blocker

The SKS migration and key lengths are no longer the first failure. The seed
phase reaches mount-phase-2 with the real encrypted Data volume mounted, but
copies zero template files. Early userspace then panics with:

```
Creating classD marker file in /var/keybags in early boot task failed
```

The final cold witness is
`/tmp/dvm/probe/BOOTSTRAP_SEED_SKS_FINAL_COLD.serial.log:586`. This demonstrates
that mount-phase-2 does not populate a completely empty real Data volume merely
because it is mounted. Supplying the initial Data filesystem layout is a
separate bootstrap-design problem; it is not evidence for more SKS opcode
guessing.

## Scope limits

The 40-byte record remains opaque and is never parsed or rewritten. Stable test
material is intentional under `no-effaceable-storage`; this model makes no
claim about real SEP secret generation or persistence. The names of the
unlabelled request fields and the semantics of later status-only opcodes such as
`0x19` and `0x04` remain unverified.
