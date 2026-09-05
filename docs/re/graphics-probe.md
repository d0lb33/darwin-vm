# Native graphics bitmap probe

`tools/re/graphics_probe.c` is a one-shot, signed guest diagnostic.  It makes
one 64 by 64 BGRA8 premultiplied-first CoreGraphics bitmap, creates its image,
then uses that known source image in both IconFoundation context paths:

1. `+[IFGraphicsContext pixelFormatFromCGImage:]`, followed by
   `bitmapContextWithSize:scale:pixelFormat:`;
2. `bitmapContextWithSize:scale:derivePixelFormatFromCGImage:`.

`IFGraphicsContext.image` returns a `UIImage`; the probe sends its verified
`CGImage` accessor before it calls any CoreGraphics image inspection API.  That
accessor is borrowed and is never passed to `CGImageRelease`.  The probe logs
every returned context, UIImage, and CGImage, plus source/result dimensions,
bits/component, bits/pixel, bytes/row, alpha/bitmap info, and color-space
model/components.  An optional PNG positional argument is decoded with
`+[UIImage imageWithContentsOfFile:]` and runs the same two calls, avoiding a
Settings launch.

The target geometry is configurable while keeping allocations bounded:

```
graphics-probe --size 120 120 --scale 3 /path/to/icon.png
```

Logical width/height must be positive and at most 4096, scale must be positive
and at most 16, and the unrounded physical width/height and area must not
exceed 4096 by 4096 and 16 MiPixels.  The request is logged before both private
calls.  This maps to the historical caller: `_IconServices_SwiftUI` obtains
`size` and `scale` from its target image (`x21`) at `0x1d41e3d84..0x1d41e3d9c`,
uses the source image's `CGImage` (`x20`) at `0x1d41e3d5c..0x1d41e3d70`, then
passes `d0/d1`, `d2`, and `x2` at `0x1d41e3da0..0x1d41e3db0`.

Before `image`, the probe follows the remaining relevant historical sequence:
it gets `cgContext`, sets interpolation quality 3, gets `bounds`, and sends
`drawCGImage:inRect:` with the source image and returned bounds.  Those calls
occur at `_IconServices_SwiftUI+0xd8` (`0x1d41e3dc0`), `+0xe8`
(`CGContextSetInterpolationQuality`), `+0xf8` (`bounds`), and `+0x104`
(`drawCGImage:inRect:`).  The `bounds` CGRect is returned in `d0` through
`d3` and immediately supplies the draw call.  The probe logs its exact bounds
and uses a matching HFA CGRect declaration.

Build without changing a trust cache:

```
tools/re/build_graphics_probe.sh /tmp/dvm/GRAPHICS_PROBE1
```

That writes `graphics-probe`, `codesign.txt`, `hashes.txt`, and `helper.tc`.
`helper.tc` is deliberately unmerged; image installation owns the disposable
trust-cache merge and launchd invocation.

The ABI is taken from `WARM_SETTINGS1`'s 24A5430a local shared-cache artifacts,
not guessed from an SDK header.  At `IconFoundation+0x42fd4`, the pixel-format
class method stores `x2` then consumes the `CGSize` and scale from `d0`, `d1`,
and `d2`; `CGBitmapContextCreate` is called at `0x22ff431a4`.  The derived
class method is at `0x22ff43278`; `-[IFGraphicsContext image]` is at
`0x22ff43570` calls `CGBitmapContextCreateImage` at `0x22ff43590`, wraps that
result in a UIImage, releases the temporary CGImage, and returns the UIImage.
`_IconServices_SwiftUI` called the derived method at `0x1d41e3db0`, then
`image` at `0x1d41e3df4`, before the historical nil branch trapped at
`0x1d41e3ea0`.  The source records are inherited from the
`NATIVE_HOME_UART_FIXED1` checkpoint; this probe does not claim a new Settings
reproduction.

Exit 1 means an expected framework/symbol/class/selector is absent.  Exit 3
means a required bitmap context or image was nil after all calls completed.
The output line prefix is `GRAPHICS_PROBE_` for launchd/serial collection.
