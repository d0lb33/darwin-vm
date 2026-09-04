#!/usr/bin/env python3
"""Experimental CPU-only virtual platform adapter for 24A5430a / T8140.

Creates a separate kernelcache; never edits firmware inputs. Requires QEMU's
DARWIN_SMP_PV=1 bridge and -smp N. This substitutes the IOPMGR CPU interface,
not CPU registration, SPTM startup, scheduler code, or online counts.

Source contract: XNU iokit/Kernel/arm/AppleARMSMP.cpp:cpu_boot_thread,
processor_idle_wrapper, idle_timer_wrapper; IOKit/IOPMGR.h.
Virtual CPUs use ordinary interruptible WFI with no physical voltage rails
or idle deadline. Suspend/hotplug is not yet supported.
"""
import argparse
import hashlib
import os
from pathlib import Path
import struct
import tempfile

BASE = 0xFFFFFFF007004000
SHA256 = "dc0f5b6a6fa848053c301949c8376c216c6223c047203b93e408a93d3440f906"


def branch(src, dst):
    return 0x14000000 | (((dst - src) // 4) & 0x3FFFFFF)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("output must be separate from the firmware input")
    data = bytearray(args.input.read_bytes())
    if hashlib.sha256(data).hexdigest() != SHA256:
        parser.error("unsupported kernelcache: expected iPhone17,3 24A5430a")

    def put(address, words, why):
        encoded = struct.pack("<" + "I" * len(words), *words)
        offset = address - BASE
        print(f"{address:#x}: {why}; {data[offset:offset+len(encoded)].hex()} -> {encoded.hex()}")
        data[offset:offset + len(encoded)] = encoded

    # Return no physical PMGR service; retain matching dictionary release.
    put(0xFFFFFFF00B2F8194, [0xD2800000], "virtual platform owns CPU power management")
    # ml_processor_info_t is already zeroed, including idle latency/deadline.
    put(0xFFFFFFF00B2F8314, [branch(0xFFFFFFF00B2F8314, 0xFFFFFFF00B2F8344)],
        "keep zero idle parameters; register real XNU processors")
    # BTI; CBZ timeout,+8; STR XZR,[timeout]; RET. IOPMGR.h permits zero timeout.
    put(0xFFFFFFF00B2F8950, [0xD503245F, 0xB4000041, 0xF900003F, 0xD65F03C0],
        "idle timer: no physical power-gating deadline")
    put(0xFFFFFFF00B2F898C, [0xD503245F, 0xB4000042, 0xF900005F, 0xD65F03C0],
        "idle enter/exit: ordinary WFI")
    # cpu_start has prepared the secondary's SPTM state and loaded its ID in w1.
    # MSR S3_0_C15_C15_7,x1 is our explicitly virtual CPU-start ABI.
    put(0xFFFFFFF00AC61804, [0xD518FFE1, branch(0xFFFFFFF00AC61808, 0xFFFFFFF00AC61834)],
        "release requested CPU through QEMU virtual CPU-start register")
    put(0xFFFFFFF00AC61844, [0xD65F03C0], "return after original authenticated epilogue")
    # Never truncate an artifact another owned VM might currently map.
    with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(data)
            out.flush()
            os.replace(temporary, args.output)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"wrote {args.output}; SHA256={hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
