#!/usr/bin/env python3
"""Convert a first_surface_callbacks IOSurface RAM capture to a PPM frame."""
import argparse
import hashlib
import json
from pathlib import Path


FORMATS = {
    0x42475241: (2, 1, 0),  # 'BGRA' bytes -> RGB
    0x41524742: (1, 2, 3),  # 'ARGB'
    0x52474241: (0, 1, 2),  # 'RGBA'
    0x41424752: (3, 2, 1),  # 'ABGR'
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("event", help="success.NONNULL_IOSURFACE.json")
    parser.add_argument("output", help="destination .ppm")
    args = parser.parse_args()

    event = json.loads(Path(args.event).read_text())
    metadata = event["surface_metadata"]
    capture = event["surface_capture"]
    raw_path = Path(capture["raw_path"])
    raw = raw_path.read_bytes()
    expected_hash = capture.get("capture_sha256")
    actual_hash = hashlib.sha256(raw).hexdigest()
    if expected_hash and actual_hash != expected_hash:
        raise SystemExit("raw IOSurface SHA-256 does not match the event")

    width = metadata["width"]
    height = metadata["height"]
    stride = metadata["bytes_per_row"]
    pixel_format = metadata["pixel_format"]
    channels = FORMATS.get(pixel_format)
    if channels is None:
        fourcc = pixel_format.to_bytes(4, "big").decode("ascii", "replace")
        raise SystemExit("unsupported IOSurface pixel format 0x%x (%r)" %
                         (pixel_format, fourcc))
    if not (0 < width <= 16384 and 0 < height <= 16384 and
            width * 4 <= stride <= 1024 * 1024):
        raise SystemExit("implausible IOSurface geometry")
    if len(raw) < stride * height:
        raise SystemExit("short IOSurface RAM capture")

    rgb = bytearray(width * height * 3)
    destination = 0
    red, green, blue = channels
    for y in range(height):
        row = raw[y * stride:y * stride + width * 4]
        for x in range(0, len(row), 4):
            rgb[destination] = row[x + red]
            rgb[destination + 1] = row[x + green]
            rgb[destination + 2] = row[x + blue]
            destination += 3

    header = "P6\n%d %d\n255\n" % (width, height)
    output = Path(args.output)
    output.write_bytes(header.encode("ascii") + rgb)
    print(json.dumps({
        "output": str(output),
        "width": width,
        "height": height,
        "pixel_format": pixel_format,
        "source": str(raw_path),
        "source_sha256": actual_hash,
        "ppm_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "nonzero_rgb_bytes": sum(byte != 0 for byte in rgb),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
