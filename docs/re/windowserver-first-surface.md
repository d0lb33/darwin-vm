# WindowServer first IOSurface and swap boundary

## Source metadata

- Guest: iOS 27 beta 8, build 24A5430a; target iPhone17,3 / H17P (t8140).
- Executable: `/Users/jdolbe1/dvm-artifacts/extract/bin/backboardd`, arm64e PIE, `__TEXT` static VM address `0x100000000`.
- Framework: QuartzCore from `/Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e` (symbols from the adjacent `.a2s`), `__TEXT` static VM address `0x1843e1000`.
- Kernel-side comparison: `docs/re/iomfb-d575-external-cancel.md`; its kernel static addresses are for the same guest build and must be re-slid for each boot.

`backboardd` enters `CAWindowServer` at static `0x1000257cc`, chooses a display-backed path only when both its main display and server-specific display objects are non-null, then reaches the normal setup-complete path.  QuartzCore's IOMFB display implementation allocates a real IOSurface at static `0x18471bf38` and hands it to `IOMFBDisplay::fb_swap_set_layer` at static `0x18447238c`; this is the narrowest userspace point at which a non-null surface can be demonstrated.  The existing H17P kernel trace begins at `swap_submit` and can distinguish a real submission reaching the display driver from a userspace-only render or a D575-related external-cancel path.

## Static path and evidence

| Phase | Static address | Observed decision or handoff | Evidence |
| --- | ---: | --- | --- |
| WindowServer start | `backboardd:0x1000257cc` | `StartWindowServer` creates `CAWindowServer` through `[CAWindowServer serverWithOptions:]` at `0x100025918`. | Direct `bl` in `StartWindowServer`; prior disassembly in `docs/re/ca-software-path.md`. |
| Display availability | `backboardd:0x10002593c`, `0x100025954` | Gets `[CADisplay mainDisplay]`, then asks the server for `displayWithDisplayId:`. | Direct calls in the same function; `x21` holds main display and `x22` holds the server display. |
| Headless gate | `backboardd:0x1000259fc`, `0x100025a00` | `cbz x21` or `cbz x22` reaches the same headless branch at `0x100025df0`; otherwise execution remains display-backed. | Disassembly and string `StartWindowServer: headless (display:%{BOOL}u/server:%{BOOL}u)` at `0x100083db4`; setup-complete site is `0x100025f74` (see `docs/re/ca-software-path.md`). |
| Display discovery | `QuartzCore:0x1843e2e58`, `0x1843e3318` | `+[CADisplay mainDisplay]` calls `ensure_displays()` and returns the cached main display. | QuartzCore symbols/disassembly; cached display data at `0x1e7059188`. |
| Internal display/server | `QuartzCore:0x1845f7cb0`, `0x1845f7f84`, `0x1845f77f8` | `AppleInternalDisplay::open(unsigned long)` builds the Apple display (`AppleDisplay` constructor at `0x1845f36b4`); `AppleDisplay::new_server()` builds an `AccelServer` (constructor `0x184629180`). | QuartzCore symbol map and direct call sites. |
| Render route | `QuartzCore:0x184451598`, `0x184776174`, `0x1844510f0` | `AccelServer::render_update` can fall through to `Server::render_update`, which calls `Display::render_display`. | Direct calls; the earlier software-renderer route is documented in `docs/re/ca-software-path.md`. |
| IOSurface creation | `QuartzCore:0x1847ae2cc`, `0x1847ae310`, `0x18471bf38` | `IOMFBDisplay::create_surface` directly calls `CA::WindowServer::IOSurface::allocate_iosurface`. | `bl` at `0x1847ae310`; allocation is reached through `Display::allocate_surface` at `0x1846820e4`, and `IOMFBDisplay::update_surface` at `0x18445b330`. |
| First swap boundary | `QuartzCore:0x18446bb14`, `0x184478b48`, `0x18447238c` | `IOMFBDisplay::finish_update` calls `swap_set_layer`, which reaches `fb_swap_set_layer`; its entry has `x3 = __IOSurface *` (implicit `this` is `x0`, framebuffer reference `x1`, layer `w2`). | `finish_update` calls `swap_set_layer` at `0x18446c734` and `0x18446c768`; `fb_swap_set_layer` calls `IOSurfaceGetID(x3)` at `0x1844723d8` / `0x1844723fc` before its framebuffer handoff. |
| Kernel correlation | `kernel:0xfffffff00918e3c4` | H17P `swap_submit` is the first known kernel-side submit point; its normal map path reaches generic mapping and then A408 or A407 construction. | `docs/re/iomfb-d575-external-cancel.md`: generic map `0xfffffff00a0c366c`, map calls `...40d4` / `...41cc`, A408 `...4664`, A407 `...477c`. |

The two `finish_update` calls do not alone establish a populated layer: they also occur for layer-state updates.  A non-null `x3` at `fb_swap_set_layer`, followed by `IOSurfaceGetID`, is the required userspace proof that an actual IOSurface reached the handoff.

## Runtime address calculation

Do not use these static addresses as runtime breakpoint addresses without resolving each image's loaded `__TEXT` base:

```
backboardd_runtime(address) = B + (static_address - 0x100000000)
QuartzCore_runtime(address) = Q + (static_address - 0x1843e1000)
```

Here `B` and `Q` are the runtime `__TEXT` starts reported by the guest debugger/image list for `backboardd` and QuartzCore respectively.  For the previously observed kernel boot, the slide was `+0x20000000`, making H17P `swap_submit` live at `0xfffffff02918e3c4`; re-measure that slide rather than assuming it persists across boots.

## Ranked runtime test plan

| Rank | Breakpoint or observation | What to record | Interpretation |
| ---: | --- | --- | --- |
| 1 | Run the established no-D575 control first. | Whether the normal display path reaches the breakpoints below. | Prevents a known D575 external-cancel condition from being mistaken for a general display failure. |
| 2 | `QuartzCore:0x18447238c` (`IOMFBDisplay::fb_swap_set_layer`). | `x3`, layer `w2`, and the `IOSurfaceGetID` result. | Non-null `x3` plus the ID call proves the first real IOSurface has reached the IOMFB swap boundary. This is the primary userspace breakpoint. |
| 3 | Kernel H17P `swap_submit` (`0xfffffff00918e3c4` static). | Hit/no-hit and the existing submission fields described in `iomfb-d575-external-cancel.md`. | A hit after rank 2 proves the submission crossed from QuartzCore into the kernel display path. |
| 4 | Kernel generic map `0xfffffff00a0c366c`, then A408 `...0a0c4664` and A407 `...0a0c477c`. | Which mapping variant executes and returned status/handle. | Separates valid imported-surface mapping from a later DCP/driver rejection. |
| 5 | `QuartzCore:0x1847ae310` or callee `0x18471bf38`. | Allocation return and the returned surface pointer. | If rank 2 is absent, distinguishes no surface allocation from a later rendering/swap omission. |
| 6 | `backboardd:0x1000259fc`, `0x100025a00`, and `0x100025f74`. | `x21`/`x22` at the gates and setup-complete log. | Distinguishes failure to obtain a CADisplay from a display-backed WindowServer that never paints. |

## Open questions

| Question | Observation that settles it |
| --- | --- |
| Does the present guest allocate a display surface at all? | A hit on `allocate_iosurface` with a non-null returned surface, followed by rank-2 `x3` non-null. |
| Does an IOSurface submission cross into H17P? | A rank-2 hit followed in order by `swap_submit` in the same boot. |
| Does the failure occur during mapping or after a valid mapped surface? | Capture the generic-map and A407/A408 hits, including their returned status, after `swap_submit`. |
| Is the failure before WindowServer obtains a display? | Capture either headless gate with null `x21`/`x22`, or setup-complete plus the later surface breakpoints. |

