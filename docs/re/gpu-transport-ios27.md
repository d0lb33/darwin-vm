# Dedicated GPU transport experiment: exact 24A5430a guest

The dedicated auxiliary byte channel is **proven for the narrow workload**
in AUX14 and AUX16, on the unchanged 24A5430a guest. This is a transport feasibility
experiment, not a GPU driver or a claim of accelerated SpringBoard. The prior guest → host Metal → guest IOSurface proof
is in [gpu-roundtrip-ios27.md](gpu-roundtrip-ios27.md). Its UART transport
works for nine verified operations, but is too expensive for the intended
workload and temporarily owns the input console.

## Baseline and isolation

Worktree `/Users/jdolbe1/Downloads/darwin-vm-gpu-transport`, branch
`codex/gpu-transport-ios27`, created from local main `99e012c`. GPU commits
`586fc56`, `b147f0d`, and `f2ba340` were cherry-picked as `feece11`, `eec903f`,
and `9a7ceff`. The main worktree's ongoing warm-boot edits were not changed.
The QEMU worktree/branch is `qemu-sptm`, `codex/gpu-transport-qemu`, based on
`a280b46`. Builds use that worktree's own build directory.

The disk lineage starts at `/tmp/dvm/warm-input-v5/warm-manifest.json` and
read-only `/tmp/dvm/warm-input-v5/disk.qcow2`, SHA-256
`d95356ee3ed9344ead971120c610ee48482aaf098840d3f4b9da40a694a099ae`.
Every install and System trial verifies the complete backing chain and pinned
firmware, then creates a fresh qcow2 child. Only copied small restore ramdisks
are attached to the host, through `tools/rootfs/safe_attach.sh`. System/Data
are installed through an owned restore VM; they are never host-mounted.

The guest remains iOS 27 / 24A5430a / iPhone17,3 / T8140, with SPTM and TXM,
six virtual CPUs, 12 GiB RAM, and the existing 1179×2556 display configuration.
The original input launch service remains present and unchanged. The added
helper does not register a GPU, select Metal globally, or change the software
renderer. Other running VMs are not experiment targets.

## Why test an auxiliary ANS namespace?

**Observed baseline:** the guest already roots through
`AppleANS3CGv2Controller/NS_01@1/IOBlockStorageDriver` and `disk1s1`.
`GPU_CHANNEL_BASE1/serial.log` records the original seven DT namespace tuples
and one namespace in the emulated controller. The existing UART result's
nine operation elapsed times total 401.382 s. Its host worker recorded
33.347 ms wall time, including 1.153 ms GPU time. These are different scopes;
the difference cannot all be attributed to a particular UART primitive or
to TCG without additional instrumentation.

**Static evidence:** `darwin.c` wires one Samsung UART to `serial_hd(0)`.
The ARM platform-console reference selects a single UART; copying a DT node
would not establish a second independent BSD channel. The exact guest has
USB device-mode NCM components, but that route still needs a modeled USB
device controller and a host-facing link. The absence of a demonstrated
virtio/vsock path is not proof that a custom implementation is impossible.

**Architectural hypothesis:** a synthetic auxiliary namespace can reuse the
guest's working storage driver and offer a separate raw byte channel. This
is not concurrent host access to live APFS Data. It has its own newly created
64 MiB backing file; the migrated disk remains on namespace 1.

The original DT property contains:

```
(1,1,0) (2,2,0x800) (3,3,0x20) (4,4,2)
(5,5,2) (6,8,0x100) (7,13,0x8000)
```

`tools/gpu/aux_namespace_dt.py` appends `(8,6,0)` and verifies that every
other property is byte-identical. It deliberately avoids generic string
decoding/re-encoding of opaque data such as `random-seed`. The original DT
SHA is `4d403afd2bf3be1458999929572ebdd6b24a04884572752b79581dd47490e843`;
the extended DT SHA is
`80b6d97ad677a69f91191fe6e0aa7f79c5488983184f3b564ffd289cb3dac181`.
The tuple is an explicit virtual extension, not a reconstructed Apple
hardware property.

## Implemented experiment and its contracts

`DARWIN_ANS_AUX_DRIVE=gpu_aux` opts into the additional QEMU backend. There is
no fallback lookup to the root disk. Read/write/zero/Identify route by NSID;
the auxiliary drive must be distinct, nonempty, and LBA-aligned. The active
namespace list uses an exclusive cursor, as in QEMU's NVMe reference. With
the option absent, the historical single-namespace behavior remains, apart
from an overflow-safe I/O range check and clearer inactive-namespace logging.

The signed helper enumerates IOKit classes, BSD devices, network interfaces,
and socket-family construction results. The initial raw-BSD probe requires
an `IOMedia` path containing `/NS_06@6/`, a whole numeric `diskN` name, a
64 MiB capacity with 4096-byte blocks, and the dedicated marker before
opening `/dev/rdiskN` writable. It rechecks the header after that open. It
never opens namespace 1. The host peer opens only `aux.raw` that it creates
exclusively in the current trial's directory.

The byte experiment is one 1 MiB host seed read/verification, one 1 MiB guest
result write verified independently by the host, and ten 4096-byte requests.
The frame has a 64-byte identity (magic, random session nonce, zero padding),
sequence at byte 64, CRC32 at 68, and payload at 72. Each side owns separate
request/reply regions. Sequence 9 is deliberately unanswered, followed by
sequence 10 to test recovery. The helper polls with a deadline; there is no
console reader, terminal-mode change, or input-service handoff.

The raw-file sharing tests the current QEMU/OS file-cache visibility. It
does not establish durable storage, GPU cache coherence, zero-copy memory,
multi-client ownership, replay after worker death, or a checkpoint contract.
The current bulk timers bracket the I/O call only; verification and CRC work
follow that interval. They still measure the guest call including scheduling
and emulation, rather than isolated NVMe or host memory bandwidth.

Outgoing checkpoint creation is blocked when the auxiliary backend is
enabled. Actual HMP in `GPU_CHANNEL_AUX1/migration-blocker.txt` returned:

```
Error: ANS auxiliary transport state has no checkpoint contract yet
```

The final source also rejects incoming ANS state on an aux-configured
destination in `pre_load`. That incoming guard has been built and reviewed;
an actual incoming checkpoint test remains untested. Existing non-auxiliary
checkpoint state uses the same VMState version/layout.

## Runtime trials and failures

Artifacts below are under `/tmp/dvm` (durable archive recorded with the final
results). Every trial has a unique tag, owned PID, UART socket, monitor, and
child disk. The UART is continuously drained.

| Trial | Observed result | Scope |
| --- | --- | --- |
| `GPU_CHANNEL_INSTALL1` | `probe.sh`: 506 serial lines, 0 XNU panics, reached shell yes; guarded install marker | Original QEMU, separate read-only inventory helper installed |
| `GPU_CHANNEL_BASE1` | Input READY at 145.193 s; no helper marker at 240.206 s deadline | Inventory not established; no inference of absent devices |
| `GPU_CHANNEL_QEMU_CTRL1` | `probe.sh`: 303 lines, 0 panics, reached shell yes | Isolated QEMU build, auxiliary backend disabled |
| `GPU_CHANNEL_AUX1` | Driver obtained eight DT tuples, identified NSID 6, normal `disk1s1` root; input READY at 177.184 s; no helper marker by 480.414 s | Kernel identification proven; BSD-media access not yet established by this trial |
| `GPU_CHANNEL_INSTALL4` | `probe.sh`: 506 lines, 0 panics, reached shell yes; guarded install marker | Revised helper and QEMU; no aux backend during install |
| `GPU_CHANNEL_AUX4` | Input READY at 184.501 s; helper completion gate fails at 600 s | Inherited stderr control; no successful byte transfer |

The lack of a marker initially allowed several explanations. Read-only HMP
snapshots in AUX4 resolved an actual pre-main execution path. A first 1 GiB
scan found no validated helper process; the next 1 GiB found it at physical
`0x10077f5c720`, thread `0xffffffe76da6e890`. Absence from the first scan was
not treated as process absence. A subsequent fresh read resolved cache slide
`0x102dc000` by matching 64 instruction bytes against the exact guest cache.

`probe-symbols.txt` maps saved PC `0x1908333a8` to static `0x1805573a8`,
`libsystem_malloc::_mvm_guard_plat +120`, beneath `___malloc_init` and dyld
`runAllInitializersForMain`. The earlier saved state was in feature-table
initialization beneath the same startup path. The changing states show
startup activity; they do not prove a permanent hang, scheduler starvation,
or a particular failing syscall. No transport operation had run at those
snapshots. The snapshots pause/resume this owned VM and add their recorded
pause durations to its wall-clock deadline; they write no guest memory or
registers.

The checked XNU reference's sleeping console reader releases its TTY mutex
through `ttysleep`/`msleep0`. It does not support the proposed persistent
reader-held lock blocking `open`. Removing the helper's redundant console
open was a control, not a verified fix. The scheduling control changed
only the added helper's launchd `ProcessType` to `Interactive`, matching the
existing input service. AUX5 reached main at 17.471 s and completed inventory
at about 28 s; AUX6 reached main at about 16 s. This supports a scheduling
explanation for the prior pre-main delay, without proving its precise cause. `GPU_CHANNEL_STAGE5/control-difference.json` verifies
that it is the sole decoded plist change.

## Exact namespace type and user-client contract

With Identify Namespace vendor byte `+0x180` zero, the kernel identified NS6
but published no corresponding service or BSD disk (AUX6). Raw exact-guest
bootkc code at `0xfffffff00a104c54` loads that byte, maps namespace type to
ordinal at `...a104ccc`, and AllocateNodes (`...a104298`) dispatches by type.
The opt-in QEMU3 sets only NS6's byte to 8. Namespace 1 retains its historical
response. AUX7 then recorded:

```
Identified nsid[6] as nstype[8]
GPU_LOAD_SERVICE match=AppleNVMeNamespaceDevice class=AppleNVMeNamespaceDevice path=IOService:/AppleARMPE/arm-io@10F00000/AppleH17PPlatformIO/ans@79600000/AppleASCWrapV6/iop-ans-nub/RTBuddy(ANS2)/RTBuddyService/AppleANS3CGv2Controller/NS_06
```

This proves service publication, not byte access. There was still no NS6
IOMedia/BSD disk. The ordinary `/dev/rdiskN` route is disproven for this
specific type-8 configuration. It does not rule out other namespace types.
Type 8 may have existing kernel consumers; their ownership expectations are
unresolved and must be addressed before treating this as a production channel.

A significant RE correction: radare2 pseudo mode rendered the condition at
`0xfffffff00a1043f0` incorrectly. Raw bytes `61060054` decode as
`b.ne 0xfffffff00a1044bc`, not an equality branch. The raw assembly and runtime
agree on `AppleNVMeNamespaceDevice`; the earlier prediction of a generic BSD
block device was rejected. `GPU_CHANNEL_NS_RE/typed-dispatch-assembly.txt`
retains the raw branch evidence.

The [QAS namespace implementation](https://raw.githubusercontent.com/ChefKissInc/QEMUAppleSilicon/master/hw/nvme/ns.c)
and [Identify structure](https://raw.githubusercontent.com/ChefKissInc/QEMUAppleSilicon/master/include/block/nvme.h)
provided a reference hypothesis for NSTYPE. The exact guest independently
established the offset and branch behavior; another platform's model was not
accepted as proof of compatibility.

Raw bootkc `AppleNVMeNamespaceUC::externalMethod` at
`0xfffffff00a124acc` supplies this narrow contract:

| Selector | Scalars | Static operation |
| --- | --- | --- |
| 0 | inputs `[user_address, byte_count, byte_offset]` | Read namespace |
| 1 | same three inputs | Write namespace |
| 2 | no inputs, one output | Logical block size |
| 3 | no inputs, one output | Block count |

Selectors 0/1 require nonzero, block-aligned count/offset within capacity and
`com.apple.AppleNVMeNamespaceDevice.allow`. The gate is `...a125198`; mask
`0x13` covers 0, 1, 4. The service's derived `newUserClient` (`...a12322c`)
forwards the IOServiceOpen type, with no derived type check; type 0 is the
smallest tested attempt. These are static contracts, not runtime successes.

AUX9 (platform-application only) and AUX10 (adding the namespace entitlement)
both observed:

```
GPU_LOAD_AUX_UC_OPEN type=0 kr=0xe00002e2 client=0x0
```

The return is `kIOReturnNotPermitted`. Their `result=recorded` completion
marker denotes completed observation, **not successful opening**. No selector
ran. Namespace entitlement alone therefore does not satisfy the open contract.
AUX13 added the specific `AppleNVMeNamespaceUC` IOKit class exception and
opened successfully: `kr=0x0 client=0xdb7`. Selectors 2/3 returned 4096 and
16384. AUX10 had explicitly logged `IOUC AppleNVMeNamespaceUC failed MACF`;
that denial no longer occurred. The absent SideBar entitlement is separately
logged but did not prevent opening or metadata queries.

The user-client byte adapter completes enumeration before opening a unique,
exact full-path/class match. This service did not publish NSID/NSTYPE
properties in the inventory. After opening, selectors 2/3 must return 4096
and 16384, then selector 0 must return the dedicated header before any write.
Buffers are 16 KiB aligned; all accesses are bounded to the owned 64 MiB
backend. The Python host independently requires its random 64-byte session
identity, CRC, and expected bytes. No additional kernel patch is used to bypass this open gate; the established
VM boot patches are unchanged.

AUX5's independent network inventory observed only `lo0`, successful IPv4/IPv6
socket construction, and `socket(AF_VSOCK, SOCK_STREAM)` failure with errno 19
(`ENODEV`). There were no IONetworkInterface, IOSerialBSDClient, or
IOUSBDeviceController matches. These are scoped observations for this boot,
not proof that adding a new device model/driver is impossible.

## Host checks and review

All 30 project host regressions and the required shell syntax checks pass.
`tools/gpu/test_aux_transport.py` exercises the actual C byte peer against
the Python host peer, verifies the output, tests timeout/recovery, rejects
corrupted output, and checks both wrong magic and a valid packet with an old
session nonce. Its DT fixture specifically preserves a 256-byte printable
opaque seed and rejects a second extension and truncated input.

The first integration test failed at request 1: the C code assumed a
52-byte identity, but the magic plus nonce occupied 53 bytes. It timed out
after 15 s; `GPU_CHANNEL_HOST_TEST2.log` retains the failure. The corrected
fixed 64-byte layout passes all three tests (`HOST_TEST3`, `4`, `5`, and final `HOST_TEST14`). These
host tests use POSIX I/O and bypass the guest user client. They cannot prove
the private selector mapping; AUX13 demonstrates precisely that limitation.

Terra/high agents supplied read-only UART/reference mining, exact-guest
driver mining, source review, dump scanning, and symbolization. Parent review
rejected the early overbroad dismissal of auxiliary storage, checked the
namespace/guard proposals, and incorporated reserved-cursor validation and
the incoming-checkpoint guard. A later review caught transfer-before-uniqueness
and the helper was corrected before runtime. Review was not sufficient by
itself: both parent and agent initially inverted I/O direction labels. The
AUX13 failure forced tracing to the actual opcode builder; the corrected
contract is recorded below, with the failed trial retained. The existing non-aux CNS=3 behavior was
identified as imperfect but retained to avoid an unrelated default change.

## Reproduction entry points

```
python3 -m unittest discover -s tools/tests -v
bash -n tools/probe.sh tools/re/setup_gate_probe.sh tools/re/setup_gate_sweep.sh
python3 tools/gpu/test_aux_transport.py -v

mkdir qemu-sptm/build
cd qemu-sptm/build
../configure --target-list=aarch64-softmmu --disable-pvg --disable-docs
make -j18
cd ../..

python3 tools/gpu/aux_namespace_dt.py ORIGINAL_DT NEW_DT
python3 tools/gpu/prepare_aux_manifest.py ORIGINAL_MANIFEST QEMU NEW_DT NEW_MANIFEST
bash tools/gpu/build_transport_inventory.sh NEW_BUILD --uc-probe \
  --namespace-entitlement --class-exception
python3 tools/gpu/prepare_guest_load.py --build NEW_BUILD \
  --cache ORIGINAL_LAUNCHD_CACHE --system-tc ORIGINAL_SYSTEM_TC \
  --output NEW_STAGE --interactive-load
python3 tools/gpu/run_guest_install.py --manifest NEW_MANIFEST \
  --stage NEW_STAGE --tag UNIQUE_INSTALL_TAG
python3 tools/gpu/run_guest_load.py /tmp/dvm/UNIQUE_INSTALL_TAG/warm-manifest.json \
  --tag UNIQUE_RUN_TAG --aux-probe --seconds 180
```

The manifest records the exact QEMU, firmware, DT, trust cache and disk lineage.
`launch.json`, `result.json`, both serial captures, and `aux-host.jsonl` retain
the actual trial arguments and witnesses. `--aux-namespace` without
`--aux-probe` is the read-only discovery configuration when paired with the
inventory-only helper build; it does not run the byte peer.

## AUX13 direction correction

AUX13 did **not** establish read access. The initial selector interpretation
was reversed: parent and agent had inspected the instructions but assigned
the wrong meanings to IOMemoryDescriptor direction values. Local SDK
`IOMemoryDescriptor.h:59` defines `kIODirectionIn=1` (user read), and
`kIODirectionOut=2` (user write). Selector 1 uses direction 2, reaches
`...a122564`, then `...a12260c`, then `...a139038`; instruction
`...a1390a4` sets NVMe opcode **1, write**. Selector 0 reaches
`...a138f1c`, whose `...a138f80` sets opcode **2, read**. It wrote the zeroed helper buffer
to the first 4096 bytes of the owned auxiliary file. Its original seed at
1 MiB remained intact. The failed header guard stopped further operations.
This was an experiment error, not evidence of a kernel consumer erasing it.
Namespace 1 was not selected. The underlying opcode evidence is retained in
`GPU_CHANNEL_NS_RE/uc-write-opcode-raw.txt`.

BUILD14 corrects selector 0 to read and selector 1 to write, initializes the
header buffer to `0xcc`, logs the returned prefix, and fails at the inventory
cap instead of accepting a truncated uniqueness check. The host verifier
also requires its original header to remain unchanged. The corrected trial
uses a freshly created auxiliary file and a new child disk.

## Verified byte result and feasibility decision

`GPU_CHANNEL_AUX14/result.json` records `passed=true`. The exact guest opened
the namespace client, read the original transport header, verified the 1 MiB
host seed, and wrote the expected transformed 1 MiB result. The independent
host verified that output, all ten request payloads, and the unchanged header.
Guest replies 1–8 and 10 passed identity/sequence/CRC/payload checks; sequence
9 intentionally received no host reply, and sequence 10 recovered. No GPU
command or shader was part of this new byte experiment.

AUX14's observed I/O-call times were 2.433 ms for the 1 MiB read and 2.312 ms
for the write. Requests 1–8 took 6.897–11.260 ms. These are guest monotonic
elapsed times, including emulation and scheduling. The host serial-arrival
and host-peer times are independently recorded in `result.json` and
`aux-host.jsonl`; they are not GPU execution times. The nominal one-second
polling deadline completed after **2.414 s**, and recovery reply 10 took
**348.827 ms**. A software loop deadline cannot prevent a synchronous I/O or
scheduling delay from overshooting it. This is not a hard latency guarantee.
The first eight replies are encouraging, but quoting only those would hide
the tail behavior.

The prior UART proof's nine GPU-operation durations include guest Metal and
IOSurface work, while this experiment measures bytes. A precise speedup ratio
between them would not be valid. What is established here is that bulk data
can bypass the console through the existing guest storage driver with small
observed I/O-call costs. The 64 MiB transport is a separate raw file, not an
APFS mailbox in the migrated Data volume.

The fresh AUX15 repeat used the same immutable QEMU3 and signed BUILD14,
a newly initialized auxiliary file, and another qcow2 child. Bytes again
passed: read 2.772 ms, write 2.003 ms; early replies 7.651–11.159 ms;
timeout 1.755 s; recovery 718.607 ms. The input helper restarted (PID 250),
then reached READY at 171.886 s. The early sync sent during sequence 9 had
no observed ACK. The diagnostic was stopped with SIGINT to its identified
runner, allowing its normal owned-VM teardown, to correct that check. This
is a recorded input-check failure, not an overall `passed=true` trial.
AUX16 sends a fresh no-event sync (900002) after READY; the early sync
(900001) remains a separate observation. An ACK proves protocol availability
after transport use, not concurrent touch delivery or a graphical input test.

| Route/contract | Status and scope |
| --- | --- |
| Existing guest QuartzCore AIR → host Metal → verified pixels | **Proven previously**, exact selected library/workload; not repeated here |
| Guest-created work → host Metal → guest IOSurface through UART | **Proven previously**, nine operations; expensive transport/input handoff |
| Auxiliary ANS namespace discovery and client loading | **Proven**, type 8 / NSID 6 and the tested namespace-entitlement plus class-exception configuration |
| Auxiliary byte channel | **Proven**, AUX14/AUX16 bulk, request/reply, timeout/recovery; latency tails remain |
| Raw BSD-disk access for this type-8 namespace | **Disproven within tested scope**: no auxiliary IOMedia/BSD disk |
| Opening with platform-application or namespace entitlement alone | **Disproven within tested scope**: MACF denial, `0xe00002e2` |
| GPU commands and pixels carried over auxiliary channel | **Untested**; next integration step |
| Adapted Apple PV GPU stack | **Untested end to end**; transport result neither proves nor disproves it |
| Metal-discovered custom forwarding plugin / system UI integration | **Untested end to end**; existing process-local proof remains narrower |
| Interception/reuse of the guest's existing GPU components | **Untested end to end** |
| Original input protocol after auxiliary use | **Proven**, AUX16 READY and fresh sync ACK; concurrent graphical input not tested |
| Auxiliary live-state checkpoint/restore | **Untested and explicitly blocked**, outgoing refusal observed |
| Windows host GPU backend | **Untested**; byte protocol portability does not supply a Windows Metal/AIR backend |

The smallest worthwhile next implementation is to replace the prior
process-local proof's UART byte adapter with this namespace adapter and
replay the **same exact-guest shader and verified IOSurface workload**. Cache
the library once, carry bounded command/resource IDs and copied buffers,
and compare full operation time with the prior proof. Start with one client
and one outstanding operation; preserve the software renderer and original
input service.

Major dependencies before that can become useful acceleration:

1. **Protocol and lifetime:** framing/chunking of the existing GPU messages,
   host-worker failures, stale completions, bounded resources, and exactly
   which party owns buffers until completion. The ten-ping peer is not a
   production resource protocol. Its CRC/nonce checks are error detection,
   not a security boundary against another entitled guest process.
2. **Scheduling and synchronization:** measure the full GPU roundtrip and
   steady-state latency tails under normal input/display activity. This
   polling copied-buffer channel proves neither zero-copy coherency nor
   frame pacing.
3. **Guest integration:** discover/register the Metal plugin and establish
   the actual narrow interface calls required by a real client. The byte
   route uses `AppleNVMeNamespaceDevice`, so an IOGPUFamily subclass is not
   needed merely to transport bytes. That does not establish whether the
   eventual Metal registration path needs one.
4. **Namespace ownership:** characterize existing type-8 consumers and
   concurrent opens before production use. The dedicated backend avoids
   modifying baseline storage; it does not itself prove exclusive service
   ownership.
5. **Checkpoint state:** quiesce both endpoints, resolve in-flight work,
   rebuild host Metal objects, and invalidate/reconcile epochs after restore.
   Existing copied-pixel restore evidence does not cover these live objects.

Transport feasibility work did not require the pending warm-boot changes.
Warm-boot stabilization will matter for representative steady-state and
checkpoint validation, but it was not a prerequisite for this isolated proof.

## Exact reproduction inputs

Final QEMU commit: `89b238c` on `codex/gpu-transport-qemu`. Its immutable binary
is `/tmp/dvm/GPU_CHANNEL_QEMU3/qemu-system-aarch64`, SHA-256
`f749c511de6be51b5e308136c9e4df6592e24714fc036211661ae6dda23f2049`.
Signed BUILD14 helper SHA-256:
`df269e3bb4da33fbb6584831809cd3346af246dc129ea9e721fdbea1819e43d8`.
The exact entitlement plist contains only these booleans and class exception:

```
platform-application = true
com.apple.AppleNVMeNamespaceDevice.allow = true
com.apple.security.exception.iokit-user-client-class = [AppleNVMeNamespaceUC]
```

Actual final staging/install/run commands, from this worktree:

```sh
bash tools/gpu/build_transport_inventory.sh /tmp/dvm/GPU_CHANNEL_BUILD14 \
  --uc-probe --namespace-entitlement --class-exception
python3 tools/gpu/prepare_guest_load.py \
  --build /tmp/dvm/GPU_CHANNEL_BUILD14 \
  --cache /tmp/dvm/WARM_RUNTIME_STAGE2/launchd-input.plist \
  --system-tc /tmp/dvm/warm-input-v5/system.tc \
  --output /tmp/dvm/GPU_CHANNEL_STAGE14 --interactive-load
python3 tools/gpu/run_guest_install.py \
  --manifest /tmp/dvm/GPU_CHANNEL_ORIGINAL3.manifest.json \
  --stage /tmp/dvm/GPU_CHANNEL_STAGE14 --tag GPU_CHANNEL_INSTALL14
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_CHANNEL_INSTALL14/warm-manifest.json \
  --tag GPU_CHANNEL_AUX14 --aux-probe --seconds 180
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_CHANNEL_INSTALL14/warm-manifest.json \
  --tag GPU_CHANNEL_AUX15 --aux-probe --aux-wait-input --seconds 300
# Corrected post-READY input check:
python3 tools/gpu/run_guest_load.py \
  /tmp/dvm/GPU_CHANNEL_INSTALL14/warm-manifest.json \
  --tag GPU_CHANNEL_AUX16 --aux-probe --aux-wait-input --seconds 300
```

All tools deliberately reject reused output directories. To reproduce,
choose new build/stage/install/run tags rather than overwriting these records.
The installed manifest pins its full backing chain, firmware, TC, QEMU, and
DT. `GPU_CHANNEL_ORIGINAL3.manifest.json` retains the original warm-input-v5
lineage with only the experimental QEMU/DT replacements.

## Durable evidence and limits

Evidence is copied with SHA-256 verification under
`/Users/jdolbe1/dvm-artifacts/research/gpu-transport-ios27-20260905`.
`files.json` inventories the retained logs, manifests, RE assembly, signed
helpers, trust caches, QEMU binaries/source snapshots, test results, and final
auxiliary backing files. Large disposable System/Data children, restore
ramdisks and raw RAM snapshots are excluded and listed explicitly. The
manifest's original disk/firmware hashes remain the reproduction contracts;
this evidence archive is not a self-contained guest installation.

`GPU_CHANNEL_FINAL_BASELINE_CHECK.json` records a final successful verification
of the original warm-input-v5 **full backing chain and every pinned input**.
No original migrated disk or firmware hash changed. AUX14/AUX15/AUX16 use fresh
System boots; no saved RAM was restored, no other VM was targeted, and no
warm-boot change was merged into this worktree. Software rendering remains
selected. Display presentation is prior evidence, not a new pixel-display
claim from these byte tests.

## Final fresh repeat and input witness

`GPU_CHANNEL_AUX16/result.json` records `passed=true`,
`input_sync_ack_observed=true`, and `kept_paused=false`. Its independent host
again verified the unchanged header, exact 1 MiB guest output and all ten
requests. The guest verified the host seed and all expected replies.

| AUX16 event | Observed |
| --- | --- |
| 1 MiB read call | 2.121 ms |
| 1 MiB write call | 1.584 ms |
| Replies 1–8 | 7.459–9.644 ms |
| Deliberate timeout 9 | 1.504 s for a nominal 1 s loop budget |
| Recovery reply 10 | 445.198 ms |
| Byte completion | 23.735 s after VM launch |
| Original input READY | 156.051 s, PID 271 after a boot-time restart |
| Fresh sync ACK | `DVM_INPUT_ACK 900002 1` at 156.284 s |
| Owned VM stopped | 156.531 s; no saved RAM restoration |

This establishes post-transfer availability of the existing input protocol.
It does not explain the boot-time helper restart, establish sustained UI
responsiveness, or claim a touch event reached the screen. No input service
was replaced by the byte helper, which never reads the console.

All 30 required project host tests passed (`GPU_CHANNEL_TESTS16.log`), along
with the three byte-protocol/DT tests (`GPU_CHANNEL_HOST_TEST14.log`) and
required shell syntax checks. The QEMU changes build successfully and were
exercised by the guarded restore installs and these fresh System trials.
The final result supports proceeding to the same shader/IOSurface workload
over this adapter, with the dependencies listed above still explicit.
