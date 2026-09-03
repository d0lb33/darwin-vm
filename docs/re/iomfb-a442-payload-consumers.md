# iOS 27 A442 userspace payload consumers

## Source metadata and scope

This note covers iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140
(H17P).  The userspace sources are the device dyld cache at
`~/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e` and its subcaches.
QuartzCore and IOMobileFramebuffer were extracted read-only with `ipsw dyld
extract ... --stubs`; their unslid cache virtual addresses are retained.

The dynamic identification of external selector `0x4f` and block IDs `0x4c`,
`0x4a`, and `0x79` comes from `/tmp/dvm/UI_A442_CLIENT2.lldb.log`.  The three
requests and lengths are independently visible in
`/tmp/dvm/probe/UI_A442_CLIENT2.stderr.log:1603-1641`.  Static analysis
artifacts used below are:

- `/tmp/dvm/a442-static/QuartzCore.otool.txt`
- `/tmp/dvm/a442-static/IOMobileFramebuffer.otool.txt`
- `/tmp/dvm/a442-static/GetBlock.all.calls.txt`
- `/tmp/dvm/a442-static/update_display_bandwidth_limits.slice.txt`
- `/tmp/dvm/a442-static/bandwidth-consumers.slice.txt`

This analysis establishes only the userspace parsers and their control flow.
It does not assign undocumented physical meaning to individual payload fields.

## Result

The three all-zero, successful A442 replies do **not** make
`+[CADisplay mainDisplay]` nil and do **not** suppress IOSurface creation
through the code paths that consume them:

- Blocks `0x4a` and `0x4c` jointly describe an optional object exposed by
  `IOMFBDisplay::average_pixel_constraints()`.  Zero or malformed data leaves
  that object absent and the `AppleDisplay` constructor continues normally.
- Block `0x79` is the backing store for
  `IOMobileFramebufferGetBandwidth()`.  An all-zero result explicitly disables
  QuartzCore's display-bandwidth rejection predicate, so the predicate returns
  `false` (not over-limit).

Consequently, inventing nonzero A442 data is not an evidence-backed way to
unblock the first surface or Welcome frame.  It can instead enable optional
constraints with fabricated parameters.

## Blocks 0x4c and 0x4a: optional average-pixel constraints

Both calls occur directly inside
`CA::WindowServer::AppleDisplay::AppleDisplay(...)`, whose entry is
`0x1845f36b4`:

| Block | Call | Buffer | Size |
|---|---:|---:|---:|
| `0x4c` | `0x1845f4a98` | `sp+0xc90` | `0x808` |
| `0x4a` | `0x1845f4ac4` | `sp+0x30` | `0xc5c` |

Both are calls to the device-cache stub at `0x1881501c0`, identified by
`ipsw dyld stubs` as `_IOMobileFramebufferGetBlock`.  That public wrapper is
at `0x22a3c00b8`; it loads the connection's function pointer from `+0xe10`.
The installed implementation is `_kern_GetBlock` at `0x22a396900`, which
issues external selector `0x4f`.

The consumer first requires both IOReturn values to be zero
(`0x1845f4ac8-0x1845f4acc`).  The remaining acceptance tests are:

| Buffer field | Test | Evidence |
|---|---|---|
| block `0x4a` `+0x00` u32 | nonzero | load/`cbz` at `0x1845f4ad0-0x1845f4ad4` |
| block `0x4c` `+0x00` u32 | nonzero | `0x1845f4ad8-0x1845f4ae0` |
| block `0x4c` `+0x04` u32 | inclusive range 2 through 256 | subtract 2 and compare with `0xfe` at `0x1845f4ae4-0x1845f4af4` |
| block `0x4c` float array at `+0x08` | for each index 1 through count-1, value must be greater than element 0 | `0x1845f4af8-0x1845f4b20` |
| block `0x4c` float array at `+0x408` | each element after the first must be strictly less than its predecessor | `0x1845f4b24-0x1845f4b44` |

The apparently out-of-range accesses at `block_4a+0xc60`, `+0xc64`, and
`+0xc68` are not fields of the `0xc5c`-byte block.  The compiler placed the
`0x4c` buffer immediately afterward at `sp+0xc90`, so those addresses are
exactly block `0x4c` offsets `+0`, `+4`, and `+8`.

Only after every test succeeds does QuartzCore allocate a 0x30-byte local
constraint object and store it at `AppleDisplay+0x6bb0`
(`0x1845f4b48-0x1845f4b74`).  Its observable local layout is:

| Object field | Source |
|---|---|
| `+0x00` pointer | copy of `count * 4` bytes from block `0x4c+0x08` |
| `+0x08` pointer | copy of `count * 4` bytes from block `0x4c+0x408` |
| `+0x10`, `+0x14`, `+0x18` f32 | block `0x4a+0x90`, `+0x94`, `+0x98` |
| `+0x20` u64 | zero-extended count from block `0x4c+0x04` |

Any failed test reaches the normal constructor continuation at
`0x1845f4848`.  A malformed nonzero array additionally logs
`"Unexpected pixel constraints data"` at `0x1845f4e80-0x1845f4ed0`, then
reaches the same continuation.  An all-zero reply takes the first field-fail
branch at `0x1845f4ad4`; it is not an error exit.

The member's identity and optional nature have independent named evidence:

- `IOMFBDisplay::average_pixel_constraints()` at `0x184431e20` simply returns
  `self+0x6bb0`.
- QuartzCore's brightness diagnostic prints
  `"Average Pixel Constraints: NONE"` when that accessor returns null
  (`0x18468c138-0x18468c1dc`).
- The base `Display::average_pixel_constraints()` also exists at
  `0x1846125e8`, so absence is supported by the class contract rather than an
  impossible constructor state.

### Relationship to display discovery

`+[CADisplay mainDisplay]` at `0x1843e2e58` calls `ensure_displays()` and
returns the cached display at `0x1e7059188`.  Internal-display discovery goes
through `AppleInternalDisplay::open(unsigned long)` at `0x1845f7cb0`, then
`create_display_of_type(...)` at `0x1845f7d6c`.  Its construction block
allocates the `AppleInternalDisplay` and calls the above `AppleDisplay`
constructor at `0x1845f7fc0-0x1845f7fd4`.

After construction, `create_display_of_type` rejects the object only if its
framebuffer connection field at `+0x6490` is null
(`0x1845f7ef4-0x1845f7f20`).  The average-pixel parser neither writes that
field nor exits the constructor.  Therefore its all-zero optional result
cannot be the cause of a nil `mainDisplay` on this path.

## Block 0x79: display-bandwidth limits

Block `0x79` is wrapped by the named public function
`_IOMobileFramebufferGetBandwidth` at `0x22a3bfb9c`.  It zeroes a 0x20-byte
temporary, calls the connection's `+0xe10` GetBlock implementation with
`w1=0x79` and length `0x20` at `0x22a3bfbc4-0x22a3bfbec`, and copies the
32-byte result to its caller only when IOReturn is zero
(`0x22a3bfbf0-0x22a3bfc08`).

QuartzCore's immediate higher-level consumer is
`IOMFBDisplay::update_display_bandwidth_limits()` at `0x1847aa104`.  Its call
to `IOMobileFramebufferGetBandwidth` is at `0x1847aa164`, and a successful
result is copied to `IOMFBDisplay+0x6a90` through `+0x6aaf` at
`0x1847aa16c-0x1847aa180`.

The same function's log string gives the four fields their code-visible
names:

| Result offset / stored field | Logged name | Validity test |
|---|---|---|
| `+0x00` / display `+0x6a90` u64 | `gpBW` | nonzero |
| `+0x08` / display `+0x6a98` u64 | `gpLiteBW` | no enabling test |
| `+0x10` / display `+0x6aa0` u64 | `gpSumBW` | nonzero |
| `+0x18` / display `+0x6aa8` f64 | `line_time` | greater than 0.0 |

The format string is
`"Display ID:%d BW limit - gpBW:%llu gpLiteBW:%llu gpSumBW:%llu line_time:%f"`
at `0x1847aa314-0x1847aa37c`.  The three enabling tests are at
`0x1847aa2c8-0x1847aa2e0`.  If they all pass, byte
`IOMFBDisplay+0x6ab0` becomes one at `0x1847aa2e4-0x1847aa2e8`; otherwise it
is explicitly cleared at `0x1847aa384`.

The public rejection predicate proves the meaning of that byte.
`IOMFBDisplay::exceeds_disp_bandwidth_limits_p(...)` at `0x18454def8` tests
`self+0x6ab0` at `0x18454df1c-0x18454df30`.  When it is not one, the function
returns false at `0x18454dfe4`; only the enabled branch can reach the detailed
detach and clone bandwidth comparisons.  Thus the current all-zero block
`0x79` reply disables rejection instead of treating every surface as over
budget.

The update routine is reached from
`AppleDisplay::update_display_limits()` at `0x1845f73e8` and from
`IOMFBDisplay::update_framebuffer_locked()` at `0x1847afe00` (with a second
site in the same function at `0x1847b0064`).  It is display-state maintenance,
not the creation of the `CADisplay` object.

## Surface-creation consequence

No parser above returns a value that is used to accept or reject the display
object.  The average-pixel result is an optional accessor value, while a zero
bandwidth result makes the over-limit predicate unconditionally false.
QuartzCore's IOSurface allocation path remains
`IOMFBDisplay::create_surface` (`0x1847ae2cc`, call at `0x1847ae310`) to
`CA::WindowServer::IOSurface::allocate_iosurface` (`0x18471bf38`); none of
the A442 payload fields is an allocation precondition there.

This does not prove that real A442 values are irrelevant to brightness,
average-pixel limiting, or bandwidth policy after rendering begins.  It does
rule out the present zero payloads as the reason display discovery is headless
or the reason the first IOSurface is never allocated.

## Positive-control boundary

There is no captured physical-H17P reply and therefore no evidence-backed
**semantic** positive-control payload for these blocks.  The following are
only minimum *parser-acceptance* examples derived from the exact branches;
they must not be presented as real DCP data:

- Block `0x4a`, length `0xc5c`: set little-endian u32 `+0x00 = 1`; all other
  bytes may be zero for this parser.
- Block `0x4c`, length `0x808`: set u32 `+0x00 = 1`, u32 `+0x04 = 2`, f32
  `+0x08 = 0.0`, f32 `+0x0c = 1.0`, f32 `+0x408 = 1.0`, and f32
  `+0x40c = 0.0`; all other bytes zero.  This satisfies the minimum count and
  the two observed float comparisons.
- Block `0x79`, length `0x20`: u64 `+0x00 = 1`, u64 `+0x08 = 0`, u64
  `+0x10 = 1`, and f64 `+0x18 = 1.0` passes the enable tests.  It is a poor
  behavioral control because it enables bandwidth enforcement with invented,
  extremely small limits.

The existing all-zero replies are the safer control for the Welcome effort:
they take explicit optional/disabled paths.  A real nonzero payload should be
introduced only from a physical trace for this target or another authoritative
source that establishes the values' units and range.
