# CADisplay discovery and pre-surface gates

## Source metadata

- Guest: iOS 27 beta 8, build 24A5430a; iPhone17,3 / H17P (t8140).
- QuartzCore: `/tmp/dvm/a442-static/QuartzCore`, extracted read-only from
  `/Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e`.
  All QuartzCore addresses below are unslid cache virtual addresses; its
  `__TEXT` begins at `0x1843e1000`.
- IOMobileFramebuffer: `/tmp/dvm/a442-static/IOMobileFramebuffer`, extracted
  from the same cache.  Its library addresses below are unslid cache virtual
  addresses.
- Dynamic positive control: `/tmp/dvm/probe/UI_NO_D575_SWAP1.serial.log` and
  `/tmp/dvm/probe/UI_NO_D575_SWAP1.stderr.log`, 180-second persistent-NVMe
  boot with `DARWIN_DCP_IOMFB=4` and no D575 callback.

`+[CADisplay mainDisplay]` returns a cached pointer only after
`ensure_displays()` obtains display descriptions from the CoreAnimation render
server; a failed query can therefore leave that pointer null.  On the server
side, an internal display survives discovery only if QuartzCore obtains a
non-null `IOMobileFramebuffer` connection and stores it at
`AppleDisplay+0x6490`; `AppleDisplay::new_server()` has no DCP response test
after that point.  A later, separate pre-update gate reads the IOMobile
Framebuffer digital-out state through user-client selector `0x19`, but the
current evidence does not identify the DCP message that supplies that state.

## Discovery and object-creation gates

| Stage | Static address / condition | Effect on display or surface path | Evidence |
| --- | --- | --- | --- |
| Main-display cache | QuartzCore `+[CADisplay mainDisplay]` `0x1843e2e58-0x1843e2e80` | Calls `ensure_displays()` and returns the global pointer at `0x1e7059188`; a null cache is directly observable by backboardd. | `QuartzCore.otool.txt:8-24` under `/tmp/dvm/a442-static/`. |
| Client query | `ensure_displays()` `0x1843e3318`; `query_displays()` call `0x1843e33b4` | Prevents cache population if the render server cannot return displays. | `QuartzCore.otool.txt:316-364`. |
| Render-server retry / failure | `query_displays()` `0x1844e4830-0x1844e48b0`, error exit `0x1844e4e54-0x1844e4eb0` | It retries a missing or transient `__CASGetDisplays` response until a five-second deadline; another nonzero result logs `CoreAnimation: Unable to query displays from server (%d)` and returns without creating display objects. | `QuartzCore.otool.txt:265681-265722`, `:266054-266080`. |
| Server discovery loop | `-[CAWindowServer _detectDisplays]` `0x18475767c-0x1847577e0` | Invokes each display-family open/count pair; only a non-null opened display enters `attach_contexts` and the server-display collection. | `QuartzCore.otool.txt:916269-916357`. |
| Internal-display count | `AppleInternalDisplay::open` `0x1845f7ce0-0x1845f7d18`; `display_count_of_type` `0x1845f8028-0x1845f80b8` | A missing/empty type-0 list yields no candidate for `create_display_of_type`. | `QuartzCore.otool.txt:550537-550564`, `:550763-550798`. |
| Display-list construction | `_IOMobileFramebufferCreateDisplayList` `0x22a393e5c-0x22a393eb8` | Calls `iomfb_populate_all_display_infos`; returns null if its global display count is zero. | `IOMobileFramebuffer.otool.txt:3369-3394`. |
| Candidate filtering | `create_display_of_type` `0x1845f7dac-0x1845f7f30` | Returns null if the list is unavailable/empty, no entry has the requested type, its name is null, its name/type/index checks fail, or the factory block returns null. | `QuartzCore.otool.txt:550586-550733`. |
| IOMFB open | `AppleDisplay` constructor `0x1845f3d3c-0x1845f3d6c` | Calls `_IOMobileFramebufferOpen` when the numeric ID is nonzero, otherwise `_IOMobileFramebufferOpenByName`; null return skips the connection store. | Stub mapping in `/tmp/dvm/dyld-stubs.txt:43112` and constructor disassembly at `QuartzCore.otool.txt:546461-546477`. |
| Definitive object gate | Store at `AppleDisplay+0x6490`, `0x1845f3d68-0x1845f3d94`; test at `0x1845f7efc-0x1845f7f20` | `create_display_of_type` destroys and rejects the constructed `AppleDisplay` iff `+0x6490` is null. | `QuartzCore.otool.txt:546478-546493`, `:550687-550698`. |
| Server allocation | `AppleDisplay::new_server` `0x1845f77f8-0x1845f7914` | The sole early failure is a null allocation at `0x1845f78e0`; a non-null allocation immediately calls `AccelServer` at `0x1845f78e8-0x1845f78f4`.  No DCP response is examined in this function. | `QuartzCore.otool.txt:550221-550263`. |
| First IOSurface helper | `IOMFBDisplay::create_surface` `0x1847ae2cc-0x1847ae31c` | Always tail-calls `IOSurface::allocate_iosurface`; this helper has no IOMFB/DCP status branch of its own. | `QuartzCore.otool.txt:1006597-1006615`. |

The present no-D575 control supplies a partial positive control for the
hardware-facing half of discovery: the kernel writes
`IOMobileFramebufferUserClient::s_map_display_type ... display type is 0` at
`/tmp/dvm/probe/UI_NO_D575_SWAP1.serial.log:651`.  This proves that the live
user client accepts type zero, but it does **not** by itself prove that the
specific QuartzCore `AppleDisplay` constructor stored a non-null `+0x6490`.

## The later digital-out-state gate

| Item | Contract | Evidence |
| --- | --- | --- |
| QuartzCore condition | `IOMFBDisplay::update_framebuffer_locked` calls `get_hotplug_state` at `0x1847ae5a4-0x1847ae5b4`; it ORs IOReturn with the returned state at `0x1847ae5bc-0x1847ae5c8`.  Nonzero takes the `Display %u get_hotplug_state - not active` exit. | `QuartzCore.otool.txt:1006769-1006814`. |
| Userland wrapper | `CA::IOMobileFramebuffer::get_hotplug_state` is `0x1847b4370`; it invokes `_IOMobileFramebufferGetDigitalOutState` at `0x1847b4404-0x1847b441c`. | `QuartzCore.otool.txt:1012805-1012844`; stub map `/tmp/dvm/dyld-stubs.txt:43112`. |
| IOMFB external method | `_kern_GetDigitalOutState` at `0x22a396754` invokes IOConnect method selector `0x19`, with no input scalars/structure and exactly one scalar output.  It copies that output to the caller only when IOReturn is zero. | `IOMobileFramebuffer.otool.txt:6039-6063`, especially `w1=0x19` at `0x22a396788` and call at `0x22a396794`. |
| Model relationship | Selector `0x19` is implemented by the real guest kernel's IOMFB user client, not directly by `darwin_iomfb.c`.  Its output is derived from guest driver state, which in turn can depend on DCP traffic. | The userland call contract above; no current trace ties selector `0x19` to a particular AP-to-DCP FourCC. |

The last row is deliberately an uncertainty: no current evidence supports
inventing an `Axxx` or `Dxxx` reply as the source of digital-out state.  The
current RPC trace proves `A401` success and then `A454`, `A033`, `A453`,
`A000`, `A412`, A442, A030, and A385 activity
(`/tmp/dvm/probe/UI_NO_D575_SWAP1.stderr.log:140-269`, `:1087-1091`,
`:1586-1626`, `:2327-2331`); it does not prove which one, if any, changes the
selector-`0x19` result.  The known all-zero A442 block replies are separately
optional/disabled policy paths rather than discovery gates; see
`docs/re/iomfb-a442-payload-consumers.md`.

## Staged live breakpoint plan

Resolve each image's runtime `__TEXT` base before setting a breakpoint:

```
backboardd_runtime(a) = B + (a - 0x100000000)
QuartzCore_runtime(a) = Q + (a - 0x1843e1000)
```

`B` and `Q` are the runtime `__TEXT` starts from the guest debugger image
list.  Do not reuse a prior boot's image slide.

| Stage | Breakpoints | Record | Decision | 
| --- | --- | --- | --- |
| 1: is this discovery? | `backboardd:0x1000259fc`, `0x100025a00`, and success log site `0x100025f74` | `x21` (main `CADisplay`) and `x22` (server-specific display) at the two conditional branches. | A null value directs the next run to stage 2.  Both non-null values rule out the main-display discovery gate and skip directly to stage 3. |
| 2: locate discovery failure | `QuartzCore:0x1845f3d68` and `0x1845f7efc` | IOMFB-open result in `x23`, then object pointer and `[object+0x6490]`. | A null `x23`/`+0x6490` identifies an IOMFB open/list problem.  A non-null value means the internal display was retained. |
| 3: test actual pre-update state | `QuartzCore:0x1847ae5b4` and return site `0x1847ae5b8`; if reached, `0x1847b441c` | Update reason, IOReturn in `w0`, and the caller's `u32` hotplug-state output. | Nonzero IOReturn or state proves selector `0x19` suppresses the update.  It calls for kernel/DCP-state tracing, not an invented RPC reply. |
| 4: prove first surface | `QuartzCore:0x1847ae310`, then `0x18447238c` | Allocation result; at `fb_swap_set_layer`, non-null IOSurface argument `x3` and its `IOSurfaceGetID` result. | Separates no render/update from an actual IOSurface handoff. |
| 5: only after stage 4 | H17P `swap_submit` and the existing generic-map/A408/A407 breakpoints in `docs/re/iomfb-d575-external-cancel.md`. | Submission fields and mapping return. | Distinguishes userspace allocation from a later kernel/DCP scanout failure. |

## Open questions

| Question | Observation that settles it |
| --- | --- |
| Is `mainDisplay` currently null because `AppleDisplay+0x6490` was never set? | A stage-1 headless stop plus stage-2 records from the same boot. |
| Is selector `0x19` reached on the current path and does it report inactive? | A stage-3 hit recording both IOReturn and its output scalar. |
| Which guest DCP state, if any, controls selector `0x19`? | Kernel call trace from `IOMobileFramebufferUserClient` selector `0x19` to the state producer, correlated with a DCP request/callback trace. |
| Does the guest allocate an IOSurface once it has an active display? | Stage-4 allocation and `fb_swap_set_layer` hits with a non-null `x3`. |
