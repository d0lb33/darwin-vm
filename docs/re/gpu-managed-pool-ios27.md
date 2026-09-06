# Managed shared GPU surface — exact 24A5430a

## Result and scope

The managed-page blocker is resolved for one fixed service-owned surface.
`POOL_GUEST2` allocates and maps an IOBufferMemoryDescriptor, wraps it in an
IOSurface, verifies two-way aliasing, presents three CPU patterns and receives
three native D594/mode-1 retirements. The independent final oracle verifies all
3,013,524 pixels: RGB SHA256
`366902f4e53195aacc9a9efbd5fb92497a295b92b714c36f7641cb1f6865f9d0`.

`POOL_GPU_GUEST2` then registers all 759 managed pages, maps the same backing
into the host worker and executes **33 displayed frames / 99 GPU dispatches**
through the custom process-local Metal driver. These are the exact guest's
unmodified QuartzCore AIR blur passes, followed by the existing GPU conversion.
No per-frame CPU memcpy into the guest IOSurface and no per-frame verification
readback is performed. Final GPU backing, guest IOSurface and actual DCP scanout
agree byte-for-byte; SHA256
`3fada214a8a9aafb60f27667806aaf0ab6c127526abd5e64c8133b0d8eee799d`.
The tail guard is intact. Native presentation/input recovers after the batch.

**Final implementation:** `POOL_GPU_GUEST7` repeats the complete 33-frame batch
with service arbitration locking, wrong-client/aperture rejection and explicit
unmap/remap retention checks. Final SHA256 is
`68a21a6e771bf3c845b336fd896acb05eb7b878b114f6aa243e511001c722aa3`.
Both rejected mappings return outer code `0xe00002c2`, address/length zero.
The provider is reopened successfully for the other-client test. After unmap,
the legitimate client remaps successfully and reads its retained canary before
the IOSurface/host alias and GPU presentation tests. No panic occurs.

This is **not** system-wide QuartzCore registration, a complete Metal API,
Liquid Glass, a sustained scrolling benchmark, or live GPU checkpoint support.
The original copying mode and software rendering remain available.

## Ownership and transport

The signed helper opens the existing boot transport. Its new memory type
`0x44560002` requests one fixed **12,435,456-byte** descriptor with 16 KiB
alignment and cache mode `0x700`. The provider arbitrates one owner after the
stock open entitlement check; another client cannot map that resource.
IOKit owns allocation and userspace mappings. The descriptor is prepared before
export and retained by the provider for the entire disposable VM lifetime.

The last point is intentional: **unmapping/releasing a host resource does not
free the guest pool**. A bounded, single-owner pool avoids recycling memory
under a live GPU or display reference. General reclamation, client replacement,
and graceful recovery from worker death remain outside this implementation.
Another owner requires a fresh VM. This is not proof of arbitrary teardown.

Commands still use the existing MMIO shared RAM and doorbell. A third 16 KiB
DT range at `0x4f1004000` is mapped only by the kernel shim. It accepts this
experimental registration protocol:

| Offset | Kernel operation |
|---|---|
| 0 | Write fixed resource size, once |
| 8 | Write each descriptor-derived physical page, in logical order |
| 16 | Write generation 1 to publish; read 1 only after successful registration |

The userspace mapping dispatcher never returns this third aperture. No workload
request supplies an address, GPA, or file path. QEMU rejects duplicate,
unaligned, out-of-DRAM pages and incomplete/repeated registration. It publishes
a host-owned record containing session identity, fixed geometry and page
offsets. The host validates that record before mapping any pages. The first GPU
trial's 759 pages span **94 physical runs**, so that runtime success did not
depend on a physically contiguous guest allocation.

Opt-in `DARWIN_GPU_MANAGED_RAM_PATH` changes this VM's ordinary DRAM backing
from anonymous memory to a fresh private 12 GiB file. **XNU's DRAM layout and
allocator ownership do not change.** The host worker reserves a contiguous
virtual range and maps the approved file pages into it; host and guest virtual
addresses differ. `newBufferWithBytesNoCopy` wraps that host range. The worker
is a trusted host component with access to its own VM's backing file; this is
not a hardened capability boundary against a compromised host worker.

The host resource sequence is:

`available → GPU work/completion → display pending → native guest wait → retirement RPC → available`

The worker waits for actual Metal completion. It rejects another draw or
release while display retirement is pending, and requires the matching frame
number. The guest sends retirement only after native mode-1 wait returns.
The display evidence remains scoped to our active synchronous DCP model, which
copies into QEMU's console before D594. Asynchronous physical scanout, blanking
and sleep are not established by this result. The guest CPU copy was removed;
this does not claim every copy in the presentation stack has disappeared.

## Static evidence versus runtime evidence

The BootKC input is pinned by its full SHA256. The shim builder guards virtual
slots, PAC discriminators, entry instructions and executable padding. It does
not disable XNU managed-page validation, SPTM or TXM.

- `b175ad8`: class factory's C-string wrapper; `b17594c`: symbol lookup/factory.
- `b246a48`: IOBufferMemoryDescriptor class-name construction; `b246558`:
  metaclass allocator installs object vtable `7e57290`.
- `7e57290+128`, diversity `1c03`, target `b245af4`:
  `initWithPhysicalMask` matching this SDK's emitted call.
- `7e57290+d8`, diversity `f5b3`, target `b255f8c`: prepare.
- `7e57290+98`, diversity `649a`, target `b256e60`: physical segment lookup,
  explicitly using `kIOMemoryMapperNone`.
- `7e57290+e8`, diversity `3ed6`, target `b251dfc`: kernel mapping.
- `7e1ed48+78`, diversity `34f6`, target `b251a50`: mapping virtual address.
- `7e58dd0+300/+308`, diversities `c6b2/8a43`, targets `b1faef8/b1facbc`:
  recursive service arbitration lock/unlock around allocation and publication.

Addresses are unslid under `0xfffffff000000000`. Method identification and
compiler compatibility are static evidence. Successful registration, pixel
aliasing and presentation are runtime evidence; the former alone would not
have established the latter.

## Measurements

`POOL_GPU_GUEST2` is a fresh disk boot with 32 steady samples after first use.

| Stage | Median | p95 | Maximum |
|---|---:|---:|---:|
| GPU execution | 0.373 ms | 0.380 ms | 0.381 ms |
| Draw/completion | 2.270 ms | 3.581 ms | 10.571 ms |
| Post-completion surface delivery | 0.056 ms | 0.156 ms | 0.823 ms |
| Swap/wait plus retirement RPC | 8.965 ms | 16.195 ms | 22.607 ms |
| End to end | **11.440 ms** | **19.794 ms** | **25.154 ms** |

Setup: 944.855 ms. Five of 32 steady frames exceed 16.667 ms; none exceed
33.333 ms. Host observed displayed submissions: 74.022/s, without vsync pacing.
**The consistent-60-Hz gate still fails.** The earlier copying control measured
15.040/27.374 ms median/p95, but these runs also differ in RAM backend and
retirement protocol, so this is not a controlled measurement of the copy's
isolated cost. The delivery-stage result and direct alias evidence establish
removal of that work more specifically.

The final `POOL_GPU_GUEST7` run measures:

| Stage | Median | p95 | Maximum |
|---|---:|---:|---:|
| GPU execution | 0.374 ms | 0.375 ms | 0.375 ms |
| Draw/completion | 1.953 ms | 2.503 ms | 2.956 ms |
| Post-completion surface delivery | 0.043 ms | 0.125 ms | 0.632 ms |
| Swap/wait plus retirement RPC | 6.761 ms | 8.301 ms | 8.614 ms |
| End to end | **8.884 ms** | **10.418 ms** | **11.695 ms** |

Setup is 687.444 ms and first frame 23.774 ms. All 32 steady frames are below
16.667 ms; observed unpaced displayed submissions are 108.053/s. This run passes
the short-batch 60-Hz latency gate, but the first run's slower tail demonstrates
variability. Sustained, paced 60-Hz UI performance is **still untested**.

`POOL_COPY_GUEST1` reboots the preserved copying control with the **same rebuilt
QEMU**, managed sharing disabled, and passes all 33 frames plus final pixels and
native display/input recovery. Its delivery median/p95 is 8.231/16.559 ms;
end-to-end median/p95/max is 17.307/53.687/67.261 ms. Setup is 857.571 ms and
first frame 105.040 ms. This confirms the fallback works and illustrates its
copy cost. It uses the earlier helper/kernel and anonymous DRAM, so the timing
comparison remains descriptive rather than an isolated same-configuration A/B.

## Validation and stop conditions

The runner retains its 15-second request, 60-second no-progress and 240-second
run bounds. Stop on panic, mapping/identity/geometry mismatch, Metal error,
incorrect pixels/guards, or native display/input regression. Setup and first use
are separate from steady timing; final verification follows the timed batch.

The host-only `managed_mapping_probe.m` assembles 759 reversed file pages;
Metal blit writes verify every byte. This only proves the host mapping contract.
`test_managed_driver.py` additionally executes real guest AIR and tests foreign
session, duplicate/unaligned/out-of-range pages, stale handles, early draw/release,
wrong retirement, release/recreate, and a GPU event delayed by 150 ms. Before
the event is released, neither output conversion nor completion reply occurs.
These host tests do not substitute for guest/display ownership tests.

Retained diagnostic failures (all stopped before their displayed GPU batch):

| Trial | Observed failed contract and correction |
|---|---|
| `POOL_GPU_GUEST4` | Diagnostic demanded the inner `kIOReturnUnsupported` code at the public mapping API. The actual code was not recorded in this first failure. No mapping or GPU success is inferred from this run. |
| `POOL_GPU_GUEST5` | Explicitly records the forbidden-aperture call returning `0xe00002c2`, address/length zero. The next test cannot obtain an openable second provider through `IOConnectGetService`. |
| `POOL_GPU_GUEST6` | Records `IOConnectGetService` itself returning `0xe00002c7`. Reopening the original device-tree provider succeeds; the other client's pool mapping is rejected with `0xe00002c2` and zero address/length. Duplicate mapping, even with `kIOMapUnique`, returns the existing address. The diagnostic's distinct-address assumption was wrong. |

The passing `POOL_GPU_GUEST7` tests explicit unmap/remap with retained contents
instead of demanding a second virtual address. No kernel validation is bypassed to
accommodate these API results. QEMU's malformed registration rejection is
implemented but has not been exercised by a deliberately faulty kernel shim;
the malformed-record tests cited above exercise the host importer.

Live migration remains explicitly blocked. Draining and recreating host
resources, crash recovery, multiple concurrent surfaces and multiple clients
must be established before expanding beyond this bounded implementation.

Final checks: 79 project tests pass; 66 GPU-tool tests pass with one pre-existing
optional skip; all three native IOMFB swap unit tests pass. Shell syntax,
Python compilation, final-only pixel/guard verifiers, and immutable input
checks pass. The QEMU changes are committed as `697993b`; runtime evidence uses
the frozen executable built from those exact changes. No unrelated VM was
modified; all owned test VMs are stopped.

## Reproduction

All paths below are disposable outputs. Install only through an isolated restore
VM with the guarded updater, using the preserved native-SMC/migrated lineage.
Never boot a modified baseline or reuse another VM's RAM file.

```sh
python3 tools/gpu/build_boot_transport.py --bootkc "$HOME/dvm-artifacts/native-smc/bootkc" --dtree "$HOME/dvm-artifacts/native-smc/system.dtree" --out /tmp/dvm/NEW_POOL_BOOT --managed-export
bash tools/gpu/build_driver.sh /tmp/dvm/NEW_POOL_BUILD --mmio-present-pool
# Rebuild in this worktree before freezing a QEMU binary; no concurrent boot.
make -C qemu-sptm/build -j18
python3 tools/gpu/prepare_driver_update.py --before-build OLD_BUILD --build /tmp/dvm/NEW_POOL_BUILD --cache PINNED_LAUNCHD --system-tc PINNED_TC --out /tmp/dvm/NEW_POOL_STAGE
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_install.py --manifest RESTORE_INPUT_MANIFEST --stage /tmp/dvm/NEW_POOL_STAGE --tag NEW_POOL_INSTALL
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/prepare_display_state_trial.py --boot-build /tmp/dvm/NEW_POOL_BOOT --qemu "$PWD/qemu-sptm/build/qemu-system-aarch64" --installed /tmp/dvm/NEW_POOL_INSTALL/warm-manifest.json --out /tmp/dvm/NEW_POOL_STATE
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py /tmp/dvm/NEW_POOL_STATE/state.json --tag NEW_POOL_TRIAL --seconds 240 --driver-mmio --driver-present --driver-wait-display --driver-worker /tmp/dvm/NEW_POOL_BUILD/driver_host --library-cache "$HOME/dvm-artifacts/gpu-shared-resource-control-ios27/QuartzCore.metallib"
python3 tools/gpu/report_present_batch.py /tmp/dvm/NEW_POOL_TRIAL
python3 tools/gpu/verify_managed_surface.py /tmp/dvm/NEW_POOL_TRIAL --require-negative
```

Use `derive_gpu_manifest.py` to substitute original native-SMC BootKC/DT for a
restore installer manifest while preserving the pinned GPU disk, TC, SPTM and
TXM. `POOL_RESTORE.json` records that derivation. `POOL_INSTALL2`,
`POOL_GPU_INSTALL2` and `POOL_GPU_INSTALL4` are successive guarded children;
ordinary test boots create further disposable children and never write parents.

The final implementation uses `POOL_EXPORT_BOOT4`, `POOL_GPU_BUILD7`,
`POOL_GPU_INSTALL7/warm-manifest.json`, and `POOL_GPU_STATE7/state.json`.
The preserved working package is `~/dvm-artifacts/gpu-managed-pool-ios27`:

```sh
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py "$HOME/dvm-artifacts/gpu-managed-pool-ios27/control.json" --tag NEW_MANAGED_CONTROL --seconds 240 --driver-mmio --driver-present --driver-wait-display --driver-worker "$HOME/dvm-artifacts/gpu-managed-pool-ios27/driver-build/driver_host" --library-cache "$HOME/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib"
```

The packager flattens into a new disk, compares it byte-for-byte with its
installed source, and preserves the exact executable, BootKC/DT, trust cache,
SPTM/TXM, ramdisk, launchd cache, driver build and AIR. It does not alter the
earlier copying package or the migrated baseline, and does not claim a saved
GPU checkpoint. Runtime RAM and disk children are newly created by the runner.

Small evidence is preserved at
`~/dvm-artifacts/research/gpu-managed-pool-ios27`, including raw MMIO audit
buffers, kernel page records, final resource/scanout bytes, compiler/ABI ledgers,
successful and failed boot logs, installer preimages and test results. Full
guest DRAM is excluded; `managed-verification.json` binds the bounded final
resource snapshot to the checked runtime result and registration record.
