# SKS opcode 0x0f: APFS media-key migration to a protection class

## Source metadata

iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140. Primary binary: /tmp/dvm/kexts/com.apple.driver.AppleSEPKeyStore, extracted from firmware/bootkc with ipsw kernel extract; 363,432 bytes, SHA-256 4e94489161384d9fc1b14704ad064e27e674fd2e586055b5bf388a95c92bf273. Addresses below are unslid VAs; runtime uses the project-standard +0x20000000 slide. APFS cross-check: /tmp/dvm/kexts/com.apple.filesystems.apfs, SHA-256 1eb00b3ccafc79e70fb11e62ab34b9b6acfdd27e7118be11eea17e0e4bb5e131. Live witnesses: /tmp/dvm/probe/BOOTSTRAP_SEED_SKSDEBUG.stderr.log:579-617 and /tmp/dvm/probe/BOOTSTRAP_SEED.serial.log:425-451,575.

## Summary

SKS wire opcode 0x0f is the AppleSEPKeyStore path that logs as fs_migrate_media_key_to_class, not fs_new_media_key_wrapped_to_class.
The failing request selects variant 3, supplies a 40-byte opaque media-key record and class 0x0e, and reserves a 64-byte output; the current status-only reply leaves that output at length zero and APFS panics.
Static marshaller evidence derives a one-blob, 64-byte variant-3 reply, but no real SEP reply has been captured, so the proposed byte layout needs the acceptance run below before it is treated as confirmed protocol.

## Register/protocol/layout evidence

| Item | Observed or derived behavior | Evidence |
| --- | --- | --- |
| Wire operation | Opcode 0x0f is passed to transport at 0xfffffff00957bc08; its enclosing wrapper is called from 0xfffffff009574e48. | 0xfffffff00957bc00-0x957bc18 loads w1 = 0x0f immediately before the signed transport call; live request at BOOTSTRAP_SEED_SKSDEBUG.stderr.log:579-595. |
| Operation name | High-level wrapper is fs_migrate_media_key_to_class. This corrects the false association with fs_new_media_key_wrapped_to_class, which calls distinct opcode 0x31 at 0xfffffff00957d6e4. | Migration wrapper 0xfffffff009573024-0x95731a0 logs cstring 0xfffffff007703f68; it calls 0xfffffff009574e48, then 0xfffffff00957bc08. New-key cstring 0xfffffff007703ebd, function 0xfffffff0095731a4, bridge 0xfffffff00957d6e4. |
| Request header | IPC v1 has 76 bytes: u32 body-size 0x48 at +0, digest[16] at +4, u32 version 1 at +0x14. Reply digest is SHA-256 over [+0x14,+0x4c) followed by body, truncated to 16 bytes at +4; it is not a secret MAC. | Live bytes: BOOTSTRAP_SEED_SKSDEBUG.stderr.log:582-593. Model mirror: qemu-sptm/hw/arm/darwin_sep.c:340-356,823-848. AP-side digest trace: docs/re/sks-feasibility.md “Question 1a.” |
| Body +0x4c | u32 variant = 3. | BOOTSTRAP_SEED_SKSDEBUG.stderr.log:587-588. |
| Body +0x50..+0x67 | Fixed in this request: three u32 values 1, 0, 0, then u64 UINT64_MAX. Field names are unverified. | BOOTSTRAP_SEED_SKSDEBUG.stderr.log:588-589. |
| Target class | u32 0x0e at +0x6c. APFS takes class-0x0e path and selects argument 3 at 0xfffffff00a8723d0-0xa8723f8; symbolic class name unverified. | Request line 589; APFS dispatch 0xfffffff00a8723d0-0xa872400. |
| Input record | u32 record_len=0x28 at +0x78 then opaque record[40] at +0x7c..+0xa3. | BOOTSTRAP_SEED_SKSDEBUG.stderr.log:590-593; successful unwrap treats the same form as opaque at qemu-sptm/hw/arm/darwin_sep.c:1048-1067. |
| Output capacity | u32 0x40 at +0xa4, followed by zero at +0xa8. The final four request bytes are not printed by the 172-byte debug truncation. Migration wrapper allocates/passes 0x40. | Request line 593; the logger caps its dump at header + 96 in qemu-sptm/hw/arm/darwin_sep.c:942-950; wrapper AppleSEPKeyStore 0xfffffff0095730f8-0x957310c. |
| Reply plumbing | The 0x0f marshaller has one pointer/length output pair. It initializes local var_148/var_150 at 0xfffffff00957bbcc-0x957bbfc and publishes it at 0xfffffff00957bc20-0x957bc60; no second pair or returned scalar is copied. | AppleSEPKeyStore instructions cited. |
| Derived reply | u32 variant=3; u32 output_len=0x40; output[64]: 72-byte body and 148-byte OOL reply (0x4c+0x48). This is not dynamically confirmed. | Smallest layout matching selected variant, single output pair, and capacity. Existing 0x10/0x31/0x32 replies echo their variants at qemu-sptm/hw/arm/darwin_sep.c:989-1069; negative control is 80-byte status-only reply at stderr:594-595. |
| Key material | First control is existing stable 64-byte sks_media_key. It must survive reboot; no AP-side secret comparison is known. | Fake-key mode at BOOTSTRAP_SEED.serial.log:427-439; stable material remount control in docs/re/sks-feasibility.md “Implementation result.” |
| Failure semantics | Zero output length is not success: APFS panics in apfs_crypto_state_init. | First panic BOOTSTRAP_SEED.serial.log:575; immediate predecessor is status-only reply at stderr:594-595. |
| Cross-reboot state | No extra secret/persistent state is justified in fake-key mode. Preserve deterministic 0x31/0x32 material; do not generalize this claim to real SEP mode. | Fake-key and second-boot controls cited above. |

### Write-only or ignorable fields

None can safely be called ignorable. The 40-byte input is opaque to the model but protocol-significant; variant and class must remain parser-selected. The fixed-looking scalars at +0x50..+0x77 remain unlabelled rather than declared write-only.

## Device-modeler specification

Add SKS_MIGRATE_MEDIA_KEY_TO_CLASS for wire code 0x0f in qemu-sptm/hw/arm/darwin_sep.c; do not alter APFS or bootstrap scripts. Validate the existing v1 header and require the captured 0xb0-byte request, variant 3, record length 0x28, and output capacity 0x40; log a distinct rejection for another shape rather than silently succeeding.

For the captured shape, construct a 148-byte reply with existing identity-copy and SHA-256 helpers and body { le32(3), le32(64), sks_media_key[64] }, on the same endpoint/tag/id. Do not parse, rewrite, or persist the 40-byte record and do not create another output; if acceptance produces a parser error or another output length, replace this inferred body with captured evidence rather than padding speculatively.

Acceptance needs all three independent witnesses:

1. SEP stderr logs code 0x0f, request length 176, variant 3, and a 148-byte SHA-256-authenticated reply with 64-byte output.
2. Serial log has no apfs_crypto_state_init invalid-key-length panic, proceeds past former line 575, and retains disk1s2 encrypted/protect mount evidence.
3. Following 0x32 unwraps still show 64-byte keys; a cold reboot repeats migration length with no transport-digest error.

## Open questions

| Open question | Observation that settles it |
| --- | --- |
| Is {variant 3, len 64, bytes} exact? | Capture real SEP 0x0f OOL reply, or run derived 148-byte response and verify no decoder error plus a 64-byte APFS result. |
| What are +0x50..+0x77 and what is class 0x0e called? | Recover type metadata for fs_migrate_media_key_to_class, or compare captures for two requested classes. |
| Does a later path compare key and record-specific material? | Boot through mount-phase-2 and cold-boot, then compare two deliberate stable 64-byte values and record the first divergent check. |
| Is state needed outside fake-key mode? | Repeat with no-effaceable-storage absent and a real effaceable backend; this note makes no claim for that configuration. |
