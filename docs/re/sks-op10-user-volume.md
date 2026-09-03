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
