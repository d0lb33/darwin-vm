#!/usr/bin/env python3
"""Create an opt-in host-wall-clock read kernelcache for 24A5430a / T8140.

The paired QEMU implementation exposes DVM_PV_RTC_NS only with
DARWIN_RTC_PV=1.  The patched AppleARMPE::getGMTTimeOfDay reads that register
as Unix nanoseconds, writes its seconds and nanoseconds result, and returns
the native success status.

This does not implement an IORTC service, RTC persistence, settimeofday,
alarms, scheduler changes, readiness changes, or date validation.  It never
edits the input and refuses to overwrite an existing output.
"""
import argparse
import hashlib
import os
from pathlib import Path
import struct
import tempfile


BASE = 0xFFFFFFF007004000
GET_GMT_TIME_OF_DAY = 0xFFFFFFF0085CDFD0
TARGET_UUID = bytes.fromhex("16ff5bb5e04d6dd550f2c6623cf19a56")
ORIGINAL_SHA256 = "dc0f5b6a6fa848053c301949c8376c216c6223c047203b93e408a93d3440f906"
MACHO_64_MAGIC = 0xFEEDFACF
LC_UUID = 0x1B

# First ten instructions of the unmodified 24A5430a AppleARMPE method.
# The tenth is `adrp x0, #0xfffffff007179000` at 0xfffffff0085cdff4.
ORIGINAL_WORDS = (
    0xD503237F,  # pacibsp
    0xD100C3FF,  # sub sp, sp, #0x30
    0xA9014FF4,  # stp x20, x19, [sp, #0x10]
    0xA9027BFD,  # stp fp, lr, [sp, #0x20]
    0x910083FD,  # add fp, sp, #0x20
    0xAA0203F4,  # mov x20, x2
    0xAA0103F3,  # mov x19, x1
    0x528003C8,  # mov w8, #0x1e
    0xF90007E8,  # str x8, [sp, #8]
    0x90FF5D60,  # adrp x0, #0xfffffff007179000
)

# Preserve the landing-pad required by the kernel's indirect call.  Then read
# Unix nanoseconds from S3_0_C15_C15_1, divide by 1,000,000,000, store seconds
# through x1 and nanoseconds through x2, return kIOReturnSuccess (zero).
BTI_C = 0xD503245F
NANOSECONDS_PER_SECOND = 1_000_000_000
PATCH_WORDS = (
    BTI_C,       # bti c
    0xD538FF23,  # mrs x3, S3_0_C15_C15_1 (DVM_PV_RTC_NS)
    0xD2994004,  # movz x4, #0xca00
    0xF2A77344,  # movk x4, #0x3b9a, lsl #16 (x4 = 1,000,000,000)
    0x9AC40865,  # udiv x5, x3, x4
    0x9B048CA3,  # msub x3, x5, x4, x3
    0xF9000025,  # str x5, [x1]
    0xB9000043,  # str w3, [x2]
    0x52800000,  # mov w0, #0 (kIOReturnSuccess)
    0xD65F03C0,  # ret
)


def pack_words(words: tuple[int, ...]) -> bytes:
    return struct.pack("<" + "I" * len(words), *words)


ORIGINAL_BYTES = pack_words(ORIGINAL_WORDS)
PATCH_BYTES = pack_words(PATCH_WORDS)
PATCH_OFFSET = GET_GMT_TIME_OF_DAY - BASE


def validate_patch_words(words: tuple[int, ...]) -> None:
    """Reject a patch shape that would fault at an indirect branch target."""
    if len(words) != len(ORIGINAL_WORDS):
        raise ValueError("RTC PV patch length does not cover its guarded entry")
    if words[0] != BTI_C:
        raise ValueError("RTC PV patch must begin with a BTI c landing pad")


def decode_mov_wide(word: int) -> tuple[int, int]:
    """Decode the immediate and halfword from a MOVZ/MOVK used by this leaf."""
    return (word >> 5) & 0xffff, (word >> 21) & 3


def emulate_patched_get(epoch_ns: int) -> tuple[int, int, int]:
    """Emulate the leaf arithmetic: seconds, nanoseconds, IOReturn status."""
    low, low_hw = decode_mov_wide(PATCH_WORDS[2])
    high, high_hw = decode_mov_wide(PATCH_WORDS[3])
    divisor = (low << (16 * low_hw)) | (high << (16 * high_hw))
    seconds, nanoseconds = divmod(epoch_ns, divisor)
    status_word = PATCH_WORDS[8]
    if status_word & 0xFFE0001F != 0x52800000:
        raise ValueError("RTC status instruction is not MOVZ W0")
    status, status_hw = decode_mov_wide(status_word)
    return seconds, nanoseconds, status << (16 * status_hw)


def macho_uuid(data: bytes) -> bytes:
    """Return the UUID from a little-endian 64-bit Mach-O, or raise ValueError."""
    if len(data) < 32:
        raise ValueError("file is too short for a mach_header_64")
    magic, _, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from("<IiiIIIII", data)
    if magic != MACHO_64_MAGIC:
        raise ValueError("not a little-endian 64-bit Mach-O")
    end = 32 + sizeofcmds
    if end > len(data):
        raise ValueError("Mach-O load commands extend past end of file")
    cursor = 32
    for _ in range(ncmds):
        if cursor + 8 > end:
            raise ValueError("truncated Mach-O load command")
        command, size = struct.unpack_from("<II", data, cursor)
        if size < 8 or cursor + size > end:
            raise ValueError("invalid Mach-O load command size")
        if command == LC_UUID:
            if size != 24:
                raise ValueError("invalid LC_UUID size")
            return data[cursor + 8:cursor + 24]
        cursor += size
    raise ValueError("Mach-O has no LC_UUID")


def validate_input(data: bytes) -> str:
    """Verify target identity and unmodified instruction guard; return SHA-256."""
    if macho_uuid(data) != TARGET_UUID:
        raise ValueError("unsupported kernelcache UUID (expected iPhone17,3 24A5430a)")
    if PATCH_OFFSET + len(ORIGINAL_BYTES) > len(data):
        raise ValueError("kernelcache is too short for AppleARMPE::getGMTTimeOfDay")
    observed = bytes(data[PATCH_OFFSET:PATCH_OFFSET + len(ORIGINAL_BYTES)])
    if observed != ORIGINAL_BYTES:
        raise ValueError(
            "AppleARMPE::getGMTTimeOfDay guard mismatch; refusing to patch an "
            "unknown or already patched kernelcache"
        )
    return hashlib.sha256(data).hexdigest()


def patch_image(data: bytes) -> tuple[bytearray, str]:
    """Return a separately allocated image with only the guarded entry replaced."""
    validate_patch_words(PATCH_WORDS)
    source_sha = validate_input(data)
    result = bytearray(data)
    result[PATCH_OFFSET:PATCH_OFFSET + len(PATCH_BYTES)] = PATCH_BYTES
    return result, source_sha


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="verify and describe the patch without creating output")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("output must be separate from the firmware input")
    if args.output.exists() and not args.dry_run:
        parser.error("refusing to overwrite an existing output")

    patched, source_sha = patch_image(args.input.read_bytes())
    print(f"{GET_GMT_TIME_OF_DAY:#x}: host wall-clock nanoseconds read")
    print(f"  {ORIGINAL_BYTES.hex()} -> {PATCH_BYTES.hex()}")
    print(f"verified UUID={TARGET_UUID.hex()} source SHA256={source_sha}")
    if source_sha != ORIGINAL_SHA256:
        print("accepted by the target UUID and original-instruction guard (derived input)")
    if args.dry_run:
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(patched)
            out.flush()
            os.fsync(out.fileno())
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"wrote {args.output}; SHA256={hashlib.sha256(patched).hexdigest()}")


if __name__ == "__main__":
    main()
