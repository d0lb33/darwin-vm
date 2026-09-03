# MobileGestalt `DeviceClassNumber` (D47AP)

## Source metadata

- Guest: iOS 27 beta 8, build `24A5430a`; D47AP / t8140 / iPhone17,3.
- Device tree: `/tmp/dvm/data-seed/dt_nvme_welcome.bin`, decoded with the
  repository's `tools/dt_dump.py` / `dt_fixup.py` parser.
- Cache: `/Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e`,
  UUID `58C54E82-C171-300E-AEEE-06DF937AA565`; `maxSlide = 0x20000000`.
- Primary binary: extracted
  `/tmp/dvm/mobilegestalt.y8j8DT/libMobileGestalt.dylib`.

`DeviceClassNumber` is a generic MobileGestalt answer, not a literal mapping
from the visible D47AP marketing/device-tree identity in the inspected
library.  The current generated tree contains no `deviceclass` or
`classnumber` property, so it cannot by itself prove an allowed SetupAssistant
value.  The decisive, non-mutating test is to read `w0` immediately after
SetupAssistant's MobileGestalt query on a disposable boot.

## Query and input evidence

| Item | Address / location | Evidence |
|---|---:|---|
| Root hardware identity | `dt_nvme_welcome.bin` root | `model = iPhone17,3`; `compatible = D47AP\\0iPhone17,3\\0AppleARM`. |
| Product identity | `dt_nvme_welcome.bin:/product` | `unique-model = D47AP`, `sub-product-type = iPhone17,3`, `fdr-product-type = iPhone17,3`, `artwork-device-idiom = phone`, and `large-format-phone = 0`. |
| No direct numeric input | Whole decoded `dt_nvme_welcome.bin` | Recursive normalized property-name scan for `deviceclass` or `classnumber` produced no match.  This does not rule out a computed/cache-backed answer. |
| Public numeric helper | `libMobileGestalt:0x1af7321ac` | `_MobileGestalt_get_deviceClassNumber` initializes `w21 = -1` at `0x1af7321cc`, calls `_MGCopyAnswer` with `DeviceClassNumber` at `0x1af7321d4`--`0x1af7321e0`, verifies CFNumber at `0x1af7321ec`--`0x1af732210`, and returns the value at `0x1af732264`--`0x1af732280`. |
| Generic query resolver | `libMobileGestalt:0x1af726594` | The resolver first looks up the C-string question at `0x1af726604`; on a miss it calls `CNEncode` at `0x1af72665c` and looks up the transformed 22-byte form at `0x1af72667c`. |
| Descriptor dispatch | `libMobileGestalt:0x1af726c48` | For the transformed query, `0x1af726ca4`--`0x1af726cb0` indexes a `0x40`-byte descriptor at `0x1ec293170 + index * 0x40`; it validates the key before returning the descriptor. |
| Cached-value conversion | `libMobileGestalt:0x1af726d78`--`0x1af726e44` | The generic cache read uses descriptor fields `+0x3a` (cache index) and `+0x3c` (type), creating CFNumber answers for numeric descriptor types. |
| Persistent answer cache | `libMobileGestalt:0x1af76b958`--`0x1af76bc98` | `__MGWriteCache` walks up to `0xb74` descriptors, calls `_MGCopyAnswer` at `0x1af76b9ec`, and builds the MobileGestalt cache at `/private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/Library/Caches/com.apple.MobileGestalt.plist`. |
| SetupAssistant branch | `SetupAssistant:0x1cac11fb8`--`0x1cac11fdc` | It queries at `0x1cac11fc4`; `sub/cmp/ccmp/cset` at `0x1cac11fc8`--`0x1cac11fd4` accepts exactly `{1,2,3,4,7}` and stores the consumed flag at `0x1e72ba900`. |

## Positive controls

`ipsw dyld a2s` maps `0x1af7321e0` and `0x1af726c48` to
`/usr/lib/libMobileGestalt.dylib` `__TEXT.__text`, and maps `0x1ec293170` to
that image's `__AUTH_CONST.__const`.  The same complete shared-cache set (not
only its main container) yields the `DeviceClassNumber` cstring at
`0x1af79de6a`, which the helper materializes at `0x1af7321d4`--`0x1af7321d8`.

## Read-only live confirmation

Use a disposable reproduction.  Determine the current shared-cache slide and
first validate `slide + 0x1cac11fc8` with `tools/hmp.py <monitor.sock>
"gva2gpa <VA>"`; do not reuse a slide from another boot.  Set a one-shot,
auto-continuing breakpoint at `slide + 0x1cac11fc8`, record `w0`, then remove
the breakpoint.  A result in `{1,2,3,4,7}` proves this query is not the
unsupported-device-class gate; any other result proves the following branch
stores false at `slide + 0x1e72ba900`.

The public helper provides an independent check when it is exercised: break at
`slide + 0x1af732264` and record `w21` just before it is copied to the return
register.  Neither observation writes device-tree properties, preferences,
disk images, or QEMU model state.

## Open questions

| Question | Observation that settles it |
|---|---|
| What value does this boot return? | `w0` at `slide + 0x1cac11fc8`. |
| Is the answer freshly derived or served from the MobileGestalt cache? | A trace of the selected descriptor and its `+0x3a` cache slot while the query executes. |
| Which backing input determines this descriptor on D47AP? | The resolver callback selected for this query, with its IOKit/DT property reads recorded. |
