# SEP: the AP-facing protocol, the boot gates, and where it stands

Source: iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140. Kexts extracted with
`ipsw kernel extract firmware/bootkc <id> -o /tmp/dvm/kexts`:
`com.apple.driver.AppleSEPManager` (928.0.2), `AppleSEPCredentialManager`,
`AppleSEPKeyStore` (2383.2.1), `AppleA7IOP`, `AppleA7IOP-ASCWrap-v6`; TXM
`firmware/txm`. Analysed 2026-09-02. All kext addresses are unslid; runtime is
`+0x20000000` (verified: `gva2gpa 0xfffffff0295a30e0` maps and the bytes
match the file at `0x5a30e0 - 0x7004000`; **`0x0b5a...` was a typo that cost an
hour, so check the arithmetic**). TXM is linear from `0xfffffff017004000`
(`otool -l firmware/txm`), so its runtime addresses are the file's.

The model is `qemu-sptm/hw/arm/darwin_sep.c`; its header comment carries the
register map and every opcode with its source. This note is the story around
it: what blocked, how each block was found, and what is still open.

## Summary

Three things stood between `AppleSEPManager::start` and a published
`sep-endpoint,scrd`, and none of them was the mailbox protocol:

1. `start()` never returned. It blocks in
   `callPlatformFunction("function-wait_for_power_gate", waitForFunction=true)`
   on the IOP nub, because that property points at `/arm-io/pmgr` (phandle
   `0x22`) whose driver `dt_fixup.py` strips. Found with gdbstub breakpoints on
   the calls either side (`0x5a3318` hit, `0x5a332c` never).
   **Fix: drop the property from `/arm-io/sep/iop-sep-nub`.**
2. With `start()` complete, `_setPowerState(0 -> 2)` opened the mailbox, read
   the ROM's status announce and stopped, because it only boots the SEP when
   it holds a firmware object, and normal boots get that from
   `AppleSEPFirmware::fromPreload`, gated on the nub property `sepfw-loaded`
   (`0x5a33a0`). `fromPreload` wraps `/chosen/memory-map/SEPFW` in a memory
   descriptor without reading it (`0x591df4..0x591eb4`).
   **Fix: `sepfw-loaded = u32:1` on the nub plus a zero-filled `SEPFW` region
   reserved by the loader.**
3. With the boot conversation complete and sepOS "alive", AMFI's
   `AMFIUpdateDeviceState` makes TXM read the SEP secure-channel page and TXM
   panicked `[code: 0x000000F3 | 9]`: the SCRD magic `0x5c01` was missing at
   page+0x200 (`txm 0x1704123c`). **Fix: the model writes the page through
   the DART at the dart-sep node's `txm-secure-channel-base`.**

After those, in a 120 s restore-ramdisk boot: full ROM handshake, discovery,
`AppleCredentialManager: getSEPEndpoint: SEPEndpoint enabled`, zero
`waitForSEPEndpoint` timeouts, no panic, shell reached. The earlier claim in
`sep-bringup.md` that the *class* wait succeeded and the *scrd* wait timed out
was wrong: the log line is the same for both, and before fix 1 it was the
class wait (`start()` had not called `registerService()`).

## What the AP does, in order (from the live trace, `DARWIN_SEP_DEBUG=1`)

```
AP writes I2A_CTRL enable            -> model announces ROM status: ep255 op210 data 1
ep255 tag1 op2  GET_STATUS           <- op102 data 1      "SEP status: 1"
ep255 tag1 op5  BOOT_TZ0             <- op105             "SEP accepted Tz0"
ep255 tag1 op2  GET_STATUS           <- op102 data 2      "SEP status: 2"
ep255 tag1 op6  BOOT_IMG4 param 0x20 <- op106             "SEP accepted IMG4"
                                     <- ep0 op13 NOTIFY_ALIVE, then discovery pairs
ep254 L4Info  size 0x14 pages, dva 0x10000210000            "Shmbuf for SEP"
ep0 tag8 op54 x12  GET_ENTROPY_FOR_XNU_PRNG                  FIPS reseed
ep0 tag8 op4/op2   SET_OOL_IN_SIZE/ADDR for xarm (19), then xars, sks, scrd
ep10 tag4 len36    ACM cmd 10        ep10 tag4 len40   ACM cmd 25
ep18 tag77 op1     sks IPC request, 0x5c bytes in the OOL in-buffer
```

`param 0x20` on BOOT_IMG4 is `type | slot << 4` with the default slot 2
("Setting default slot for boot param", `sep-boot-slot` absent from the DT).

### bootSEP's ART branch

`bootSEP` (`0x59bc6c`) fetches an ART and sends LOAD_SEP_ART (op 7) only when
`manager+0x158` is set, and that byte is `copyProperty("self-power-gate") !=
NULL` (`0x5a3004..0x5a3028`). Our tree has `self-power-gate` on `/arm-io/sep`,
yet no ART traffic appeared and the boot went straight to BOOT_IMG4: the
firmware-type test at `0x59c084..0x59c09c` (`type == 1 && slot[0] == 0` skips
the ART) evidently took the skip branch for the preload type 0 + default
slot combination. Not traced further because it did not bite; if a future
tree hits "SEP Boot Failure: failed to fetch ART from storage", this is where
to look, and `AppleSEPARTService` (IOProviderClass `AppleSEPManager`) is the
service it waits on with no timeout (`0x59c0b0..0x59c0cc`).

## The endpoints after discovery

| id | name | driver (from `__PRELINK_INFO`) | what it sent us |
|---|---|---|---|
| 0 | cntl | AppleSEPControl (in AppleSEPManager) | OOL setup, op 54 entropy |
| 10 | scrd | AppleCredentialManager (IOResources; waits by name) | cmd 10, cmd 25 |
| 16 | xars | AppleSEPXARTService | nothing yet |
| 18 | sks  | AppleKeyStore (IOResources; waits by name) | one IPC request |
| 19 | xarm | AppleSEPXARTService | nothing yet |

Endpoints the kernelcache knows and the model does not advertise: `log`,
`arts`, `artr`, `debu`, `unit`, `hilo`, `pair`, `sprl`, `hdcp`, `sse`.
`DARWIN_SEP_EPS=name,name` changes the list without a rebuild.

## scrd: what AppleCredentialManager sends and expects

Frame (`AppleSEPCredentialManager` receive handler `0x52a808`): byte 0 must be
10, byte 1 is the tag, bits [31:16] are a byte length, the upper 32 bits are
the SEP's status word. On receipt ACM copies `length` bytes out of the
endpoint's OOL out-buffer ("readFromSEP == msg.call.length") and wakes the
sender. Requests use the same frame with the body length; the body, captured
from the OOL in-buffer:

```
cmd 10 (36 bytes):
  01 00 1c 00 | 01 00 00 00 | 00 00 00 00 | 00 00 00 00 | 00 00 00 00 |
  00 00 00 00 | 00 00 00 00 | 44 52 43 53 | 0a 28 00 00
cmd 25 (40 bytes):
  01 00 1c 00 | 03 00 00 00 | 00 00 00 00 | ff ff ff ff | 00 00 00 00 |
  00 00 00 00 | 00 00 00 00 | 44 52 43 53 | 19 00 00 02 | 29 00 00 00
```

i.e. a 28-byte header `{u16 version 1, u16 header length 0x1c, u32 sequence
(the "~N" in the log), u32 0, u32 suid (-1 or 0), u32[3] 0, 'SCRD'}` and a
payload beginning with the command byte (`0x0a`, `0x19`). ACM logs
`cmd(25) ... outLen=1`, so it expects a one-byte answer. cmd 25 is the
developer-mode query AMFI makes on every spawn ("AMFI: trying to get
developer mode status from ACM"), which is why it matters: unanswered, each
costs 5000 ms. `DARWIN_SEP_SCRD_FAIL_FAST=1` answers `{10, tag, 0, status 1}`
at once; ACM then reports `ioErr=0x0 acmErr=1` after 1-3 ms and AMFI logs
`ACM status -536870199 ... finished: 1 1`. The status value is a placeholder.
A real answer needs the SCRD command semantics, which are not in this note.

## sks: the keystore IPC, as far as it was captured

First request after `sep-endpoint,sks` appears: frame `{18, tag 77, byte2 1,
0, data 0x005c0000}`; the top 16 bits of `data` are the IPC message size
(0x5c = 92), and the 92 bytes in the OOL in-buffer are:

```
48 00 00 00 | 71 8c e9 bc 9f 95 48 e9 f7 59 2f 2d 8f 50 ed 7d | 01 00 00 00 |
fc fa 26 00 00 00 00 00 | 00 ... (zeros to 0x5c)
```

which is the `AppleKeyStore` `ipc.c` header (`__PRELINK_INFO` lists the kext
source as `Sources/AppleKeyStore/ipc.c`): `u32 header body size (0x48)`,
`u8[16] payload hash`, `u32 ipc_version (1)`, `u64 time_msecs`, then flags /
id / proc uid / audit session / digest fields, all zero here. The kext logs
"negotiated to ipc header theirs:v%llu, ours:v%u" on the first reply, so the
first exchange is a version negotiation and the reply must carry a header of
its own; it retries every ~5 s ("sks timeout strike N") until answered.
`AppleKeyStore` is what `keybagd`'s `aks_get_system()` lands in
(`docs/re/keybag.md`), so this is the keybag's next gate. The public
reference for the reply shapes is Inferno's `sep-sim.c` keystore handler
(AGPL-3.0: facts only, not code); it should be re-derived from
`AppleSEPKeyStore` here because the message code space has moved (77 is not
in their table).

## The TXM secure channel

`firmware/txm`, functions by file address (= runtime):

* `0x1704123c`: `get_shared_page(&p)`, then `*(u32*)(p+0x200) & 0xffff` must
  be `0x5c01` else it returns 9. Bit 16 is printed as "SCRD | xART: %u".
* `0x17033954`: calls the above and panics `(0xf3, rc)` on failure -- the
  `TXM [Panic]: [code: 0x000000F3 | 9]` we hit. Reached from `0x17033b8c`,
  which is what AMFI's `AMFIUpdateDeviceState` selects (backtrace frames
  `0xfffffff01703b50c` -> `0x17033bd4` -> `0x170339cc`).
* `0x17033bd4..0x17033c40`: with the xART bit clear it takes the no-xART path
  and never reads the seqlock record at `+0x290`; with it set it would read
  that record (`0x17041328`) and derive Lockdown / Demo mode.
* `0x1704129c`: the lockdown reader wants `u16 0x5c02` at `+0x400` and reads
  the flag byte at `+0x448`.

The page's DVA is `txm-secure-channel-base` on `/arm-io/dart-sep`
(`0x10000004000`, size `0x4000`, equal to the DART's `vm-base-0`), and the AP
maps it before booting the SEP (`AppleSEPManager::_loadFlagData: Detected
hardcoded TXM-SEP secure channel DVA`). The model writes `0x00005c01`,
`0x5c02`, `0` at those offsets once the mapping translates (retried from every
AP message until it does).

## Device tree and loader changes this needed

All in the same commits, flagged for the orchestrator to redo in their own
style:

* `dt_fixup.py fixup_sep()`: remove `function-wait_for_power_gate` from the
  nub; add `sepfw-loaded = u32:1` to the nub; add a `SEPFW` placeholder to
  `/chosen/memory-map`.
* `xnuboot_sptm.c`: reserve `SEPFW_RESERVED_SIZE` (2 MiB) after the RAMDisk
  and fill the entry, only when the placeholder exists.
* `darwin.c`: `darwin_sep_create()` and `"sep"` in `claimed_ascs`.

## Method notes worth keeping

* `tools/probe.sh --keep` then `tools/hmp.py <sock> "gva2gpa <va>"` is the
  cheap way to confirm a slide; `memory read` from lldb fails while the CPU
  sits in the idle loop.
* lldb attaches to `-s -S` fine (`gdb-remote 1234`), but batch output is lost
  unless it runs under `script -q`, and macOS has no `timeout`; the wrapper is
  in the session scratchpad (`run_lldb_trace.sh`) and should move to
  `tools/` if it is used again.
* r2's Mach-O loader mis-maps the TXM; `r2 -n -a arm -b 64 -m
  0xfffffff017004000 firmware/txm` works.
* Vtable entries in the extracted kexts are chained fixups: target =
  `0xfffffff007004000 + (q & 0xffffffff)`; many point at a `bti c` landing pad
  four bytes before the prologue r2 shows.
