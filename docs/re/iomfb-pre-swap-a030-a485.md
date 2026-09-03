# iOS 27 IOMFB calls before the A385 poll loop

## Source metadata

This note covers iOS 27.0 beta 8 (24A5430a), iPhone17,3 / t8140 (H17P),
using `firmware/bootkc`.  The AP wrappers are in the stripped
`com.apple.iokit.IOMobileGraphicsFamily-DCP` fileset image (UUID
`BCADE513-BD2C-39D1-A3D6-C3A21F3AD63E`); the SoC-specific `A030` sender is in
the stripped `com.apple.driver.AppleMobileDispH17P-DCP` image.  Static VAs are
unslid and the runtime address used by the current guest is `static +
0x20000000`; dynamic evidence is
`/tmp/dvm/probe/UI_OP19_DCP1.stderr.log`.

## Summary

The observed pre-poll sequence has fourteen write-only `A030` calls, then
`A414`, `A466`, `A485`, the separately overridden `A412`, `A484`, `A442`, and
only then the first `A385` poll.  `A414`, `A415`, `A458`, and `A484` copy reply
payload to their caller and separately forward a returned word to a common
imported tail, while
`A466` and `A030` have no reply buffer at all.  `A485` is the only listed
wrapper whose reply is directly reduced to a Boolean (`out[0] & 1`), but the
current all-zero reply has not been shown to gate `A407`/`A408`, a surface map,
or a swap submission; no evidence-backed render-submission gate is known yet.

## Observed ordering

The following is one continuous AP-to-DCP sequence in the current run:

```
#24 A415 (12/12)                         stderr:8592-8603
#25 A458 (4/4)                           stderr:8599-8603
#26-28 A442 (92/4100), then              stderr:8605-8639
#29-42 A030 (108/0), 14 calls            stderr:8641-8764
#43 A414 (8/8), #44 A466 (0/0),          stderr:12343-12354
#45 A485 (0/4), #46 A412 (0/4),          stderr:12356-12365
#47 A484 (16/8), #48 A442 (92/4100),     stderr:12367-12384
#49 A385 (0/4), then the poll loop.      stderr:12386 onward
```

`A412` is intentionally nonzero in this run (`01 00 00 00` is configured at
`stderr:35` and reported applied at `stderr:5373-5377` and `12362-12365`).
Thus this sample is specifically a test where the other known one-bit result
was already enabled before `A484` and the A385 loop.  It contains no `A407`,
`A408`, `D589`, `D591`, or `surface_map` trace line (the `rg` scan of that log
returned no match).

## Wire layout and reply consumption

| RPC | static wrapper / runtime entry | request and reply size | AP-side reply consumption | Evidence |
|---|---|---:|---|---|
| `A415` | `0xfffffff00a0c95dc` / `0xfffffff02a0c95dc` | 12 / 12 | The request is a caller-supplied `u64` at `in+0`, a byte null flag at `in+8`, and three initialized padding bytes.  The wrapper copies `out[0..7]` to the caller when non-NULL, then passes `u32 out+8` to imported tail `0xa0dc4a0`. | Input construction `0xa0c95f0-0xa0c9630`; call lengths `0xa0c9650-0xa0c966c`; copyback/word load `0xa0c9670-0xa0c9680`; live request `stderr:8592-8597`. |
| `A458` | `0xfffffff00a0cb560` / `0xfffffff02a0cb560` | 4 / 4 | The request is its `w1` argument; the wrapper reads only `u32 out+0` and forwards it to imported tail `0xa0dc4a0`. | `str w1, [x29,-4]` at `0xa0cb578`; lengths at `0xa0cb5a0-0xa0cb5ac`; `ldr w0,[sp,#8]` at `0xa0cb5b8`; live request `stderr:8599-8603`. |
| `A030` | `0xfffffff009179fc4` / `0xfffffff029179fc4` | 108 / 0 | No AP reply exists for this call.  The wrapper copies exactly 27 `u32` values from its second argument into the request and returns immediately after dispatch. | Copy loop `0x9179ff4-0x917a004`; `A030`, `in_len=0x6c`, `out_len=0` at `0x917a024-0x917a040`; live #29-42 at `stderr:8641-8764`. |
| `A414` | `0xfffffff00a0c9528` / `0xfffffff02a0c9528` | 8 / 8 | The request contains a caller `u32` at `in+0` and byte null flag at `in+4`.  If the caller supplied an output pointer, the wrapper stores `u32 out+0` there; it separately passes `u32 out+4` to imported tail `0xa0dc4a0`. | Construction `0xa0c953c-0xa0c9578`; lengths `0xa0c95a0-0xa0c95ac`; copyback/word load `0xa0c95b8-0xa0c95c8`; live request `stderr:12343-12348`. |
| `A466` | `0xfffffff00a0cb9d8` / `0xfffffff02a0cb9d8` | 0 / 0 | A tail dispatch with all pointer and length arguments zero; it has no response field to consume. | Zero argument setup and tail branch `0xa0cb9dc-0xa0cba20`; live request `stderr:12350-12354`. |
| `A485` | `0xfffffff00a0cc50c` / `0xfffffff02a0cc50c` | 0 / 4 | The wrapper initializes the four-byte reply, sends no request body, and returns only `out[0] & 1`.  It neither has nor reads a separate status word. | Zero input / four-byte output `0xa0cc51c-0xa0cc55c`; `ldurb` and `and #1` `0xa0cc560-0xa0cc564`; live all-zero reply `stderr:12356-12360`. |
| `A484` | `0xfffffff00a0cc458` / `0xfffffff02a0cc458` | 16 / 8 | Request `in+0` is the first `u64` argument; bytes `+8..+11` are four scalar argument bytes and `+12` is the null flag for the caller's output pointer.  With that pointer present it receives `u32 out+0`; `u32 out+4` goes to imported tail `0xa0dc4a0`. | Packing `0xa0cc470-0xa0cc4a8`; lengths `0xa0cc4d0-0xa0cc4dc`; copyback/word load `0xa0cc4e8-0xa0cc4f8`; observed request begins with `u64 1` at `stderr:12367-12372`. |

The common dispatch at AP vtable offset `+0x8b0` is the interface used by the
five base-family wrappers (`ldr x8,[x16]` after `add x16,#0x8b0`; for example
`A414` at `0xa0c958c-0xa0c95b4`).  The AppleCLCD2 / UnifiedPipeline2 vtable
maps that slot to `0xfffffff00918733c`, PAC modifier `0xfd64`
(`/tmp/dvm/cpp_appleclcd2_d120.txt:280`).  That override loads its backing
link object from `self+0x5e28` and forwards to the generic RPC dispatcher at
`0x9187340-0x918734c`; independently, the `A030` wrapper constructs its
108-byte request at `0x9179fc4-0x917a040`.  This locates both sides in the AP
driver and avoids assigning a DCP-firmware semantic name to `A030`.

## What the zero replies do and do not prove

The all-zero replies for `A415`, `A458`, `A414`, and `A484` set their visible
copyback fields and the word forwarded to `0xa0dc4a0` to zero.  The imported
tail's semantic name is unverified here; the wrappers prove only that their
own immediate result path receives zero.  Their callers may still
interpret copyback data, and no direct callsite/backtrace for those virtual
wrappers was captured in this run.  `A030` and `A466` cannot be response gates
in this sample because their wire `out_len` is zero.

`A485=0` is different: the wrapper's architectural return is definitely false
because `out[0] & 1` at `0xa0cc560-0xa0cc564` receives an all-zero four-byte
reply.  It remains an unverified hypothesis that changing that bit could lead
to a render client: the exact same run proceeds from this false result through
the `A412=1` override, `A484`, and `A442` before entering 9,621 logged `A385`
requests, and still never constructs `A407` or `A408`.  Therefore claiming
that any listed zero reply is a real render-submission gate would overstate the
evidence.

## Ranked next experiment

1. Run the identical delayed-D575 / `A412=1` harness with **only** an `A485`
   output override of `01 00 00 00`.  Capture the first occurrence of runtime
   `0xfffffff02a0cc560` and its return address, then require an independent
   result: first hits at `0xfffffff02a0c8f8c` (`A407`) or
   `0xfffffff02a0c91d8` (`A408`), followed by `0xfffffff02a0b9600`
   (surface-map path).  A mere different A385 count is not success.
2. If no submission wrapper hits, repeat with breakpoints at the post-RPC
   sites `0xfffffff02a0c9670` (`A415`), `0xfffffff02a0cb5b8` (`A458`),
   `0xfffffff02a0c95b8` (`A414`), and `0xfffffff02a0cc4e8` (`A484`).  Record
   `x30`/a backtrace and the reply bytes before their copyback/status reads;
   that identifies which caller, if any, branches on a currently-zero
   payload field.
3. Do not mutate `A030` or `A466`: static code shows neither consumes a DCP
   output.  Their useful next evidence is an AP callsite/backtrace identifying
   the structure that supplies A030's 27 words, not a fabricated reply.

## Open questions

| Question | Observation that settles it |
|---|---|
| Does `A485`'s Boolean control a path relevant to submission? | A single-variable `A485=1` run which either hits `A407`/`A408` and surface-map or repeats the existing A385-only result, with no other reply changes. |
| Which caller consumes `A415`/`A458`/`A414`/`A484` copyback data? | A runtime post-wrapper breakpoint recording `x30` and a stack trace for each call. |
| What does the 27-word A030 record configure? | A caller trace plus pre-call memory dump of its argument; the current wrapper only proves the length and write-only transfer. |
| Is any callback required before A407/A408? | A run showing first ordering among an A407/A408 hit, D589/D591, and `0xfffffff02a0b9600`; this log contains none of those events. |
