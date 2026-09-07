# Linked runtime driver loading on 24A5430a

This follows the bounded experiments in `gpu-development-loader-ios27.md`.
The user removed the fix limit. The current question is whether the unchanged
TXM's native OOP-JIT signature route can accept a newly built driver in a fresh
process of the persistent runner. This is a test-loading mechanism, not global
Metal discovery or a production signing policy.

## Why the previous hash route failed

Read-only QEMU instruction/register tracing in `CA_LOAD_TRACE_GUEST3`, with
`CA_LOAD_TRACE_BUILD3/signature.jsonl`, observed two identical attempts:

* At TXM `0xfffffff01704687c`, configuration byte `+0x4b` is zero. The
  compilation-service route returns `0x12893` at `0xfffffff017047d90`.
* The candidate's complete 32-byte CodeDirectory hash equals the authorized
  hash slot. The matcher is not reached: equality cannot enable the route.
* At `0xfffffff0170467e0`, the alternative ad-hoc route has configuration
  `+0x49 = 1`; its predicate returns zero at `0xfffffff017046808`.
  Its result at `0xfffffff017047da4` is `0x1283d`.

Static inspection ties `0x1283d` to `0xfffffff017046028`: the original
CodeDirectory version `0x20400` lacks the linkage fields required by this route.
Version `0x20600`, application type 2, and subtype 1 or 2 are the next concrete
contract to test. TXM `0xfffffff017047220` checks the current loader's
`com.apple.private.oop-jit.loader` value against `previews` or `ml-compiler`.
The string comparison path is at `0xfffffff017074fac` / `0xfffffff017074ff0`.
These static facts do not establish successful guest loading.

The first trace attempt eventually recorded 14 events, but register capture
was invalid: register handle zero was incorrectly treated as unavailable and
QEMU appends register bytes. An early empty-file observation was not evidence
of address relocation. The second attempt broadened address matching and
captured unrelated userspace PCs, exhausting the cap; it is invalid TXM
evidence. The third uses exact runtime VAs and corrected register capture.

## Reproduction inputs

`build_driver_revision.py --bootstrap --development-loader --oopjit-loader
previews` builds the fixed boot-trusted helper. `sign_linked_revision.py`
adds the v20600 linkage fields to an owned ad-hoc test bundle, linking it to
the helper's complete CodeDirectory hash. It updates affected load commands
and page hashes, verifies every code page, and requires host
`codesign --verify --strict` success. Host verification is separate from TXM
acceptance. Subtype 2 provides a mismatch control for the `previews` helper.

`build_runtime_witness.py` changes the driver's factory to emit its revision
number and rebuilds only that bundle, keeping helper and backend unchanged.
Only the bundle is deployed at runtime; its inherited helper trust cache is
not a trust cache for the new driver and must not be staged.

The isolated installation `CA_LOAD_OOPJIT_INSTALL1` uses Fix 3's opt-in BootKC,
not Fix 4's ineffective global hash authorization. SPTM/TXM, native SMC,
device tree, baseline disk and existing MMIO service remain unchanged.
`CA_LOAD_OOPJIT_STATE1/state.json` pins the uninstrumented inputs.
`build_signature_trace.py` adds a read-only plugin without rebuilding QEMU;
instrumented execution cannot establish normal latency.

## Collector correction

`CA_LOAD_OOPJIT_GUEST1` reached display/input readiness and the installed
control returned exit zero, but host evidence collection raised
`job UART evidence exceeds bound` before the linked candidate ran. The serial
log was only about 1 MiB. Reading again after a short read at EOF had observed
a newly appended byte and falsely interpreted it as exceeding 4 MiB.

`capture_uart_interval` now snapshots the end offset and reads that exact
interval once. It records offsets, rejects true overflow and truncation, and
leaves later appends outside the snapshot. Two regressions cover concurrent
append and real overflow/truncation; all 81 host regressions pass. GUEST1 is
a host collector failure, not evidence against the linked signature.

## Acceptance

Require two code-distinct runtime bundles absent from the boot trust cache,
one built after VM start, in separate fresh processes of one boot. Each must
execute actual guest CARenderer, submit work through the driver to host Metal,
produce the exact red64 pixels, and release all host resources. Preserve the
wrong-subtype outcome, an ordinary control afterward, and display/input
observations separately. Confirm any instrumented success without the plugin.

## Linked-format result and next failed contract

`CA_LOAD_OOPJIT_GUEST2` passed installed controls in fresh PIDs 282 and 341
(jobs `1788749518777929` and `1788749677547972`), independently reverified
from saved audit CRCs, upload records and pixels. Two linked candidates failed:
job `1788749518831743` / PID 306 and post-boot witness 6,
job `1788749655319382` / PID 316. AMFI's original callback returned zero,
flags `3 -> 20004003`, signer 9, fatal length zero. TXM recognized class 2
and returned `0x130a8` at `0xfffffff017047f30`, twice per candidate.
The prior `0x1283d` format rejection is resolved within this scope.

Wrong subtype 2 (`1788749655377215` / PID 317) fails earlier: AMFI returns 1,
signer 0, fatal length 1024. The correct subtype without development opening
also fails (`1788749677490624` / PID 340). None is a runtime-driver pass.

`CA_LOAD_OOPJIT_final-prerequisites.disass` identifies `0x130a8` at
`0xfffffff017047914` / `0xfffffff01704792c`: signature classes 1 through 5
are rejected when the environment callback at `+8` returns true. The observed
callback pointer is `0xfffffff017033fe8`. Its exact implementation returns
true for a present SCRD magic with xART bit 16 clear. Trace records show
device state `+0xc = 0x00005c01`, matching `darwin_sep.c`'s published value.
The old model comment calling xART absence permissive was incorrect here.

The next isolated experiment enables `DARWIN_SEP_TXM_XART_TEST=1`. TXM
`0xfffffff017041328` reads a 60-byte snapshot at secure-channel page `+0x290`,
checks its sequence against a cached monotonically increasing value, and
returns the 56-byte payload. The opt-in model publishes sequence 2, then
SCRD `0x00015c01`. Payload `+12 = 1` preserves Developer Mode;
`+16 = 0` and `+8 = 0` disable Lockdown and Demo Mode. These names come from
the exact consumers and adjacent log strings at `0xfffffff017033b18`,
`0xfffffff017033c04` and `0xfffffff017033c24`, not guessed bit meanings.
Other fields remain reserved zero. This is an immutable boot-time status
fixture, not complete xART emulation or checkpoint support. No TXM code,
memory, signature-validation return or SPTM policy is patched.

The opt-in QEMU build and `CA_LOAD_XART_STATE1` preserve the installed helper,
Fix 3 BootKC, trust cache and full disk ancestry.

## Runtime loading proven within the development-runner scope

With the xART snapshot, `CA_LOAD_XART_GUEST1` records TXM class 2 completion
return `3` (success) at `0xfffffff017047f30`. Its trace shows SCRD
`0x00015c01`, and its ordinary AMFI callback returns zero, signer 9. The
existing scoped mmap exception remains necessary. Both runtime candidates
execute actual guest CARenderer and produce all 4,096 expected red pixels.

The independent disk boot `CA_LOAD_XART_GUEST2` repeats this **without the
trace plugin or any debugger connection**:

| Job / PID | Work | Result | Spawn to exit |
| --- | --- | --- | --- |
| `1788750208025665` / 274 | Installed control | Verified render | 15.349 s |
| `1788750208085133` / 293 | Linked new revision, development open | Verified render | 2.306 s |
| `1788750282284610` / 296 | Code-changed witness 8, built after boot | Verified render | 2.376 s |
| `1788750282348272` / 298 | Wrong subtype | Signature rejected | 0.443 s |
| `1788750282410644` / 300 | Installed control afterward | Verified render | 4.439 s |

The two runtime hashes are `c76e553e76456876f5318d6eef2f6e886b2af82cc1e57dd2efe8689739c70078`
and `fcdbedd3f70d5a88392674e0596b96cf932e4edd8465e4a55f1758b67b85fcfe`.
Their CodeDirectory hashes are absent from the 3,962-entry boot trust cache.
Witness 8's recorded build started at Unix `1788750208.123852`, after owned
QEMU PID 66706 started; bundle-only compilation/link/sign/import verification
took 4.132 s. Its factory source changes, package hash, guest staging witness,
and verified draw establish revision identity. The factory's stderr marker
is not present in captured UART and is not used as evidence of execution.

All successful jobs were independently checked with `verify_runner_job.py`:
ring sequence/CRC, staged package hash, upload hashes, exact guest AIR hash,
one indexed draw, 16,384 output bytes with SHA-256
`c34fb4331b2d031d7c644860b54a678424c66ef12352fc165a91dc09840d98fd`,
and zero live host resources. These are whole-test iteration timings, not
frame latency or sustained rendering performance.

The traced run's no-development control (`1788750133837174` / 318) still
fails with `file system sandbox blocked mmap()`. Wrong subtype is rejected
in both boots. These controls demonstrate those specific boundaries; they do
not establish that the native OOP-JIT path enforces the supplied parent hash
as an authorization boundary. The loader entitlement is privileged.

Uninstrumented readiness took 131.334 s, with 59 native presents and a fresh
input ACK after ten stable seconds. At completion there were 311 presents,
69 ACKs versus 56 at readiness, the same input PID 157/epoch, and zero
timeouts. One pre-readiness helper restart/rejection did not increase.
The traced run had 57 -> 758 presents and 56 -> 92 ACKs with unchanged input
PID 160 and zero timeouts. These prove display/input preservation at the
existing readiness level, not a home-screen gesture test. The CARenderer
target remains offscreen. Both owned VMs were stopped and reaped.

## Reuse

Durable test package: `~/dvm-artifacts/gpu-runtime-loader-ios27/control.json`.
It retains a byte-identical sealed installed overlay above the existing
durable migrated base; no baseline image is modified or promoted to the
default launcher. `preserve_runner_baseline.py` requires an uninstrumented
passed trial and two independently verified distinct runtime revisions.

```sh
PATH="$PWD/qemu-sptm/build:$PATH" python3 tools/gpu/run_guest_load.py \
  ~/dvm-artifacts/gpu-runtime-loader-ios27/control.json \
  --tag YOUR_UNIQUE_TAG --seconds 1200 \
  --driver-mmio --driver-present --driver-consumer --driver-runner --driver-wait-display \
  --driver-worker ~/dvm-artifacts/gpu-runtime-loader-ios27/driver-build/driver_host \
  --library-cache ~/dvm-artifacts/gpu-managed-pool-ios27/QuartzCore.metallib

# In another shell after the owned qemu.pid exists: build the changed bundle,
# give it the linked signature, then queue it. Do not stage a new trust cache.
python3 tools/gpu/sign_linked_revision.py NEW_BUILD/DVMProxy.bundle NEW_LINKED \
  --parent ~/dvm-artifacts/gpu-runtime-loader-ios27/driver-build/dvm-gpu-load
python3 tools/gpu/runner_control.py /tmp/dvm/YOUR_UNIQUE_TAG \
  --bundle NEW_LINKED/DVMProxy.bundle --mode data --development
python3 tools/gpu/runner_control.py /tmp/dvm/YOUR_UNIQUE_TAG --stop
```

Remaining dependencies for wider use: this is a privileged test loader and
immutable xART fixture; changing kernel/helper/entitlements still requires
installation and boot. In-place replacement within an existing process,
global Metal discovery, broad scenes, live-resource checkpointing and
system-wide Liquid Glass remain untested. The next GPU implementation can
now use successive fresh guest processes for its writable-buffer/coherence
and changing-scene batch instead of reinstalling for each driver revision.

Full diagnostic evidence is preserved under
`~/dvm-artifacts/research/gpu-linked-runtime-loading-ios27/`: 1,242 indexed
records plus 53 supplemental executable/signature/TC files. Fourteen successful
GPU jobs were independently reverified from that durable copy. The final check
confirms all 12 distinct pinned boot inputs and full backing-chain files are
unchanged, including SPTM/TXM and the migrated baseline. All 81 host regressions,
shell syntax checks and diff whitespace checks pass. Original and traced
failed attempts remain labelled separately from the final acceptance runs.
