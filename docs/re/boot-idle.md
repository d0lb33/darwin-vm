# Why the SpringBoard boot goes idle

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140, kernelcache
`firmware/bootkc`.  Investigated from superproject `6432ffc`, QEMU submodule
`41dbd86`, and the preserved `XARTFIX` 40-GiB RAM snapshot under
`/tmp/dvm/xart/ram`.  Addresses below are unslid unless a slid value is shown;
the runtime slide in that snapshot is `0x20000000` (the sampled idle PC
`0xfffffff02ab217a0` corresponds to `0xfffffff00ab217a0`).

## Verdict

The quiet machine is not stuck inside SpringBoard.  All four SpringBoard
threads are asleep: three are blocked in Mach message receive on three
distinct receive rights, and the fourth is an ordinary parked pthread
workqueue worker.  The processor therefore has no runnable work and enters
the idle loop.  This identifies the mechanism behind the silence, but does not
identify which producer should send the next message.

The display experiment does not turn that quiet state into a working UI.  It
does prove that the display stack is live and materially changes execution:
the guest opens an `IOMobileFramebufferUserClient`, makes further IOMFB RPCs,
and launchd ultimately reports repeated SpringBoard crashes.  Therefore the
display is on the blocked path, but the current zero-output IOMFB stubs are not
a solution.  The next measured display gap remains the input-carrying D-series
callbacks described in `docs/re/iomfb-dseries.md`; no semantics for those
callbacks are guessed here. The corrected `D120` control proves a real nested
callback-context transaction and AP-generated completion
(`/tmp/dvm/probe/IOMFB_NESTED_D120_FIX1.stderr.log:111-176`), superseding the
earlier synthetic “reflection/kick” interpretation. It still does not produce
a framebuffer or establish `default_fb_gated`.

The last console line is not a termination record.  It is the constraint
failure at `/tmp/dvm/probe/XARTFIX.serial.log:42078`; the preserved RAM scan
prints only four `DumpPanic` records and no SpringBoard `OS_REASON` in
`/tmp/dvm/xart/oskcdata.txt:1-17`.  The independent process-presence positive
control is SpringBoard's dyld `executable_path` at
`/tmp/dvm/xart/execpath_ctx.txt:171`.

## Recovering the SpringBoard threads

The SpringBoard `thread_group` is at physical dump address
`0x105668eda20` (`010540000000.bin+0x268eda20`) and has virtual address
`0xffffffe196315a20`.  Its layout is independently identifiable from
`osfmk/kern/thread_group.c:48-68`: group id `10`, the 32-byte name
`SpringBoard`, refcount `5`, and flags `0x102` occur consecutively there.

Four `struct thread` objects point to that group at offset `+0x290`.  Their
physical locations and saved continuations are:

| dump location (`struct thread`) | state at `+0x1f0` | continuation at `+0xe0` |
|---|---:|---:|
| `010540000000.bin+0x2717e0c0` | `0x1` | `0xfffffff02aa8f658` |
| `010580000000.bin+0x3a3720c0` | `0x41` | `0xfffffff02aa8f658` |
| `010600000000.bin+0x2e03a0c0` | `0x1` | `0xfffffff02afb0b48` |
| `010600000000.bin+0x325a98f0` | `0x41` | `0xfffffff02aa8f658` |

`TH_WAIT` is bit `0x1` and `TH_WAIT_REPORT` is bit `0x40` in
`osfmk/kern/thread.h:512-521`, so every row is waiting.  The actual build's
layout is confirmed in the binary rather than assumed from the open-source
header: `thread_block()` stores the continuation/parameter pair at thread
offset `+0xe0` at `0xfffffff00ab1bd1c`, and
`waitq_assert_wait64_locked()` first checks `thread->waitq` at offset `+0x28`
at `0xfffffff00ab5eba8`.

The repeated continuation unslides to `0xfffffff00aa8f658`.  Its strings at
`0xfffffff007047d6b` and `0xfffffff007047d33`, referenced at
`0xfffffff00aa8f6c0` and `0xfffffff00aa8f718`, name it
`ipc_mqueue_receive_results`.  The fourth continuation unslides to
`0xfffffff00afb0b48`; it obtains the current uthread at
`0xfffffff00afb0b64-0xb78` and blocks again with itself as the continuation at
`0xfffffff00afb0c80-0xc98`.  This matches `workq_unpark_continue()` and its
`thread_block(workq_unpark_continue)` park path at
`bsd/pthread/pthread_workqueue.c:4847-4893` and `:3961`.

## The three Mach receive waits

The build stores `wait_event` and `waitq` at thread offsets `+0x18` and
`+0x28`, respectively (`0xfffffff00ab5edcc` and
`0xfffffff00ab5edd0`).  For the three IPC threads, `wait_event` is zero, as
required by `IPC_MQUEUE_RECEIVE == NO_EVENT64`
(`osfmk/ipc/ipc_mqueue.h:122`).  Their wait queues translate through the
snapshot's 16-KiB kernel page tables rooted at physical `0x10007068000`:

| thread | waitq VA | waitq physical location | type | receive name |
|---|---:|---:|---:|---:|
| 1 | `0xffffffe3626b3b00` | `010600000000.bin+0x3391bb00` | `3` | `0xa03` |
| 2 | `0xffffffe3620af050` | `010580000000.bin+0x14e17050` | `3` | `0x5603` |
| 4 | `0xffffffe3620a6f30` | `010600000000.bin+0x06442f30` | `3` | `0x2203` |

The low three bits of each waitq's first word are `3`, which is `WQT_PORT`
(`osfmk/kern/waitq.h:198`).  In this build the embedded `ipc_mqueue` begins at
waitq `+0x18`; the receiver name is therefore the high 32 bits at `+0x20`,
following `imq_seqno` (`osfmk/ipc/ipc_mqueue.h:83-91`).  The queue limits at
`+0x2a` are all `5`, another layout cross-check.  These are three empty,
distinct Mach receive queues, not a shared mutex or a running loop.

The workqueue thread has a nonzero private park event at thread `+0x18` and
its waitq's type bits are `1` (`WQT_QUEUE` at `osfmk/kern/waitq.h:196`), which
is consistent with the parked-worker continuation above.

## Display experiment

Probe `FULL4_A385_ONE` enabled ANS, DCP, SEP and SMC and set
`DARWIN_DCP_IOMFB=4`.  The model-side positive controls are the SMC instance at
`/tmp/dvm/idle/probe/FULL4_A385_ONE.stderr.log:34`, the ANS endpoint starts at
`:73-115`, the SEP endpoint advertisement at `:32`, and all eleven DCP AFK
endpoints at `:308-693`.  The guest-side positive controls are:

- `Early boot complete` at
  `/tmp/dvm/idle/probe/FULL4_A385_ONE.serial.log:42101`;
- backboardd's AppleSEPKeyStore request at `:42104`;
- the guest mapping IOMFB display type 0 at `:42164` and asking for display
  power state 1 at `:42514`;
- the post-startup IOMFB calls `A427` and `A412` at
  `/tmp/dvm/idle/probe/FULL4_A385_ONE.stderr.log:1218-1230`.

This is not a silent no-op.  It also is not a successful display boot.  The
constraint-query error occurs at
`/tmp/dvm/idle/probe/FULL4_A385_ONE.serial.log:42534`, followed by launchd's
`rebooting due to critical process crashes: SpringBoard` at `:42563`.  The
first kernel panic is the consequent reset timeout at `:42692`, exactly the
machine limitation in `IOPlatformExpert.cpp:900`, not the cause of the
SpringBoard exits.

The `pmemsave` snapshot taken at that launchd marker contains no usable
SpringBoard exit reason.  `tools/oskcdata.py` finds two buffers containing the
word `SpringBoard` at
`010680000000.bin+0x3b7cd070` and `+0x3c281070`, but both top-level buffers are
`KCDATA_BUFFER_BEGIN_OS_REASON` (`osfmk/kern/kcdata.h:469`) for a process named
`exc handler`.  The large item in each is a padded array
(`KCDATA_TYPE_ARRAY_PAD0/PAD8`, `osfmk/kern/kcdata.h:420-428`), whose flags
identify its elements as `TASK_CRASHINFO_UDATA_PTRS`
(`osfmk/kern/kcdata.h:1406`), not a backtrace.  Neither buffer has
`EXIT_REASON_SNAPSHOT`, exception codes, or a description, so those opaque
user pointers do not justify assigning a crash cause.

One bounded canned-output test rules out the conspicuous repeated getter.
`A385`'s wrapper constructs a four-byte-output request and returns bit zero at
`0xfffffff00917ef58-0xfffffff00917efa4`.  With the explicitly experimental
`DARWIN_DCP_IOMFB_OUT=A385=01`, it runs only once and is completed from the
override at `/tmp/dvm/idle/probe/FULL4_A385_ONE.stderr.log:1972-1973`, rather
than polling thousands of times, but SpringBoard still reaches the same
launchd critical line at serial `:42563`.  This is evidence that the `A385`
poll itself is not the missing event; it is not a claim that returning one is
real device behaviour.

The hot-plug-shaped input-carrying callback was then tested at the point the
guest actually asks for display state, rather than during DCP startup.  The
current `D575` handler requires `0x58` input bytes and `0x4c` output bytes
(`0xfffffff00a0d9e60-0xfffffff00a0d9e9c`).  It forwards an input `uint64_t`,
an optional `0x4c`-byte structure, and a trailing boolean to the framebuffer
object's `vtable+0x8c8` method
(`0xfffffff00a0d9ee4-0xfffffff00a0d9f7c`).  The extracted kext independently
contains the diagnostic signature
`hotPlug_notify_gated(uint64_t, IOMFB_TiledDisplayInfo *, bool)` at
`com.apple.iokit.IOMobileGraphicsFamily-DCP` file offset `0x2fc0`; this
identifies the call shape without claiming the FourCC's payload semantics.

Probe `FULL4_D575_LATE` supplied only the fields used directly by that
wrapper: `uint64_t 1` at input `+0`, boolean 1 at `+0x53`, and optional-pointer
flag 1 at `+0x54` (which makes the wrapper pass null at
`0xfffffff00a0d9f40-0xfffffff00a0d9f4c`); all unknown bytes were zero.  The
callback was not sent until the guest's `A385` at
`/tmp/dvm/idle/probe/FULL4_D575_LATE.stderr.log:2017-2022`.  The AP
read the preceding `A385` completion and the callback consecutively at
`:2023-2030`, then emitted a direct subkind-1 completion with status zero at
`:2031-2036`.  There is no intermediate subkind-0 acknowledgement and no
`PROBE kick` line.  This demonstrates that the input-callback handler runs
after userspace reaches the display, but it does not settle the transport's
kick mechanism because the immediately preceding `A385` completion is a
confounding class-2/subkind-1 message.  It also does not fix the boot: launchd
still reports SpringBoard crashes at
`/tmp/dvm/idle/probe/FULL4_D575_LATE.serial.log:42611`.  The first kernel panic
remains only the consequent reset timeout at `:42768`.

Nor do the calls after `D575` establish a new display sequence.  The
`A420/A421/A424/A428` calls at
`/tmp/dvm/idle/probe/FULL4_D575_LATE.stderr.log:2042-2075` are also present in
the no-callback control at
`/tmp/dvm/idle/probe/FULL4_A385_ONE.stderr.log:1984-2017`.  `A411` at the D575
log `:2086` is the only additional request observed, and no meaning is
assigned to that timing difference.

### Artifact qualification

The exact populated persistent encrypted-Data image from the merged SKS work
is not present in this checkout or under `/tmp/dvm`.  The four-feature run
therefore used the reproducible 40-GiB `-ephemeral-data -skip-keybag` boot
configuration and `/Users/jdolbe1/dvm-artifacts/build/rootfs_cx.dmg`; its data
volume is guest-created for the boot rather than the user's populated
persistent encrypted volume.  The preserved quiet-state snapshot was made
with the same two boot properties, as recorded in
`docs/re/sep-xart-epochs.md:133`.  This limitation means the display-path
measurement is valid, but an exact same-image A/B claim is not.  Repeating it
against the persistent image requires its path; no host image was mounted or
modified during this investigation.

## Regression probes

The DCP-only `IDLE_REG_IOMFB4` probe reached a shell with zero XNU panics.  Its
eleven AFK endpoints are recorded at
`/tmp/dvm/idle/probe/IDLE_REG_IOMFB4.stderr.log:144-595`, and its guest-issued
IOMFB transactions (`A401`, `A465`, `A353`) are at `:484-585`.  The matching
default-off `IDLE_REG_IOMFB_OFF` probe also reached a shell with zero XNU
panics; all eleven endpoint starts are at
`/tmp/dvm/idle/probe/IDLE_REG_IOMFB_OFF.stderr.log:141-526`, while
`rg -c '^iomfb:'` over that file returns zero.  These two `tools/probe.sh`
verdicts preserve both required invariants: 11/11 AFK endpoints with the
level-4 experiment enabled, and no IOMFB model output when its environment
switch is absent.
