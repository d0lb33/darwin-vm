# Why the lock-screen wallpaper is black

2026-09-07. The lock screen draws its clock, date, status bar and controls
but the wallpaper behind them is black. The wallpaper is a Metal render,
so this is blocked on GPU acceleration, not on the display path.

## Evidence

The configured poster provider is `MercuryPosterExt`
(`/System/Library/ExtensionKit/Extensions/MercuryPosterExtension.appex`,
the process that crashed with AMX SIGILLs before `docs/re/native-amx.md`).
Read from the merged System image (`rootfs_cx.dmg`, attached read-only
through `tools/rootfs/safe_attach.sh`):

- `otool -L` on the extension binary links `Metal.framework`,
  `MetalKit.framework`, `libswiftMetal.dylib`, `libswiftMetalKit.dylib`,
  plus `QuartzCore`, `CoreImage` and `USDObjCKit`.
- Its Swift symbols include a `MercuryPosterExtension.WallpaperMetalRendering`
  protocol, and its ObjC metadata references the Metal 4 API surface
  (`MTL4CommandQueue`, `MTL4Compiler`, `MTL4ArgumentTable`,
  `MTLAccelerationStructure`, ...).
- The bundle ships three shader libraries, `Celosia.metallib`,
  `Space.metallib`, `default.metallib`, and EXR height/depth maps
  (`D1_D2_D3_16b_Z-*.exr`, `DHero_DBorder_Z_16b_Z-*.exr`) that the shaders
  sample.

With no `IOAcceleratorES` service in the guest (`dt_fixup.py` deletes
`/arm-io/sgx`; see the GPU section of `CLAUDE.md`),
`MTLCreateSystemDefaultDevice()` returns nil, the extension has nothing to
render into, and the poster layer stays black. CoreAnimation's software
rasteriser composites everything else, which is why the UI on top is
visible.

## What would change it

- GPU acceleration (the paravirtualised GPU work) is the real fix; nothing
  in the DCP or IOMFB models can supply a Metal device.
- A non-Metal poster (a static photo or a gradient provider) would show a
  wallpaper without a GPU, but that is a change to the guest's wallpaper
  configuration, not a hardware fix, and was not pursued.

Not measured here: whether any other lock-screen element is Metal-only.
The clock, date, status bar, flashlight/camera buttons and home indicator
all render through the software path today.
