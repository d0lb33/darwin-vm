# rootfs-boot

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/H17P), IPSW at the URL in
`firmware/info` line 2, kernelcache `firmware/bootkc` (`xnu-13432.2.10~5/RELEASE_ARM64_T8140`,
confirmed via `strings -a firmware/bootkc | grep 'Darwin Kernel Version'`).
`BuildManifest.plist` fetched directly from `<ipsw-dir>/BuildManifest.plist`
(Apple serves this one file outside the zip; every other component below was
read via HTTP range requests against the IPSW's own zip central directory —
no multi-GB file was downloaded to produce this report). XNU behavior is cited
against a shallow clone of `apple-oss-distributions/xnu` tagged `xnu-12377.1.9`
(the newest public OSS tag available), which is **not** the exact `13432.2.10`
build we boot — flagged inline wherever that gap matters. Extracted 2026-09-01.

## Summary

The restore ramdisk we boot today is architecturally nothing special: XNU's
`rd=md0` path (`bsd/kern/bsd_init.c`, `iokit/bsddev/IOKitBSDInit.cpp`) wraps
whatever physical pages the boot loader hands it in `/chosen/memory-map/RAMDisk`
as a generic block device and lets ordinary IOKit media matching find an APFS
container on it — there is nothing NVMe- or ANS-specific anywhere in that path,
and our own loader (`qemu-sptm/hw/arm/xnuboot_sptm.c`) already sizes that blob
dynamically with no cap but available DRAM. The real iOS system volume
(`094-13182-141.dmg.aea`, ~8.7 GB compressed/AEA-encrypted) is, per its own
restore manifest (`.mtree`), self-contained — it ships `SpringBoard.app`,
`backboardd`, `UIKitCore.framework` and `/usr/lib/dyld` directly, not through
a separate cryptex — so the cheap path is plausible in principle. The one real
unknown is APFS's Sealed System Volume ("ARV" in Apple's own kernel strings)
enforcement at mount time, which this report could not fully resolve without
either disassembling the closed-source `apfs.kext` or just trying the boot;
several independent signals (all security-related `chosen` device-tree
properties already zero, a documented `allow-root-hash-mismatch` boot-arg, an
Apple-native "root DMG from a RAM buffer" code path) suggest it's likely to be
tolerant, but that is not proven.

## 1. Image inventory

`BuildManifest.plist` has 5 `BuildIdentities` for this build: `Developer Erase
Install`, `Research Developer Erase Install`, `Developer Upgrade Install`,
`Research Developer Upgrade Install`, and `Recovery Customer Install`. The
`firmware/` directory we boot today was pulled from identity 0 (`Developer
Erase Install (IPSW)`). Mapping every top-level `.dmg`/`.dmg.aea` to its
manifest role (`python3 -c 'import plistlib; ...'` over the fetched
`BuildManifest.plist`, keys below are the literal `Manifest` dict keys):

| File | Manifest key | Identity | Size | AEA? | `.root_hash`? | `.trustcache` | Role (evidence) |
|---|---|---|---|---|---|---|---|
| `094-13182-141.dmg.aea` | `OS` / `SystemVolume` | 0–3 | 8.7 GB (8,724,152,320 B measured off the zip64 central directory) | yes | yes, 229 B | 91 kB | **The system volume.** `OSDiskImageSize`=9562 (MB) and `MinimumSystemPartition`=9028 (MB) in `Info` both track this key, i.e. it's sized like "the whole system partition". Confirmed self-contained by decoding its `.mtree` (§3): contains `SpringBoard.app`, `backboardd`, `UIKitCore.framework`, `/usr/lib/dyld`. |
| `094-13150-145.dmg.aea` | `Cryptex1,SystemOS` | 0–3 | 2.3 GB | yes | yes, 229 B | **3.2 kB** | `Cryptex1,SystemOSSize`=5726 (MB) in `Info`. Its trustcache is 28× smaller than the OS volume's — not consistent with holding the bulk of `/System/Library`. Purpose not confirmed (see Open questions). |
| `094-13724-198.dmg` | `Cryptex1,AppOS` | 0–3 | 15 MB | **no** | yes, 229 B | 455 B | `Cryptex1,AppOSSize`=14 (MB). Tiny; not AEA-wrapped at all. Purpose not confirmed. |
| `094-13753-197.dmg` | `RestoreRamDisk` | 0,1 (Erase) | 241 MB | no | **none** | 8.2 kB | This is `firmware/ramdisk.dmg` today (same size, `NXSB` magic at 0x20, confirmed by `diskutil apfs list` below: single volume, `Sealed: No`, role "No specific role"). No `.root_hash` exists for it anywhere in the IPSW — restore ramdisks are never sealed. |
| `094-14091-195.dmg` | `RestoreRamDisk` | 2,3 (Upgrade) | 243 MB | no | none | 8.4 kB | Same role, different identity variant (used for in-place OTA-style restore); unused by us. |
| `094-14052-182.dmg.aea` | `Ap,ExclaveOS` | 0–3 | 164 MB | yes | yes, 229 B | 1.5 kB + `.integrity_catalog` | The Exclave/SPTM-side secure-world OS image, unrelated to the AP root filesystem. |
| `094-14537-189.dmg.aea` | `OS` / `SystemVolume` | 4 (Recovery Customer) | 248 MB | yes | yes, 229 B | 8.0 kB | The much smaller system volume used by the plain "Recovery Customer Install" identity — this is what a non-developer recoveryOS restore would use, not the developer/full-OS path. |

**Answer to "does a full UI boot need the system volume alone, or system
volume + cryptex":** the `OS` image alone is the strongest candidate — see §3
for the direct evidence. `Cryptex1,SystemOS`/`Cryptex1,AppOS` are additive
components whose purpose this report could not pin down (no `.mtree` is
shipped for either, unlike `OS`, which is itself a signal they're not restored
via `asr` the same way and are mounted separately at runtime if at all).

## 2. Decryption

`ipsw fw aea --fcs-key <file>` and `ipsw extract --remote --fcs-key <url>`
(`ipsw` 3.1.713) both work against this build **with no Apple ID, no paired
device, and no personalization** — this is a public "class key" fetch, not a
FairPlay/SEP-personalized one:

1. It reads only the first ~4 KB of the target `.aea` (`AEA1` magic + a
   `com.apple.wkms.auth-data` blob), never the payload. Verified two ways: the
   built-in `ipsw extract --remote --fcs-key` run (no `--dmg`) logs
   `Extracting 0x1000 bytes of .../094-13150-145.dmg.aea` and completes in
   ~1.1 s; separately, I hand-parsed the IPSW's ZIP64 central directory
   (`Content-Length: 12166623242`, `Accept-Ranges: bytes` on the `.ipsw` URL
   itself) to locate `094-13182-141.dmg.aea`'s local-file-header offset
   (`2348810311`, stored/uncompressed, comp size `8724152320`), range-fetched
   only its first 8192 bytes over HTTPS, and ran `ipsw fw aea --fcs-key` on
   that 8 KB local file — it worked identically for the **8.7 GB system
   volume** too, producing a real EC private key.
2. The key exchange itself is an HTTPS POST to
   `https://wkms-public.apple.com/fcs-keys/<id>` (seen in the tool's own
   `--info` dump as `[com.apple.wkms.fcs-key-url]`) carrying the auth-data
   blob read from the file; the response (`fcs-keys.json`:
   `{"9VShu2K9...":"<base64 PEM>"}`) is the AES/EC key needed by `ipsw fw aea
   --key-val`/decrypt. `wkms-public.apple.com` is a public Apple endpoint —
   it answered with a real, usable key for a randomly-chosen beta IPSW with no
   credentials attached to the request.
3. **Footgun to avoid:** `ipsw extract --fcs-key --dmg <type>` (with `--dmg`
   set to `app`/`sys`/`fs`/`exc`/`rdisk`/`rosetta`) does **not** stay
   header-only — it downloads and writes out the *entire* matching DMG first
   (I confirmed `--dmg sys` pulled all 2.3 GB of `Cryptex1,SystemOS` and
   `--dmg fs` was 6 GB into the 8.7 GB `OS` image before I killed it at the
   2-minute timeout). Use the bare `--fcs-key`/`--info` flags (no `--dmg`) or
   a manual header-only range fetch, never `--dmg` combined with `--fcs-key`.

**Not yet confirmed:** whether the AEA1 profile used here also LZFSE-compresses
the payload (there's a real gap between the AEA-wrapped size, 8.72 GB, and the
manifest's stated decompressed size, `OSDiskImageSize`=9562 MB ≈ 9.56 GB — a
1.10× ratio, plausible either way) is unresolved because doing so requires the
actual full decrypt, which needs the full 8.7 GB download. `ipsw fw aea` is
expected to handle this transparently if so; this needs to be checked
empirically during the first real attempt (§6, action 2).

## 3. What's actually inside the `OS` volume (`.mtree` evidence)

`Firmware/094-13182-141.dmg.aea.mtree` (listed at 39 MB in the zip, DEFLATE
compressed) is itself an `IM4P` (`type=isys`... no — verified as a payload
container) wrapping a chunked, LZFSE-compressed blob using a `pbze` magic
(a `pbzx`-family framing `ipsw pbzx` doesn't recognize; Apple's normal `pbzx`
expects XZ chunks, this one uses `bvx2`/LZFSE chunks instead). I range-fetched
and DEFLATE-decompressed the 39 MB `.mtree` entry, hand-parsed the `pbze`
chunk framing (magic(4) + one 8-byte header field + repeating
`[uncompressed_size:8][compressed_size:8][bvx2 payload]`, 37 chunks, all
consumed cleanly to EOF), and fed each chunk to `ipsw decomp -a lzfse`
(Apple's own `libcompression`-backed LZFSE decoder) to get 153,829,564 bytes
of a plain BSD-`mtree`-format restore manifest for the whole volume. Grepping
that text for actual file entries (`strings -n 6`, then `grep`) found, directly
inside the `OS` image, not behind a mountpoint:

| Path grepped | Hits | What it shows |
|---|---|---|
| `SpringBoard.app` | 1486 lines | `# ./System/Library/CoreServices/SpringBoard.app` and full contents (`PlugIns/SpringBoardDiagnosticExtension.appex`, etc.) |
| `backboardd` (as a line, not `.plist`) | 1 real binary entry | `    backboardd  xattrsdigest=authapfs.1152921500312410694 \` — the daemon binary itself, with an on-disk integrity digest already attached |
| `dyld` (the loader, not the cache) | 1 | `    dyld        xattrsdigest=authapfs.1152921500312409729 \` — `/usr/lib/dyld` present |
| `PrivateFrameworks` | 168,456 lines | full framework tree present, not an empty mountpoint — spot-checked `UIKitCore.framework`: 171 lines including `Artwork.bundle/DoubleTapUndo.ca/assets` |
| `System/Cryptexes` | 2 lines, both `# ./System/Cryptexes` comments, **no children listed** | This is an *empty mountpoint* in the OS volume's own manifest — consistent with cryptexes being mounted there at runtime, and consistent with them not being needed for the paths above, which already live directly in `OS` |
| `dyld_shared_cache` | 0 | No entry by that literal name anywhere in this manifest (see Open questions — could be named differently or the mtree tool doesn't enumerate it, not proof it's absent) |

This is the strongest evidence in this report for the "system volume alone"
answer to Q1: SpringBoard, backboardd, and their framework dependencies are
not behind the empty `/System/Cryptexes` mountpoint.

## 4. Ramdisk boot mechanism (XNU side) and our loader

`rd=md0` does not go anywhere near a storage controller. Two independent code
paths confirm this, both in the public `xnu-12377.1.9` source (gap to
`13432.2.10` flagged in the header — this is old, stable BSD/IOKit plumbing,
low risk of having changed, but not verified against the exact build):

- **RAMDisk → `md0` device creation**, `iokit/bsddev/IOKitBSDInit.cpp:770-791`:
  reads `/chosen/memory-map/RAMDisk` (two `uintptr_t`s: base, size), asserts
  `size <= MAX_PHYS_RAM` (`(UINT_MAX) << 12`, i.e. ~16 TB on LP64), and calls
  `mdevadd(-1, ml_static_ptovirt(base) >> 12, size >> 12, 0)`. `mdevadd`'s
  actual signature (`bsd/dev/memdev.c:180`) takes `size` as `unsigned int`
  **in units of 4 KB pages** — again a ~16 TB cap, nowhere close to anything
  we'd load.
- **`rd=mdN` root selection**, `IOKitBSDInit.cpp:797-834`: parses the boot-arg
  digit/letter, calls `mdevlookup()`, and roots on it directly — no media
  type, no content-type matching, no filesystem-specific code before this
  point. Generic `IOMedia`/APFS matching only starts after the device exists.
- There *is* a gate, `IOSecureBSDRoot()` (`IOKitBSDInit.cpp:1036-1061`, called
  from `bsd_init.c:916` right after `IOFindBSDRoot`): it calls into
  `IOPlatformExpert`'s `SecureRootName` platform function (closed-source, in
  `AppleARMPlatform`, compiled into the kernelcache — not in the OSS xnu
  source) with the resolved root device *name* (`"md0"`), and if that returns
  `kIOReturnNotPrivileged`, calls `mdevremoveall()`, which would kill the
  memdev outright. This gate is keyed on the device *name/type*, not its
  *contents* — and it is **already being crossed today**: our current
  `rd=md0` boot with the small unsealed ramdisk reaches a shell (per
  `CLAUDE.md`), so whatever `SecureRootName` decides for `"md0"` in our
  current, unpersonalized device-tree state, it isn't blocking `md0` root
  today. Swapping what bytes live inside that same `md0` device shouldn't
  change this specific decision, since the check runs before the volume is
  even mounted.
- Apple's own kernel has a **native "root DMG from a RAM buffer" path**
  distinct from `rd=md0`: `bsd/kern/imageboot.c:241-243` reads boot-arg
  `-bsdmgroot-ramdisk`; when set, `imageboot_pivot_image()` calls
  `di_root_ramfile_buf(buf, bufsz, ...)` instead of mounting a file from an
  already-attached disk. This confirms RAM-resident authenticated root images
  are an intentional, supported Apple mechanism, not something exotic we'd be
  inventing.

**Our loader** (`qemu-sptm/hw/arm/xnuboot_sptm.c:385-390`, `:489-490`,
`:528`, `:536`) already places the RAMDisk with no fixed size: it pushes
`info->ramdisk_f.len` bytes into the boot blob and writes the actual length
into `/chosen/memory-map/RAMDisk`'s `reg` (`set_adt_mmap`). The only ceiling is
`info->dram_size` (total guest DRAM), which `darwin.c:278-279` reads from the
device tree's `chosen/dram-base`/`dram-size` — and `dt_fixup.py:210-214`
currently hardcodes that to `0x200000000` (8 GB) regardless of soc-generation
branch. `run.sh:86` and `tools/probe.sh:84` separately pass QEMU `-m 8G` to
match. **Both of those files are orchestrator-owned** (`CLAUDE.md`) — I did
not, and could not, edit them; raising the ramdisk past ~7 GB (leaving room
for bootkc/trustcache/etc. in the same DRAM blob) requires the orchestrator to
bump both the `dt_fixup.py` `dram-size` literal and the QEMU `-m` flag
together, e.g. to `0x400000000`/`16G`, comfortably inside the host's 128 GB.

## 5. Sealing / "ARV" (Authenticated Root Volume) — the real risk

Apple's own kernel calls this feature **ARV**, and it lives in `apfs.kext`,
which is statically linked into `firmware/bootkc` (confirmed:
`strings -a firmware/bootkc | grep '^com\.apple\.'` lists dozens of
`com.apple.private.apfs.*`/`com.apple.apfs.*` entitlement strings, including
`com.apple.private.apfs.arv.limited.snapshot` and
`com.apple.private.apfs.create-sealed-snapshot`). `apfs.kext` itself is
closed-source, so everything below is inferred from panic/log strings and
device-tree state, not from reading the check's actual logic — flagged
per-item.

**Panic/log strings pulled directly from `firmware/bootkc`**
(`strings -a firmware/bootkc | grep -iE 'sealed|root.?hash|\bARV\b'`):

```
!apfs_is_sealed(apfs)
"APFSX: could not validate root hash %s (%d)\n"
"Failed to find the root snapshot. Rooting from the live fs of a sealed
 volume is not allowed on a RELEASE build\n"
"could not authenticate personalized root hash! (%d) (%p, %zu)\n"
%s:%d: %s could not authenticate personalized root hash, skipping verification
%s:%d: %s root hash validation is required as the boot-arg is set
%s:%d: %s authenticated mount requested but volume is not sealed
%s:%d: ================ ARV enabled ================
allow-root-hash-mismatch
import_iboot_forwarded_roothash
is_root_hash_authentication_required
is_root_hash_authentication_required_ios
validate_on_disk_root_hash
apfs_extract_root_hash_arm
```

What this supports:

1. **There is a real boot-arg escape hatch**, `allow-root-hash-mismatch`, and
   a distinct message that says validation only runs "as the boot-arg is
   set" — i.e. the check has an on/off boot-arg-driven condition, it isn't
   unconditionally hard-wired for every mount.
2. **`is_root_hash_authentication_required_ios()` exists as a separate,
   named decision function** — the requirement is policy-gated, not
   automatic on "this volume happens to be sealed". `import_iboot_forwarded_roothash`
   is the "expected" value that would normally arrive from iBoot; since we
   skip iBoot entirely (SPTM/TXM/bootkc load directly, per
   `qemu-sptm/hw/arm/xnuboot_sptm.c`), whatever populates that path in our
   boot is untouched/zero, not a real personalized value.
3. **The `chosen` device-tree node, as shipped in this IPSW before any
   personalization, already reports every relevant security flag as
   disabled**, dumped with the repo's own decoder
   (`dt_fixup.py`'s `ADTNode`/`decode_node` over `Firmware/DeviceTree.d47ap.im4p`,
   extracted as `dt/dtree.raw`):
   `boot-manifest-hash` = 50 zero bytes, `secure-boot` = 0,
   `system-trusted` = 0, `certificate-security-mode` = 0,
   `effective-security-mode-ap` = 0, `effective-production-status-ap` = 0.
   If `is_root_hash_authentication_required_ios()` keys off any of these
   (plausible, unverified), our current boot posture already looks like
   "authentication not required".
4. **The volume "sealed" state is a real, checkable on-disk property.** I
   attached `firmware/ramdisk.dmg` locally (`diskutil image attach
   --readOnly --noMount`) and ran `diskutil apfs list`: it reports `Sealed:
   No`, single volume, "No specific role". This matches §1 — restore
   ramdisks ship with no `.root_hash` at all. The real `OS` volume, once
   restored from `094-13182-141.dmg.aea`, is expected to report `Sealed: Yes`
   given it ships a `.root_hash`; this asymmetry is exactly why today's boot
   is easy and the real one is the open question.
5. **A real, filename-confirmed risk**: `firmware/bootkc`'s own version
   string is `root:xnu-13432.2.10~5/RELEASE_ARM64_T8140` — a `RELEASE`
   XNU build config, matching `KernelCache: kernelcache.release.iphone17` for
   identity 0 in `BuildManifest.plist`. The panic string above is explicit
   that "rooting from the live fs of a sealed volume" (i.e., no root snapshot
   found) "is not allowed on a RELEASE build". `BuildManifest.plist` identity
   1/3 ("Research Developer ... Install") instead lists `KernelCache:
   kernelcache.research.iphone17` (`InstalledKernelCache: Research`) — a
   less restrictive variant available in the same IPSW if we hit this
   specific panic. Mitigating factor: `asr`-based restore is documented to
   preserve APFS-native structure bit-for-bit, so the shipped `OS` DMG should
   already contain its own root snapshot without needing anything from us —
   this panic should only fire if that assumption is wrong.
6. `bsd/kern/imageboot.c` — the *other* authenticated-boot path (`arp0=`/
   `rp1=` "pivot image" boot-args, used on macOS for BaseSystem-style pivots,
   `imageboot_pivot_image()` at line ~200) — is **not** the path a plain
   `rd=md0` root selection goes through (that function mounts at
   `/System/Volumes/BaseSystem` and pivots, a different mechanism than
   direct-root). Notably its hard `panic()` calls on auth failure
   (`imageboot.c:315`/`327`/`358`) are wrapped in
   `#if defined(__arm64__) && XNU_TARGET_OS_OSX` — i.e. those specific
   panics are compiled out on iOS/embedded targets, only a `printf` warning
   plus a returned error remain. This is evidence in favor of iOS being more
   forgiving than macOS in the *pivot* path specifically; it says nothing
   about the direct-root mount path in item 1-4 above, where the enforcement
   logic lives entirely inside closed-source `apfs.kext`.

**Net assessment:** every signal found is consistent with "this checks out as
not-required in our current unpersonalized configuration" but none of them is
a direct read of the actual `is_root_hash_authentication_required_ios()`
decision, which lives in a part of `apfs.kext` this report did not disassemble.
This is the one item that should be resolved by an actual boot attempt before
spending more research time on it.

## 6. Is NVMe/ANS genuinely required? — No evidence that it is

- Root-device selection (`rd=`/`IOFindBSDRoot`/`mdevadd`, §4) is completely
  storage-bus-agnostic; it operates on raw physical pages regardless of
  whether those pages came from DRAM, NVMe DMA, or anything else.
- `com.apple.iokit.IONVMeFamily` and `com.apple.driver.AppleSART` **are**
  present in `firmware/bootkc` (confirmed: both extracted successfully from
  the boot KC in an earlier session, present under `kexts/` in this
  scratchpad) — XNU is fully capable of talking to ANS/NVMe hardware when the
  matching device-tree node exists — but IOKit personality matching only
  activates a driver when its `IONameMatch`/provider node is present in the
  device tree XNU actually boots with. `dt_fixup.py` does not currently
  expose ANS/NVMe nodes for our boot (no `-enable` flag for it exists today),
  and our existing `rd=md0` boot already proves those drivers never need to
  start for `md0` rooting to work.
- No string, panic message, or code path found anywhere in this investigation
  ties APFS's seal/ARV verification (§5) to the underlying media type — every
  reference found (`validate_on_disk_root_hash`, `apfs_is_sealed`,
  `is_root_hash_authentication_required_ios`) operates in terms of the
  mounted **volume**, not the **device** backing it.
- This is the weakest-confidence conclusion in this report because it rests
  on the *absence* of a counter-example inside a closed-source kext, not on a
  positive citation that says "this is storage-agnostic". If it turns out to
  be wrong, the failure mode should be an explicit panic/log line naming
  ANS/NVMe or the storage controller, which `tools/probe.sh`'s panic decoder
  and `DARWIN_UNIMP_DEBUG=1` would surface immediately — cheap to falsify.

## Verdict

The ramdisk path is very likely viable and should be tried before anyone
starts modeling ANS/NVMe. The `OS` system volume alone (not `OS` + cryptex)
is the right target based on direct `.mtree` evidence. The dominant remaining
risk is APFS seal/ARV enforcement inside closed-source `apfs.kext`, which has
a documented boot-arg escape hatch (`allow-root-hash-mismatch`) and several
signals suggesting our already-unpersonalized boot posture may sail through
it — but this needs an actual boot to confirm, not more static analysis.

## Ranked next actions

1. **Cheap, local, no VM involved:** finish the AEA decrypt of
   `094-13182-141.dmg.aea` end-to-end (`ipsw fw aea --key-val <key from
   fcs-keys.json> ...`, or let `ipsw extract --dmg fs --lookup` do the whole
   thing) — this is the one unavoidable large download (~8.7 GB in, ~9.5 GB
   out). Immediately attach the result locally with `diskutil image attach
   --readOnly --noMount` and run `diskutil apfs list` on it. This alone
   answers "does the shipped DMG already report `Sealed: Yes` with a valid
   root snapshot" without touching QEMU, and resolves whether `ipsw` needed
   an extra LZFSE/pbzx decompress step (§2) — do this before anything else.
2. Also pull `Firmware/094-13182-141.dmg.aea.trustcache` (small, 91 kB) the
   same header/range way used for the `.root_hash` (§3) — it replaces
   `firmware/ramdisk.tc` for the real boot.
3. Ask the orchestrator to raise `dt_fixup.py`'s `dram-size` (currently
   `0x200000000`/8 GB, `dt_fixup.py:210-214`) and `run.sh`/`tools/probe.sh`'s
   `-m 8G` to something like `0x400000000`/`16G`, matching the decrypted `OS`
   volume's ~9.5 GB plus headroom for bootkc/trustcache/dtree/SPTM/TXM.
4. Boot: swap `-ramdisk firmware/ramdisk.dmg` → the decrypted `OS` dmg and
   `-tc firmware/ramdisk.tc` → the `OS` trustcache, keep `rd=md0` and
   everything else unchanged. Use `tools/probe.sh --ramdisk <path> --tc
   <path> --secs 240 --grep 'panic\(|root hash|ARV|sealed|SpringBoard|backboardd'`.
5. If it panics on a root-hash/ARV string from §5, try boot-arg
   `allow-root-hash-mismatch` first (cheapest). If the panic specifically
   names "not allowed on a RELEASE build" (§5 item 5), fetch
   `kernelcache.research.iphone17` (`BuildManifest` identity 1) instead of
   the release one, and its matching `RestoreSPTM`/`RestoreTrustedExecutionMonitor`
   if identity 1 lists different ones (check before swapping only the
   kernel).
6. If SpringBoard still doesn't start despite the OS volume mounting cleanly,
   revisit the "cryptex not needed" conclusion in §3 — mount
   `Cryptex1,SystemOS` alongside via the `arp0=`/`-bsdmgroot-ramdisk`
   mechanism (§4) as a fallback, now that its exact boot-arg shape is known.
7. Separately track whether a writable `Data`-role APFS volume (and the
   `/usr/share/firmlinks` split) is needed for SpringBoard to get past first
   boot — the `OS` DMG almost certainly ships System-role content only
   (`MinimumSystemPartition` describes only the system partition size; `Data`
   is created empty by `asr`/`diskmanagementd` on a real restore, sized to
   the device's free NAND). This wasn't investigated here and is a plausible
   second blocker even if seal verification passes cleanly.

## Open questions

- What `Cryptex1,SystemOS`/`Cryptex1,AppOS` are actually for, if not
  SpringBoard/backboardd/frameworks (§3 shows those already live in `OS`).
  Their small trustcaches and the absence of a shipped `.mtree` for either
  suggest they're not restored the same way as `OS` — possibly an OTA staging
  ("Update Brain") or Rapid-Security-Response-style mechanism. Settling this
  needs either Apple documentation or decoding one of their (undecoded) AEA
  payloads.
- Whether `is_root_hash_authentication_required_ios()` actually keys off the
  `chosen` device-tree flags found all-zero in §5, or off something else
  entirely (e.g. presence/absence of a boot-arg, or the mount call site).
  Only resolvable by disassembling the relevant `apfs.kext` code inside
  `firmware/bootkc`, or empirically via action 4 above.
- Whether the AEA1 profile used for `094-13182-141.dmg.aea` includes an
  internal LZFSE compression layer beyond decryption (§2) — the 8.72 GB vs.
  9.56 GB size gap is suggestive but not proven; resolves itself once action
  1 is done.
- The exact structure of the `isys`-tagged `root_hash` IM4P payload (208
  bytes: a 16-byte header then three 32-byte-hash + 32-zero-byte blocks) —
  decoded far enough to confirm it's SHA-256-sized hash material, not fully
  parsed; not needed for the decision but would help confirm the seal
  mechanism precisely.
- Whether a separate `Data`-role volume/firmlink split is required for a
  functional SpringBoard first-boot (action 7) — not investigated.
