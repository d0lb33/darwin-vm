# iBoot image: static inventory and direct-load facts

Date: 2026-09-04. This note is static only: no QEMU boot ran and no firmware,
disk, or orchestrator-owned file changed. All fetched copies are disposable
files below `/tmp/dvm/iboot-fw-static-20260904/`.

## Source and identity

The project selects this IPSW at `get_files.sh:8`:

```
https://updates.cdn-apple.com/2026SpringSeed/2d03d580-843b-4b2a-b09d-976b31c10744/iPhone17,3_27.0_24A5430a_Restore.ipsw
```

Read-only `ipsw info --remote ... --list` found
`Firmware/all_flash/iBoot.d47.RELEASE.im4p` (1.7 MB),
`Firmware/all_flash/iBoot.d47.RESEARCH_RELEASE.im4p` (1.8 MB), paired
`iBootData` files (31 kB), `DeviceTree.d47ap.im4p` (65 kB), the 8.2-kB
`Firmware/094-13753-197.dmg.trustcache`, and `BuildManifest.plist` (860 kB).
No line matched case-insensitive `apticket`, `ap.?ticket`, `im4m`, or `ticket`.
Therefore this is an unpersonalized-component inventory, not a device ticket.

`ipsw extract --iboot --remote` fetched both iBoot variants. The exact files
and hashes are:

| component | SHA-256 | SHA-384 / manifest digest |
| --- | --- | --- |
| release IM4P | `4cd55c25523d8b7cde23387c73bfd2d3a0072e5aece8bbe7a4217206cfc468a4` | `82e537c8b3c99a2d01b46a3737d16d5bc290e716c37359575103e9ebd9fa7d39c12480f1cdf7cac46811d823aa4d83b5` |
| research IM4P | `71c30475c0e9f3224979a49f412c33e97ed1b442f4cc1fa240605ec1b59a57f9` | `51aa886c7affe2398eb505130be0fafad2d3f108123831928e6d206b4079e41bee29dd17cd2bde811915cd22555e7ae2` |
| release/research iBootData IM4P (identical) | `0318c8c4c1f410238c642756244362940c7d2e41302baed6b6542e49a948ec1a` | — |
| BuildManifest | `c5e9ad8875624d5d08d01cd73aa6b2272742f5c8c18847e00da08b92f95ccb9c` | — |

Paths are `/tmp/dvm/iboot-fw-static-20260904/24A5430a__iPhone17,3/Firmware/all_flash/`.
The release SHA-384 exactly equals `BuildIdentities[0].Manifest.iBoot.Digest`;
the research SHA-384 equals identity 1's digest. Both are `Trusted=true` and
`Info.Personalize=true`. Identity 0 is Developer Erase and identity 1 is
Research Developer Erase; both declare `ApChipID=0x8140`, `ApBoardID=0x08`, and
`DeviceClass=d47ap`.

## Container versus executable payload

`ipsw img4 im4p info` reports:

| input | type | version | compression | raw size |
| --- | --- | --- | --- | ---: |
| release iBoot | `ibot` | `mBoot-20457.2.37` | LZFSE | 3,885,976 B |
| research iBoot | `ibot` | `mBoot-20457.2.37` | LZFSE | 3,952,376 B |
| iBootData | `ibdt` | `mBoot-20457.2.37` | LZFSE | 189,604 B |
| DeviceTree | `dtre` | `EmbeddedDeviceTrees-12661.2.14` | LZFSE | 354,268 B |
| restore trust cache | `rtsc` | `1` | none | 8,208 B |

The direct executable is thus the decompressed `ibot` payload, not the IM4P
DER wrapper. Its paths and SHA-256 values are:

| raw payload | path | SHA-256 |
| --- | --- | --- |
| release | `/tmp/dvm/iboot-fw-static-20260904/iboot-release.raw` | `fff9f51bf2f90487fbf04b2b9a091bc739865a5ec0793c03fbe469eeeb00d8e2` |
| research | `/tmp/dvm/iboot-fw-static-20260904/iboot-research.raw` | `8e2a7ee4955871de9c577b555606495b636e743e35e58b6843983d98aabbb9cb` |

`file` calls both raw payloads `data`; the release first four bytes are
`1f 21 1c d5`, not a Mach-O magic. Its raw offset `0x280` contains
`iBoot for d47 Copyright 2007-2026, Apple Inc.`

## Architecture, entry point, and base

All addresses here are raw offset plus the statically evidenced base. Release
and research have the same first instructions and base literal.

| fact | static evidence |
| --- | --- |
| AArch64, little endian | `r2 -a arm -b 64` decodes raw `0x0` (`1f 21 1c d5`) as `msr vttbr_el2, xzr`; raw `0x8` reads `hcr_el2`; raw `0x14` writes it. Capstone 5.0.7 decodes the same exact byte sequence identically. |
| EL2 required at entry | Those `VTTBR_EL2`/`HCR_EL2` system-register operations occur before the first branch, raw `0x0`–`0x18`. |
| entry / in-place PC | Raw `0x0`, intended as PC `0x1fc080000`: at raw `0x1c`, `adrp x0, 0x1fc080000; add x0, x0, 0`. |
| self-base check | Raw `0x24` loads the u64 at raw `0x380`, `0x00000001fc080000`; raw `0x2c` compares it with the PC-derived `x0`, and raw `0x30` takes the no-copy path only if equal. |
| rounded extent | Raw `0x388` is `0xfffffc01fc434bc0`; raw `0x408` is `0xfffffc0000000000`. The code subtracts those and self-base, then `+0x3f`/`&~0x3f` at raw `0x3c`–`0x48`, producing `0x3b4bc0`: the 64-byte rounding of raw length `0x3b4b98`. |
| PAuth used later | `pacibsp` is at raw `0x25a28` / runtime `0x1fc0a5a28`. |

This proves an in-place base/load address of `0x1fc080000` for the raw iBoot
payload and an entry at its offset zero. It is not a QEMU-runtime proof: a
direct boot must still test reset registers, mappings, caches, and execution
at EL2. The raw format has no Mach-O header, so it does not establish a Mach-O
`arm64e` subtype; the defensible architecture claim is AArch64 plus PAuth use.

## Device-tree, trust-cache, and ticket inputs

`BuildIdentities[0].Manifest.iBootData.Info.IsLoadedByiBoot=true`; it selects
`Firmware/all_flash/iBootData.d47.RELEASE.im4p`. This is the manifest's
strongest explicit iBoot input flag. `RestoreTrustCache` similarly has
`IsLoadedByiBoot=true`, `Trusted=true`, and `Personalize=true`, selecting the
`rtsc` image above. In contrast, `StaticTrustCache`
`Firmware/094-13182-141.dmg.aea.trustcache` is trusted/personalized but marked
`IsLoadedByiBoot=false`; do not substitute it based on its name alone.

The raw iBoot has the following input strings at file offsets:

| offset | string / implication |
| ---: | --- |
| `0x2b8007` | `/usr/standalone/firmware/FUD/iBootData.img4` |
| `0x2b8073` | `/usr/standalone/firmware/devicetree.img4` |
| `0x2b840f`, `0x2b854f` | `/boot/devicetree`, `/boot/boot/devicetree` |
| `0x2ba70b`, `0x2ba758` | `UpdateDeviceTree` failure strings for `/chosen` and `/defaults` |
| `0x2b9b2f`, `0x364bc7` | `boot-args` |
| `0x2ba926`, `0x2ba9bb`, `0x2ba9ce` | `system-trusted`, `boot-manifest-hash`, `sidp-rom-manifest-hash` |
| `0x2baf97`, `0x2c05d5`, `0x3b2ae8` | `TrustCache`, `trst`, `TrustCache` |
| `0x2b84cc`, `0x2b850d` | `/boot/apticket.der`, `/boot/boot/apticket.der` |

`r2 aaa; axt` further finds references to the `/chosen` string at runtime
`0x1fc0a6a48` and `/defaults` at `0x1fc0a5ff8`, both in analyzed function
`0x1fc0a5a28`. These are code references, not isolated strings.

The ticket paths and `Personalize=true` / `ApRequiresImage4` manifest rules
prove that ticket/personalization is part of normal boot. They do not prove a
minimal direct-loader ticket ABI. No ticket was present in the remote ZIP list,
and none was fabricated. Other explicitly named downstream inputs are
kernelcache (`0x2b8039`), root hash (`0x2b809c`), SPTM (`0x2b80ce`), TXM
(`0x2b8113`), and SEP (`0x2b8232`).

## Compatibility boundary and remaining work

`run.sh:80-93` currently passes `-bootkc`, `-dtree`, `-tc`, `-ramdisk`, and
optionally `-sptm`/`-txm`; it has no iBoot option. The current project route
is therefore a direct SPTM/TXM/BootKC loader, not an iBoot loader. This report
makes no boot or display-success claim.

Before implementation, an isolated bounded probe must establish:

1. reset ABI (initial EL/SP/MMU/cache state and input registers);
2. RAM mapping and EL2 execution at `0x1fc080000` for `0x3b4bc0` bytes;
3. the handoff ABI for iBootData, `dtre`, boot arguments, and `rtsc`;
4. which personalization/APTicket checks are bypassable for research versus
   release identity; and
5. ownership of SPTM/TXM, kernelcache, root hash, SEP, and device-tree changes.

## Commands and positive controls

`ipsw` version was `3.1.713` (commit `cdbc3a57114b5b240d23b00aa29cfad4d1f1d3fd`).
Before target extraction, it was positively controlled by creating an
uncompressed `ibot` IM4P from `/bin/echo`, inspecting it, extracting it, and
matching both SHA-256 values to
`768909621255ea45047f3f447d567301d69ffc9394beb72f2213e60245aa3ed2`.
`file /bin/echo` plus `otool -hv /bin/echo` independently identified a valid
universal Mach-O with arm64e slice; raw iBoot was never incorrectly treated as
Mach-O. The r2 decode was cross-checked with `cstool arm64 1f211cd5`.

```sh
ipsw info --remote "$IPSW_URL" --list | rg -i 'iBoot|iBEC|iBSS|LLB|BuildManifest|DeviceTree|kernelcache|sptm|txm|trustcache'
ipsw extract --iboot --remote --output /tmp/dvm/iboot-fw-static-20260904 "$IPSW_URL"
ipsw extract --dtree --remote --output /tmp/dvm/iboot-fw-static-20260904 "$IPSW_URL"
ipsw img4 im4p info .../iBoot.d47.RELEASE.im4p
ipsw img4 im4p extract .../iBoot.d47.RELEASE.im4p -o .../iboot-release.raw
shasum -a 256 .../iBoot.d47.RELEASE.im4p .../iboot-release.raw
shasum -a 384 .../iBoot.d47.RELEASE.im4p
r2 -q -a arm -b 64 -m 0x1fc080000 -c 'pd 40 @ 0x1fc080000; pxq 0x90 @ 0x1fc080380' .../iboot-release.raw
```

No QEMU loader was invoked: this independent static worktree has no
`qemu-sptm` checkout/binary, and runtime experimentation is out of scope.
