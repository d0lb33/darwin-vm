#!/usr/bin/env python3
"""Verify final-only RGhA scanout delivery, not compositor scene semantics.

Requires numpy/Pillow. Reads the actual A408 request and source/output retained
by QEMU at presentation, then compares the stopped console screenshot. No
linear-to-sRGB transform: exact guest tags 13/1 denote sRGB/BT.709 primaries.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
import numpy as np
from PIL import Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture', type=Path)
    a = p.parse_args()
    root = a.capture
    names = ['last-scanout.a408', 'last-scanout.rgha', 'last-scanout.bgra', 'scanout.ppm']
    hashes = {n: hashlib.sha256((root / n).read_bytes()).hexdigest() for n in names}
    request = (root / names[0]).read_bytes()
    if len(request) != 4084:
        raise ValueError('A408 size')
    u32 = lambda off: struct.unpack_from('<I', request, off)[0]
    base = 0x6e0
    fmt, transfer, color = u32(base + 0xb), request[base + 0x13], request[base + 0x14]
    row, w, h = u32(base + 0x15), u32(base + 0x21), u32(base + 0x25)
    if (fmt, transfer, color) != (0x52476841, 13, 1) or request[0x54c]:
        raise ValueError('unsupported format/color/EDR contract')
    if not (0 < w <= 8192 and 0 < h <= 8192 and row >= w * 8 and row % 8 == 0):
        raise ValueError('geometry')
    source = (root / names[1]).read_bytes()
    if len(source) != row * h:
        raise ValueError('source span')
    rgba = np.frombuffer(source, dtype='<f2').reshape(h, row // 2)[:, :w*4].reshape(h, w, 4).astype(np.float64)
    if not np.isfinite(rgba).all():
        raise ValueError('nonfinite component')
    # Independent floating-point expression; QEMU uses binary16 bit arithmetic.
    expected = np.floor(np.clip(rgba, 0, 1) * 255 + .5).astype(np.uint8)[:, :, [2, 1, 0, 3]]
    output = np.frombuffer((root / names[2]).read_bytes(), dtype=np.uint8).reshape(h, w, 4)
    screenshot = np.asarray(Image.open(root / names[3]).convert('RGB'))
    conversion_errors = int(np.count_nonzero(expected != output))
    display_errors = int(np.count_nonzero(screenshot != output[:, :, [2, 1, 0]]))
    report = dict(scope=__doc__, swap=u32(0x98), width=w, height=h, row=row,
                  format=hex(fmt), transfer=transfer, primaries=color,
                  minimum=rgba.min(axis=(0, 1)).tolist(), maximum=rgba.max(axis=(0, 1)).tolist(),
                  conversion_errors=conversion_errors, display_errors=display_errors,
                  opaque_alpha=bool(np.all(rgba[:, :, 3] == 1)),
                  nonzero_rgb_components=int(np.count_nonzero(rgba[:, :, :3])),
                  source_to_console_verified=conversion_errors == display_errors == 0,
                  compositor_semantic_correctness_verified=False, hashes=hashes)
    (root / 'scanout-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    # Direct format conversion of the actual console; no brightness adjustment.
    Image.open(root / 'scanout.ppm').save(root / 'scanout.png')
    print(json.dumps(report, indent=2))
    if not report['source_to_console_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
