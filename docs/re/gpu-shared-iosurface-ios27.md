# Reusable shared IOSurface backing, exact iOS 27

**Result: guest-managed caller memory aliases and presents successfully; direct
MMIO backing panics in XNU managed-page lookup. Zero-copy GPU sharing is blocked
on managed-page export and lifetime ownership.** The reconstructed copying GPU
control passes 33 displayed frames, with 15.040/27.374 ms median/p95 latency.

Continue from root 6a157e0 and QEMU 0d06606 on the isolated
codex/metal-driver-ios27 worktree. Preserve 24A5430a, iPhone17,3/T8140,
SPTM/TXM, native SMC, software rendering and native display/input.

## Preregistered first contract

Test the exact guest export `kIOSurfaceClientAddress` with our existing owned
MMIO transport RAM at offset 0x300000, screen geometry 1179×2556, row 4864,
cache mode 0x700. This export is static evidence of an available property;
it is not proof that the kernel accepts this memory descriptor as backing.
The entire 16 MiB region belongs to one disposable VM/session. Never accept
an arbitrary guest GPA, host pointer, pathname or unrelated IOSurface.

Before GPU work, write distinct words through the original shared mapping and
IOSurface mapping and require two-way aliasing. Stop on allocation, geometry,
lock or alias failure. No fallback memcpy in the shared variant. The existing
copying variant remains available as the control and software rendering stays
installed. The allocation failure is a prerequisite failure, not evidence that
shared IOSurfaces or custom Metal drivers are impossible in general.

If aliasing passes, hold the surface lock before requesting host GPU work,
wait for actual Metal completion before unlocking, and wait for native mode-1
surface retirement before submitting the next frame. Reuse the same allocation
for 33 exact-guest QuartzCore blur/conversion frames. Verify final pixels only
after timing; require all 33 frame markers in DCP and fresh native display/input
recovery. Retain setup/first-use and median/p95/max end-to-end stage timing,
16.667/33.333 ms slow-frame counts and sustained submission throughput.
Stop on existing 15-second request / 60-second no-progress / 240-second run
limits, corruption, stale identity, GPU/API error or display/input regression.

Further ownership acceptance, before expanding scope: explicitly acknowledge
presentation retirement, reject draw/release while retained by the display,
reject stale handles and wrong-session/range requests, test delayed GPU
completion, release/recreate safely, and check guard regions. The current
synchronous single-resource implementation alone does not prove those negative
contracts or process-death recovery. Live GPU migration remains unsupported.

## Reconstruction after /tmp cleanup

The previous overlay chain is incomplete: its migrated base image was deleted.
The durable native-SMC package contains a flattened, pinned migrated System/Data
disk with no backing file. New disposable installer children use its default.json;
no old incomplete overlay is rebased onto a different parent. Rebuild the
transport BootKC/DT from the package's hash-guarded native-SMC inputs and install
the signed custom driver/helper through the restore guest. Preserve the baseline
launchd cache except the added diagnostic job; require exact preimages before
writes. This changes the test's disk parent relative to the previous two runs,
so a latency comparison requires a fresh copying control on the same parent.

## Observed contracts

**The current MMIO RAM cannot be used as caller-address IOSurface backing in
this scope. Guest-managed caller memory can. Host sharing of the latter is
still untested.** No zero-copy GPU or latency improvement is claimed.

| Trial | Result | Scope |
|---|---|---|
| SHARED_SURFACE_GUEST3 | **Disproven within scope:** IOSurfaceCreate over existing transport RAM panics in managed-page lookup. | Exact signed helper, cache 0x700, 1179×2556, output GPA 0x4f0300000. No resident GPU draw was reached. |
| SHARED_MANAGED_GUEST1 | **Proven:** owned page-aligned guest memory aliases the IOSurface in both directions, presents three CPU patterns, receives three native retirements, then releases surface before backing. | Guest allocation, not host-shared output. Full final CPU pattern matches all 3,013,524 pixels. |
| Host Metal writing the managed IOSurface directly | **Untested.** | Needs managed-page export, host mapping, explicit ownership and completion leases. |
| Safe delayed GPU completion, stale generations, teardown during work, checkpoint | **Untested for a shared managed resource.** | A CPU allocation/release control does not establish asynchronous GPU safety. Live transport migration remains blocked. |

SHARED_SURFACE_GUEST3 reaches native display/input readiness at 133.065 s,
loads the custom bundle/device, and records at 133.726 s:

```
GPU_LOAD_SHARED_CREATE key=IOSurfaceAddress address=0x101c64000 bytes=12432384 cache=0x700
panic(cpu 2 caller 0xfffffff02b33f5f4): physical page is before the start of DRAM: 0x13c0c0 < 0x4000000) @vm_resident.c:3170
```

The runner stops the owned VM at 134.103 s. The parent disks remain immutable.
The exact DT's chosen/dram-base is 0x10000000000; dram-size is 0x300000000.
With this guest's 16 KiB pages, 0x13c0c0 × 0x4000 = 0x4f0300000, exactly the
requested shared output; 0x4000000 × 0x4000 is the DT's DRAM base. This is
observed physical-page contract failure, not merely a missing method/symbol.
Do not suppress the panic or move an unowned MMIO region into the DRAM range
and assume allocator ownership has been established.

Static evidence in SHARED_SURFACE_STATIC1 records the matching IOSurface
address-range/direction validation at a29418c–a2942b4, descriptor preparation
failure at a290d7c–a290db0, and cache compatibility diagnostics. Those are
candidate subcontracts, not a runtime branch trace. The panic identifies a
managed-page lookup; the exact intervening call chain was not captured.

The control uses posix_memalign(16384, 12435456), faults/zeros the allocation,
and supplies the same exported key/cache/geometry. It reports owned address
and IOSurface base both 0x78e4804000; forward and reverse marker writes match.
Each of three frames returns begin/layer/end/wait=0, with wait mode 1. DCP
witnesses identify frames 1, 2, 3 in order, each followed by D594 completion.
Final RGB SHA256 is
366902f4e53195aacc9a9efbd5fb92497a295b92b714c36f7641cb1f6865f9d0.
The helper releases the IOSurface before freeing its backing, after all waits.
This proves reuse/release only in the existing active, synchronous DCP model;
it does not prove blanking, sleep, asynchronous scanout or process-death safety.
The run also completes eight separate exact-AIR luma GPU submissions through
the custom driver. Those are not the pixels in this CPU display control.

## Reconstruction evidence and retained failures

- SHARED_SURFACE_INSTALL1 installs only the signed GPU bundle/helper and its
  launchd job on a new child of native-smc/default.json. Cached-job preimage
  matches BATT_NB1/payload/nb-launchd-after; original powerd/native SMC remains.
- SHARED_SURFACE_GUEST1 was stopped before the workload: the flattened baseline
  manifest lacked DARWIN_INPUT_UART=1. The runner now rejects that configuration
  before boot when display readiness is required.
- SHARED_SURFACE_GUEST2 enabled that setting, but the old v6 input helper could
  not meet the current native-HID gate. It was stopped before any surface test.
- SHARED_SURFACE_HID_INSTALL1 rejects the initially selected v6 binary preimage
  before writes. INSTALL2 matches warm-input-v6-native-tap/dvm-input exactly
  (cksum 4185248090, 53824 bytes) and installs the reviewed dvm_hid.c helper.
  It verifies the launchd-cache preimage and keeps all jobs unchanged.
- The first generic GPU-test invocation selected the stale METAL_DRIVER_BUILD1
  default. Rerunning with DVM_DRIVER_BUILD explicitly set to the newly built
  worker passes 63 tests with one skip. These logs are retained separately.
- SHARED_COPY_BUILD1 exposed Bash 3's empty-array/nounset expansion error; no
  guest boot occurred. Default flags now explicitly undefine both experimental
  modes. Its incomplete stage/installer attempt is retained as a host setup
  failure, not a guest or GPU failure.

## Smallest next implementation and unresolved dependencies

Keep commands and notifications in the existing MMIO transport. Add a bounded
managed-memory resource owned by the boot service, mapped to the entitled guest
client, and retained independently of individual frames. The positive control
supports trying an IOSurface over that mapping; it does not prove a kernel
IOBufferMemoryDescriptor allocation behaves identically to a user allocation.
Test that distinction before connecting host Metal.

Then export only that resource's verified page ranges to the host under a
session/generation handle. QEMU currently allocates main DRAM anonymously in
hw/arm/xnuboot_sptm.c, unlike the existing file-backed MMIO RAM. Host access to
managed pages therefore needs an explicit host-sharing mechanism; reusing the
current worker's fixed output mmap cannot accomplish it. A restricted shared
DRAM backend/page mapping or an in-QEMU Metal worker are alternatives, both
untested. Neither implies a new shader compiler.

The major dependencies are allocation/mapping and page pinning in the exact
kernel ABI; cache consistency; validated export without arbitrary-GPA writes;
Metal support for the resulting aligned/scattered backing; and a lease that
keeps pages alive through GPU completion and native DCP retirement. A process
exit must revoke work or retain its bounded pool until quiescence, never recycle
live GPU pages. Checkpoint remains rejected until a drain/recreate contract is
proven. Merely adding a release RPC or naming a fence would not prove this.

An alternative small export prototype could identify our owned surface through
its actual DCP/DART submission and share those managed pages. That would avoid
new guest allocation APIs, but the display witness alone does not pin memory or
prove client ownership. It is not yet a safe resource-registration mechanism.

Do not expand to general textures, QuartzCore registration or Liquid Glass
until one managed shared resource passes two-way host/guest aliasing, wrong
handle/range/session rejection, delayed completion, repeated retirement/reuse,
release/recreate, guard checks, and final-only pixel verification in a displayed
exact-guest QuartzCore GPU batch. Preserve the copying control meanwhile.

## Reproduction commands

Outputs are isolated; use fresh directory/tag names. The MMIO-address probe is
an intentional reproduction of a known **guest panic**, not a usable driver mode.

```sh
bash tools/gpu/build_driver.sh /tmp/dvm/NEW_MANAGED_BUILD --mmio-present-managed-contract
bash tools/gpu/build_driver.sh /tmp/dvm/NEW_COPY_BUILD --mmio-present
# Known-panic reproduction, disposable overlay only:
bash tools/gpu/build_driver.sh /tmp/dvm/NEW_MMIO_ADDRESS_BUILD --mmio-present-shared-probe

python3 tools/gpu/build_boot_transport.py --bootkc "$HOME/dvm-artifacts/native-smc/bootkc" --dtree "$HOME/dvm-artifacts/native-smc/system.dtree" --out /tmp/dvm/NEW_SHARED_BOOT
python3 tools/gpu/prepare_guest_load.py --build /tmp/dvm/NEW_MANAGED_BUILD --cache /tmp/dvm/BATT_NB1/payload/nb-launchd-after --system-tc "$HOME/dvm-artifacts/native-smc/tc" --output /tmp/dvm/NEW_SHARED_STAGE --interactive-load --memory-limit-mb 256
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_install.py --manifest "$HOME/dvm-artifacts/native-smc/default.json" --stage /tmp/dvm/NEW_SHARED_STAGE --tag NEW_SHARED_INSTALL
```

If reconstructing from the flattened baseline, use
prepare_shared_input_update.py with the recorded exact v6-native-tap preimage
and reviewed native HID build, then the same restore installer. Use the original
non-MMIO install manifest for restore boots. Rebind the resulting installed
child with prepare_display_state_trial.py; explicitly set DARWIN_INPUT_UART=1
in the new derived manifest. This keeps the disk and all immutable ancestors.

```sh
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py /tmp/dvm/SHARED_MANAGED_STATE1/state.json --tag NEW_MANAGED_TRIAL --seconds 240 --driver-mmio --driver-wait-display --driver-worker /tmp/dvm/SHARED_MANAGED_BUILD1/driver_host --library-cache /tmp/dvm/GPU_FEAS_SHADER1/air/slice0.metallib
# Use a Python with Pillow installed for the independent full-image oracle:
python3 tools/gpu/verify_present_pattern.py /tmp/dvm/NEW_MANAGED_TRIAL/last-presented.bgra /tmp/dvm/NEW_MANAGED_TRIAL/pixel-verification.json --frame 3
python3 tools/gpu/verify_present_contract.py /tmp/dvm/NEW_MANAGED_TRIAL
```

## Reconstructed copying GPU control

SHARED_COPY_GUEST1 passes the complete displayed batch on the reconstructed
native-SMC/native-HID lineage: **33 frames / 99 GPU dispatches**, zero per-frame
verification reads, exact final pixels, resource count zero, and fresh native
input/display recovery. All final scanout/shared-output/guest-surface bytes agree;
SHA256 8513f065f58f4b7f1a47e9ed6217fca8c7c3aa70040cc693eb5af7799b7d9d39.
This is the copying path, not the failed MMIO-address variant.

| Metric | Measured |
|---|---:|
| Setup | 599.570 ms |
| First frame | 36.754 ms |
| Steady end-to-end median / p95 / max | 15.040 / 27.374 / 28.453 ms |
| Submit-to-GPU-completion median / p95 / max | 2.041 / 2.853 / 2.997 ms |
| Guest IOSurface delivery median / p95 / max | 7.250 / 15.467 / 18.025 ms |
| Swap/retirement median / p95 / max | 5.573 / 10.966 / 18.678 ms |
| GPU duration median / p95 / max | 0.375 / 0.376 / 0.376 ms |
| Steady frames over 16.667 / 33.333 ms | 9/32 / 0/32 |
| Host displayed submissions/s | 58.701 |
| Guest steady submissions/s | 58.907 |

Correctness passes; the consistent-60-Hz gate fails. A single short batch and
static resident input do not characterize native UI scrolling or Liquid Glass.
The disk/helper reconstruction also prevents treating this as a controlled
performance improvement against the older two runs. It remains evidence that
copy and display delivery dominate this particular GPU workload's compute time.

Validation: 79 project tests pass; 63 GPU-tool tests pass with one pre-existing
optional integration skip, explicitly using SHARED_COPY_BUILD2. Shell syntax,
Python compilation, exact input hashes and independent CPU pixel/display
verifiers pass. QEMU is unchanged at 0d06606; the reused measured executable
matches SHA256 6fc5d215620311c7e1d49611130fd1024005e28ab813750ec41a1e9d5f280b26.
Transport BootKC and DT reconstruct byte-for-byte to the prior hashes. No
unrelated running VM or migrated baseline was modified.

The reproducible working control is preserved outside /tmp at
`~/dvm-artifacts/gpu-shared-resource-control-ios27`. The packager verifies the
passed trial and source build provenance, flattens and compares guest disk
contents, and copies the exact boot inputs, helper build, launchd cache and AIR.
Its control.json is for run_guest_load.py, not a normal QEMU launch without
transport setup. Flattening does not claim a checkpoint or a new runtime test.

```sh
python3 tools/gpu/verify_shared_surface_contract.py /tmp/dvm/SHARED_SURFACE_GUEST3 --kind mmio-panic
python3 tools/gpu/verify_shared_surface_contract.py /tmp/dvm/SHARED_MANAGED_GUEST1 --kind managed-control
python3 tools/gpu/report_present_batch.py /tmp/dvm/SHARED_COPY_GUEST1
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py "$HOME/dvm-artifacts/gpu-shared-resource-control-ios27/control.json" --tag NEW_PRESERVED_CONTROL --seconds 240 --driver-present --driver-mmio --driver-wait-display --driver-worker "$HOME/dvm-artifacts/gpu-shared-resource-control-ios27/driver-build/driver_host" --library-cache "$HOME/dvm-artifacts/gpu-shared-resource-control-ios27/QuartzCore.metallib"
```

Evidence is preserved at `~/dvm-artifacts/research/gpu-shared-iosurface-ios27`,
including failed reconstruction trials, raw owned transport audits, the panic,
managed CPU control and copying GPU batch. Two verifier negative controls reject
a panic relabeled as a pass and a corrupted managed-memory audit. The final
lineage check verifies all five immutable disk ancestors and seven boot inputs.
