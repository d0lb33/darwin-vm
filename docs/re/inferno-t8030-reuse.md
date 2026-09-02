# Inferno / qemu-t8030: what is reusable, and under what licence

Survey of two QEMU forks that emulate Apple ARM guests, for device models we
would otherwise derive from scratch. Clones at `~/dvm-artifacts/ref/`
(Inferno `cc4302a9`, qemu-t8030 `fd4b0f79`).

**Provenance note that matters:** `ChefKissInc/QEMUAppleSilicon` **is Inferno
under its old name**. `docs/re/ans-nvme-references.md` and
`qemu-sptm/hw/arm/darwin_sart.c` already cite it, so one of these two projects
is already a source in this repo.

## Licence — read before copying anything

| Project | Licence | Effect for us |
|---|---|---|
| qemu-t8030 | GPL-2.0(-or-later), stock QEMU | Same family as `qemu-sptm`. Reusable with attribution. |
| Inferno | **AGPL-3.0** for its own parts (`LICENSE:1-6`) | **Cannot be dropped into our GPL-2.0-or-later files.** |

`sep.c`, `sep-sim.c`, `ans.c`, `sart.c`, `smc.c` all carry the AGPL header
(Visual Ehrmanntraut, Christian Inci).

Register offsets, opcode numbers, endpoint IDs and protocol shapes are **facts,
not copyrightable expression** — safe to use as a hypothesis, then re-derive and
cite against our own binaries, which the house rules require anyway. Literal
code, struct layouts as C, and comments must not be copied from Inferno.
Attribute as: *informed by ChefKissInc/Inferno `hw/arm/apple-silicon/sep-sim.c`
(AGPL-3.0) and TrungNguyen1909/qemu-t8030 `hw/arm/apple_sep.c` (GPL-2.0), not
copied.*

## SEP — the endpoint-discovery mechanism

Both projects hit our exact blocker. qemu-t8030's author, 2021: *"Started
working on SEP because my restored system seems to stuck at
AppleCredentialManager waiting for SEP's scrd endpoint."* Neither closes it with
a device-tree property; both have the emulated SEP **advertise named endpoints
over the mailbox**.

Wire format (both agree): the 8-byte AKF message
`[ep:1][tag:1][op:1][param/id:1][data:4]` — the shape `darwin_asc.c` already
speaks. The transport is not new work; only the SEP opcodes are.

Endpoint IDs, identical across two independent implementations four years apart
(Inferno `sep-sim.c:82-111`, qemu-t8030 `apple_sep_protocol.h:6-13`):

| Name | ID |
|---|---|
| control | 0 |
| **credentials ("scrd")** | **10** |
| xart slave | 16 |
| keystore ("sks ") | 18 |
| xart master | 19 |
| discovery | 253 |
| l4info | 254 |
| bootstrap / SEPROM | 255 |

Bootstrap then discovery (Inferno `sep-sim.c:520-608`, qemu-t8030
`apple_sep.c:89-150`): PING / GET_STATUS / GENERATE_NONCE / GET_NONCE_WORD /
BOOT_TZ0 / BOOT_IMG4, then an unsolicited NOTIFY_ALIVE on the control endpoint,
then per endpoint an `EP_ADVERT` (`ep=253, op=0, id=<n>, name=<4CC>`) followed by
an `OOL_ADVERT` (`op=1`, `{in_min,in_max,out_min,out_max}` page counts).

### Corroborated in our own iOS 27 kexts

The reason this is worth acting on. From `/tmp/dvm/kexts/`:

```
AppleSEPManager:           EP_DISCOVERY == msg->endpoint
                           sep-endpoint,xxxe        (template, name patched in)
                           Duplicate OOL advertisement for endpoint ID %u
                             name 0x%x OOL in [%u,%u] OOL out [%u,%u]
AppleSEPCredentialManager: msg.call.endpoint == SCRD_ENDPOINT
                           sep-endpoint,scrd
AppleSEPKeyStore:          sep-endpoint,sks
```

That four-field OOL format string structurally matches the
`{in_min,in_max,out_min,out_max}` shape both projects use. The mechanism
reconstructed for A9-A13 / iOS 14-18 is still architecturally present in our
A19 / iOS 27 kernelcache — five generations with no visible break at this layer,
much more stable than the AFK ring granule was.

**The shape is confirmed from our binary; the numbers are not.** Pin the real
endpoint IDs and opcodes by disassembling around the `EP_DISCOVERY` xref in
`AppleSEPManager`.

### Two things this does not give us

- **Inferno's full `sep.c` is not a path.** It executes real decrypted SEPOS
  firmware and depends on hardcoded per-chip, per-iOS physical base offsets
  (`SEPOS_PHYS_BASE_T8015` ... `_T8030_IOS18`). Nothing exists for t8140, and
  producing it means obtaining a decrypted iOS 27 SEPFW and re-deriving every
  constant. `sep-sim.c`'s protocol-stub approach is the model to follow.
- **Neither implements real SCRD semantics**, only its advertisement. Inferno
  logs "Unknown opcode" for every credentials message; qemu-t8030 registers no
  handler. Advertisement plausibly satisfies `waitForSEPEndpoint` (an IOKit
  registry match, not a live round-trip), and the boot then reaches
  `AppleCredentialManager`'s first real request — uncharted. Budget for it.

Keystore (`sks`) does have a faked-but-real protocol reference in Inferno
(`sep-sim.c:674-994`): a 0x54-byte `KeystoreIPCHeader`, selector `0x01` create
keybag (replies `kb_id='BAG1'`), `0x02` copy, `0x03` load. Fabricated crypto, no
real key material — useful only for "does the driver stop blocking".

## ANS / NVMe — nothing new to take

Both wrap QEMU's stock `hw/nvme` rather than reimplementing NVMe, with a thin
Apple MMIO shim in front. Our register map is now cross-validated from a fourth
independent source; values are byte-identical in both clones:

| Offset | Name | Readback |
|---|---|---|
| `0x1210` | MAX_PEND_CMDS | `(64<<16)|64` |
| `0x1300` | BOOT_STATUS | `0xde71ce55` |
| `0x1308` | BASE_CMD_ID | `0x6000` |
| `0x24908` | LINEAR_SQ_CTRL | bit0 = EN |
| `0x1304` | MODESEL | — |

`ans-nvme-references.md` flagged `0x1308`'s `0x6000` as possibly a guess; it is
the same in two independent clones spanning four years. Still uncited to any
Apple source, but treat it as stable with moderate confidence.

**The finding that sharpens our plan:** `LINEAR_SQ_CTRL` is defined in Inferno
but **never read or acted on** — writes land in a generic `vendor_reg[]` with no
side effect. Inferno does **not** implement Apple's linear submission queue; it
works because T8030's driver falls back to the classic ring-buffer doorbell that
stock `hw/nvme` understands. Our t8140 tree has `nvme-linear-sq` as a boolean on
`/arm-io/ans`. **Do not read Inferno's working boot as evidence that linear-SQ
is solved — it is not solved there either.**

SART: nothing to take. Already implemented in `darwin_sart.c` and cross-checked
against SPTM disassembly, which is a stronger source than either project.

## Display — skip

t8030 is pre-DCP, so `apple_displaypipe_v4.c` wires `/arm-io/disp0` straight to
sysbus MMIO and AIC with no AFK ring, no EPIC, no RTKit handshake. No
architectural overlap with the DCP path we are building, and nothing
userspace-facing that our boot framebuffer does not already have.

## Other

Inferno's `hw/misc/apple-silicon/smc.c` is a genuine key-value SMC store with a
real FourCC table (thermal `TP0d`/`TV0s`, battery `AC-N`/`CHAI`, `#KEY`, `CLKH`)
— worth reading when SMC bring-up starts. AIC/DART/RTKit: ours already target
the right generation; do not swap working models for older-generation ones.

## Still open in every project

- Real SCRD protocol semantics.
- Apple's linear-submission-queue NVMe path.
- Anything t8140/A19-specific for SEP — both cap out at T8030/A13, iOS <= 18.
