# SKS opcode `0x10`: protected-object UUID request on the User volume

This note covers iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140. The
AppleSEPKeyStore bridge is modeled in
`qemu-sptm/hw/arm/darwin_sep.c:424-451,1099-1170,1356-1386`.

## Wire shape

The long form is a 140-byte AppleSEPKeyStore IPC-v1 request. Its common
header has body size `0x48` and version `1`; the union selector at `+0x4c`
is `2`. The fixed words at `+0x50..+0x5c` are `{1, 0, UINT32_MAX, 0}`.
The requested protection class is the little-endian u32 at `+0x60`.

For the protected-object form, `+0x64` is tag `2`, `+0x68` is size `0x1c`,
`+0x70` is zero, `+0x74` is the u16 value `1`, and `+0x76..+0x85` is an
opaque 16-byte APFS volume UUID. The six bytes at `+0x86..+0x8b` are zero.
The implementation checks these fields and rejects other shapes without a
reply (`darwin_sep.c:1117-1165`). It deliberately does not compare the UUID
against a fixed Data-volume value: the User capture and Data control carry
different UUIDs (`/tmp/dvm/probe/SKS_OP10_CAPTURE.stderr.log:710-720` and
`/tmp/dvm/probe/DATA_SEED_OP10_CLASSFIX2.stderr.log:702-715`).

The short 112-byte availability form remains accepted only for class 1;
it is a distinct zero-tag/zero-size shape (`darwin_sep.c:1133-1137`).

## Validated classes

The long-form validator now accepts protection classes **2, 3, and 4**
(`darwin_sep.c:1144-1151`). This is narrow shape validation, not a claim
that every possible class is supported. Class 2 is witnessed by the clean
persistent-NVMe boot log:

`/tmp/dvm/probe/PERSIST_NVME_ROLES_PREINIT_TZ1_CLASS2_BOOT2.stderr.log:971-974`

The same log records successful class-4 replies at lines 142-149 and later
requests; the full seed control records successful class-3 and class-4
replies at `/tmp/dvm/probe/DATA_SEED_FULL_CLASSFIX.stderr.log:707-720,1643-1679`.
The User-volume capture shows the class-3 request shape at
`SKS_OP10_CAPTURE.stderr.log:710-720`; the implementation accepts that same
long-form shape, with its UUID treated as opaque. A successful class-3
acceptance is recorded in the Data control at
`DATA_SEED_OP10_CLASSFIX2.stderr.log:712-715`.

On acceptance, the fake-key response is a 140-byte authenticated IPC-v1
message. The body begins with selector `2`, supplies two 16-byte keys and
three empty blobs, then (for the long form) writes the requested class into
both trailing u32 scalars (`darwin_sep.c:1359-1386`). The APFS-side rationale
for those scalars is recorded in the source comment at
`darwin_sep.c:1368-1375`.

## What this proves

The SKS model can answer the observed long-form op10 requests needed by the
persistent Data/User-volume boot path for classes 2, 3, and 4. The evidence
proves request acceptance and authenticated replies; it does not prove SEP
cryptographic equivalence or establish semantics for other protection
classes. The per-volume UUID and the u32 inside the tagged object remain
request data, not constants.

## Class 13 after native display and first-unlock progress (2026-09-04)

`DISPLAY_NATIVE_R5` resumes the migrated seed through native DCP state ACKs,
valid SKS device-state DER, and User-volume 3→1/1→3 media-key transfers. Its
next unsupported request is a 140-byte op10 class-13 protected-object query:

- Rejection: `/tmp/dvm/checkpoints/DISPLAY_PREOP0F_READY3/restores/DISPLAY_NATIVE_R5/qemu.stderr.log:105217`.
- Exact OOL capture: `/tmp/dvm/DISPLAY_NATIVE_R5.request.bin`.
- SHA-256: `a5b8ab23539ecd0216892fdc86974cbc928896178d107b66df25129f409075b4`.
- `+0x60=13`; all existing framing checks match. The object has the same
  User-volume tagged UUID bytes, and a new opaque object identifier at `+0x6c`.

The failure guard stopped QEMU immediately on the rejection, before the shared
SKS request pool could time out. The validator adds class 13 to its existing
long-form class list (1, 2, 3, 4, 17), retaining the object checks and existing
fake-key response contract. `DISPLAY_NATIVE_R7` now proves native acceptance: caller `managedassetsd`,
status 0, two 16-byte keys, and class scalars `{13,13}` at kernel runtime
`0xfffffff02957be84` (event
`/tmp/dvm/DISPLAY_NATIVE_R7.events/progress.SKS_OP10_AFTER_CLASS13.json`).
The exact capture and malformed/old-form tests pass: 16/16 SKS tests in
`/tmp/dvm/DISPLAY_CLASS13.tests.log`.
The older list above predates classes 1 and 17, documented in the source's
`sep_sks_validate_check_class_request` comment.

The generated op10 call is at kernel static `0xfffffff00957be80`. Its in-memory
request structure has the class at `x2+0x74` (wire `+0x60`); observed class 3
matches both at the first `DISPLAY_PRECLASS13_R6` hit. This gives a read-only
conditional breakpoint for a healthy pre-class-13 checkpoint.

`DISPLAY_PRECLASS13_READY6` is a healthy checkpoint taken during execution
before any class-13 request, not at the exact conditional breakpoint. The
later `DISPLAY_CLASS13_DONE7` checkpoint captures the successful native return.
Both preserve the migrated seed's disk lineage; neither includes a dropped
SKS request or kernel panic.

`DISPLAY_NATIVE_R8` then reaches the previously blocked installation-group
return natively at runtime `0x100af247c`, event
`/tmp/dvm/DISPLAY_NATIVE_R8.events/progress.IC_PENDING_INSTALLS_COMPLETED.json`.
The blocked coordinator was `0x101404b90`, identified as `com.apple.iBooks`
through its seed (`+0xe0`), identity (`seed+0x28`) and bundle ID
(`identity+0x10`; `-[MIAppIdentity bundleID]` static `0x1aae23884`). Its
outstanding group at `+0xf8` had state `0xfffffffd` at group `+0x30`, one
outstanding operation per `_dispatch_group_enter` static `0x1ae8bb368..374`.
This establishes progress after the fix, not exclusive causality between
managedassetsd's class-13 reply and iBooks' completion.


The remaining migration work is a finite coordinator cleanup, not a proven
fixed hang. In `DISPLAY_NATIVE_R9`, the second coordinator is
`com.apple.Posters.WeatherPosterApp` (`0x76a0ce8800`). It naturally passes the
same group wait at `IC_PENDING_INSTALLS_COMPLETED` hit 2. The enumerated array
at `0x76a0c1ad00` holds 14 coordinator pointers at `0x76a0d28000`.
During the wait, `installd` is actively validating app-extension bundles:
`_MILoadInfoPlistFromBundleWithError` static `0x1aae817c8`,
`-[MIBundle _validateWithError:] +156` at `0x1aae92de4`,
`-[MIBundle appExtensionBundlesPerformingPlatformValidation:withError:] +68`
at `0x1aae963c8`. Stacks: `/tmp/dvm/DISPLAY_NATIVE_R9.installd.json`.
The first two observed coordinator waits finish without guest patches;
no full-migration, SpringBoard launch, or pixel result follows from those
individual completions alone.
