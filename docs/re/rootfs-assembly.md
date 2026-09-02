# rootfs-assembly

Source: iOS 27.0 beta (24A5430a), iPhone17,3 (t8140/H17P), IPSW at the URL in
`firmware/info` line 2. Base images: `094-13182-141.dmg.aea` (system volume,
`OS` role) and `094-13150-145.dmg.aea` (`Cryptex1,SystemOS`), both decrypted
per `docs/re/rootfs-boot.md` §2 and mounted at `/tmp/dvm/aea/rootmnt` and
`/tmp/dvm/aea/cryptexmnt`. Trustcaches fetched fresh for this report from
`Firmware/*.trustcache` in the same IPSW. Kernelcache `firmware/bootkc`
(`xnu-13432.2.10~5/RELEASE_ARM64_T8140`), `/usr/lib/dyld` and `/sbin/launchd`
read directly off the decrypted system volume. Extracted/built 2026-09-01.
This continues `docs/re/rootfs-boot.md`, which established that the ramdisk
boot path works in principle and that a hard 4GiB `mdevadd`/`memdev.c` limit
in the kernelcache blocks actually booting anything this large — that limit
is **still unpatched**; nothing in this report resolves it. This report answers
the three remaining questions (cryptex placement, ownership without sudo,
code signing) and produces the assembled image so that work is ready to go
the moment the size limit is lifted.

## Summary

A complete, bootable-once-the-4GiB-limit-is-fixed iOS root filesystem was
assembled at `/tmp/dvm/build/system_assembled.dmg` (19,463,667,712 bytes,
~18.1GiB container, ~15.4GiB of live content) entirely without `sudo`: it is
a byte-for-byte grown copy of the real system volume DMG (which preserves
Apple's real root ownership because raw disk-image cloning never goes through
a chown() call) with the cryptex's dyld shared cache (6.66GiB, 80 files)
added directly at `System/Library/Caches/com.apple.dyld/` — bypassing the
`/private/preboot/Cryptexes` mount mechanism entirely, because dyld's own
statically-linked "libignition" cryptex-grafting code checks for that
directory first and skips cryptex-mounting altogether if it's already
present. A QEMU model must, once the ramdisk size limit is lifted, boot this
image via `-ramdisk` with a merged v2-format trustcache (built by
`merge_tc.py` below, no C changes needed) via `-tc`, and pass a device tree /
boot-args combination with `dram-size` and `-m` raised past ~18GiB.

## Problem 1: where the cryptex goes

### Finding: dyld's own loader carries a self-contained cryptex-mount fallback

`/usr/lib/dyld` (1,446,976 bytes) statically links Apple's `libignition`
cryptex-grafting library — confirmed by an embedded build-path debug string:

```
/Library/Caches/com.apple.xbs/DF6EB712-7317-43E1-8707-683531E309A6/TemporaryDirectory.xEI7tj/Binaries/libignition/install/Symbols/ignition_core
```

This has to be the case: dyld is the very first thing that runs at boot (it
maps `libSystem` for `launchd`, process 1), so it cannot depend on some
later-running daemon to graft the OS cryptex before it can even resolve
`libSystem`'s own path — it has to be able to do the whole cryptex-discovery
dance itself, standalone, before anything else exists.

`r2` disassembly of `/usr/lib/dyld` (`aaa; pdf @ sym.__dylib_cache_fire`)
shows the exact decision function, `sym.__dylib_cache_fire` at file offset
`0x19b40`:

| Step | Address | What it does | Evidence |
|---|---|---|---|
| 1 | `0x19b78` | `_boot_get_dylib_root(bootargs)` → dir fd for the current "dylib root" (rootfs by default) | disassembly |
| 2 | `0x19bc4` | `_ignition_get_shared_cache_directory(buf)` → fills `buf` with a literal path | `sym._ignition_get_shared_cache_directory` at `0x19ac0`, `strlcpy` from string at `0xa495d` |
| 3 | `0x19c0c` | `openat(dirfd_from_step1, buf, O_DIRECTORY)` | disassembly |
| 4 | `0x19c30`-`0x19c8c` | if `openat` succeeds → `_realpathfd`, log `"opened shared cache directory: %s"`, call `_boot_set_root(...)`, **return success — no cryptex graft attempted** | strings at `0xaed61`/`0xaecdc`, disassembly |
| 5 | `0x19c98` | if `openat` fails (`ENOENT`) → log `"shared cache not found: root = %s, path = %s"`, fall through to the real cryptex-mount path (not reached in our layout) | string at `0xaed9b` |

The literal path from step 2, read directly out of the binary
(`iz~Library/Caches` in `r2`):

```
0xa495d  "/System/Library/Caches/com.apple.dyld/"        (normal process)
0xa4984  "/System/DriverKit/System/Library/dyld/"         (DriverKit process, selected when
                                                            _configuration()->field_0x24 == 0xa)
```

A second, independent function in the same binary, `sym.__graft_select_fire`
(`0x19348`), performs the equivalent check before *forcing* a cryptex/livefs/
rootfs fallback via boot-arg, and its log string is the most direct evidence
of intent:

```
0xad96d  "libignition: %d: %12s: dylib cache directory present; not overriding\n"
```

i.e. **if `System/Library/Caches/com.apple.dyld/` already exists as a real
directory under whatever the current dylib root resolves to (the system
volume itself, by default, since no cryptex is mounted yet), dyld does not
attempt to graft or mount any cryptex at all.** `DYLD_SHARED_CACHE_DIR`
(string at `0xa5xxx`, referenced from `dyld4::CacheFinder::CacheFinder`) is a
*separate*, later-stage override read by the full `dyld4::ProcessConfig`
machinery for individual process launches, not part of this bootstrap path —
it was a red herring for the *initial* cryptex problem, though it may still
be useful as a debugging override once the system boots further.

### Decision

Populate `System/Library/Caches/com.apple.dyld/` **directly on the system
volume**, not under `/private/preboot/Cryptexes/OS`. This needs no second
APFS volume, no `cryptexd`/`libignition` grafting at runtime, and no
`/private/preboot` population at all — it is the same directory dyld already
ships (0 bytes, confirmed empty in `docs/re/rootfs-boot.md` §"CORRECTION"),
just filled in.

Verified empirically (§"Build recipe" below): all 80 files of the split
shared cache (`dyld_shared_cache_arm64e` + `.01`-`.79`, `.symbols`, `.atlas`),
7,156,434,645 bytes total, copied in and byte-identical to the source
(`cmp` clean) to the assembled image at that exact path.

### Open question this leaves

Whether any binary's own **Launch Constraint** metadata specifically requires
residing under a cryptex mount point (see Problem 3's Launch Constraint
finding) — this was not found for the shared cache itself (which is `mmap`ed,
not `execve`d, so launch constraints likely don't even apply to it), but was
not exhaustively checked for every one of the ~4,700 images inside the cache.

## Problem 2: ownership without sudo

### The constraint that actually bites is UNIX permissions, not chown

The premise "files created on an owners-off mount get uid 501" is correct
but incomplete: the real blocker discovered empirically is that
`/System`, `/System/Library`, and `/System/Library/Caches` are `root:wheel`
mode `0755` on the real system volume (`stat -f "%Op %u %g"` on a
`-owners on` mount → `40755 0 0` for all three). A non-root process cannot
`mkdir` inside a `0755` directory it doesn't own, **regardless of whether it
also owns the raw bytes of the disk image file the directory lives in** —
`chmod`/permission checks are enforced by the kernel against the *mounted
filesystem's* recorded uid, not against whoever owns the backing store file
on the host.

### What actually works: raw disk-image cloning + a noowners mount for adds only

1. **Cloning preserves real ownership for free.** `cp` (not `ditto`, not a
   file-tree walk) of the flat, headerless `CRawDiskImage`/`UDRW` `.dmg`
   copies the literal on-disk APFS b-tree bytes, uid fields included. No
   `chown()` syscall is ever issued, so none can fail. Verified: `cp
   094-13182-141.dmg → system_assembled.dmg` (10,026,483,712 bytes), then
   re-mounted with `-owners on`: every pre-existing file (`sbin/launchd`,
   `System/Library/LaunchDaemons/com.apple.SpringBoard.plist`,
   `System/Library/CoreServices/SpringBoard.app/SpringBoard`, all 661
   LaunchDaemons) still reports `uid 0 gid 0`.
2. **Growing the file must avoid two footguns.** `dd if=/dev/zero bs=1m
   count=N >> file 2>&1` corrupts the image: the `2>&1` mixes `dd`'s own
   "records in/out" status text into the raw APFS bytes it's appending,
   and `hdiutil` then reports `image not recognized`. Plain `dd if=/dev/zero
   bs=1m count=N >> file` (stderr left alone) or `dd ... seek=<exact old EOF
   in blocks> conv=notrunc` both work and are byte-exact — confirmed via
   `hdiutil imageinfo` reporting the correct new `Total Bytes` after growth.
3. **`hdiutil resize` cannot grow these images at all** —
   `hdiutil resize -limits` reports identical min/cur/max for both the
   original `CRawDiskImage`/UDRW format and `hdiutil convert`-produced
   UDRW/UDSP variants, because none of them carry a partition table
   describing spare capacity. Growing the flat file directly (above) and
   then telling APFS about the new space is the only path that worked.
4. **`diskutil apfs resizeContainer <container> 0` claims the grown space**,
   entirely as a normal user, no privilege elevation prompt. `0` means "use
   the maximum the backing device now supports". Verified against the real
   9.4GB-used/10GB-container system volume grown to 19,463,667,712 bytes:
   resulting container correctly reports `19.5 GB` capacity with `10.0 GB`
   newly free, `fsck_apfs -n -x` passes clean before the grow proceeds.
5. **The permission barrier is bypassed by mounting the destination with
   `-mountOptions noowners`** (`diskutil mount`, not the deprecated
   `hdiutil attach -owners off`, which doesn't accept a `-mountPoint`
   reliably in this macOS build) while performing the *add*. Under
   `noowners`, macOS reports every object on the volume as owned by the
   mounting user for the duration of the mount, which makes root's `0755`
   directories pass the owner-write check and lets `mkdir -p
   System/Library/Caches/com.apple.dyld` succeed. This is a **host-macOS
   mount-time display/enforcement override only** — it changes nothing about
   the bytes actually written to the APFS b-tree, which is what the guest
   XNU will read regardless of what macOS mount option was active while we
   built the image.
6. **New files created this way get real uid 501 baked into the actual
   on-disk inode**, confirmed by remounting with `-mountOptions owners`
   afterward and re-reading: pre-existing content stayed `0 0`, everything
   newly added showed `501 20` — on both a disposable 400MB test volume and
   the final 18GB assembled image.

### Does the added content need to be root-owned at all?

No evidence found that it does. `strings -n 6 /usr/lib/dyld | grep -i owner`
turns up nothing about file ownership (only unrelated `os_unfair_lock`
messages); dyld's `libignition` cryptex/cache-directory code checks
*existence* and *S_IFDIR* (`fstatat` + `st_mode & S_IFMT`), never `st_uid`.
This matches the project's own prior finding
(`darwin-vm-no-sudo-ramdisk` memory note): "trustcache gates execution, not
ownership." The one confirmed ownership check in this stack is **launchd's**,
over `LaunchDaemons` plists (`"Caller specified a plist with bad
ownership/permissions"`, string in `/sbin/launchd`) — irrelevant here since
no new plist is added; the system volume's 661 real LaunchDaemons are
untouched, byte- and uid-identical to the source.

### Result

`/tmp/dvm/build/system_assembled.dmg`, 19,463,667,712 bytes. `sbin/launchd`,
every `LaunchDaemons/*.plist`, `SpringBoard`, `backboardd`, all `0:0` as
shipped. `System/Library/Caches/com.apple.dyld/*` (80 files, 6.66GiB) `501:20`
— acceptable per the above.

## Problem 3: code signing / trustcache

### Apple's shipped `.trustcache` files are IM4P-wrapped; `-tc` wants the raw payload

```
$ xxd 094-13182-141.dmg.aea.trustcache | head -1
00000000: 3083 0164 9c16 0449 4d34 5016 0474 7273  0..d...IM4P..trs
                              ^^^^^^^^^^^ "IM4P"    ^^^^^^^^ "trst"
```

vs. `firmware/ramdisk.tc` (built by the repo's own `build_tc.py`), which is
the bare struct with no wrapper at all:

```
00000000: 0100 0000 4141 4141 4141 4141 4141 4141  ....AAAAAAAAAAAA
           ^^^^^^^^^ version=1  ^^^^^^^^^^^^^^^^^^ uuid = placeholder "AAAA...A"
```

`qemu-sptm/hw/arm/xnuboot_sptm.c` (read only, not modified) confirms which
format `-tc` actually wants: `arm_load_xnu`/`arm_load_xnu_nosptm` write
`info->tc_f.buf` — the file passed to `-tc` — **verbatim** into guest memory,
directly after a synthesized `trust_cache_offsets_t` header
(`address_space_write(..., info->tc_f.buf, info->tc_f.len)`, lines
481-482/585-586). There is no IM4P/DER parsing anywhere in that path, so a
raw Apple `.trustcache` handed to `-tc` unmodified would present `30 83 01
64...` as the module's `version` field to XNU — garbage, not `1` or `2`.

`ipsw img4 im4p extract <file>.trustcache -o <out>` unwraps it correctly
(confirmed structurally and, more importantly, semantically — see below).

### The unwrapped format is version 2, not version 1

`build_tc.py`'s comment cites `TrustCacheModule1_t`/`TrustCacheEntry1_t` from
`xnu-8796.101.5` (20-byte hash + 1-byte hashType + 1-byte flags = 22-byte
entries). Apple's real, current trustcaches for this build are version 2:

| File | IM4P FourCC | Unwrapped size | version | uuid | numEntries | entry size |
|---|---|---|---|---|---|---|
| `094-13182-141.dmg.aea.trustcache` (system volume) | `trst` | 91,272 B | **2** | `c56ab797-712b-4ff7-9b79-ea4567c6b10c` | 3802 | 24 B |
| `094-13150-145.dmg.aea.trustcache` (cryptex, `Cryptex1,SystemOS`) | `trcs` | 3,144 B | **2** | `a5fb5d92-6ab3-433e-a54b-8fe8c2f2335f` | 130 | 24 B |
| `094-13753-197.dmg.trustcache` (RestoreRamDisk, matches today's `firmware/ramdisk.dmg` role) | `rtsc` | 8,208 B | **1** (legacy) | `4c7e98a4-c53a-4836-8590-b5fde630692b` | 372 | 22 B |

`24 = 91,248 / 3802` and `24 = 3,120 / 130` exactly, confirming the v2 entry
layout: 20-byte hash + 1-byte hashType + 1-byte flags + **2-byte trailing
field** (a per-entry "constraint category" — see below), vs. v1's 22 bytes.
Entries in both v2 files are already sorted ascending by hash (verified in
Python: `hashes == sorted(hashes)` → `True`), matching `build_tc.py`'s own
`sorted(hashes)` convention — this is a real requirement, not incidental, and
must be preserved by anything that merges modules.

### Direct proof the unwrapped v2 payload is correct, by CDHash cross-reference

Computed real CDHashes off the actual mounted volumes with `ipsw macho info
--sig` and searched for their 20-byte prefix in the unwrapped trust cache
entry lists:

| Binary | Source | Full CDHash (SHA-256) | Found in |
|---|---|---|---|
| `/sbin/launchd` | system volume | `459d7292…e3bc8e6` | system-volume trustcache, entry #1065 |
| `/usr/lib/dyld` | system volume | `e6335477…8e52a619` | system-volume trustcache, entry #3432 |
| `dyld_shared_cache_arm64e` (the main cache file itself, via `ipsw dyld info --sig`, which reports a per-subcache CDHash from its own trailing code-signature blob) | **cryptex** | `4e435e92…9bc53a5` | **cryptex trustcache**, entry #39 — **not** in the system-volume one |

The third row is the load-bearing result: **the dyld shared cache's own
CDHash lives only in the cryptex's trustcache, never the system volume's.**
Since our layout (Problem 1) puts the cache's *bytes* on the system volume,
the guest still needs *both* trustcache modules loaded simultaneously for
AMFI to accept the cache once it's mapped, even though the file now lives
outside any cryptex mount point.

### Multiple trust caches: XNU's own boot protocol already supports this; today's C code only loads one

`qemu-sptm/include/xnu/boot/trustcache.h` (read only):

```c
// This is the structure iBoot uses to deliver the trust caches to the system
typedef struct _trust_cache_offsets {
    uint32_t num_caches;
    uint32_t offsets[1];
} __attribute__((__packed__)) trust_cache_offsets_t;
```

i.e. real iBoot already hands XNU a *list* of trust cache modules at boot,
by design. `xnuboot_sptm.c`'s `get_tcinfo()` (not read in full — out of scope
to modify) evidently synthesizes `num_caches=1` for whatever single file
`-tc` points at. Two ways forward, neither requiring the protected C files to
be touched by this task:

1. **Self-service, works today:** merge the two v2 modules ourselves before
   handing them to the *existing* single-file `-tc` flag. Implemented as
   `merge_tc.py` (full source below) — concatenates entries from N v2
   modules, drops exact-duplicate hashes, re-sorts, re-emits one module with
   a single header. Run against the two real trustcaches:

   ```
   $ python3 merge_tc.py merged_sysvol_cryptex_tc.bin sysvol_tc_raw cryptex_tc_raw
   merged 2 modules -> 3932 unique entries (0 duplicate hashes dropped),
   uuid=c56ab797712b4ff79b79ea4567c6b10c, out=merged_sysvol_cryptex_tc.bin
   ```

   3932 = 3802 + 130 exactly (no collisions). Re-verified structurally: all
   three CDHashes from the table above are present in the merged file, and
   the merged entry list is still sorted ascending. This is the trustcache
   to pass via `-tc` for the assembled image, and it needs no orchestrator
   change at all.
2. **Proper fix, for the orchestrator if wanted later:** extend `-tc` to
   accept a comma-separated list (or repeatable flag) and have `get_tcinfo()`
   emit a real `num_caches=2` header with two offsets, one per file, matching
   how iBoot actually does it on real hardware. Not done here — out of this
   task's lane (`xnuboot_sptm.c` is not `darwin.c`/`darwin_asc.c`/etc., not
   explicitly protected by `CLAUDE.md`'s ownership table, but it is C code,
   which this task was explicitly told not to touch).

### Important negative result: Launch Constraints, not just trustcache membership, can still reject a binary whose hash *is* present

Tested directly (not just structurally) by booting the **existing, unmodified
`firmware/ramdisk.dmg`** — which the baseline `tools/probe.sh` boots cleanly
to a shell — with only `-tc` swapped from `firmware/ramdisk.tc` (this
project's own `build_tc.py` output) to the real, IM4P-unwrapped
`094-13753-197.dmg.trustcache` (Apple's official trustcache for this exact
ramdisk role):

```
serial lines : 458
xnu panics   : 0
reached shell: no
AMFI: '/bin/sh' is adhoc signed.
AMFI: '/bin/sh': unsuitable CT policy 0 for this platform/device, rejecting signature.
AMFI: code signature validation failed.
AMFI: Launch Constraint Violation (enforcing), error info: c[8]p[1]m[5]e[5],
  (in-tc-with-constraint-category) launching proc[vc: 0 pid: 3]: /bin/sh, ...
```

The `(in-tc-with-constraint-category)` phrasing shows the trust cache
**lookup itself succeeded** (the hash was found, a constraint category was
retrieved) — this is not a "wrong format" or "hash missing" failure, it's a
**separate Launch Constraints policy check** (the v2 format's trailing
2-byte per-entry field from the table above) rejecting the launch. `launchd`
then throttled and endlessly retried, never reaching a shell — a real
regression versus the `build_tc.py`-built trustcache, which encodes no
constraint category (flags byte only, v1 format) and apparently lets
everything through unconditionally.

**This means "swap in Apple's real trustcache" is not a drop-in win for
every image** — it depends on what launch-constraint metadata the target
binary actually carries and whether this unpersonalized VM's identity
satisfies it. It was *not* tested against the real system volume's own
`launchd`/`SpringBoard`/`backboardd` (their trustcache is the one this report
actually needs), because that requires the still-unsolved 4GiB ramdisk
limit to be lifted first. Flagged as the single largest open risk for
Problem 3 — see Open questions.

## Build recipe (reproducible, no sudo anywhere)

```bash
# --- sources already decrypted per docs/re/rootfs-boot.md ---
# /tmp/dvm/aea/out/094-13182-141.dmg   (system volume, 10,026,483,712 B)
# /tmp/dvm/aea/out/094-13150-145.dmg   (Cryptex1,SystemOS, mounted read-only
#                                        at /tmp/dvm/aea/cryptexmnt)

mkdir -p /tmp/dvm/build
cp /tmp/dvm/aea/out/094-13182-141.dmg /tmp/dvm/build/system_assembled.dmg   # ~2s, clone-backed

# grow by 9000 MiB (headroom for 6.66GiB of cache + APFS metadata).
# NEVER redirect dd's own stderr into the target file (2>&1 corrupts it).
dd if=/dev/zero bs=1m count=9000 >> /tmp/dvm/build/system_assembled.dmg

hdiutil attach -nomount -nobrowse /tmp/dvm/build/system_assembled.dmg
# -> note the disk identifier, e.g. disk15 (container) / disk15s1 (volume)
diskutil apfs resizeContainer disk15 0        # 0 = claim all new space, no sudo needed

mkdir -p /tmp/dvm/build/mnt
diskutil mount -mountOptions noowners -mountPoint /tmp/dvm/build/mnt disk15s1
mkdir -p /tmp/dvm/build/mnt/System/Library/Caches/com.apple.dyld
cp -R /tmp/dvm/aea/cryptexmnt/System/Library/Caches/com.apple.dyld/. \
      /tmp/dvm/build/mnt/System/Library/Caches/com.apple.dyld/
diskutil unmount /tmp/dvm/build/mnt

# --- trustcache: fetch, unwrap, merge ---
ipsw extract --remote "$(sed -n 2p firmware/info)" --output /tmp/dvm/tc \
    --flat --pattern "trustcache"
ipsw img4 im4p extract "<dir>/094-13182-141.dmg.aea.trustcache" -o /tmp/dvm/tc/sysvol_tc_raw
ipsw img4 im4p extract "<dir>/094-13150-145.dmg.aea.trustcache" -o /tmp/dvm/tc/cryptex_tc_raw
python3 merge_tc.py /tmp/dvm/tc/merged_sysvol_cryptex_tc.bin \
    /tmp/dvm/tc/sysvol_tc_raw /tmp/dvm/tc/cryptex_tc_raw

# --- boot (blocked today by the unpatched 4GiB memdev limit, see rootfs-boot.md) ---
# once fixed, replace firmware/ramdisk.dmg -> system_assembled.dmg and
# firmware/ramdisk.tc -> merged_sysvol_cryptex_tc.bin, and raise dram-size/-m
# past ~18GiB (orchestrator-owned files, not done here):
tools/probe.sh --ramdisk /tmp/dvm/build/system_assembled.dmg \
    --mem 24G --secs 240 \
    --grep 'panic\(|SpringBoard|backboardd|AMFI|Launch Constraint'
```

`merge_tc.py` (full source, not committed to the repo — kept at
`/tmp/dvm/tc/merge_tc.py` and reproduced here for the record):

```python
#!/usr/bin/env python3
"""Merge two or more raw (IM4P-already-unwrapped) TrustCacheModule2_t blobs
into a single sorted module, so multiple '-tc' inputs can be fed through the
single-file -tc option in xnuboot_sptm.c without any C changes.

Format (xnu-13432-era, confirmed empirically against 24A5430a):
  header: version(u32 LE)=2, uuid(16 bytes), numEntries(u32 LE)
  entry (24 bytes): hash(20) + hashType(1) + flags(1) + constraintCategory(u16 LE)
"""
import struct, sys

HDR = struct.Struct("<I16sI")
ENTRY_LEN = 24

def load(path):
    data = open(path, "rb").read()
    version, uuid, n = HDR.unpack_from(data, 0)
    assert version == 2, f"{path}: expected version 2, got {version}"
    assert len(data) == HDR.size + n * ENTRY_LEN, f"{path}: size mismatch"
    entries = [data[HDR.size + i*ENTRY_LEN : HDR.size + (i+1)*ENTRY_LEN] for i in range(n)]
    return uuid, entries

def main():
    out_path = sys.argv[1]
    in_paths = sys.argv[2:]
    assert in_paths, "usage: merge_tc.py out.bin in1.bin [in2.bin ...]"
    all_entries = []
    seen = set()
    dupes = 0
    uuid0 = None
    for p in in_paths:
        uuid, entries = load(p)
        if uuid0 is None:
            uuid0 = uuid
        for e in entries:
            h = e[:20]
            if h in seen:
                dupes += 1
                continue
            seen.add(h)
            all_entries.append(e)
    all_entries.sort(key=lambda e: e[:20])
    with open(out_path, "wb") as f:
        f.write(HDR.pack(2, uuid0, len(all_entries)))
        for e in all_entries:
            f.write(e)
    print(f"merged {len(in_paths)} modules -> {len(all_entries)} unique entries "
          f"({dupes} duplicate hashes dropped), uuid={uuid0.hex()}, out={out_path}")

if __name__ == "__main__":
    main()
```

## Result on disk

`/tmp/dvm/build/system_assembled.dmg` — 19,463,667,712 bytes (18.13 GiB
container, `diskutil apfs list`: 16,592,740,352 B / 15.45 GiB consumed, 2.8GB
free headroom left in the container). Re-mounted read-only with `-owners on`
after every write step as a final integrity check: `sbin/launchd` and
`System/Library/LaunchDaemons/com.apple.SpringBoard.plist` both `uid 0 gid 0`
(byte-identical timestamps to the untouched source), `System/Library/Caches/
com.apple.dyld/dyld_shared_cache_arm64e` `cmp`-identical to the cryptex
source, all 80 cache files present.

`/tmp/dvm/tc/merged_sysvol_cryptex_tc.bin` — 94,392 bytes, version 2, 3932
sorted entries, verified to contain the CDHashes of `launchd`, `dyld`, and
the main shared cache file.

Neither is committed to the repo (both are under `/tmp/dvm/`, excluded per
`CLAUDE.md`). Original decrypted intermediates in `/tmp/dvm/aea/out` (15GB)
were kept rather than deleted — they're the expensive-to-regenerate AEA
decrypt output and the host has 298GB free after this work, well inside
budget.

## Open questions

- **Does the real system-volume trustcache clear Launch Constraints for
  `launchd`/`SpringBoard`/`backboardd` in this unpersonalized VM?** The one
  real end-to-end trustcache-swap test performed (§Problem 3) *failed* this
  check for `/bin/sh` on the small ramdisk. It is not known whether the same
  failure mode applies to the real system volume's own daemons — plausible
  either way: on one hand real hardware launches these same binaries with
  the same trustcache, on the other this VM has no real device identity/ACM
  personalization for the constraint check to key off. Only resolvable by an
  actual boot of the assembled image once the ramdisk limit is fixed, or by
  disassembling AMFI's `(in-tc-with-constraint-category)` check to learn
  exactly what identity fields it compares.
- **The 4GiB `memdev.c` ramdisk limit is still unpatched** (`docs/re/
  rootfs-boot.md`, "HARD LIMIT FOUND"). This report assembled the image and
  the trustcache assuming it will be, but could not itself perform a full
  boot test of either artifact against real SpringBoard/launchd content.
- **`Cryptex1,AppOS` (`094-13724-198.dmg`, 15MB) and its trustcache (455B)
  were fetched but not investigated.** `docs/re/rootfs-boot.md` couldn't
  determine their purpose either; not included in the assembled image or the
  merged trustcache. If SpringBoard needs something from it, that would show
  up as a missing-file or AMFI-rejection panic once a real boot is attempted.
- **Whether any binary requires being physically located under
  `/System/Cryptexes/...` for a Launch Constraint's "on-disk location" check**
  (a documented category of Apple launch constraint) rather than just having
  its hash present in a loaded trust cache. Not checked against every one of
  the ~4,700 images in the shared cache — only that the cache *itself* isn't
  individually launched via `execve` (it's `mmap`ed), so this specific class
  of constraint is unlikely to apply to it, but this wasn't proven for every
  loose binary the cryptex ships outside the cache (`usr/lib/swift`,
  `usr/lib/objc`, `System/DriverKit`).
- **The exact boot-arg name(s)** behind `__graft_select_fire`'s three-way
  "boot-arg forced cryptex/livefs/rootfs fallback" switch were not
  identified (only the log strings and the fact they're gated by a mode
  value at a fixed struct offset were confirmed) — not needed for the chosen
  approach (the directory-already-present fast path requires no boot-arg at
  all), but would be a useful debugging lever if the fast path doesn't fire
  for an unexpected reason on real boot.
- **A `Data`-role APFS volume / firmlink split for SpringBoard's first-boot
  writable state** — flagged as unresolved in `docs/re/rootfs-boot.md` §7
  already, still unresolved here; not needed for the assembly itself but
  likely the next blocker after a boot succeeds.
