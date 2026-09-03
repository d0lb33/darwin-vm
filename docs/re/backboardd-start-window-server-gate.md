# backboardd StartWindowServer startup gate

## Source metadata

- Guest target: iOS 27 beta 8, build 24A5430a; iPhone17,3 / H17P (t8140).
- Executable: `/Users/jdolbe1/dvm-artifacts/extract/bin/backboardd`, arm64e PIE;
  static `__TEXT` starts at `0x100000000`, and `LC_MAIN` is `0x1000181c8`.
- Shared-cache comparison: QuartzCore from
  `/Users/jdolbe1/dvm-artifacts/extract/dyld/dyld_shared_cache_arm64e`; see
  `docs/re/cadisplay-discovery-gates.md` for its independently derived
  CADisplay/IOMFB gates.

`StartWindowServer` is not an unreferenced helper: `backboardd` main registers
the literal `"window server"` task, whose global block reaches a once-wrapper
at `0x100018e88`; that wrapper invokes a second global block whose invoke
pointer is `0x1000257cc`.  The only gate before that invoke in this chain is
the `dispatch_once` predicate at `0x1000aa418`, so this static evidence finds
no session, preference, or device-state branch that can suppress the first
attempt.  Inside `StartWindowServer`, null `CADisplay` or null
CAWindowServer-display objects take the explicit headless log path; both
non-null values reach the normal setup-complete path.

## Startup and display gate

| Stage | Static address / data | Behavior | Evidence |
| --- | ---: | --- | --- |
| Startup task registration | `backboardd:0x100018598-0x1000185b4` | Main passes the CF string at `0x10008fbd8` (bytes at `0x10007a993`: `window server`) and global block `0x10008c4b8` to the local task-registration helper at `0x100017a58`. | `backboardd` disassembly (`/tmp/dvm/backboardd-otool.txt:23380-23435`); CFString bytes read at `0x10007a993`. |
| Task block | `__DATA_CONST:0x10008c4b8`; invoke pointer at `0x10008c4c8` | The `__NSConcreteGlobalBlock` object has invoke target `0x100018e88`. | `ipsw macho info backboardd --fixups`: `0x10008c4c8 auth-rebase -> 0x100018e88`; static memory words at `0x10008c4b8-0x10008c4d0`. |
| Once wrapper | `backboardd:0x100018e88-0x100018eac` | Returns only if predicate `0x1000aa418 == -1`; otherwise calls `_dispatch_once` with the block at `0x10008edc8`. | Direct instructions at `0x100018e88`; block argument setup at `0x100018e9c-0x100018eac`. |
| Direct `StartWindowServer` caller | `__DATA_CONST:0x10008edc8`; invoke pointer at `0x10008edd8` | This global block's invoke field is an authenticated rebase to `0x1000257cc`.  Thus `dispatch_once` is the direct dynamic caller of `StartWindowServer`. | `ipsw macho info backboardd --fixups`: `0x10008edd8 auth-rebase -> 0x1000257cc`; static block words at `0x10008edc8-0x10008ede0`. |
| Main display gate | `backboardd:0x10002593c`, `0x1000259fc` | Calls `+[CADisplay mainDisplay]`; `cbz x21` branches to headless handling when the returned main display is null. | `backboardd-otool.txt:36400-36490`; `x21` is preserved across the call then tested at `0x1000259fc`. |
| Server display gate | `backboardd:0x100025954`, `0x100025a00` | Asks the newly created `CAWindowServer` for its display and branches to the same headless path when `x22` is null. | `backboardd-otool.txt:36400-36490`; `cbz x22, 0x100025df0` at `0x100025a00`. |
| Headless witness | `backboardd:0x100025fdc-0x10002602c`; C string `0x100083db4` | Emits `StartWindowServer: headless (display:%{BOOL}u/server:%{BOOL}u)`, with booleans derived from null tests of `x21` and `x22`. | Direct instructions and string at `backboardd-otool.txt:36850-36887`; static string read at `0x100083db4`. |
| Success witness | `backboardd:0x100025f74-0x100025f88` | Normal path logs `StartWindowServer: Setup complete`. | Direct log call and literal in `backboardd-otool.txt:36825-36845`. |

The two `CAWindowServer` option constants configured before the null tests
(`kCAWindowServerDisableUpdatesOnMainDisplay` and
`kCAWindowServerDisableOutOfProcessDisplayObservation`, loaded at
`0x1000258c8-0x100025930`) are options passed to server creation.  This
analysis has no evidence that either suppresses the startup task, so treating
them as its cause would be unverified.

## Narrow runtime probes

Derive the PIE base for **this boot**, after `backboardd` is mapped, from its
runtime `__TEXT` start in the guest debugger's image list.  Equivalently, if
the runtime address of main is available, strip any arm64e pointer tag and use:

```
B = runtime(backboardd main) - (0x1000181c8 - 0x100000000)
runtime(a) = B + (a - 0x100000000)
```

| Order | Breakpoint | Result that distinguishes the branch |
| ---: | --- | --- |
| 1 | `B + 0x18e88` | Proves the registered window-server task entered its once wrapper.  A stop with predicate `0x1000aa418 == -1` means it was already consumed; otherwise continue into `dispatch_once`. |
| 2 | `B + 0x257cc` | Proves the `dispatch_once` block actually began `StartWindowServer`; this is the correct positive control for cache breakpoints that cannot cover PIE text. |
| 3 | `B + 0x259fc`, then `B + 0x25a00` | Record `x21` and `x22`.  Null `x21` means the CADisplay discovery route failed; non-null `x21` plus null `x22` means CAWindowServer did not return its display object. |
| 4 | `B + 0x25fdc` and `B + 0x25f74` | The first proves the exact headless error path; the second proves display-backed setup completed.  Capture the matching serial log literal as a second witness. |

## Ranked missing-model hypotheses

1. **IOMobileFramebuffer display discovery/open state — strongest evidence.**
   QuartzCore rejects an internal display when its `AppleDisplay+0x6490`
   IOMFB connection is null (`docs/re/cadisplay-discovery-gates.md:37-40`).
   The prior kernel log only proves type-zero user-client mapping at
   `/tmp/dvm/probe/UI_NO_D575_SWAP1.serial.log:651`, not that this particular
   connection was stored (`cadisplay-discovery-gates.md:43-48`).
2. **CA render-server display query — independent, not yet ranked by a hit.**
   `+[CADisplay mainDisplay]` populates only after its render-server display
   query (`QuartzCore:0x1843e2e58`, `0x1843e3318`), which can fail before an
   AppleDisplay is exposed (`cadisplay-discovery-gates.md:31-33`).  A null
   `x21` at the backboardd gate selects this hypothesis over the next one.
3. **Digital-out state / DCP-backed driver state — later than startup.**
   QuartzCore's update path calls IOMFB selector `0x19` and stops on a nonzero
   active-state result (`cadisplay-discovery-gates.md:50-57`).  It cannot
   explain failure to enter `StartWindowServer`; test it only after a
   setup-complete and surface/update hit.

## Open questions

| Question | Observation that settles it |
| --- | --- |
| Did this boot execute the once wrapper but skip its block because it had already run? | Stop at `B+0x18e88`, record the predicate at runtime `B+0xaa418`, and compare with a hit at `B+0x257cc`. |
| Which display object is absent? | Stop at `B+0x259fc`/`B+0x25a00` and preserve `x21`/`x22` plus the headless log. |
| Is the missing main display caused by render-server query or IOMFB open/list construction? | From the same boot, use the stage-2 QuartzCore probes at `0x1843e33b4`, `0x1845f3d68`, and `0x1845f7efc` described in `docs/re/cadisplay-discovery-gates.md:81-85`. |
| Does DCP/selector-`0x19` matter before a first frame? | First capture `B+0x25f74`, then the QuartzCore update and selector-`0x19` probes; without those ordered hits it remains unverified. |
