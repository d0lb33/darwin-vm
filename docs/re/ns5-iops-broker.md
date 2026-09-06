# NS5 IOPS reply stall: frozen-state evidence

Scope: read-only analysis of the still-paused `WARM_NATIVE_SERVICES5` capture.
This does not attribute the earlier `powerd` fault to the PV source or RTC
patch, and it makes no device-tree or service-model change.

## Observed client wait

The capture's dyld slide is `0x1bf90000`. SpringBoard is live at
`proc_pa=0x10002558720`, pmap root `0x1001c736000`. Its thread
`0xffffffe981b93830` has this relevant saved user stack, in runtime VAs:

```
0x1aaf94740 -> 0x2714629dc -> 0x271462d5c -> 0x2714614cc
```

Subtracting the capture slide gives:

| Runtime | Static | identity |
|---:|---:|---|
| `0x1aaf94740` | `0x18f004740` | `IOPSCopyPowerSourcesByTypePrecise + 0xa8` |
| `0x2714629dc` | `0x2554d29dc` | BatteryCenter `_BCPowerSourceController` `connectedDevices` path (parent trace identity) |

The IOKit consumer entry begins at `0x18f004698`. It creates and resumes an
XPC Mach-service connection, creates a request dictionary, then calls the
reply-wait stub at `0x18f00473c`; the saved return PC is exactly
`0x18f004740`. The service-name cstring at `0x18f09229f` is
`com.apple.iokit.powerdxpc`. The function’s request-key cstring at
`0x18f092f89` is `powerInfoPrecise` and the backing MIG routine is
`_io_ps_copy_powersources_info` at `0x18eff38f8` (message ID `0x30004003`).
Thus the saved frame is a synchronous wait for the powerd service reply, not
BatteryCenter percent arithmetic.

## Server absent in the same frozen state

A scan of all 12 RAM chunks finds no valid live `powerd` process object. It
does retain a durable crash record at
`ram/010040000000.bin+0x3b568000`:

```
pid=51 name=powerd
path=/System/Library/CoreServices/powerd.bundle/powerd
exception type=10 (EXC_CRASH)
exception code=0xb100001 subcode=0x20
exit reason=SIGNAL 11
crashed thread ID=0x43f
```

The crash KCDATA has no saved user register state or PC. It cannot establish
which binary instruction dereferenced `+0x20`, nor when the crash occurred
relative to PV publication.

The local PowerManagement source is not the exact iOS build but independently
confirms the protocol topology: `pmconfigd.m:xpc_register` listens on
`com.apple.iokit.powerdxpc`; `BatteryTimeRemaining.m:_io_ps_copy_powersources_info`
handles the read after `dispatch_sync(batteryTimeRemainingQ, ...)`.

## Testable root-cause proposal

The evidence supports this bounded explanation for the pending reply: the only
server for SpringBoard’s synchronous request is absent because `powerd` has
crashed. It does not identify the crash cause. A next probe should first
establish whether PID 51 is live and serving `com.apple.iokit.powerdxpc`, then
call `IOPSCopyPowerSourcesByTypePrecise` under a bounded timeout. If a live
server replies, retain the existing verified PV dictionary unchanged. If it
dies again, freeze immediately and capture the crashed thread’s PC/registers
and a live pmap before attempting any power-source contract change.

## Artifacts

- `ns5-iops-process-evidence.json`: read-only process scans, including the
  absence of a valid powerd object.
- `ns5-iops-exit-reasons.txt`: KCDATA scan of the 12 frozen RAM chunks.

SHA-256:

```
711cd1d6a246d1416413add930c9b8d8074aa84797d06bb4e519b4dfd645b1cc  ns5-iops-process-evidence.json
15c229d90bc0dcd2342e6b3e6869ec61ff2b531b46eb75b19c1d35156a987627  ns5-iops-exit-reasons.txt
```
