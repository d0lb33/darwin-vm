#!/usr/bin/env python3
"""Stage the measured 24A5430a software-display allocation policy.

QuartzCore 0x1847ae2ec skips the UC-normal-memory capability check when the
software renderer requested cached surfaces. The AP rejects that 0x400 mode.
Replace only that branch with NOP, so the existing capability check selects
0x700 when supported. No driver validation or allocation result is changed.
See docs/re/surface-cache-and-completion.md. Development-image experiment only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess

ADDRESS = 0x1847AE2E4
OFFSET = 0x43AE2E4
EXPECTED = bytes.fromhex("0ae44e3906808052ea001037ea4f70b2")
REPLACEMENT = bytes.fromhex("1f2003d5")
PAGE_SIZE = 16384
SUPERBLOB_MAGIC = 0xFADE0CC0
CODEDIRECTORY_MAGIC = 0xFADE0C02
CSSLOT_CODEDIRECTORY = 0
SUPERBLOB_HEADER = struct.Struct(">III")
SUPERBLOB_INDEX = struct.Struct(">II")
CODEDIRECTORY_HEADER = struct.Struct(">9I4BI")


def parse_code_directory(signature: bytes, page_index: int, *,
                         required_code_end: int = 0) -> tuple[bytearray, int, int]:
    """Return the primary CodeDirectory and its superblob/hash offsets.

    Every offset is checked against the *declared* superblob length.  The
    signature mapping may include trailing bytes, which must never make a
    truncated SuperBlob or CodeDirectory appear valid.
    """
    if len(signature) < SUPERBLOB_HEADER.size:
        raise ValueError("signature is too short for a SuperBlob header")
    magic, length, count = SUPERBLOB_HEADER.unpack_from(signature)
    if magic != SUPERBLOB_MAGIC:
        raise ValueError("unsupported signature SuperBlob magic")
    if length < SUPERBLOB_HEADER.size or length > len(signature):
        raise ValueError("invalid declared SuperBlob length")
    index_end = SUPERBLOB_HEADER.size + count * SUPERBLOB_INDEX.size
    if index_end > length:
        raise ValueError("truncated SuperBlob index")

    slots = {}
    for index in range(count):
        slot, offset = SUPERBLOB_INDEX.unpack_from(
            signature, SUPERBLOB_HEADER.size + index * SUPERBLOB_INDEX.size
        )
        if slot in slots:
            raise ValueError("duplicate SuperBlob slot")
        slots[slot] = offset
    if CSSLOT_CODEDIRECTORY not in slots:
        raise ValueError("SuperBlob has no primary CodeDirectory")

    cd_offset = slots[CSSLOT_CODEDIRECTORY]
    if cd_offset < index_end:
        raise ValueError("CodeDirectory overlaps the SuperBlob index")
    if cd_offset + CODEDIRECTORY_HEADER.size > length:
        raise ValueError("truncated CodeDirectory header")
    fields = CODEDIRECTORY_HEADER.unpack_from(signature, cd_offset)
    (magic, cd_length, _version, flags, hash_offset, _identifier_offset,
     _special_slots, code_slots, limit, hash_size, hash_type, _platform,
     page_shift, _spare) = fields
    if magic != CODEDIRECTORY_MAGIC:
        raise ValueError("primary SuperBlob slot is not a CodeDirectory")
    if cd_length < CODEDIRECTORY_HEADER.size or cd_offset + cd_length > length:
        raise ValueError("truncated CodeDirectory")
    if flags != 2 or hash_type != 2 or hash_size != 32 or page_shift != 14:
        raise ValueError("requires existing ad-hoc SHA-256/16 KiB CodeDirectory")
    if required_code_end > limit:
        raise ValueError("target instructions are outside the CodeDirectory code limit")
    if page_index >= code_slots:
        raise ValueError("target code page is outside the CodeDirectory hash table")
    hashes_end = hash_offset + code_slots * hash_size
    if hash_offset < CODEDIRECTORY_HEADER.size or hashes_end > cd_length:
        raise ValueError("invalid CodeDirectory code-hash table")

    slot_offset = hash_offset + page_index * hash_size
    return bytearray(signature[cd_offset:cd_offset + cd_length]), cd_offset, slot_offset


def patch_code_page(source: bytes) -> tuple[bytes, bytearray]:
    """Guard and replace the four target instruction bytes in one 16 KiB page."""
    if len(source) != PAGE_SIZE:
        raise ValueError("target code page is truncated")
    page_offset = OFFSET % PAGE_SIZE
    original = source[page_offset:page_offset + len(EXPECTED)]
    if original != EXPECTED:
        raise ValueError("unsupported cache; expected 24A5430a QuartzCore allocation instructions")
    page = bytearray(source)
    page[page_offset + 8:page_offset + 12] = REPLACEMENT
    return original, page


def rehash_code_page(signature: bytes, page_index: int, source_page: bytes,
                     patched_page: bytes, *, required_code_end: int = 0
                     ) -> tuple[bytearray, int, int, bytes, bytes]:
    """Replace exactly one checked SHA-256 code-page hash in the CodeDirectory."""
    cd, cd_offset, slot_offset = parse_code_directory(
        signature, page_index, required_code_end=required_code_end
    )
    old_hash = bytes(cd[slot_offset:slot_offset + 32])
    expected_old_hash = hashlib.sha256(source_page).digest()
    if old_hash != expected_old_hash:
        raise ValueError("source code page does not match its signature")
    new_hash = hashlib.sha256(patched_page).digest()
    cd[slot_offset:slot_offset + 32] = new_hash
    return cd, cd_offset, slot_offset, old_hash, new_hash


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cache", type=Path, help="unmodified dyld_shared_cache_arm64e.01")
    p.add_argument("output", type=Path, help="new output directory")
    p.add_argument("--tc", type=Path, required=True, help="existing system/helper trust cache")
    a = p.parse_args()
    cache_size = a.cache.stat().st_size
    with a.cache.open("rb") as f:
        header = f.read(4096)
        if len(header) < 0x38 or not header.startswith(b"dyld"):
            p.error("unsupported cache header")
        signature_offset, signature_size = struct.unpack_from("<QQ", header, 0x28)
        if signature_offset > cache_size or signature_size > cache_size - signature_offset:
            p.error("signature range is outside the shared cache")
        f.seek(signature_offset)
        signature = f.read(signature_size)
        if len(signature) != signature_size:
            p.error("truncated shared-cache signature")
        page_index = OFFSET // PAGE_SIZE
        f.seek(page_index * PAGE_SIZE)
        source_page = f.read(PAGE_SIZE)
    try:
        original, page = patch_code_page(source_page)
        cd, cd_offset, slot_offset, old_hash, new_hash = rehash_code_page(
            signature, page_index, source_page, page,
            required_code_end=OFFSET + len(EXPECTED),
        )
    except ValueError as error:
        p.error(str(error))
    cdhash = hashlib.sha256(cd).hexdigest()[:40]
    absolute_hash_offset = signature_offset + cd_offset + slot_offset
    a.output.mkdir(exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    image = a.output / "ramdisk.dmg"
    shutil.copyfile(repo / "firmware/ramdisk.dmg", image)
    attach = repo / "tools/rootfs/safe_attach.sh"
    mount = Path(subprocess.check_output([str(attach), "attach", str(image), "--owners", "on"], text=True).strip())
    try:
        dest = mount / "libexec"
        (dest / "dvm-policy-header").write_bytes(header)
        (dest / "dvm-policy-before").write_bytes(original)
        (dest / "dvm-policy-after").write_bytes(original[:8] + REPLACEMENT + original[12:])
        (dest / "dvm-policy-word").write_bytes(REPLACEMENT)
        (dest / "dvm-policy-old-hash").write_bytes(old_hash)
        (dest / "dvm-policy-new-hash").write_bytes(new_hash)
        (dest / "dvm-policy-offset.sh").write_text(f"hash_offset={absolute_hash_offset}\n")
        shutil.copyfile(Path(__file__).with_name("install_display_policy.sh"), dest / "dvm-policy-install.sh")
        subprocess.run(["sync"], check=True)
    finally:
        subprocess.run([str(attach), "detach", str(mount)], check=True)
    (a.output / "hashes.txt").write_text(cdhash + "\n")
    subprocess.run(["python3", str(repo / "build_tc.py"), str(a.output / "hashes.txt"), str(a.output / "cache.tc")], check=True)
    subprocess.run(["python3", str(repo / "tools/rootfs/merge_tc.py"), str(a.output / "system.tc"), str(a.tc), str(a.output / "cache.tc")], check=True)
    (a.output / "policy.json").write_text(json.dumps(dict(cache=str(a.cache.resolve()),
        static=hex(ADDRESS + 8), offset=hex(OFFSET + 8), before=original[8:12].hex(),
        after=REPLACEMENT.hex(), code_directory_hash=cdhash,
        signature_page_hash_offset=hex(absolute_hash_offset),
        purpose="Use existing UC-normal capability selection for software display surfaces"), indent=2) + "\n")


if __name__ == "__main__":
    main()
