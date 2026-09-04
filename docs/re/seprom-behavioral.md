# Behavioral SEPROM bring-up without a physical d47 device

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / d47 / t8140. Analysed
2026-09-04. This note distinguishes bytes obtained from Apple's restore image
from behavior implemented in QEMU. It does not claim that encrypted SEP code
has executed.

## What the IPSW actually provides

The restore IPSW contains two relevant Image4 payloads:

| file | IM4P type/version | container bytes | payload bytes | SHA-256 |
|---|---|---:|---:|---|
| `sep-firmware.d47.RELEASE.im4p` | `sepi`, version `1` | 7,673,931 | 7,667,712 | `db0d02a7e3c44e1176b3bba9015b12579d7ff8087967f82a4686cf06828ed817` |
| `sep-patches.d47.im4p` | `stg1`, `AppleSEPROM-1098.0.0.200.3` | 21,136 | 20,480 | `0c1f193096641ae246e9421db8bd30babc3fc876b09c2f20c8909d571332ce51` |

Both payloads are encrypted. The production and development values printed by
`ipsw img4 im4p info` are wrapped keybag fields in the container, not usable
plaintext decryption keys. Key lookup produced no usable key for 24A5430a; an
independent d47 control from 18.3.2 (22D82) resolves to an `Unknown` public-key
entry and also cannot be decrypted. The model must therefore not claim to run
either payload without a separately evidenced decryption path.

The `sepi` container exposes unencrypted Image4 metadata including `arms =
15728640`, `tz0s = 102498304`, `tz1s = 84279296`, and `mmap`, `drmc`, `rddg`,
`tbmr`, and `tbms` properties. The `stg1` object exposes its version, `mmap`,
and `rddg`. This is enough to mine layout and protocol constraints even while
the code remains encrypted. `stg1` is a SEPROM patch bundle; it is not the
immutable physical mask ROM itself.

The extracted `sepi` and `stg1` SHA-384 digests exactly match their
BuildManifest entries:

```text
sepi f2f1cf76aaade6c7a420dd2f5aa6cf6469d3c11b9e8024e856b467ec06a8f777e89ee281944d5ee822ffb79601375d32
stg1 deabecf93bbfb690fff3fc305782ac108d357907cb7acbb6cc0caf3495332e279e7b5032a337f03f8cce84e482f69f2d
```

Extraction tooling had a positive IM4P create/extract control before these
files were attributed. The malformed `/bin/echo` input and the authentic
`stg1` object are both negative `-sepfw` controls: QEMU rejects each rather
than accepting a file by name.

## Implemented transport and strict boot boundary

Direct SPTM boot now has an opt-in `-sepfw FILE` input. The loader:

1. requires a structurally recognized IM4P whose adjacent DER strings name
   `IM4P` and payload type `sepi`;
2. reserves the exact container length in `/chosen/memory-map/SEPFW`, rounded
   only for the following allocation;
3. writes every container byte into guest memory and leaves the historical
   2 MiB zero region unchanged when `-sepfw` is absent; and
4. enables a ROM-side BOOT_IMG4 check that reads the AP-supplied firmware DVA
   through `dart-sep`, verifies the outer DER length, and streams the complete
   mapped container through SHA-256; and
5. requires the exact observed d47 sequence `GET_STATUS(1)`, `BOOT_TZ0`,
   `GET_STATUS(2)`, `BOOT_IMG4(tag=1,param=0x20)`. Invalid order or fields,
   resume-with-firmware, and unimplemented ART/TMM/patch inputs get no reply.

This is deliberately a transport and protocol validation boundary, not a
signature, decryption, or execution claim. `-sepfw` is not accepted with
experimental `-iboot` yet: iBoot must eventually own loading and describing
the SEP image itself.

The frozen-guest byte witness copied all 7,673,931 bytes back from physical
`0x10015d70000`; its SHA-256 is the same
`db0d02a7e3c44e1176b3bba9015b12579d7ff8087967f82a4686cf06828ed817`
as the extracted file. In the live boot, AppleSEPBooter passed firmware page
`0x1000000c`; the strict model translated DVA `0x1000000c000`, verified all
7,673,931 bytes, and only then emitted reply opcode 106:

```text
darwin: preloaded encrypted SEP firmware IM4P at 0x10015d70000 (7673931 bytes)
sep(SEP): ROM: verified complete mapped IM4P/sepi at dva 0x1000000c000 (7673931 bytes, SHA-256 db0d02a7e3c44e1176b3bba9015b12579d7ff8087967f82a4686cf06828ed817)
sep(SEP): ROM: IMG4 accepted (firmware page 0x1000000c, param 0x20); sepOS "running"
```

The pre-change wire trace at
`/tmp/dvm/iboot-main/probe/SEP_BOOTSEQ_TRACE1.stderr.log:59-99` records every
request/reply in the enforced order. The strict positive is
`SEP_STRICT_FULLHASH_POS1.stderr.log:46-78`. Two runtime negative controls
prove that this is not a header-only check:

- `SEP_STRICT_DER_NEG1.stderr.log:95-97` passes a 32-byte `IM4P/sepi` prefix;
  QEMU computes the declared DER size as 7,673,931, compares it with the
  32-byte preload, and refuses BOOT_IMG4.
- `SEP_STRICT_HASH_NEG1.stderr.log:109-111` changes one word at physical
  preload address `0x10015d70020` after QEMU records the source identity. The
  AP maps the changed container, the envelope and length still pass, but its
  SHA-256 is
  `07c9b086412c57ff3ece1b1bb093880acac6b6d6d0bef0868f02367f5aeb9688`;
  QEMU logs both hashes and refuses BOOT_IMG4 without replying.

The disposable-overlay regression is in
`SEP_STRICT_STATE_FULL1.stderr.log` and `SEP_STRICT_STATE_FULL1.serial.log`.
It reached
`SEP accepted Tz0`, `SEP accepted IMG4`, `SEP/OS is alive`, registered
AppleSEPManager, logged `Early boot complete`, and reached the restore shell
with zero XNU panics. The matched `SEP_STRICT_STATE_DEFAULT1` run, without
`-sepfw`, reached the same shell and early-boot milestone with zero panics,
confirming that the compatibility default retains its prior zero-region and
permissive protocol behavior. The gated iBoot regression remains at its
existing first unsupported access:

```text
unimp: read  0x300040000 (pmgr[2]+0x40000) -> 0x0 size 4 pc=0xfffffc01fc10b610
```

That log is
`/tmp/dvm/iboot-main/probe/IBOOT_SEP_STRICT_REGRESSION1.stderr.log`.

## Honest boundary and next implementation

QEMU still does not execute SEPROM or sepOS. After the state and complete-byte
checks pass, it continues with the existing AP-visible protocol model.
Therefore the current behavior is comparable at the observed AP/ROM message
boundary, but not internally or cryptographically equivalent to a d47 SEP.

The next independently justified SEPROM work is semantic parsing of the
unencrypted Image4 property dictionary. A bounded implementation should decode
DER lengths and property records without trusting offsets, require the d47
`arms`, `tz0s`, `tz1s`, and memory-map constraints recorded above, and fail
closed on duplicate, missing, or out-of-range values. `stg1`/BOOT_PATCH and
ART/TMM must remain refused until a trace actually selects those paths and
their buffer contracts are attributed.

Decryption and instruction execution are a later, separate boundary. They
require either a legitimately obtained usable key or an independently
implemented high-level sepOS replacement. Lack of a physical device does not
block that metadata work, because the Image4 envelope and properties are
available before payload decryption. Separately, iBoot still must cross its
current PMGR range-2 boundary before it can own the `SEPFW` placement and
handoff that direct boot currently performs.
