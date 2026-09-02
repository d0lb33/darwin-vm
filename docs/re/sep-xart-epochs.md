# xART: the epoch requests and the reply frame AppleSEPXART expects

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140,
`com.apple.driver.AppleSEPManager` 928.0.2 extracted with
`ipsw kernel extract firmware/bootkc com.apple.driver.AppleSEPManager -o
/tmp/dvm/kexts` and read with `r2 -e bin.relocs.apply=true`. Addresses are
unslid kext file addresses; runtime is `+0x20000000` (the panic caller
`0xfffffff0295bfd64` on the console is `0x5bfd64` below). Analysed 2026-09-02.

The model is `qemu-sptm/hw/arm/darwin_sep.c`, `sep_handle_xart()`.

## The failure

Six lines after `Early boot complete`, immediately after `SEP EP 16 enabled`:

```
panic(cpu 0 caller 0xfffffff0295bfd64): "REQUIRE fail: expected_out_len == out_len
  @ IOReturn AppleSEPXART::getFullEpochs(SEPEpoch::EpochSlot *, size_t)_block_invoke:1021
  ... AppleSEPXART_embedded.cpp: " @AppleSEPXART_embedded.cpp:1021
```

The model's stderr for the same boot (`CXBOOT2.stderr.log` line 357) shows
the only xART traffic there had ever been:

```
sep(SEP): ep 16 'xars' OOL in  dva 0x10000034000 size 0x8000
sep(SEP): ep 16 'xars' OOL out dva 0x10000040000 size 0x8000
sep(SEP): xART ep 16 op 23 param 0 data 0x00000000: acknowledged without action
```

`sep_handle_xart()` answered every xART request with `{ep, tag, 0, 0, 0}` and
wrote nothing to the OOL out-buffer. It is not specific to the storage path:
this run was the plain ramdisk path (`rootdev=md0`, `rootfs_cx.dmg` as the
ramdisk) with `-enable sep`, no `-enable ans`. It fires wherever the boot gets
far enough for something to call `getFullEpochs`.

## getFullEpochs, `0x5a1da4`

```
0x5a1db4  cbz  x1, -> REQUIRE "nullptr != epoch_slots"        (0x5bfcfc)
0x5a1db8  cmp  x2, 8 ; b.ne -> REQUIRE "EPOCH_COUNT == epoch_slots_len" (0x5bfcc8)
0x5a1e14  block captures this (+0x20), 8 (+0x28), epoch_slots (+0x30)
0x5a1e24  bl 0x59f848         run the block on the command gate
```

The block, `_block_invoke` at `0x5a1e3c`:

```
0x5a1e50  mov  w8, 0x170000 ; stur x8, [x29, -0x18]     the 8-byte message: op 0x17, nothing else
0x5a1e5c  lsl  x20, x8, 2   ; stp x20, xzr, [sp]        expected_out_len = 8 << 2 = 32; in_len 0
0x5a1e6c  stp  xzr, x8, [sp+0x10]                       in_ptr 0, out_ptr = epoch_slots
0x5a1e70  str  x9(=sp), [sp+0x20]                       out_len pointer -> the 32
0x5a1e98  bl   0x59f0c4 (this, &msg, &args, timeout)    send and wait
0x5a1ea0  cmp  x20, [sp] ; b.ne -> 0x5bfd30              REQUIRE expected_out_len == out_len
```

So the request is the bare frame `{ep 16, tag, op 0x17, 0, 0}` with no OOL
body, and the AP requires exactly 32 bytes back: `SEPEpoch::EPOCH_COUNT` (8)
slots of `sizeof(EpochSlot)` = 4. The timeout argument is `0x13880` (80,000)
when `0x5a1d44` says the SEP is up, else `-1` (wait forever).

## What the AP does with a reply, `0x59f20c`

This is the gated half of the send helper every xART request builder calls
(17 call sites to `0x59f0c4`, all in `AppleSEPXART`). The parts that decide
the reply shape:

| address | what |
|---|---|
| `0x59f244` | `ldrb w25, [msg, 2]` — the request opcode, only used for the op-15 state machine at `0x59f4f0` |
| `0x59f31c..0x59f338` | if `in_ptr`: copy `min(in_len, 0x8000)` bytes into the OOL in-buffer (`this+0xc8`) |
| `0x59f36c..0x59f388` | take a tag slot from the endpoint (`this+0x78` vtable `+0xa8`), tag = `slot+0x40` + 1 written to `msg[1]`, `slot+0x28 = &msg`, `slot+0x20 = waiting` |
| `0x59f3ac` | send (vtable `+0xe8`) and sleep on the gate until the slot's waiting byte clears |
| `0x59f4bc` | `ldrb w22, [msg, 2]` — **reply byte 2 is the status**; nonzero is returned as the IOReturn |
| `0x59f530` | `ldurh w8, [msg, 3]` — **reply bytes 3..4 are a u16 byte count** |
| `0x59f538..0x59f548` | with an `out_ptr`: copy `min(count, 0x8000)` bytes from the OOL out-buffer (`this+0xd0`) to it |
| `0x59f568` | store the same count through the `out_len` pointer — this is what `getFullEpochs` compares with 32 |
| `0x59f580..0x59f59c` | no `out_ptr` but a nonzero count: log the string at `0x7711c3f` and return `0xe00002c2` (kIOReturnBadArgument) |

The receive side, `0x59eea4` (from the endpoint callback `0x59ed6c`):
`0x59ef04` compares byte 0 with the endpoint id, `0x59ef34` looks the tag in
byte 1 up (`this+0x78` vtable `+0xc0`), warns
`"AppleSEPXART (EP: %u) received message for invalid tag %u opcode %u"`
(`0x7711d3a`) if nothing is waiting on it, otherwise `0x59ef60` stores the
**whole 64-bit reply** into the sender's message and wakes the gate. An
unsolicited op `0xf0` (`0x59f03c`) feeds the op-15 state machine; not seen.

So in `darwin_sep.c`'s frame helper terms the reply is
`frame(ep, tag, status, len & 0xff, len >> 8)`, and for `getFullEpochs` that is
`{16, tag, 0, 0x20, 0}` plus 32 bytes at the endpoint's OOL out address.

## What the epoch values have to be: nothing in particular

Both consumers of the 32 bytes were read:

* `AppleSEPUserClient::DispatchGetEpochs` (`0x595d48`, the string at
  `0x70f06d`): calls `getFullEpochs(stack[8], 8)` at `0x595dc8`, then the loop
  at `0x595df4..0x595e08` copies **byte 0 of each 4-byte slot** into the
  user's buffer (`ldrb w12, [x11, x12]` after `lsl x12, x8, 2`), checked
  against `*out_struct.size_bytes_p >= sizeof(uint8_t) * epoch_slots_len`.
* `SEPEpoch::getFullEpochs` (`0x583604`): `cmp x2, 8`, call, REQUIRE
  `kIOReturnSuccess`. No look at the contents.

So `EpochSlot` is `{ u8 epoch; u8 pad[3]; }` as far as the AP reads it, and
zero is accepted. The model returns eight zero slots. That is a constant
standing in for SEP state, marked as such in the code.

## The rest of the family, for whoever meets them next

Read out of the 17 builders. `op` is byte 2 of the message; the message is
otherwise zero unless noted.

| op | builder | in | out | notes |
|---|---|---|---|---|
| `0x15` | `0x5a1b60` | none | 8 bytes, `REQUIRE out_len == 8` at `0x5a1c44` | one epoch slot; index in **byte 6** (`sturb w8, [x29, -2]` at `0x5a1c0c`, from `this+0x30`). Modelled: 8 zero bytes. |
| `0x16` | `0x5a1c5c` | 8 x u16 `Epoch` = 16 bytes (`lsl x10, x10, 1` at `0x5a1d14`) | none (`stp xzr, xzr` at `0x5a1d1c`) | `SEPEpoch::commitEpochs`; REQUIRE `EPOCH_COUNT == epochs_len`. Modelled: acknowledged, payload logged, not stored. |
| `0x17` | `0x5a1da4` | none | 32 bytes | `getFullEpochs`, above. Modelled. |
| `0x11` | `0x5a1854` | ? | ? | message `0x110000`; not decoded |
| `0x12` | `0x5a1a5c` | ? | ? | message `0x120000`; not decoded |
| `0x18` | `0x5a1ebc` | yes | yes | in and out buffers, 5 arguments; not decoded |
| `0x19`..`0x1c` | `0x5a20e8`, `0x5a21b8`, `0x5a228c`, `0x5a2374` | | | not decoded |
| blob family | `0x5a0a38`, `0x5a0b6c`, `0x5a0cac`, `0x5a1254`, `0x5a1370`, `0x5a1474`, `0x5a1584` | 32-byte arg struct | | the xART / Locker save and fetch path (`"Fetched %s-xART with CRC"`); op byte inside the struct, not decoded |

Every undecoded op gets `{status 0, length 0}` from the model. A request
without an out buffer is satisfied by that; one with an out buffer will hit
its own `REQUIRE expected_out_len == out_len`, which names the next opcode.
That is deliberate: a wrong length is a panic that says what it wanted, a
guessed payload is a silent lie.

## Result

Probe `XARTFIX` (2026-09-02): the same tree and boot-args as `CXBOOT2`
(`-enable sep -ephemeral-data -skip-keybag -dram 40G`, `rootfs_cx.dmg` as the
ramdisk, `rootdev=md0 ignition_level=1 launchd_unsecure_cache=1
vm_compression_limit=524288 serial=3 -v wdt=-1 wlan-olyhal-abort`), binary
built from submodule `fdf86bd`.

| | `CXBOOT2` (before) | `XARTFIX` (after) |
|---|---|---|
| serial lines | 42,079 to the panic | 42,078, 0 panics, still running at 45 min |
| after `SEP EP 16 enabled` | `panic ... REQUIRE fail: expected_out_len == out_len` | `SEP EP 16 disabled`, boot continues |
| model log | `xART ep 16 op 23 ... acknowledged without action` | `xART ep 16 getFullEpochs: 8 zero slots, 32 bytes` |

The guest did the work, not just "0 panics": the model's stderr shows the
request answered, and the console shows the endpoint being *disabled* again
afterwards, which is the AppleSEPXARTService releasing it after a completed
call -- the line that never printed before.

**SpringBoard is launched.** A RAM dump of the frozen guest (40 GiB in 1 GiB
chunks, `tools/guest_memgrep.py`) contains dyld's `executable_path=` string --
present only on a spawned process's stack -- for
`/System/Library/CoreServices/SpringBoard.app/SpringBoard`, for
`/usr/libexec/backboardd`, and for the `xpcproxy` that launched
`com.apple.backboardd`; neither was there in any earlier boot's dump.
`tools/oskcdata.py` over the same dump finds **no** OS_REASON record for
SpringBoard; the only records are four `'exc handler'` exits of `DumpPanic`,
a launchd early-boot task that is expected to fail with no panic to dump.

**What it does next: nothing.** After
`_process_matches_constraint: error: Requirement not satisfied: Constraint not
matched` the console goes quiet, and the CPU is idle, not stuck. Six samples of
the PC through the monitor land in the kernel at slid `0xfffffff02ab218b8`
(unslid `0xab218b8`, slide `0x20000000` -- derived from two sampled PCs both
sitting on the same `mrs x9, s3_4_c15_c10_6` timebase read, the only pair of
hits `0xe1dc` apart). The enclosing function (`0xab217a0`) is the processor
idle loop: `mrs x20, tpidr_el1`, `ldr w8, [x19]; cmp w8, 4` (processor state
IDLE), the pending-IPI bitmap tests at `cpu_data+0x3e70/0x3e80/0x3e90`, an
idle-count `ldadd` on `0xb694ff0`, a call to the WFI wrapper at `0xac6a604`,
then the timebase read. TCG returns from WFI at once, which is why QEMU sits at
99% host CPU while the guest does nothing. Every userspace process is blocked on
something; the serial log is complete (the kernel msgbuf's write head is the
constraint line, and the text after it in the ring is the oldest surviving
data, `SoundScapesPickerAssets` copies that the serial log already has 148
of).

The constraint message is not a spawn denial. It comes from AMFI's
`__mac_syscall` selector switch (`0x91af128`, `sub w16, w1, 0x5a` / 15 cases;
the logger prints `%s: error: %s` with the selector's name), i.e. a userspace
caller asking "does this process match this launch constraint" and getting
an error back. Which daemon asked, and whether SpringBoard is waiting on
backboardd for a display it cannot get in a boot with no `-enable dcp`, is the
next question, and it needs SpringBoard's own log rather than the console.

The `AppleSEPXART::getFullEpochs` panic was recorded in `CLAUDE.md` as specific
to `-enable sep` on the ANS root path. Both `CXBOOT2` and `XARTFIX` are the
plain ramdisk path with no `-enable ans`; it fires wherever the boot reaches
userspace with SEP enabled, and it is fixed by the model, not the storage path.
