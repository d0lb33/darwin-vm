#!/usr/bin/env python3
"""Exhaustively check the exact ARM half-conversion fast-path domain."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile


SOURCE = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "target/arm/tcg/darwin-f16-fast.h"

static uint32_t native_widen(uint16_t bits)
{
    _Float16 h;
    float f;
    uint32_t out;
    memcpy(&h, &bits, sizeof(h));
    f = (float)h;
    memcpy(&out, &f, sizeof(out));
    return out;
}

int main(void)
{
    uint64_t widen_accepted = 0, narrow_accepted = 0;
    for (uint32_t h = 0; h <= UINT16_MAX; h++) {
        uint32_t f = 0, roundtrip = 0;
        bool accepted = dvm_f16_to_f32_exact(h, &f);
        uint32_t exponent = (h >> 10) & 0x1f;
        bool expected = (exponent != 0 && exponent != 0x1f) ||
                        ((h & 0x7fff) == 0);
        assert(accepted == expected);
        if (!accepted) {
            continue;
        }
        assert(f == native_widen(h));
        assert(dvm_f32_to_f16_exact(f, &roundtrip));
        assert(roundtrip == h);
        widen_accepted++;
        narrow_accepted++;
    }

    /* Every rejected narrowing case below would need rounding or SoftFloat. */
    uint32_t out;
    assert(!dvm_f32_to_f16_exact(0x00000001, &out)); /* f32 subnormal */
    assert(!dvm_f32_to_f16_exact(0x33800000, &out)); /* f16 subnormal */
    assert(!dvm_f32_to_f16_exact(0x3f800001, &out)); /* discarded bit */
    assert(!dvm_f32_to_f16_exact(0x47800000, &out)); /* exponent 143/AHP */
    assert(!dvm_f32_to_f16_exact(0x7f800000, &out)); /* infinity */
    assert(!dvm_f32_to_f16_exact(0x7fc00000, &out)); /* NaN */

    printf("widen_accepted=%llu narrow_roundtrips=%llu\n",
           (unsigned long long)widen_accepted,
           (unsigned long long)narrow_accepted);
    return 0;
}
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path,
                        default=Path(__file__).resolve().parents[2] / "qemu-sptm")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="dvm-f16-fast-") as tmp:
        tmp = Path(tmp)
        source = tmp / "test.c"
        binary = tmp / "test"
        source.write_text(SOURCE)
        subprocess.run([
            "clang", "-std=c11", "-O3", "-Wall", "-Wextra", "-Werror",
            "-fsanitize=address,undefined", "-I", str(args.qemu.resolve()),
            str(source), "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
