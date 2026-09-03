# QuartzCore first-surface runtime probe

## Scope

`UI_QC_SURFACE1` was a single no-D575 runtime probe of the ordered
first-surface path documented in `docs/re/windowserver-first-surface.md`.
The guest was iOS 27 beta 8 (24A5430a), target iPhone17,3 / H17P (t8140),
booting the persistent NVMe image through a fresh qcow2 child.  QEMU was
started paused with `-S`; breakpoints were installed before launchd entered
its main startup path.  The scheduled probe window was 180 seconds.  The
early resolver and debugger setup consumed about six seconds of wall time,
and the guest log reached approximately 155 seconds after launchd startup.

The run used the established healthy display control inputs, with D120 and
D586 callbacks only:

```text
tag: UI_QC_SURFACE1
base: /tmp/dvm/data-seed/sks-op09-complete-2.qcow2
child: /tmp/dvm/data-seed/ui-qc-surface1-definitive.qcow2
dtree: /tmp/dvm/data-seed/dt_nvme_welcome.bin
bootargs: rootdev=disk1s1 ignition_level=1 launchd_unsecure_cache=1 serial=3 -v wdt=-1 wlan-olyhal-abort
display: -fb 1179x2556 -fbmode graphics
callbacks: DARWIN_DCP_IOMFB_CB='D120::4,D586:9b040000fc090000:4'
```

No source files were changed for the probe.

## Same-boot dyld-cache slide proof

The QuartzCore slide was measured from live execution, not copied from an
earlier boot.  A small read-only resolver continued the initially paused CPU,
polled for the first cache-backed EL0 PC, stopped immediately, read eight
instructions at that PC, and searched the main shared cache and every
subcache for the complete sequence.

The observed runtime PC was `0x1a1b3ed68`.  Its eight words matched exactly
once: subcache `dyld_shared_cache_arm64e.01` at file offset `0x18f2d68`.
That subcache's mapping table maps the offset to static address
`0x181cf2d68`, yielding:

```text
runtime PC       0x1a1b3ed68
static PC        0x181cf2d68
slide            0x1fe4c000
runtime cache    0x19fe4c000
cache-base GPA   0x1001eaf8000
```

`ipsw dyld a2s` resolves the static PC inside
`/usr/lib/swift/libswiftCore.dylib`, in
`swift::ConcurrentReadableHashMap...getOrInsertExternallyLocked... +1584`.
Thus the positive control is stronger than a merely accepted breakpoint:
the CPU was directly observed executing a uniquely matched shared-cache
function, and the runtime cache-base page also translated with `gva2gpa`.
The raw resolution is in `/tmp/dvm/UI_QC_SURFACE1.slide.json`.

At the resolver stop, the serial log ended at the system-wide dyld-cache map
message; launchd's own startup messages followed.  The QuartzCore breakpoints
therefore covered launchd startup and the later UI-daemon launch period.

## Breakpoint result

Correction: the zero-hit table below is not a valid runtime localization.
The LLDB 21 command-file form hid 69 matching breakpoint stops; see
`docs/re/lldb-breakpoint-command-trap.md`.  The corrected rerun is documented
in `docs/re/quartz-corrected-runtime.md`.  The same-boot slide proof,
independent RPC/black-frame/storage witnesses, and the recorded absence of
the expected downstream trace records remain valid.  The claim that the
missing producer gate is upstream of QuartzCore must be rerun with the
validated Python callback form.

The measured `+0x1fe4c000` slide was applied to every QuartzCore static
address below.  LLDB accepted all 20 breakpoints
(`/tmp/dvm/UI_QC_SURFACE1.lldb.log:14-78`), then ran until the scheduled stop.

| Ordered edge | Runtime address | Hits |
| --- | ---: | ---: |
| `+[CADisplay mainDisplay]` | `0x1a422ee58` | 0 |
| `ensure_displays` | `0x1a422f318` | 0 |
| `AccelServer::render_update` | `0x1a429d598` | 0 |
| `Server::render_update` | `0x1a45c2174` | 0 |
| `Display::render_display` | `0x1a429d0f0` | 0 |
| `IOMFBDisplay::create_surface` | `0x1a45fa2cc` | 0 |
| `IOSurface::allocate_iosurface` | `0x1a4567f38` | 0 |
| `IOMFBDisplay::finish_update` | `0x1a42b7b14` | 0 |
| `IOMFBDisplay::swap_set_layer` | `0x1a42c4b48` | 0 |
| `IOMFBDisplay::fb_swap_set_layer` | `0x1a42be38c` | 0 |
| H17P `swap_submit` | `0xfffffff02918e3c4` | 0 |
| generic submit/map, primary map, A408, A407 | `0xfffffff02a0c366c`, `...40d4`, `...40d8`, `...4664`, `...477c` | 0 |

`CA::Transaction::commit` at `0x1a424a604` was also armed and did not hit.
There are zero `=== QC_*` or `=== H17P_*` hit markers in the LLDB log.  Its
only stops are the resolver's initial SIGTRAP at the live positive-control PC
(`/tmp/dvm/UI_QC_SURFACE1.lldb.log:6-13`) and the probe's final SIGINT.

The deepest requested edge reached was therefore **none**.  In this run the
missing producer gate is upstream of QuartzCore CADisplay discovery, not at
IOSurface allocation, layer swap, kernel mapping, or DCP submission.  This is
a per-run localization, not proof that those downstream paths are correct.

No defensible live PIE base for `backboardd` was available without perturbing
startup, so its `StartWindowServer` headless gates were not breakpointed.
Consequently `x21`/`x22`, the headless state, and breakpoint task attribution
remain unknown.  Process attribution cannot be manufactured from a
breakpoint that never fired.

## Health and display evidence

- The kernel selected NVMe `disk1s1` as BSD root
  (`/tmp/dvm/probe/UI_QC_SURFACE1.serial.log:314`).
- Mount phase 2 found Data at `disk1s2` (line 448), mounted the encrypted Data
  volume on `/private/var` (lines 486-493), mounted Hardware (lines 494-499),
  and later mounted encrypted User (lines 586-589).
- Early-boot and UMD initialization completed (lines 594-595), followed by
  `Early boot complete` (line 640).  The serial log contains no first
  `panic(cpu` and no `Copying ` operation.
- The display user client reported internal display type 0 (line 652), issued
  hotplug notification (line 660), and requested an OFF-to-ON internal-display
  transition (lines 765-776).  These are transport/power witnesses, not proof
  that QuartzCore discovered or painted a display.
- IOMFB A401 and A353 completed, then D120 and D586 callbacks completed with
  status 0 (`/tmp/dvm/probe/UI_QC_SURFACE1.stderr.log:210-329`).  No A407,
  A408, or surface-map trace occurred.

A live screendump near the end of the run is
`/tmp/dvm/UI_QC_SURFACE1-live.ppm`; its 9,048,240-byte pixel payload contains
one unique byte value, zero.  The converted PNG is likewise entirely black:
`/tmp/dvm/UI_QC_SURFACE1-live.png`.

| Artifact | SHA-256 |
| --- | --- |
| `UI_QC_SURFACE1.slide.json` | `5d02677128c98a87f97fd5423591c86dab951cfe45744b8b54bba3fe6f3229d0` |
| `UI_QC_SURFACE1.lldb.log` | `df0b64487eef895905e835fbeb034e8fba06a3b3455e39a5a8bd2e5a99967916` |
| `UI_QC_SURFACE1.serial.log` | `bebd8331fc2048184cfb5efd6b4e979544d9a000edbe022cdacf652ca74c5394` |
| `UI_QC_SURFACE1.stderr.log` | `741542a5085765625cd85d72b7b97b7049001493168d5717fcd1d8e6d136af6a` |
| `UI_QC_SURFACE1-live.ppm` | `43d418d35e149ea7e071c60ecb4ce967addd0aa4fc2eff4c6b0276403ed3f7fb` |
| `UI_QC_SURFACE1-live.png` | `751b436d4a028fa873ce9bbc5bbac0d943765aff73631917b3b48230117798ed` |

The exact-tag guest and debugger were stopped after capture; no QEMU process
was left running.
